from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from app.enums import (
    DeliveryAttemptStatus,
    ExecutionLeaseStatus,
    FailoverRequestStatus,
)
from app.services import execution
from app.services.execution import ExecutionError


class ScalarRows:
    """Имитировать SQLAlchemy ScalarResult с all()."""

    def __init__(self, values: list[object]) -> None:
        """Сохранить подготовленные scalar-строки."""

        self.values = values

    def all(self) -> list[object]:
        """Вернуть подготовленные execution rows."""

        return self.values


class ExecutionDb:
    """Предоставить управляемые scalar/scalars и transaction counters execution service."""

    def __init__(
        self,
        *,
        scalar_values: list[object | None] | None = None,
        scalar_lists: list[list[object]] | None = None,
    ) -> None:
        """Сохранить ответы запросов и добавленные объекты."""

        self.scalar_values = list(scalar_values or [])
        self.scalar_lists = list(scalar_lists or [])
        self.added: list[object] = []
        self.flushes = 0
        self.commits = 0

    def __enter__(self) -> ExecutionDb:
        """Открыть фиктивную сессию coordinator."""

        return self

    def __exit__(self, *_args: object) -> None:
        """Закрыть фиктивную сессию без побочных эффектов."""

    def scalar(self, _statement):  # type: ignore[no-untyped-def]
        """Вернуть следующий подготовленный scalar-ответ."""

        return self.scalar_values.pop(0) if self.scalar_values else None

    def scalars(self, _statement) -> ScalarRows:  # type: ignore[no-untyped-def]
        """Вернуть следующую подготовленную scalar-коллекцию."""

        values = self.scalar_lists.pop(0) if self.scalar_lists else []
        return ScalarRows(values)

    def add(self, value: object) -> None:
        """Сохранить созданный execution объект."""

        self.added.append(value)

    def flush(self) -> None:
        """Зафиксировать ORM flush."""

        self.flushes += 1

    def commit(self) -> None:
        """Зафиксировать coordinator transaction."""

        self.commits += 1


