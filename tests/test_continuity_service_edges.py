from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from app.enums import (
    ContinuityDrillEventType,
    ContinuityDrillMode,
    ContinuityDrillStatus,
    ExecutionLeaseStatus,
    FailoverRequestStatus,
)
from app.services import continuity
from app.services.continuity import ContinuityError
from app.services.execution import ExecutionError

NOW = datetime(2026, 8, 10, 12, tzinfo=UTC)


class ScalarRows:
    """Имитировать SQLAlchemy ScalarResult с методом all()."""

    def __init__(self, values: list[object]) -> None:
        """Сохранить подготовленные строки."""

        self.values = values

    def all(self) -> list[object]:
        """Вернуть подготовленные строки."""

        return self.values


class ContinuityDb:
    """Предоставить детерминированные scalar/get/scalars для service unit-тестов."""

    def __init__(
        self,
        *,
        scalar_values: list[object | None] | None = None,
        get_values: list[object | None] | None = None,
        scalar_lists: list[list[object]] | None = None,
    ) -> None:
        """Сохранить очереди ответов и mutation counters."""

        self.scalar_values = list(scalar_values or [])
        self.get_values = list(get_values or [])
        self.scalar_lists = list(scalar_lists or [])
        self.added: list[object] = []
        self.flushes = 0

    def scalar(self, _statement):  # type: ignore[no-untyped-def]
        """Вернуть очередной scalar-ответ."""

        return self.scalar_values.pop(0) if self.scalar_values else None

    def get(self, _model, _identifier):  # type: ignore[no-untyped-def]
        """Вернуть очередной primary-key ответ."""

        return self.get_values.pop(0) if self.get_values else None

    def scalars(self, _statement) -> ScalarRows:  # type: ignore[no-untyped-def]
        """Вернуть очередную scalar-коллекцию."""

        return ScalarRows(self.scalar_lists.pop(0) if self.scalar_lists else [])

    def add(self, value: object) -> None:
        """Сохранить добавленный ORM-объект."""

        self.added.append(value)

    def flush(self) -> None:
        """Учесть flush без базы данных."""

        self.flushes += 1

    def begin_nested(self) -> SimpleNamespace:
        """Открыть фиктивный savepoint context."""

        return SimpleNamespace(
            __enter__=lambda: None,
            __exit__=lambda *_args: None,
        )


