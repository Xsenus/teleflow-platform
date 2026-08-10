from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from app.enums import (
    IncidentSource,
    IncidentStatus,
    ReadinessStatus,
    SafetySeverity,
    SLOAssessmentSource,
)
from app.services import operations
from app.services.operations import OperationsError


class ScalarList:
    """Имитировать SQLAlchemy ScalarResult для worker-обхода организаций."""

    def __init__(self, values: list[object]) -> None:
        """Сохранить подготовленные scalar-значения."""

        self.values = values

    def all(self) -> list[object]:
        """Вернуть подготовленные идентификаторы организаций или incidents."""

        return self.values


class OperationsDb:
    """Предоставить управляемые scalar/get/add/flush ответы сервису Operations."""

    def __init__(
        self,
        *,
        scalar_values: list[object | None] | None = None,
        scalar_lists: list[list[object]] | None = None,
        get_value: object | None = None,
    ) -> None:
        """Сохранить ответы запросов и mutation counters."""

        self.scalar_values = list(scalar_values or [])
        self.scalar_lists = list(scalar_lists or [])
        self.get_value = get_value
        self.added: list[object] = []
        self.flushes = 0

    def scalar(self, _statement):  # type: ignore[no-untyped-def]
        """Вернуть следующий подготовленный scalar-ответ."""

        return self.scalar_values.pop(0) if self.scalar_values else None

    def scalars(self, _statement) -> ScalarList:  # type: ignore[no-untyped-def]
        """Вернуть следующую подготовленную scalar-коллекцию."""

        values = self.scalar_lists.pop(0) if self.scalar_lists else []
        return ScalarList(values)

    def get(self, _model, _identity):  # type: ignore[no-untyped-def]
        """Вернуть подготовленную организацию для SLO evaluation."""

        return self.get_value

    def add(self, value: object) -> None:
        """Сохранить созданную policy, assessment или incident event."""

        self.added.append(value)

    def flush(self) -> None:
        """Зафиксировать ORM flush без реальной базы."""

        self.flushes += 1

    @contextmanager
    def begin_nested(self):  # type: ignore[no-untyped-def]
        """Открыть фиктивный tenant savepoint worker-оценки."""

        yield