def _settings(**overrides):  # type: ignore[no-untyped-def]
    """Создать минимальные настройки execution fencing."""

    values = {
        "execution_fencing_required": True,
        "execution_site_key": "primary",
        "execution_primary_site_key": "primary",
        "execution_site_name": "Primary",
        "execution_site_heartbeat_ttl_seconds": 60,
        "execution_lease_ttl_seconds": 30,
        "execution_require_distinct_failover_approver": True,
        "version": "test",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _lease(**overrides):  # type: ignore[no-untyped-def]
    """Создать минимальный execution lease для gate/claim/failover ветвей."""

    now = datetime(2026, 8, 10, 12, tzinfo=UTC)
    values = {
        "status": ExecutionLeaseStatus.ACTIVE,
        "active_site_key": "primary",
        "holder_worker_id": "worker",
        "epoch": 3,
        "lease_expires_at": now + timedelta(minutes=1),
        "last_renewed_at": now,
        "drain_reason": None,
        "drain_started_at": None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_site_freshness_and_runtime_evidence_blockers(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Проверить disabled/missing heartbeat, runtime mismatch и schema mismatch."""

    now = datetime(2026, 8, 10, 12, tzinfo=UTC)
    settings = _settings()
    assert (
        execution.site_is_fresh(
            SimpleNamespace(enabled=False, last_seen_at=now),
            settings,
            now=now,  # type: ignore[arg-type]
        )
        is False
    )
    assert execution._local_runtime_blockers(
        ExecutionDb(scalar_values=[None]),  # type: ignore[arg-type]
        organization_id="org",
        settings=settings,  # type: ignore[arg-type]
        now=now,
    ) == ["Локальная execution-площадка не имеет свежего heartbeat"]

    evidence = {
        "version": "test",
        "environment": "production",
        "current_revision": "head",
        "expected_revision": "head",
        "schema_current": True,
        "critical_config_sha256": "config",
        "release_payload_sha256": "release",
        "runtime_fingerprint": "runtime",
    }
    site = SimpleNamespace(
        enabled=True,
        last_seen_at=now,
        version="old",
        environment="production",
        current_revision="head",
        expected_revision="head",
        schema_current=False,
        critical_config_sha256="config",
        release_payload_sha256="release",
        runtime_fingerprint="runtime",
    )
    monkeypatch.setattr(execution, "collect_runtime_evidence", lambda *_args, **_kwargs: evidence)
    blockers = execution._local_runtime_blockers(
        ExecutionDb(scalar_values=[site]),  # type: ignore[arg-type]
        organization_id="org",
        settings=settings,  # type: ignore[arg-type]
        now=now,
    )
    assert any("version" in item for item in blockers)
    assert any("Схема БД" in item for item in blockers)


def test_runtime_pair_reports_missing_source_and_target() -> None:
    """Проверить оба heartbeat blockers до runtime comparison."""

    blockers = execution._runtime_pair_blockers(
        ExecutionDb(scalar_values=[None, None]),  # type: ignore[arg-type]
        organization_id="org",
        source_site_key="primary",
        target_site_key="secondary",
        settings=_settings(),  # type: ignore[arg-type]
    )
    assert len(blockers) == 2


def test_claim_delivery_lease_handles_disabled_runtime_draining_standby_and_restart(
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    """Проверить каждый pre-network lease claim outcome и epoch после restart."""

    now = datetime(2026, 8, 10, 12, tzinfo=UTC)
    disabled = execution.claim_delivery_lease(
        ExecutionDb(),  # type: ignore[arg-type]
        organization_id="org",
        settings=_settings(execution_fencing_required=False),  # type: ignore[arg-type]
        worker_id="worker",
        now=now,
    )
    assert disabled.allowed and disabled.epoch == 0

    monkeypatch.setattr(execution, "upsert_site_heartbeat", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(execution, "_local_runtime_blockers", lambda *_args, **_kwargs: ["bad"])
    mismatch = execution.claim_delivery_lease(
        ExecutionDb(),  # type: ignore[arg-type]
        organization_id="org",
        settings=_settings(),  # type: ignore[arg-type]
        worker_id="worker",
        now=now,
    )
    assert mismatch.code == "EXECUTION_RUNTIME_MISMATCH"

    monkeypatch.setattr(execution, "_local_runtime_blockers", lambda *_args, **_kwargs: [])
    for lease, expected in [
        (_lease(status=ExecutionLeaseStatus.DRAINING), "EXECUTION_LEASE_DRAINING"),
        (_lease(active_site_key="secondary"), "EXECUTION_SITE_STANDBY"),
    ]:
        monkeypatch.setattr(execution, "_lease_for_update", lambda *_args, _lease=lease: _lease)
        result = execution.claim_delivery_lease(
            ExecutionDb(),  # type: ignore[arg-type]
            organization_id="org",
            settings=_settings(),  # type: ignore[arg-type]
            worker_id="worker",
            now=now,
        )
        assert result.code == expected

    restarted = _lease(lease_expires_at=now - timedelta(seconds=1), epoch=3)
    monkeypatch.setattr(execution, "_lease_for_update", lambda *_args: restarted)
    result = execution.claim_delivery_lease(
        ExecutionDb(),  # type: ignore[arg-type]
        organization_id="org",
        settings=_settings(),  # type: ignore[arg-type]
        worker_id="worker",
        now=now,
    )
    assert result.allowed and result.epoch == 4


@pytest.mark.parametrize(
    ("lease", "epoch", "expected"),
    [
        (None, 3, "EXECUTION_LEASE_MISSING"),
        (_lease(status=ExecutionLeaseStatus.DRAINING), 3, "EXECUTION_LEASE_DRAINING"),
        (_lease(active_site_key="secondary"), 3, "EXECUTION_SITE_STANDBY"),
        (_lease(holder_worker_id="other"), 3, "EXECUTION_FENCE_OWNER_MISMATCH"),
        (_lease(), 2, "EXECUTION_FENCE_EPOCH_MISMATCH"),
        (_lease(lease_expires_at=None), 3, "EXECUTION_LEASE_EXPIRED"),
    ],
)
def test_execution_gate_rejects_each_lease_mismatch(
    monkeypatch,
    lease: object | None,
    epoch: int,
    expected: str,
) -> None:  # type: ignore[no-untyped-def]
    """Проверить детерминированный gate code для каждой lease/fence ошибки."""

    monkeypatch.setattr(execution, "_local_runtime_blockers", lambda *_args, **_kwargs: [])
    decision = execution.execution_gate_decision(
        ExecutionDb(scalar_values=[lease]),  # type: ignore[arg-type]
        organization_id="org",
        settings=_settings(),  # type: ignore[arg-type]
        worker_id="worker",
        fence_epoch=epoch,
        now=datetime(2026, 8, 10, 12, tzinfo=UTC),
    )
    assert decision.code == expected


def test_execution_gate_disabled_and_runtime_mismatch(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Проверить bypass при disabled fencing и fail-closed runtime evidence."""

    disabled = execution.execution_gate_decision(
        ExecutionDb(),  # type: ignore[arg-type]
        organization_id="org",
        settings=_settings(execution_fencing_required=False),  # type: ignore[arg-type]
        worker_id="worker",
        fence_epoch=None,
    )
    assert disabled.allowed
    monkeypatch.setattr(execution, "_local_runtime_blockers", lambda *_args, **_kwargs: ["runtime"])
    mismatch = execution.execution_gate_decision(
        ExecutionDb(),  # type: ignore[arg-type]
        organization_id="org",
        settings=_settings(),  # type: ignore[arg-type]
        worker_id="worker",
        fence_epoch=3,
    )
    assert mismatch.code == "EXECUTION_RUNTIME_MISMATCH"


@pytest.mark.parametrize(
    ("lease", "epoch", "message"),
    [
        (None, 3, "отсутствует"),
        (_lease(status=ExecutionLeaseStatus.DRAINING), 3, "draining"),
        (_lease(active_site_key="secondary"), 3, "active"),
        (_lease(holder_worker_id="other"), 3, "worker"),
        (_lease(), 2, "epoch"),
        (_lease(lease_expires_at=None), 3, "истёк"),
    ],
)
def test_lock_delivery_fence_rejects_each_changed_condition(
    monkeypatch,
    lease: object | None,
    epoch: int,
    message: str,
) -> None:  # type: ignore[no-untyped-def]
    """Проверить повторную row-lock валидацию непосредственно перед Telegram call."""

    monkeypatch.setattr(execution, "_local_runtime_blockers", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(execution, "_lease_for_update", lambda *_args: lease)
    with pytest.raises(ExecutionError, match=message):
        execution.lock_delivery_fence(
            ExecutionDb(),  # type: ignore[arg-type]
            organization_id="org",
            settings=_settings(),  # type: ignore[arg-type]
            worker_id="worker",
            fence_epoch=epoch,
            now=datetime(2026, 8, 10, 12, tzinfo=UTC),
        )


def test_lock_and_scheduler_handle_disabled_runtime_and_draining(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Проверить disabled lock и scheduler runtime/draining guards."""

    assert (
        execution.lock_delivery_fence(
            ExecutionDb(),  # type: ignore[arg-type]
            organization_id="org",
            settings=_settings(execution_fencing_required=False),  # type: ignore[arg-type]
            worker_id="worker",
            fence_epoch=None,
        )
        is None
    )
    disabled = execution.scheduler_site_allowed(
        ExecutionDb(),  # type: ignore[arg-type]
        organization_id="org",
        settings=_settings(execution_fencing_required=False),  # type: ignore[arg-type]
    )
    assert disabled.allowed

    monkeypatch.setattr(execution, "upsert_site_heartbeat", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(execution, "_local_runtime_blockers", lambda *_args, **_kwargs: ["bad"])
    with pytest.raises(ExecutionError, match="bad"):
        execution.lock_delivery_fence(
            ExecutionDb(),  # type: ignore[arg-type]
            organization_id="org",
            settings=_settings(),  # type: ignore[arg-type]
            worker_id="worker",
            fence_epoch=3,
        )
    mismatch = execution.scheduler_site_allowed(
        ExecutionDb(),  # type: ignore[arg-type]
        organization_id="org",
        settings=_settings(),  # type: ignore[arg-type]
    )
    assert mismatch.code == "EXECUTION_RUNTIME_MISMATCH"

    monkeypatch.setattr(execution, "_local_runtime_blockers", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(
        execution,
        "get_or_create_execution_lease",
        lambda *_args, **_kwargs: _lease(status=ExecutionLeaseStatus.DRAINING),
    )
    draining = execution.scheduler_site_allowed(
        ExecutionDb(),  # type: ignore[arg-type]
        organization_id="org",
        settings=_settings(),  # type: ignore[arg-type]
    )
    assert draining.code == "EXECUTION_LEASE_DRAINING"


def test_renew_leases_and_coordinator_heartbeat(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Проверить disabled renewal, TTL update и coordinator обход всех организаций."""

    db = ExecutionDb()
    assert (
        execution.renew_owned_leases(
            db,  # type: ignore[arg-type]
            settings=_settings(execution_fencing_required=False),  # type: ignore[arg-type]
            worker_id="worker",
        )
        == 0
    )
    now = datetime(2026, 8, 10, 12, tzinfo=UTC)
    leases = [_lease(), _lease(epoch=4)]
    db = ExecutionDb(scalar_lists=[leases])
    assert (
        execution.renew_owned_leases(
            db,  # type: ignore[arg-type]
            settings=_settings(),  # type: ignore[arg-type]
            worker_id="worker",
            now=now,
        )
        == 2
    )
    assert all(item.lease_expires_at == now + timedelta(seconds=30) for item in leases)

    coordinator_db = ExecutionDb(scalar_lists=[["org-1", "org-2"]])
    heartbeats: list[str] = []
    monkeypatch.setattr(
        execution,
        "upsert_site_heartbeat",
        lambda _db, *, organization_id, **_kwargs: heartbeats.append(organization_id),
    )
    monkeypatch.setattr(execution, "renew_owned_leases", lambda *_args, **_kwargs: 1)
    result = execution.ExecutionCoordinator(
        lambda: coordinator_db,  # type: ignore[arg-type]
        _settings(),  # type: ignore[arg-type]
        worker_id="worker",
    ).heartbeat({"ready": True})
    assert result == {"sites": 2, "leases": 1}
    assert heartbeats == ["org-1", "org-2"]
    assert coordinator_db.commits == 1


@pytest.mark.parametrize("case", ["draining", "open", "runtime"])
def test_request_failover_rejects_conflicting_or_incompatible_state(
    monkeypatch,
    case: str,
) -> None:  # type: ignore[no-untyped-def]
    """Проверить открытый/draining request и runtime mismatch до failover mutation."""

    lease = _lease(
        status=ExecutionLeaseStatus.DRAINING if case == "draining" else ExecutionLeaseStatus.ACTIVE
    )
    monkeypatch.setattr(execution, "get_or_create_execution_lease", lambda *_args, **_kwargs: lease)
    db = ExecutionDb(scalar_values=[SimpleNamespace()] if case == "open" else [None])
    monkeypatch.setattr(execution, "_site_for_update", lambda *_args, **_kwargs: SimpleNamespace())
    monkeypatch.setattr(execution, "site_is_fresh", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(
        execution,
        "_runtime_pair_blockers",
        lambda *_args, **_kwargs: ["runtime mismatch"] if case == "runtime" else [],
    )
    with pytest.raises(ExecutionError):
        execution.request_failover(
            db,  # type: ignore[arg-type]
            organization_id="org",
            target_site_key="secondary",
            reason="test",
            actor=SimpleNamespace(id="actor"),  # type: ignore[arg-type]
            settings=_settings(),  # type: ignore[arg-type]
        )


def test_failover_blockers_include_uncertain_delivery() -> None:
    """Проверить отдельный blocker несверенной неоднозначной доставки."""

    blockers = execution._failover_blockers(
        ExecutionDb(scalar_values=[0, 2]),  # type: ignore[arg-type]
        organization_id="org",
    )
    assert blockers == ["Есть несверенные неопределённые доставки: 2"]


@pytest.mark.parametrize("case", ["missing", "finished", "phrase", "lease", "draining", "changed"])
def test_approve_failover_rejects_invalid_request_and_lease(
    monkeypatch,
    case: str,
) -> None:  # type: ignore[no-untyped-def]
    """Проверить request state, confirmation и lease snapshot до переключения epoch."""

    item = (
        None
        if case == "missing"
        else SimpleNamespace(
            status=(
                FailoverRequestStatus.CANCELLED
                if case == "finished"
                else FailoverRequestStatus.REQUESTED
            ),
            requested_by_id="requester",
            target_site_key="secondary",
            source_site_key="primary",
            source_epoch=3,
        )
    )
    db = ExecutionDb(scalar_values=[item])
    lease = (
        None
        if case == "lease"
        else _lease(
            status=(
                ExecutionLeaseStatus.ACTIVE if case == "draining" else ExecutionLeaseStatus.DRAINING
            ),
            epoch=(4 if case == "changed" else 3),
        )
    )
    monkeypatch.setattr(execution, "_lease_for_update", lambda *_args: lease)
    confirmation = "wrong" if case == "phrase" else "ПЕРЕКЛЮЧИТЬ НА secondary"
    with pytest.raises(ExecutionError):
        execution.approve_failover(
            db,  # type: ignore[arg-type]
            request_id="request-id",
            confirmation=confirmation,
            actor=SimpleNamespace(id="approver", organization_id="org"),  # type: ignore[arg-type]
            settings=_settings(),  # type: ignore[arg-type]
        )


def test_approve_failover_rejects_stale_target_heartbeat(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Проверить fail-closed переключение при устаревшей целевой площадке."""

    item = SimpleNamespace(
        status=FailoverRequestStatus.REQUESTED,
        requested_by_id="requester",
        target_site_key="secondary",
        source_site_key="primary",
        source_epoch=3,
    )
    monkeypatch.setattr(
        execution,
        "_lease_for_update",
        lambda *_args: _lease(status=ExecutionLeaseStatus.DRAINING),
    )
    monkeypatch.setattr(execution, "_site_for_update", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(execution, "_failover_blockers", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(execution, "_runtime_pair_blockers", lambda *_args, **_kwargs: [])

    with pytest.raises(ExecutionError, match="Heartbeat целевой площадки"):
        execution.approve_failover(
            ExecutionDb(scalar_values=[item]),  # type: ignore[arg-type]
            request_id="request-id",
            confirmation="ПЕРЕКЛЮЧИТЬ НА secondary",
            actor=SimpleNamespace(id="approver", organization_id="org"),  # type: ignore[arg-type]
            settings=_settings(),  # type: ignore[arg-type]
        )
    assert item.blockers == ["Heartbeat целевой площадки отсутствует или устарел"]


def test_cancel_and_reconciliation_handle_missing_lease(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Проверить fail-closed cancel и no-op fence invalidation без lease."""

    item = SimpleNamespace(
        status=FailoverRequestStatus.REQUESTED,
        source_site_key="primary",
        source_epoch=3,
    )
    monkeypatch.setattr(execution, "_lease_for_update", lambda *_args: None)
    with pytest.raises(ExecutionError, match="lease отсутствует"):
        execution.cancel_failover(
            ExecutionDb(scalar_values=[item]),  # type: ignore[arg-type]
            request_id="request-id",
            actor=SimpleNamespace(organization_id="org"),  # type: ignore[arg-type]
            settings=_settings(),  # type: ignore[arg-type]
        )
    assert (
        execution.invalidate_reconciled_delivery_fence(
            ExecutionDb(),  # type: ignore[arg-type]
            job=SimpleNamespace(organization_id="org"),  # type: ignore[arg-type]
            attempt=SimpleNamespace(worker_id="worker", fence_epoch=3),  # type: ignore[arg-type]
        )
        is None
    )


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (DeliveryAttemptStatus.NETWORK_STARTED, True),
        (DeliveryAttemptStatus.UNCERTAIN, True),
        (DeliveryAttemptStatus.SENT, False),
    ],
)
def test_attempt_manual_reconciliation_statuses(
    status: DeliveryAttemptStatus,
    expected: bool,
) -> None:
    """Проверить точный набор attempt statuses, требующих ручной сверки."""

    assert (
        execution.attempt_needs_manual_reconciliation(SimpleNamespace(status=status))  # type: ignore[arg-type]
        is expected
    )