def _settings(**overrides: object) -> SimpleNamespace:
    """Создать минимальные continuity/execution настройки."""

    values: dict[str, object] = {
        "continuity_assurance_required": True,
        "continuity_default_require_live_drill": True,
        "continuity_default_max_rto_seconds": 300,
        "continuity_default_evidence_valid_days": 30,
        "continuity_require_distinct_signoff": True,
        "execution_primary_site_key": "primary",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _policy(**overrides: object) -> SimpleNamespace:
    """Создать policy со стабильным fingerprint payload."""

    values: dict[str, object] = {
        "enabled": True,
        "require_live_drill": True,
        "max_rto_seconds": 300,
        "evidence_valid_days": 30,
        "require_distinct_signoff": True,
        "updated_at": NOW,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _drill(**overrides: object) -> SimpleNamespace:
    """Создать минимальный continuity drill для guards и semantics."""

    values: dict[str, object] = {
        "id": "drill-id",
        "organization_id": "org",
        "mode": ContinuityDrillMode.LIVE,
        "status": ContinuityDrillStatus.DRAFT,
        "source_site_key": "primary",
        "target_site_key": "standby",
        "source_epoch": 3,
        "target_epoch": 4,
        "return_epoch": 5,
        "policy_sha256": "policy-sha",
        "runtime_snapshot": {"compatible": True},
        "started_at": NOW,
        "completed_at": NOW + timedelta(seconds=10),
        "target_active_at": NOW + timedelta(seconds=3),
        "primary_restored_at": NOW + timedelta(seconds=10),
        "failover_request_id": "failover-id",
        "failback_request_id": "failback-id",
        "rto_seconds": 10,
        "evidence_payload": {},
        "evidence_sha256": None,
        "signed_off_by_id": None,
        "signed_off_at": None,
        "created_by_id": "creator",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_get_or_create_policy_handles_missing_tenant_race_and_creation() -> None:
    """Проверить tenant lock, повторный lookup и safe-default создание policy."""

    with pytest.raises(ContinuityError, match="Организация"):
        continuity.get_or_create_policy(
            ContinuityDb(scalar_values=[None, None]),  # type: ignore[arg-type]
            organization_id="org",
            settings=_settings(),  # type: ignore[arg-type]
        )
    raced = _policy()
    assert (
        continuity.get_or_create_policy(
            ContinuityDb(scalar_values=[None, SimpleNamespace(id="org"), raced]),  # type: ignore[arg-type]
            organization_id="org",
            settings=_settings(),  # type: ignore[arg-type]
        )
        is raced
    )
    db = ContinuityDb(scalar_values=[None, SimpleNamespace(id="org"), None])
    created = continuity.get_or_create_policy(
        db,  # type: ignore[arg-type]
        organization_id="org",
        settings=_settings(),  # type: ignore[arg-type]
        actor_id="actor",
    )
    assert created.organization_id == "org"
    assert db.added == [created] and db.flushes == 1


@pytest.mark.parametrize("case", ["tenant", "sequence", "previous", "hash"])
def test_verify_event_chain_reports_each_integrity_failure(case: str) -> None:
    """Проверить tenant, sequence, previous-hash и event-hash привязки."""

    drill = SimpleNamespace(organization_id="org")
    event = SimpleNamespace(
        organization_id="other" if case == "tenant" else "org",
        drill_id="drill-id",
        sequence=2 if case == "sequence" else 1,
        event_type=ContinuityDrillEventType.CREATED,
        actor_user_id=None,
        payload={},
        previous_hash="bad" if case == "previous" else "0" * 64,
        event_hash="bad",
        created_at=NOW,
    )
    if case != "hash":
        event.event_hash = continuity._canonical_sha256(
            continuity._event_payload(
                organization_id=event.organization_id,
                drill_id=event.drill_id,
                sequence=event.sequence,
                event_type=event.event_type,
                actor_user_id=None,
                payload={},
                previous_hash=event.previous_hash,
                created_at=NOW,
            )
        )
    result = continuity.verify_event_chain(
        ContinuityDb(get_values=[drill], scalar_lists=[[event]]),  # type: ignore[arg-type]
        drill_id="drill-id",
    )
    assert result["valid"] is False


def test_verify_event_chain_rejects_empty_history() -> None:
    """Проверить отдельную ошибку существующего drill без событий."""

    result = continuity.verify_event_chain(
        ContinuityDb(get_values=[SimpleNamespace(organization_id="org")], scalar_lists=[[]]),  # type: ignore[arg-type]
        drill_id="drill-id",
    )
    assert result["error"] == "История continuity-учения пуста"


@pytest.mark.parametrize("missing", ["source", "target"])
def test_runtime_pair_snapshot_rejects_missing_heartbeat(
    monkeypatch: pytest.MonkeyPatch,
    missing: str,
) -> None:
    """Проверить fail-closed snapshot при отсутствии любой площадки."""

    source = None if missing == "source" else SimpleNamespace()
    target = None if missing == "target" else SimpleNamespace()
    monkeypatch.setattr(
        continuity,
        "_site_for_snapshot",
        lambda *_args, site_key, **_kwargs: source if site_key == "primary" else target,
    )
    monkeypatch.setattr(continuity, "site_is_fresh", lambda *_args, **_kwargs: True)
    with pytest.raises(ContinuityError, match="Heartbeat"):
        continuity.runtime_pair_snapshot(
            ContinuityDb(),  # type: ignore[arg-type]
            organization_id="org",
            source_site_key="primary",
            target_site_key="standby",
            settings=_settings(),  # type: ignore[arg-type]
        )


def test_runtime_pair_snapshot_rejects_incompatible_runtime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Проверить перенос blockers runtime comparison в domain error."""

    monkeypatch.setattr(
        continuity, "_site_for_snapshot", lambda *_args, **_kwargs: SimpleNamespace()
    )
    monkeypatch.setattr(continuity, "site_is_fresh", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(
        continuity,
        "compare_site_runtime",
        lambda *_args: SimpleNamespace(allowed=False, blockers=["mismatch"]),
    )
    with pytest.raises(ContinuityError, match="mismatch"):
        continuity.runtime_pair_snapshot(
            ContinuityDb(),  # type: ignore[arg-type]
            organization_id="org",
            source_site_key="primary",
            target_site_key="standby",
            settings=_settings(),  # type: ignore[arg-type]
        )


def test_policy_and_create_drill_guards(monkeypatch: pytest.MonkeyPatch) -> None:
    """Проверить stale policy, already-active target и открытое учение."""

    policy = _policy()
    monkeypatch.setattr(continuity, "get_or_create_policy", lambda *_args, **_kwargs: policy)
    with pytest.raises(ContinuityError, match="Политика"):
        continuity._assert_policy_current(
            ContinuityDb(),  # type: ignore[arg-type]
            drill=_drill(policy_sha256="stale"),  # type: ignore[arg-type]
            settings=_settings(),  # type: ignore[arg-type]
        )

    lease = SimpleNamespace(active_site_key="primary", epoch=3)
    monkeypatch.setattr(
        continuity, "get_or_create_execution_lease", lambda *_args, **_kwargs: lease
    )
    actor = SimpleNamespace(id="actor", organization_id="org")
    with pytest.raises(ContinuityError, match="уже является active"):
        continuity.create_drill(
            ContinuityDb(),  # type: ignore[arg-type]
            mode=ContinuityDrillMode.LIVE,
            target_site_key=" PRIMARY ",
            actor=actor,  # type: ignore[arg-type]
            settings=_settings(),  # type: ignore[arg-type]
        )
    with pytest.raises(ContinuityError, match="текущее"):
        continuity.create_drill(
            ContinuityDb(scalar_values=[SimpleNamespace()]),  # type: ignore[arg-type]
            mode=ContinuityDrillMode.LIVE,
            target_site_key="standby",
            actor=actor,  # type: ignore[arg-type]
            settings=_settings(),  # type: ignore[arg-type]
        )


@pytest.mark.parametrize("case", ["status", "draining", "changed", "execution"])
def test_start_drill_rejects_invalid_state_and_execution_error(
    monkeypatch: pytest.MonkeyPatch,
    case: str,
) -> None:
    """Проверить draft/lease snapshot и преобразование failover domain error."""

    drill = _drill(
        status=ContinuityDrillStatus.RUNNING if case == "status" else ContinuityDrillStatus.DRAFT
    )
    monkeypatch.setattr(continuity, "_lock_drill", lambda *_args, **_kwargs: drill)
    monkeypatch.setattr(continuity, "_assert_policy_current", lambda *_args, **_kwargs: _policy())
    lease = SimpleNamespace(
        status=ExecutionLeaseStatus.DRAINING if case == "draining" else ExecutionLeaseStatus.ACTIVE,
        active_site_key="other" if case == "changed" else "primary",
        epoch=3,
    )
    monkeypatch.setattr(
        continuity, "get_or_create_execution_lease", lambda *_args, **_kwargs: lease
    )
    monkeypatch.setattr(continuity, "runtime_pair_snapshot", lambda *_args, **_kwargs: {})
    if case == "execution":
        monkeypatch.setattr(
            continuity,
            "request_failover",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(ExecutionError("blocked")),
        )
    with pytest.raises(ContinuityError):
        continuity.start_drill(
            ContinuityDb(),  # type: ignore[arg-type]
            drill_id="drill-id",
            actor=SimpleNamespace(id="actor", organization_id="org"),  # type: ignore[arg-type]
            settings=_settings(),  # type: ignore[arg-type]
        )


def test_request_mismatch_reports_every_binding_error() -> None:
    """Проверить organization, route и monotonic epoch failover request."""

    request = SimpleNamespace(
        organization_id="other",
        source_site_key="wrong-source",
        target_site_key="wrong-target",
        source_epoch=99,
        target_epoch=None,
    )
    blockers = continuity._request_mismatch(request, _drill(target_epoch=None), failback=True)  # type: ignore[arg-type]
    assert len(blockers) == 5


def test_evidence_semantics_cover_simulation_and_live_mismatches() -> None:
    """Проверить обязательные поля simulation и live evidence."""

    simulation = _drill(mode=ContinuityDrillMode.SIMULATION, evidence_payload={})
    simulation_blockers = continuity._evidence_semantic_blockers(simulation)  # type: ignore[arg-type]
    assert any("Simulation evidence" in item for item in simulation_blockers)

    live = _drill(evidence_payload={"network_calls": "wrong"})
    live_blockers = continuity._evidence_semantic_blockers(live)  # type: ignore[arg-type]
    assert any("Live evidence-поле" in item for item in live_blockers)
    assert any("fenced failover" in item for item in live_blockers)


def test_passed_semantics_reject_missing_and_inconsistent_terminal_event() -> None:
    """Проверить независимую приёмку и terminal SIGNED_OFF event metadata."""

    drill = _drill(signed_off_by_id=None, signed_off_at=None)
    empty = continuity._passed_semantic_blockers(
        ContinuityDb(scalar_lists=[[]]),  # type: ignore[arg-type]
        drill,  # type: ignore[arg-type]
    )
    assert "История continuity-учения пуста" in empty

    drill.signed_off_by_id = "signer"
    drill.signed_off_at = NOW
    terminal = SimpleNamespace(
        event_type=ContinuityDrillEventType.FAILED,
        actor_user_id="other",
        payload={"evidence_sha256": "wrong"},
        previous_hash="previous",
    )
    blockers = continuity._passed_semantic_blockers(
        ContinuityDb(scalar_lists=[[terminal]]),  # type: ignore[arg-type]
        drill,  # type: ignore[arg-type]
    )
    assert len(blockers) >= 5


@pytest.mark.parametrize("status", [FailoverRequestStatus.CANCELLED, FailoverRequestStatus.FAILED])
def test_synchronize_drill_projects_terminal_failover_request(
    monkeypatch: pytest.MonkeyPatch,
    status: FailoverRequestStatus,
) -> None:
    """Проверить CANCELLED/FAILED projection первого failover request."""

    drill = _drill(status=ContinuityDrillStatus.RUNNING, target_active_at=None)
    request = SimpleNamespace(id="failover-id", status=status)
    db = ContinuityDb(scalar_values=[drill], get_values=[request])
    events: list[ContinuityDrillEventType] = []
    monkeypatch.setattr(
        continuity,
        "append_event",
        lambda *_args, event_type, **_kwargs: events.append(event_type),
    )
    result = continuity.synchronize_drill(
        db,  # type: ignore[arg-type]
        drill=drill,  # type: ignore[arg-type]
        settings=_settings(),  # type: ignore[arg-type]
        now=NOW,
    )
    expected = (
        ContinuityDrillStatus.CANCELLED
        if status == FailoverRequestStatus.CANCELLED
        else ContinuityDrillStatus.FAILED
    )
    assert result.status == expected
    assert events


def test_synchronize_drill_handles_missing_row_and_lease(monkeypatch: pytest.MonkeyPatch) -> None:
    """Проверить исчезнувший drill и completed failover без execution lease."""

    with pytest.raises(ContinuityError, match="не найден"):
        continuity.synchronize_drill(
            ContinuityDb(scalar_values=[None]),  # type: ignore[arg-type]
            drill=_drill(),  # type: ignore[arg-type]
            settings=_settings(),  # type: ignore[arg-type]
        )

    drill = _drill(status=ContinuityDrillStatus.RUNNING, target_active_at=None)
    request = SimpleNamespace(
        id="failover-id",
        status=FailoverRequestStatus.COMPLETED,
        organization_id="org",
        source_site_key="primary",
        target_site_key="standby",
        source_epoch=3,
        target_epoch=4,
    )
    monkeypatch.setattr(continuity, "append_event", lambda *_args, **_kwargs: None)
    result = continuity.synchronize_drill(
        ContinuityDb(scalar_values=[drill, None], get_values=[request]),  # type: ignore[arg-type]
        drill=drill,  # type: ignore[arg-type]
        settings=_settings(),  # type: ignore[arg-type]
        now=NOW,
    )
    assert result.status == ContinuityDrillStatus.FAILED
    assert "lease отсутствует" in result.failure_reason


@pytest.mark.parametrize("status", [FailoverRequestStatus.CANCELLED, FailoverRequestStatus.FAILED])
def test_synchronize_drill_projects_terminal_failback_request(
    monkeypatch: pytest.MonkeyPatch,
    status: FailoverRequestStatus,
) -> None:
    """Проверить CANCELLED/FAILED projection обратного failover request."""

    drill = _drill(status=ContinuityDrillStatus.RUNNING, primary_restored_at=None)
    request = SimpleNamespace(id="failback-id", status=status)
    events: list[ContinuityDrillEventType] = []
    monkeypatch.setattr(
        continuity,
        "append_event",
        lambda *_args, event_type, **_kwargs: events.append(event_type),
    )
    result = continuity.synchronize_drill(
        ContinuityDb(scalar_values=[drill], get_values=[request]),  # type: ignore[arg-type]
        drill=drill,  # type: ignore[arg-type]
        settings=_settings(),  # type: ignore[arg-type]
        now=NOW,
    )
    expected = (
        ContinuityDrillStatus.CANCELLED
        if status == FailoverRequestStatus.CANCELLED
        else ContinuityDrillStatus.FAILED
    )
    assert result.status == expected
    assert events


def test_synchronize_completed_failback_rejects_missing_lease(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Проверить fail-closed completed failback без authoritative lease."""

    drill = _drill(status=ContinuityDrillStatus.RUNNING, primary_restored_at=None)
    request = SimpleNamespace(
        id="failback-id",
        status=FailoverRequestStatus.COMPLETED,
        organization_id="org",
        source_site_key="standby",
        target_site_key="primary",
        source_epoch=4,
        target_epoch=5,
    )
    monkeypatch.setattr(continuity, "append_event", lambda *_args, **_kwargs: None)
    result = continuity.synchronize_drill(
        ContinuityDb(scalar_values=[drill, None], get_values=[request]),  # type: ignore[arg-type]
        drill=drill,  # type: ignore[arg-type]
        settings=_settings(),  # type: ignore[arg-type]
        now=NOW,
    )
    assert result.status == ContinuityDrillStatus.FAILED
    assert "lease отсутствует" in result.failure_reason


def test_synchronize_completed_failback_rejects_changed_lease(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Проверить status, active site и epoch authoritative lease после failback."""

    drill = _drill(status=ContinuityDrillStatus.RUNNING, primary_restored_at=None)
    request = SimpleNamespace(
        id="failback-id",
        status=FailoverRequestStatus.COMPLETED,
        organization_id="org",
        source_site_key="standby",
        target_site_key="primary",
        source_epoch=4,
        target_epoch=5,
    )
    lease = SimpleNamespace(
        status=ExecutionLeaseStatus.DRAINING,
        active_site_key="standby",
        epoch=99,
    )
    monkeypatch.setattr(continuity, "append_event", lambda *_args, **_kwargs: None)
    result = continuity.synchronize_drill(
        ContinuityDb(scalar_values=[drill, lease], get_values=[request]),  # type: ignore[arg-type]
        drill=drill,  # type: ignore[arg-type]
        settings=_settings(),  # type: ignore[arg-type]
        now=NOW,
    )
    assert result.status == ContinuityDrillStatus.FAILED
    assert "не active" in result.failure_reason
    assert "не вернулась" in result.failure_reason
    assert "epoch" in result.failure_reason


@pytest.mark.parametrize(
    "case", ["simulation", "status", "lease", "draining", "epoch", "execution"]
)
def test_request_failback_rejects_each_invalid_state(
    monkeypatch: pytest.MonkeyPatch,
    case: str,
) -> None:
    """Проверить mode/status/lease/epoch guards и ExecutionError mapping failback."""

    drill = _drill(
        mode=ContinuityDrillMode.SIMULATION if case == "simulation" else ContinuityDrillMode.LIVE,
        status=(
            ContinuityDrillStatus.RUNNING
            if case == "status"
            else ContinuityDrillStatus.AWAITING_FAILBACK
        ),
    )
    monkeypatch.setattr(continuity, "_lock_drill", lambda *_args, **_kwargs: drill)
    monkeypatch.setattr(continuity, "synchronize_drill", lambda *_args, **_kwargs: drill)
    lease = (
        None
        if case == "lease"
        else SimpleNamespace(
            active_site_key="other" if case == "lease" else "standby",
            status=(
                ExecutionLeaseStatus.DRAINING if case == "draining" else ExecutionLeaseStatus.ACTIVE
            ),
            epoch=99 if case == "epoch" else 4,
        )
    )
    if case == "execution":
        monkeypatch.setattr(
            continuity,
            "request_failover",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(ExecutionError("blocked")),
        )
    with pytest.raises(ContinuityError):
        continuity.request_failback(
            ContinuityDb(scalar_values=[lease]),  # type: ignore[arg-type]
            drill_id="drill-id",
            actor=SimpleNamespace(id="actor", organization_id="org"),  # type: ignore[arg-type]
            settings=_settings(),  # type: ignore[arg-type]
            now=NOW,
        )


@pytest.mark.parametrize("case", ["status", "distinct", "missing", "digest", "semantic", "chain"])
def test_signoff_drill_rejects_invalid_evidence(
    monkeypatch: pytest.MonkeyPatch,
    case: str,
) -> None:
    """Проверить readiness, four-eyes, evidence digest/semantics и event chain."""

    payload = {"evidence": True}
    drill = _drill(
        status=(
            ContinuityDrillStatus.RUNNING
            if case == "status"
            else ContinuityDrillStatus.AWAITING_SIGNOFF
        ),
        evidence_payload=None if case == "missing" else payload,
        evidence_sha256=("bad" if case == "digest" else continuity._canonical_sha256(payload)),
        created_by_id="actor" if case == "distinct" else "creator",
    )
    monkeypatch.setattr(continuity, "_lock_drill", lambda *_args, **_kwargs: drill)
    monkeypatch.setattr(continuity, "synchronize_drill", lambda *_args, **_kwargs: drill)
    monkeypatch.setattr(continuity, "_assert_policy_current", lambda *_args, **_kwargs: _policy())
    monkeypatch.setattr(
        continuity,
        "_evidence_semantic_blockers",
        lambda *_args: ["semantic"] if case == "semantic" else [],
    )
    monkeypatch.setattr(
        continuity,
        "verify_event_chain",
        lambda *_args, **_kwargs: {
            "valid": case != "chain",
            "error": "broken",
            "last_hash": "hash",
        },
    )
    with pytest.raises(ContinuityError):
        continuity.signoff_drill(
            ContinuityDb(),  # type: ignore[arg-type]
            drill_id="drill-id",
            actor=SimpleNamespace(id="actor", organization_id="org"),  # type: ignore[arg-type]
            settings=_settings(),  # type: ignore[arg-type]
            accepted=True,
            note="test",
            now=NOW,
        )


def test_cancel_drill_rejects_terminal_and_maps_cancel_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Проверить terminal guard и безопасное преобразование cancel_failover ошибки."""

    actor = SimpleNamespace(id="actor", organization_id="org")
    terminal = _drill(status=ContinuityDrillStatus.PASSED)
    monkeypatch.setattr(continuity, "_lock_drill", lambda *_args, **_kwargs: terminal)
    monkeypatch.setattr(continuity, "synchronize_drill", lambda *_args, **_kwargs: terminal)
    with pytest.raises(ContinuityError, match="нельзя отменить"):
        continuity.cancel_drill(
            ContinuityDb(scalar_values=[None]),  # type: ignore[arg-type]
            drill_id="drill-id",
            actor=actor,  # type: ignore[arg-type]
            settings=_settings(),  # type: ignore[arg-type]
        )


def test_cancel_drill_cancels_pending_request_and_marks_drill(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Проверить успешную отмену pending failover и terminal continuity event."""

    drill = _drill(
        status=ContinuityDrillStatus.DRAFT,
        target_active_at=None,
        primary_restored_at=None,
        failback_request_id=None,
    )
    actor = SimpleNamespace(id="actor", organization_id="org")
    request = SimpleNamespace(id="failover-id", status=FailoverRequestStatus.REQUESTED)
    cancelled: list[str] = []
    monkeypatch.setattr(continuity, "_lock_drill", lambda *_args, **_kwargs: drill)
    monkeypatch.setattr(continuity, "synchronize_drill", lambda *_args, **_kwargs: drill)
    monkeypatch.setattr(
        continuity,
        "cancel_failover",
        lambda *_args, request_id, **_kwargs: cancelled.append(request_id),
    )
    monkeypatch.setattr(continuity, "append_event", lambda *_args, **_kwargs: None)
    result = continuity.cancel_drill(
        ContinuityDb(scalar_values=[None], get_values=[request]),  # type: ignore[arg-type]
        drill_id="drill-id",
        actor=actor,  # type: ignore[arg-type]
        settings=_settings(),  # type: ignore[arg-type]
        now=NOW,
    )
    assert cancelled == ["failover-id"]
    assert result.status == ContinuityDrillStatus.CANCELLED

    pending = _drill(
        status=ContinuityDrillStatus.DRAFT,
        target_active_at=None,
        primary_restored_at=None,
        failback_request_id=None,
    )
    monkeypatch.setattr(continuity, "_lock_drill", lambda *_args, **_kwargs: pending)
    monkeypatch.setattr(continuity, "synchronize_drill", lambda *_args, **_kwargs: pending)
    monkeypatch.setattr(
        continuity,
        "cancel_failover",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(ExecutionError("cancel blocked")),
    )
    request = SimpleNamespace(id="failover-id", status=FailoverRequestStatus.REQUESTED)
    with pytest.raises(ContinuityError, match="cancel blocked"):
        continuity.cancel_drill(
            ContinuityDb(scalar_values=[None], get_values=[request]),  # type: ignore[arg-type]
            drill_id="drill-id",
            actor=actor,  # type: ignore[arg-type]
            settings=_settings(),  # type: ignore[arg-type]
        )


def test_update_policy_rejects_production_signoff_and_active_standby(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Проверить production/four-eyes настройки и запрет изменения на active standby."""

    actor = SimpleNamespace(id="actor", organization_id="org")
    base = {
        "db": ContinuityDb(),
        "actor": actor,
        "enabled": True,
        "max_rto_seconds": 300,
        "evidence_valid_days": 30,
    }
    with pytest.raises(ContinuityError, match="production"):
        continuity.update_policy(
            **base,  # type: ignore[arg-type]
            settings=_settings(is_production=True),  # type: ignore[arg-type]
            require_live_drill=False,
            require_distinct_signoff=True,
        )
    with pytest.raises(ContinuityError, match="независимую"):
        continuity.update_policy(
            **base,  # type: ignore[arg-type]
            settings=_settings(is_production=False),  # type: ignore[arg-type]
            require_live_drill=True,
            require_distinct_signoff=False,
        )

    unfinished = _drill(target_active_at=NOW, primary_restored_at=None)
    lease = SimpleNamespace(active_site_key="standby")
    with pytest.raises(ContinuityError, match="active lease"):
        continuity.update_policy(
            ContinuityDb(scalar_values=[lease], scalar_lists=[[unfinished]]),  # type: ignore[arg-type]
            actor=actor,  # type: ignore[arg-type]
            settings=_settings(is_production=False),  # type: ignore[arg-type]
            enabled=True,
            require_live_drill=True,
            max_rto_seconds=300,
            evidence_valid_days=30,
            require_distinct_signoff=True,
        )


def test_synchronize_open_drills_skips_deleted_row() -> None:
    """Проверить безопасный пропуск drill, удалённого после списка ID."""

    class NestedDb(ContinuityDb):
        """Предоставить настоящий protocol context для savepoint."""

        class Savepoint:
            """Имитировать SQLAlchemy nested transaction."""

            def __enter__(self) -> None:
                """Открыть фиктивный savepoint."""

            def __exit__(self, *_args: object) -> None:
                """Закрыть фиктивный savepoint."""

        def begin_nested(self) -> NestedDb.Savepoint:
            """Вернуть фиктивный savepoint."""

            return self.Savepoint()

    summary = continuity.synchronize_open_drills(
        NestedDb(scalar_lists=[["deleted-id"]], get_values=[None]),  # type: ignore[arg-type]
        settings=_settings(),  # type: ignore[arg-type]
    )
    assert summary.scanned == 1 and summary.updated == 0 and summary.errors == 0


def test_continuity_compliance_reports_runtime_and_evidence_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Проверить полный набор blockers для runtime и устаревшего принятого evidence."""

    policy = _policy(enabled=False)
    lease = SimpleNamespace(active_site_key="primary")
    stale_site = SimpleNamespace(id="standby-id", site_key="standby", enabled=True)
    monkeypatch.setattr(continuity, "get_or_create_policy", lambda *_args, **_kwargs: policy)
    monkeypatch.setattr(
        continuity,
        "get_or_create_execution_lease",
        lambda *_args, **_kwargs: lease,
    )
    monkeypatch.setattr(continuity, "site_is_fresh", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(
        continuity, "site_runtime_payload", lambda site: None if site is None else {}
    )
    report = continuity.continuity_compliance(
        ContinuityDb(scalar_lists=[[stale_site]], scalar_values=[None]),  # type: ignore[arg-type]
        organization_id="org",
        settings=_settings(),  # type: ignore[arg-type]
        now=NOW,
    )
    assert any("policy отключена" in item for item in report.blockers)
    assert any("Active-площадка" in item for item in report.blockers)
    assert any("Standby-площадка" in item for item in report.blockers)

    policy.enabled = True
    policy.require_live_drill = True
    active = SimpleNamespace(id="standby-id", site_key="standby", enabled=True)
    primary = SimpleNamespace(id="primary-id", site_key="primary", enabled=True)
    lease.active_site_key = "standby"
    payload = {"tampered": True}
    latest = _drill(
        mode=ContinuityDrillMode.SIMULATION,
        policy_sha256="stale",
        expires_at=NOW - timedelta(seconds=1),
        rto_seconds=999,
        evidence_payload=payload,
        evidence_sha256="bad",
    )
    monkeypatch.setattr(continuity, "site_is_fresh", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(
        continuity,
        "compare_site_runtime",
        lambda *_args: SimpleNamespace(allowed=True, warnings=["runtime warning"], blockers=[]),
    )
    monkeypatch.setattr(
        continuity,
        "verify_event_chain",
        lambda *_args, **_kwargs: {"valid": False, "error": "broken"},
    )
    report = continuity.continuity_compliance(
        ContinuityDb(scalar_lists=[[active, primary]], scalar_values=[latest]),  # type: ignore[arg-type]
        organization_id="org",
        settings=_settings(),  # type: ignore[arg-type]
        now=NOW,
    )
    assert any("устаревшей политике" in item for item in report.blockers)
    assert any("live-drill" in item for item in report.blockers)
    assert any("просрочено" in item for item in report.blockers)
    assert any("RTO" in item for item in report.blockers)
    assert any("повреждено" in item for item in report.blockers)
    assert any("История" in item for item in report.blockers)

    latest.evidence_payload = None
    latest.evidence_sha256 = None
    missing = continuity.continuity_compliance(
        ContinuityDb(scalar_lists=[[active, primary]], scalar_values=[latest]),  # type: ignore[arg-type]
        organization_id="org",
        settings=_settings(),  # type: ignore[arg-type]
        now=NOW,
    )
    assert "Continuity-evidence отсутствует" in missing.blockers


def test_continuity_compliance_selects_target_without_active_and_reports_incompatibility(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Проверить выбор fresh standby без active и перенос runtime incompatibility diagnostics."""

    policy = _policy(enabled=False, require_live_drill=False)
    lease = SimpleNamespace(active_site_key="missing")
    standby = SimpleNamespace(id="standby-id", site_key="standby", enabled=True)
    monkeypatch.setattr(continuity, "get_or_create_policy", lambda *_args, **_kwargs: policy)
    monkeypatch.setattr(
        continuity, "get_or_create_execution_lease", lambda *_args, **_kwargs: lease
    )
    monkeypatch.setattr(continuity, "site_is_fresh", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(
        continuity, "site_runtime_payload", lambda site: None if site is None else {}
    )
    selected = continuity.continuity_compliance(
        ContinuityDb(scalar_lists=[[standby]], scalar_values=[None]),  # type: ignore[arg-type]
        organization_id="org",
        settings=_settings(continuity_assurance_required=False),  # type: ignore[arg-type]
        now=NOW,
    )
    assert selected.runtime_snapshot["standby"] == {}

    active = SimpleNamespace(id="active-id", site_key="primary", enabled=True)
    lease.active_site_key = "primary"
    monkeypatch.setattr(
        continuity,
        "compare_site_runtime",
        lambda *_args: SimpleNamespace(allowed=False, warnings=[], blockers=["version mismatch"]),
    )
    incompatible = continuity.continuity_compliance(
        ContinuityDb(scalar_lists=[[active, standby]], scalar_values=[None]),  # type: ignore[arg-type]
        organization_id="org",
        settings=_settings(continuity_assurance_required=True),  # type: ignore[arg-type]
        now=NOW,
    )
    assert any("version mismatch" in item for item in incompatible.blockers)
