from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from sqlalchemy.exc import IntegrityError

from app.enums import CampaignStatus, PreflightStatus, RolloutMode, RunStatus, ScheduleType
from app.models import Campaign, CampaignRun, Organization, User
from app.services import scheduler
from app.services.capacity import CapacityError
from app.services.scheduler import SchedulerService, next_occurrence


class ScalarRows:
    """Имитировать SQLAlchemy ScalarResult с unique().all()."""

    def __init__(self, values: list[object]) -> None:
        """Сохранить подготовленные строки запроса."""

        self.values = values

    def unique(self) -> ScalarRows:
        """Вернуть тот же результат после фиктивной дедупликации ORM-строк."""

        return self

    def all(self) -> list[object]:
        """Вернуть подготовленные ORM-строки планировщика."""

        return self.values


class ExecuteRows:
    """Имитировать результат execute().all() для approval cleanup."""

    def __init__(self, values: list[object]) -> None:
        """Сохранить пары approval request и campaign."""

        self.values = values

    def all(self) -> list[object]:
        """Вернуть подготовленные пары просроченных утверждений."""

        return self.values


class SchedulerDb:
    """Предоставить управляемую транзакционную сессию для scheduler unit-тестов."""

    def __init__(
        self,
        campaign: object | None = None,
        *,
        organization: object | None = None,
        creator: object | None = None,
        approvals: list[object] | None = None,
    ) -> None:
        """Сохранить объекты, возвращаемые ORM-запросами планировщика."""

        self.campaign = campaign
        self.organization = organization
        self.creator = creator
        self.approvals = approvals or []
        self.added: list[object] = []
        self.commits = 0
        self.expired = 0

    def __enter__(self) -> SchedulerDb:
        """Открыть фиктивную сессию контекстного менеджера."""

        return self

    def __exit__(self, *_args: object) -> None:
        """Закрыть фиктивную сессию без дополнительных действий."""

    def scalars(self, _statement) -> ScalarRows:  # type: ignore[no-untyped-def]
        """Вернуть due campaign, если она задана сценарием."""

        return ScalarRows([self.campaign] if self.campaign is not None else [])

    def execute(self, _statement) -> ExecuteRows:  # type: ignore[no-untyped-def]
        """Вернуть подготовленные пары просроченных approval requests."""

        return ExecuteRows(self.approvals)

    def get(self, model, _identity):  # type: ignore[no-untyped-def]
        """Разрешить Organization, User и повторную загрузку Campaign по типу модели."""

        if model is Organization:
            return self.organization
        if model is User:
            return self.creator
        if model is Campaign:
            return self.campaign
        return None

    @contextmanager
    def begin_nested(self):  # type: ignore[no-untyped-def]
        """Открыть фиктивный savepoint для создания запуска."""

        yield

    def expire_all(self) -> None:
        """Зафиксировать перезагрузку ORM-состояния после unique-конфликта."""

        self.expired += 1

    def add(self, value: object) -> None:
        """Сохранить добавленный запуск или job для последующих утверждений."""

        self.added.append(value)

    def flush(self) -> None:
        """Назначить идентификатор созданному CampaignRun без реальной базы."""

        for value in self.added:
            if isinstance(value, CampaignRun) and value.id is None:
                value.id = "run-id"

    def commit(self) -> None:
        """Зафиксировать факт завершения scheduler-транзакции."""

        self.commits += 1