def _policy(**overrides):  # type: ignore[no-untyped-def]
    """Создать полный минимальный SLO policy для snapshot и evaluation."""

    values = {
        "id": "policy-id",
        "enabled": True,
        "evaluation_window_hours": 1,
        "delivery_success_target_bps": 9900,
        "minimum_delivery_sample_size": 1,
        "max_queue_age_seconds": 300,
        "max_worker_heartbeat_age_seconds": 60,
        "max_unresolved_delivery_reviews": 0,
        "max_open_critical_incidents": 0,
        "error_budget_warning_percent": 50,
        "error_budget_critical_percent": 200,
        "assessment_ttl_minutes": 30,
        "gate_publishing": True,
        "gate_changes": True,
        "auto_create_incidents": True,
        "auto_resolve_incidents": True,
        "suppress_incidents_during_maintenance": True,
        "updated_by_id": None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_policy_creation_handles_missing_tenant_and_concurrent_winner() -> None:
    """Проверить ошибку отсутствующей организации и повторное чтение после unique-race."""

    settings = SimpleNamespace()
    missing_db = OperationsDb(scalar_values=[None, None])
    with pytest.raises(OperationsError, match="Организация не найдена"):
        operations.get_or_create_slo_policy(
            missing_db,  # type: ignore[arg-type]
            organization_id="missing",
            settings=settings,  # type: ignore[arg-type]
        )

    winner = _policy()
    race_db = OperationsDb(scalar_values=[None, SimpleNamespace(id="org"), winner])
    assert (
        operations.get_or_create_slo_policy(
            race_db,  # type: ignore[arg-type]
            organization_id="org",
            settings=settings,  # type: ignore[arg-type]
        )
        is winner
    )


@pytest.mark.parametrize(
    "values",
    [
        {"error_budget_warning_percent": 100, "error_budget_critical_percent": 100},
        {"enabled": False},
        {"gate_publishing": False},
        {"gate_changes": False},
    ],
)
def test_policy_update_rejects_invalid_production_controls(values: dict) -> None:
    """Проверить threshold и обязательные production SLO gates в сервисном слое."""

    policy = _policy()
    settings = SimpleNamespace(is_production=True, slo_gate_required=True)
    with pytest.raises(OperationsError):
        operations.update_slo_policy(
            OperationsDb(),  # type: ignore[arg-type]
            policy=policy,  # type: ignore[arg-type]
            values=values,
            user=SimpleNamespace(id="user-id"),  # type: ignore[arg-type]
            settings=settings,  # type: ignore[arg-type]
        )


def test_create_incident_rejects_unknown_owner() -> None:
    """Проверить tenant-scoped active owner до создания incident."""

    with pytest.raises(OperationsError, match="Ответственный пользователь не найден"):
        operations.create_incident(
            OperationsDb(scalar_values=[None]),  # type: ignore[arg-type]
            organization_id="organization-id",
            title="Incident",
            summary="Owner validation",
            severity=SafetySeverity.WARNING,
            source=IncidentSource.MANUAL,
            actor=None,
            settings=SimpleNamespace(),  # type: ignore[arg-type]
            owner_user_id="missing-owner",
        )


def test_sync_slo_incident_updates_existing_and_honors_auto_resolve_off() -> None:
    """Проверить dedup-update существующего SLO incident без автоматического разрешения."""

    existing = SimpleNamespace(
        linked_slo_assessment_id=None,
        metadata_payload={"old": True},
    )
    db = OperationsDb(scalar_values=[existing])
    assessment = SimpleNamespace(
        id="assessment-id",
        checks=[
            {
                "code": "worker",
                "status": "blocked",
                "observed": 500,
                "limit": 60,
            }
        ],
    )
    operations._sync_slo_incidents(
        db,  # type: ignore[arg-type]
        organization=SimpleNamespace(id="organization-id", maintenance_mode=False),  # type: ignore[arg-type]
        policy=SimpleNamespace(
            auto_create_incidents=True,
            auto_resolve_incidents=False,
            suppress_incidents_during_maintenance=True,
        ),  # type: ignore[arg-type]
        assessment=assessment,  # type: ignore[arg-type]
        actor=None,
        settings=SimpleNamespace(),  # type: ignore[arg-type]
    )

    assert existing.linked_slo_assessment_id == "assessment-id"
    assert existing.metadata_payload["latest_observed"] == 500
    assert existing.metadata_payload["latest_limit"] == 60


def _evaluate(
    monkeypatch,
    *,
    policy: object,
    counts: list[int],
    heartbeat_age: int,
) -> object:  # type: ignore[no-untyped-def]
    """Выполнить SLO evaluation с детерминированными агрегатами и heartbeat."""

    now = datetime(2026, 8, 10, 12, tzinfo=UTC)
    organization = SimpleNamespace(id="organization-id", maintenance_mode=False)
    db = OperationsDb(
        scalar_values=[policy, None],
        get_value=organization,
    )
    count_values = list(counts)
    monkeypatch.setattr(operations, "get_or_create_slo_policy", lambda *_args, **_kwargs: policy)
    monkeypatch.setattr(operations, "_count", lambda *_args, **_kwargs: count_values.pop(0))
    monkeypatch.setattr(
        operations,
        "_last_worker_heartbeat",
        lambda _db: SimpleNamespace(
            worker_id="worker-id",
            last_seen_at=now - timedelta(seconds=heartbeat_age),
        ),
    )
    monkeypatch.setattr(operations, "_sync_slo_incidents", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(operations, "write_audit", lambda *_args, **_kwargs: None)
    return operations.evaluate_slo(
        db,  # type: ignore[arg-type]
        organization_id="organization-id",
        settings=SimpleNamespace(),  # type: ignore[arg-type]
        source=SLOAssessmentSource.MANUAL,
        now=now,
    )


def test_evaluate_slo_covers_zero_failure_target_disabled_policy_and_stale_worker(
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    """Проверить target=100%, observation-only warning, passed budget и stale heartbeat."""

    policy = _policy(
        enabled=False,
        delivery_success_target_bps=10000,
        error_budget_warning_percent=50,
        error_budget_critical_percent=200,
    )
    assessment = _evaluate(
        monkeypatch,
        policy=policy,
        counts=[1, 0, 0, 0, 0],
        heartbeat_age=120,
    )

    checks = {item["code"]: item for item in assessment.checks}
    assert assessment.error_budget_consumed_bps == 0
    assert checks["policy_disabled"]["status"] == "warning"
    assert checks["delivery_error_budget"]["status"] == "passed"
    assert checks["worker_heartbeat"]["status"] == "blocked"


def test_evaluate_slo_reports_warning_budget_band(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Проверить warning band расхода error budget между warning и critical thresholds."""

    policy = _policy(
        delivery_success_target_bps=9000,
        error_budget_warning_percent=50,
        error_budget_critical_percent=200,
    )
    assessment = _evaluate(
        monkeypatch,
        policy=policy,
        counts=[9, 1, 0, 0, 0],
        heartbeat_age=0,
    )

    budget = next(item for item in assessment.checks if item["code"] == "delivery_error_budget")
    assert assessment.error_budget_consumed_bps == 10000
    assert budget["status"] == "warning"


def test_evaluate_slo_rejects_missing_organization() -> None:
    """Проверить отсутствие tenant до чтения policy и метрик."""

    with pytest.raises(OperationsError, match="Организация не найдена"):
        operations.evaluate_slo(
            OperationsDb(get_value=None),  # type: ignore[arg-type]
            organization_id="missing",
            settings=SimpleNamespace(),  # type: ignore[arg-type]
            source=SLOAssessmentSource.MANUAL,
        )


def test_slo_gate_blocks_disabled_policy_and_excess_critical_incidents(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Проверить disabled policy и повторную проверку числа critical incidents."""

    disabled = _policy(enabled=False)
    decision = operations.slo_gate_decision(
        OperationsDb(scalar_values=[disabled]),  # type: ignore[arg-type]
        organization_id="organization-id",
        settings=SimpleNamespace(slo_gate_required=True),  # type: ignore[arg-type]
        gate="publishing",
    )
    assert decision.allowed is False and "отключена" in decision.message

    enabled = _policy(max_open_critical_incidents=0)
    assessment = SimpleNamespace(status=ReadinessStatus.PASSED, blockers=[])
    monkeypatch.setattr(operations, "latest_slo_assessment", lambda *_args, **_kwargs: assessment)
    monkeypatch.setattr(operations, "assessment_is_current", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(operations, "_count", lambda *_args, **_kwargs: 1)
    decision = operations.slo_gate_decision(
        OperationsDb(scalar_values=[enabled]),  # type: ignore[arg-type]
        organization_id="organization-id",
        settings=SimpleNamespace(slo_gate_required=True),  # type: ignore[arg-type]
        gate="publishing",
    )
    assert decision.allowed is False and "критических" in decision.message


def test_update_incident_validates_owner_and_normalizes_optional_fields(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Проверить owner validation/assignment и очистку root cause/postmortem значений."""

    actor = SimpleNamespace(id="actor-id")
    incident = SimpleNamespace(
        id="incident-id",
        organization_id="organization-id",
        owner_user_id=None,
        severity=SafetySeverity.WARNING,
        status=IncidentStatus.OPEN,
        impact=None,
        root_cause="old",
        postmortem_url="old",
    )
    with pytest.raises(OperationsError, match="Ответственный пользователь не найден"):
        operations.update_incident(
            OperationsDb(scalar_values=[None]),  # type: ignore[arg-type]
            incident=incident,  # type: ignore[arg-type]
            actor=actor,  # type: ignore[arg-type]
            owner_user_id="missing",
        )

    monkeypatch.setattr(operations, "append_incident_event", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(operations, "write_audit", lambda *_args, **_kwargs: None)
    owner = SimpleNamespace(display_name="Дежурный")
    db = OperationsDb(scalar_values=[owner])
    operations.update_incident(
        db,  # type: ignore[arg-type]
        incident=incident,  # type: ignore[arg-type]
        actor=actor,  # type: ignore[arg-type]
        owner_user_id="owner-id",
        root_cause="   ",
        postmortem_url="   ",
    )
    assert incident.owner_user_id == "owner-id"
    assert incident.root_cause is None
    assert incident.postmortem_url is None


@pytest.mark.parametrize(
    ("action", "status"),
    [
        ("acknowledge", IncidentStatus.RESOLVED),
        ("mitigate", IncidentStatus.RESOLVED),
        ("resolve", IncidentStatus.RESOLVED),
        ("close", IncidentStatus.OPEN),
        ("reopen", IncidentStatus.OPEN),
        ("unknown", IncidentStatus.OPEN),
    ],
)
def test_transition_incident_rejects_invalid_state_or_action(
    action: str,
    status: IncidentStatus,
) -> None:
    """Проверить fail-closed state machine для каждого недопустимого transition."""

    incident = SimpleNamespace(status=status)
    with pytest.raises(OperationsError):
        operations.transition_incident(
            OperationsDb(),  # type: ignore[arg-type]
            incident=incident,  # type: ignore[arg-type]
            action=action,
            note="Недопустимый переход",
            actor=SimpleNamespace(id="actor-id"),  # type: ignore[arg-type]
            settings=SimpleNamespace(),  # type: ignore[arg-type]
        )


def test_transition_comment_can_assign_owner(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Проверить делегирование owner update перед допустимым comment event."""

    calls: list[str] = []
    monkeypatch.setattr(
        operations,
        "update_incident",
        lambda *_args, **_kwargs: calls.append("owner"),
    )
    monkeypatch.setattr(operations, "append_incident_event", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(operations, "write_audit", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(operations, "create_notification", lambda *_args, **_kwargs: None)
    incident = SimpleNamespace(
        id="incident-id",
        organization_id="organization-id",
        status=IncidentStatus.OPEN,
        severity=SafetySeverity.WARNING,
        title="Incident",
    )
    operations.transition_incident(
        OperationsDb(),  # type: ignore[arg-type]
        incident=incident,  # type: ignore[arg-type]
        action="comment",
        note="Комментарий",
        actor=SimpleNamespace(id="actor-id"),  # type: ignore[arg-type]
        settings=SimpleNamespace(),  # type: ignore[arg-type]
        owner_user_id="owner-id",
    )
    assert calls == ["owner"]


def test_transition_resolve_normalizes_postmortem_url(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Проверить сохранение нормализованной postmortem-ссылки при resolve."""

    monkeypatch.setattr(operations, "append_incident_event", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(operations, "write_audit", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(operations, "create_notification", lambda *_args, **_kwargs: None)
    incident = SimpleNamespace(
        id="incident-id",
        organization_id="organization-id",
        status=IncidentStatus.OPEN,
        severity=SafetySeverity.WARNING,
        title="Incident",
        resolved_at=None,
        resolved_by_id=None,
        resolution_summary=None,
        root_cause=None,
        postmortem_url=None,
    )
    operations.transition_incident(
        OperationsDb(),  # type: ignore[arg-type]
        incident=incident,  # type: ignore[arg-type]
        action="resolve",
        note="Устранено",
        actor=SimpleNamespace(id="actor-id"),  # type: ignore[arg-type]
        settings=SimpleNamespace(),  # type: ignore[arg-type]
        postmortem_url="  https://example.com/postmortem  ",
    )
    assert incident.postmortem_url == "https://example.com/postmortem"


def test_due_policy_worker_skips_disabled_and_contains_tenant_failure(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Проверить disabled policy и изоляцию исключения tenant savepoint в worker cycle."""

    db = OperationsDb(scalar_lists=[["disabled", "broken"]])
    policies = iter([_policy(enabled=False), RuntimeError("bad tenant")])

    def next_policy(*_args, **_kwargs):  # type: ignore[no-untyped-def]
        """Вернуть disabled policy, затем имитировать повреждённого tenant."""

        value = next(policies)
        if isinstance(value, Exception):
            raise value
        return value

    monkeypatch.setattr(operations, "get_or_create_slo_policy", next_policy)
    assert (
        operations.evaluate_due_slo_policies(
            db,  # type: ignore[arg-type]
            settings=SimpleNamespace(slo_auto_evaluate_minutes=5),  # type: ignore[arg-type]
        )
        == 0
    )