def _campaign(now: datetime, **overrides):  # type: ignore[no-untyped-def]
    """Создать минимальную due campaign для ветвей tick и _create_run."""

    values = {
        "id": "campaign-id",
        "organization_id": "organization-id",
        "created_by_id": "creator-id",
        "connection_id": "connection-id",
        "name": "Проверка scheduler",
        "status": CampaignStatus.SCHEDULED,
        "next_run_at": now,
        "approved_at": now,
        "approved_by_id": "approver-id",
        "approved_fingerprint": "fingerprint",
        "schedule_type": ScheduleType.DAILY,
        "timezone_name": "UTC",
        "weekdays": [],
        "end_at": None,
        "destinations": [],
        "rollout_mode": RolloutMode.STANDARD,
        "rollout_batch_size": 1,
        "rollout_pause_seconds": 0,
        "rollout_failure_threshold_percent": 50,
        "rollout_require_checkpoint": False,
        "spacing_seconds": 0,
        "last_run_at": None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _patch_common_gates(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Разрешить все внешние scheduler-gates для изоляции выбранной ветви."""

    monkeypatch.setattr(
        "app.services.scheduler.scheduler_site_allowed",
        lambda *_args, **_kwargs: SimpleNamespace(allowed=True, message="ok"),
    )
    monkeypatch.setattr("app.services.scheduler.campaign_stage_blocker", lambda *_args: None)
    monkeypatch.setattr(
        "app.services.scheduler.slo_gate_decision",
        lambda *_args, **_kwargs: SimpleNamespace(allowed=True, message="ok", assessment=None),
    )
    monkeypatch.setattr("app.services.scheduler.approval_is_current", lambda _campaign: True)
    monkeypatch.setattr("app.services.scheduler.write_audit", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        "app.services.scheduler.create_notification", lambda *_args, **_kwargs: None
    )
    monkeypatch.setattr(
        "app.services.scheduler.StorageService", lambda _settings: SimpleNamespace()
    )
    monkeypatch.setattr(
        "app.services.scheduler.create_preflight_report",
        lambda *_args, **_kwargs: SimpleNamespace(
            id="report-id",
            status=PreflightStatus.PASSED,
            summary={},
            blockers=[],
            warnings=[],
        ),
    )
    monkeypatch.setattr(
        "app.services.scheduler.capacity_admission_decision",
        lambda *_args, **_kwargs: SimpleNamespace(allowed=True, message="ok", assessment=None),
    )


@pytest.mark.parametrize("mode", ["stage", "approval", "creator"])
def test_tick_pauses_campaign_for_each_pre_run_guard(monkeypatch, mode: str) -> None:  # type: ignore[no-untyped-def]
    """Проверить pilot-stage, stale approval и отсутствующего создателя до побочных эффектов."""

    now = datetime(2026, 8, 10, 10, tzinfo=UTC)
    campaign = _campaign(now)
    organization = SimpleNamespace(pilot_stage=SimpleNamespace(value="pilot"))
    creator = SimpleNamespace(id="creator-id")
    db = SchedulerDb(campaign, organization=organization, creator=creator)
    _patch_common_gates(monkeypatch)
    if mode == "stage":
        monkeypatch.setattr(
            "app.services.scheduler.campaign_stage_blocker", lambda *_args: "Этап запрещает запуск"
        )
    elif mode == "approval":
        monkeypatch.setattr("app.services.scheduler.approval_is_current", lambda _campaign: False)
    else:
        db.creator = None

    service = SchedulerService(
        lambda: db,  # type: ignore[arg-type]
        SimpleNamespace(pilot_readiness_required=False),  # type: ignore[arg-type]
    )
    assert service.tick(now=now) == 0
    assert campaign.next_run_at is None
    assert campaign.status in {CampaignStatus.PAUSED, CampaignStatus.DRAFT}
    if mode == "approval":
        assert campaign.approved_at is None
        assert campaign.approved_by_id is None
        assert campaign.approved_fingerprint is None


@pytest.mark.parametrize("error_kind", ["integrity", "capacity"])
def test_tick_contains_concurrent_run_failures(monkeypatch, error_kind: str) -> None:  # type: ignore[no-untyped-def]
    """Проверить изоляцию duplicate-run и capacity-race внутри одного scheduler tick."""

    now = datetime(2026, 8, 10, 10, tzinfo=UTC)
    campaign = _campaign(now)
    db = SchedulerDb(
        campaign,
        organization=SimpleNamespace(pilot_stage=SimpleNamespace(value="pilot")),
        creator=SimpleNamespace(id="creator-id"),
    )
    _patch_common_gates(monkeypatch)
    service = SchedulerService(
        lambda: db,  # type: ignore[arg-type]
        SimpleNamespace(pilot_readiness_required=False),  # type: ignore[arg-type]
    )
    if error_kind == "integrity":
        monkeypatch.setattr(
            service,
            "_create_run",
            lambda *_args: (_ for _ in ()).throw(
                IntegrityError("insert", {}, RuntimeError("duplicate"))
            ),
        )
    else:
        monkeypatch.setattr(
            service,
            "_create_run",
            lambda *_args: (_ for _ in ()).throw(CapacityError("Лимит изменился")),
        )

    assert service.tick(now=now) == 0
    if error_kind == "integrity":
        assert db.expired == 1
        assert campaign.next_run_at == next_occurrence(campaign, now)
    else:
        assert campaign.status == CampaignStatus.PAUSED
        assert campaign.next_run_at is None


def test_expire_due_approvals_skips_already_resolved_request(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Проверить идемпотентный пропуск approval request, закрытого конкурентным процессом."""

    pair = (SimpleNamespace(), SimpleNamespace())
    db = SchedulerDb(approvals=[pair])
    monkeypatch.setattr("app.services.scheduler.expire_approval_request", lambda *_args: False)
    service = SchedulerService(lambda: db, SimpleNamespace())  # type: ignore[arg-type]

    assert (
        service._expire_due_approval_requests(
            db,  # type: ignore[arg-type]
            now=datetime(2026, 8, 10, tzinfo=UTC),
            limit=10,
        )
        == 0
    )


@pytest.mark.parametrize("gate", ["execution", "stage", "slo", "capacity"])
def test_create_run_rejects_each_runtime_gate(monkeypatch, gate: str) -> None:  # type: ignore[no-untyped-def]
    """Проверить повторную проверку всех runtime-gates непосредственно перед созданием jobs."""

    now = datetime(2026, 8, 10, 10, tzinfo=UTC)
    campaign = _campaign(now)
    db = SchedulerDb(organization=SimpleNamespace())
    _patch_common_gates(monkeypatch)
    if gate == "execution":
        monkeypatch.setattr(
            "app.services.scheduler.scheduler_site_allowed",
            lambda *_args, **_kwargs: SimpleNamespace(allowed=False, message="standby"),
        )
    elif gate == "stage":
        monkeypatch.setattr(
            "app.services.scheduler.campaign_stage_blocker", lambda *_args: "pilot blocked"
        )
    elif gate == "slo":
        monkeypatch.setattr(
            "app.services.scheduler.slo_gate_decision",
            lambda *_args, **_kwargs: SimpleNamespace(allowed=False, message="slo blocked"),
        )
    else:
        monkeypatch.setattr(
            "app.services.scheduler.capacity_admission_decision",
            lambda *_args, **_kwargs: SimpleNamespace(allowed=False, message="capacity blocked"),
        )
    service = SchedulerService(lambda: db, SimpleNamespace())  # type: ignore[arg-type]

    expected = CapacityError if gate == "capacity" else ValueError
    with pytest.raises(expected):
        service._create_run(db, campaign, now)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("schedule_type", "ends_now", "expected_campaign_status"),
    [
        (ScheduleType.ONCE, True, CampaignStatus.FAILED),
        (ScheduleType.DAILY, True, CampaignStatus.COMPLETED),
        (ScheduleType.DAILY, False, CampaignStatus.SCHEDULED),
    ],
)
def test_create_empty_run_finishes_without_jobs(
    monkeypatch,
    schedule_type: ScheduleType,
    ends_now: bool,
    expected_campaign_status: CampaignStatus,
) -> None:  # type: ignore[no-untyped-def]
    """Проверить конечные статусы one-shot и recurring кампании без доступных направлений."""

    now = datetime(2026, 8, 10, 10, tzinfo=UTC)
    campaign = _campaign(now, schedule_type=schedule_type, end_at=now if ends_now else None)
    db = SchedulerDb(organization=SimpleNamespace())
    _patch_common_gates(monkeypatch)
    service = SchedulerService(lambda: db, SimpleNamespace())  # type: ignore[arg-type]

    service._create_run(db, campaign, now)  # type: ignore[arg-type]

    run = next(value for value in db.added if isinstance(value, CampaignRun))
    assert run.status == RunStatus.FAILED
    assert run.finished_at is not None
    assert campaign.status == expected_campaign_status


def test_next_occurrence_fails_closed_if_weekday_search_yields_nothing(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Проверить защитный возврат None при повреждённом механизме перебора дней."""

    now = datetime(2026, 8, 10, 10, tzinfo=UTC)
    campaign = _campaign(now, schedule_type=ScheduleType.WEEKLY, weekdays=[1])
    monkeypatch.setattr(scheduler, "range", lambda *_args: [], raising=False)

    assert next_occurrence(campaign, now) is None
