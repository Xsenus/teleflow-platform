from __future__ import annotations

import socket
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from app.audit import write_audit
from app.config import Settings
from app.enums import (
    DeliveryAttemptStatus,
    ExecutionLeaseStatus,
    FailoverRequestStatus,
    JobStatus,
    SafetySeverity,
)
from app.models import (
    DeliveryAttempt,
    DeliveryJob,
    ExecutionLease,
    ExecutionSite,
    FailoverRequest,
    Organization,
    User,
)
from app.security import aware_utc
from app.services.notifications import create_notification
from app.services.runtime_evidence import (
    collect_runtime_evidence,
    compare_site_runtime,
)


class ExecutionError(RuntimeError):
    pass


@dataclass(slots=True)
class LeaseDecision:
    allowed: bool
    code: str
    message: str
    epoch: int | None = None
    defer_until: datetime | None = None
    lease: ExecutionLease | None = None


@dataclass(slots=True)
class ExecutionGateDecision:
    allowed: bool
    code: str
    message: str
    lease: ExecutionLease | None = None


def _now(value: datetime | None = None) -> datetime:
    """Реализовать внутренний этап now step. Вспомогательная функция сохраняет детерминированность
    и тестируемость процесса.
    """
    return aware_utc(value) or datetime.now(UTC)


def site_is_fresh(site: ExecutionSite, settings: Settings, *, now: datetime | None = None) -> bool:
    """Выполнить операцию site is fresh. Аргументы интерпретируются в контексте модуля, результат
    возвращается вызывающему коду.
    """
    seen = aware_utc(site.last_seen_at)
    if not site.enabled or seen is None:
        return False
    return seen >= _now(now) - timedelta(seconds=settings.execution_site_heartbeat_ttl_seconds)


def _lease_for_update(db: Session, organization_id: str) -> ExecutionLease | None:
    """Реализовать внутренний этап lease for update step. Вспомогательная функция сохраняет
    детерминированность и тестируемость процесса.
    """
    return db.scalar(
        select(ExecutionLease)
        .where(ExecutionLease.organization_id == organization_id)
        .with_for_update()
    )


def get_or_create_execution_lease(
    db: Session,
    *,
    organization_id: str,
    settings: Settings,
    now: datetime | None = None,
    for_update: bool = False,
) -> ExecutionLease:
    """Прочитать or create execution lease. Значение возвращается без несвязанных изменений
    состояния.
    """
    current = _now(now)
    lease = (
        _lease_for_update(db, organization_id)
        if for_update
        else db.scalar(
            select(ExecutionLease).where(ExecutionLease.organization_id == organization_id)
        )
    )
    if lease is not None:
        return lease
    lease = ExecutionLease(
        organization_id=organization_id,
        active_site_key=settings.execution_primary_site_key,
        holder_worker_id=None,
        epoch=1,
        status=ExecutionLeaseStatus.ACTIVE,
        lease_expires_at=current,
        last_renewed_at=None,
    )
    db.add(lease)
    db.flush()
    return lease


def upsert_site_heartbeat(
    db: Session,
    *,
    organization_id: str,
    settings: Settings,
    worker_id: str,
    details: dict[str, Any] | None = None,
    now: datetime | None = None,
) -> ExecutionSite:
    """Зарегистрировать a site heartbeat and attach non-secret runtime compatibility evidence."""

    current = _now(now)
    site = db.scalar(
        select(ExecutionSite).where(
            ExecutionSite.organization_id == organization_id,
            ExecutionSite.site_key == settings.execution_site_key,
        )
    )
    if site is None:
        site = ExecutionSite(
            organization_id=organization_id,
            site_key=settings.execution_site_key,
            display_name=settings.execution_site_name,
            enabled=True,
        )
        db.add(site)
    site.display_name = settings.execution_site_name
    site.last_worker_id = worker_id
    site.hostname = socket.gethostname()
    site.version = settings.version
    runtime = collect_runtime_evidence(db, settings, checked_at=current)
    site.environment = runtime["environment"]
    site.current_revision = runtime["current_revision"]
    site.expected_revision = runtime["expected_revision"]
    site.schema_current = runtime["schema_current"]
    site.critical_config_sha256 = runtime["critical_config_sha256"]
    site.release_payload_sha256 = runtime["release_payload_sha256"]
    site.runtime_fingerprint = runtime["runtime_fingerprint"]
    site.runtime_checked_at = current
    site.last_seen_at = current
    # Heartbeats merge operational details instead of erasing labels entered by
    # an operator or another health subsystem.
    site.details = {**(site.details or {}), **(details or {})}
    get_or_create_execution_lease(
        db,
        organization_id=organization_id,
        settings=settings,
        now=current,
    )
    db.flush()
    return site


def _local_runtime_blockers(
    db: Session,
    *,
    organization_id: str,
    settings: Settings,
    now: datetime | None = None,
) -> list[str]:
    """Проверить сценарий the local site row still matches the running process."""

    current = _now(now)
    site = db.scalar(
        select(ExecutionSite).where(
            ExecutionSite.organization_id == organization_id,
            ExecutionSite.site_key == settings.execution_site_key,
        )
    )
    if site is None or not site_is_fresh(site, settings, now=current):
        return ["Локальная execution-площадка не имеет свежего heartbeat"]
    runtime = collect_runtime_evidence(db, settings, checked_at=current)
    expected = {
        "version": runtime["version"],
        "environment": runtime["environment"],
        "current_revision": runtime["current_revision"],
        "expected_revision": runtime["expected_revision"],
        "schema_current": runtime["schema_current"],
        "critical_config_sha256": runtime["critical_config_sha256"],
        "release_payload_sha256": runtime["release_payload_sha256"],
        "runtime_fingerprint": runtime["runtime_fingerprint"],
    }
    blockers: list[str] = []
    for field, expected_value in expected.items():
        if getattr(site, field) != expected_value:
            blockers.append(f"Runtime evidence локальной площадки изменилось: {field}")
    if site.schema_current is not True:
        blockers.append("Схема БД локальной площадки не соответствует release head")
    return blockers


def _runtime_pair_blockers(
    db: Session,
    *,
    organization_id: str,
    source_site_key: str,
    target_site_key: str,
    settings: Settings,
    now: datetime | None = None,
) -> list[str]:
    """Вернуть failover blockers when active and standby runtime evidence diverges."""

    current = _now(now)
    source = db.scalar(
        select(ExecutionSite).where(
            ExecutionSite.organization_id == organization_id,
            ExecutionSite.site_key == source_site_key,
        )
    )
    target = db.scalar(
        select(ExecutionSite).where(
            ExecutionSite.organization_id == organization_id,
            ExecutionSite.site_key == target_site_key,
        )
    )
    blockers: list[str] = []
    if source is None or not site_is_fresh(source, settings, now=current):
        blockers.append("Heartbeat исходной площадки отсутствует или устарел")
    if target is None or not site_is_fresh(target, settings, now=current):
        blockers.append("Heartbeat целевой площадки отсутствует или устарел")
    if blockers or source is None or target is None:
        return blockers
    blockers.extend(compare_site_runtime(source, target).blockers)
    return blockers


def claim_delivery_lease(
    db: Session,
    *,
    organization_id: str,
    settings: Settings,
    worker_id: str,
    now: datetime | None = None,
) -> LeaseDecision:
    """Выполнить операцию claim delivery lease. Аргументы интерпретируются в контексте модуля,
    результат возвращается вызывающему коду.
    """
    current = _now(now)
    if not settings.execution_fencing_required:
        return LeaseDecision(
            True, "EXECUTION_FENCING_DISABLED", "Execution fencing отключён", epoch=0
        )

    upsert_site_heartbeat(
        db,
        organization_id=organization_id,
        settings=settings,
        worker_id=worker_id,
        details={"claim": True},
        now=current,
    )
    runtime_blockers = _local_runtime_blockers(
        db,
        organization_id=organization_id,
        settings=settings,
        now=current,
    )
    if runtime_blockers:
        return LeaseDecision(
            False,
            "EXECUTION_RUNTIME_MISMATCH",
            "; ".join(runtime_blockers),
        )
    lease = _lease_for_update(db, organization_id)
    assert lease is not None
    if lease.status != ExecutionLeaseStatus.ACTIVE:
        return LeaseDecision(
            False,
            "EXECUTION_LEASE_DRAINING",
            "Площадка переводится в standby; новые Telegram-вызовы временно запрещены",
            lease=lease,
        )
    if lease.active_site_key != settings.execution_site_key:
        return LeaseDecision(
            False,
            "EXECUTION_SITE_STANDBY",
            f"Активной является площадка {lease.active_site_key}",
            lease=lease,
        )

    expires = aware_utc(lease.lease_expires_at)
    holder_is_current = lease.holder_worker_id == worker_id
    holder_is_live = bool(lease.holder_worker_id and expires and expires > current)
    if holder_is_live and not holder_is_current:
        return LeaseDecision(
            False,
            "EXECUTION_LEASE_HELD",
            "Организацию уже обслуживает другой active worker",
            epoch=int(lease.epoch),
            defer_until=expires,
            lease=lease,
        )

    if not holder_is_current:
        # Any takeover receives a new monotonically increasing epoch. A worker
        # carrying the previous epoch can no longer pass the network fence.
        if lease.holder_worker_id is not None or lease.last_renewed_at is not None:
            lease.epoch = int(lease.epoch) + 1
        lease.holder_worker_id = worker_id
    elif expires is not None and expires <= current:
        # The same textual worker id may be reused after a process restart. An
        # expired lease therefore also advances the epoch.
        lease.epoch = int(lease.epoch) + 1

    lease.last_renewed_at = current
    lease.lease_expires_at = current + timedelta(seconds=settings.execution_lease_ttl_seconds)
    db.flush()
    return LeaseDecision(
        True,
        "EXECUTION_LEASE_ACQUIRED",
        "Execution lease подтверждён",
        epoch=int(lease.epoch),
        defer_until=lease.lease_expires_at,
        lease=lease,
    )


def execution_gate_decision(
    db: Session,
    *,
    organization_id: str,
    settings: Settings,
    worker_id: str,
    fence_epoch: int | None,
    now: datetime | None = None,
) -> ExecutionGateDecision:
    """Выполнить операцию execution gate decision. Аргументы интерпретируются в контексте модуля,
    результат возвращается вызывающему коду.
    """
    if not settings.execution_fencing_required:
        return ExecutionGateDecision(
            True, "EXECUTION_FENCING_DISABLED", "Execution fencing отключён"
        )
    runtime_blockers = _local_runtime_blockers(
        db,
        organization_id=organization_id,
        settings=settings,
        now=now,
    )
    if runtime_blockers:
        return ExecutionGateDecision(
            False,
            "EXECUTION_RUNTIME_MISMATCH",
            "; ".join(runtime_blockers),
        )
    lease = db.scalar(
        select(ExecutionLease).where(ExecutionLease.organization_id == organization_id)
    )
    if lease is None:
        return ExecutionGateDecision(False, "EXECUTION_LEASE_MISSING", "Execution lease не создан")
    if lease.status != ExecutionLeaseStatus.ACTIVE:
        return ExecutionGateDecision(
            False, "EXECUTION_LEASE_DRAINING", "Execution lease находится в режиме draining", lease
        )
    if lease.active_site_key != settings.execution_site_key:
        return ExecutionGateDecision(
            False, "EXECUTION_SITE_STANDBY", "Текущая площадка не является active", lease
        )
    if lease.holder_worker_id != worker_id:
        return ExecutionGateDecision(
            False,
            "EXECUTION_FENCE_OWNER_MISMATCH",
            "Execution lease принадлежит другому worker",
            lease,
        )
    if fence_epoch is None or int(lease.epoch) != int(fence_epoch):
        return ExecutionGateDecision(
            False, "EXECUTION_FENCE_EPOCH_MISMATCH", "Fencing epoch задания устарел", lease
        )
    expires = aware_utc(lease.lease_expires_at)
    if expires is None or expires <= _now(now):
        return ExecutionGateDecision(
            False, "EXECUTION_LEASE_EXPIRED", "Execution lease истёк", lease
        )
    return ExecutionGateDecision(True, "EXECUTION_FENCE_OK", "Execution fence подтверждён", lease)


def lock_delivery_fence(
    db: Session,
    *,
    organization_id: str,
    settings: Settings,
    worker_id: str,
    fence_epoch: int | None,
    now: datetime | None = None,
) -> ExecutionLease | None:
    """Заблокировать the tenant execution row until the delivery transaction commits. The lock is
    intentionally held while the Telegram request is in flight. A controlled failover therefore
    cannot advance the epoch in the middle of a send. Telegram itself does not accept fencing
    tokens, so process death after the remote side accepts a message is still treated as an
    ambiguous result and never auto-retried.
    """

    if not settings.execution_fencing_required:
        return None
    current = _now(now)
    runtime_blockers = _local_runtime_blockers(
        db,
        organization_id=organization_id,
        settings=settings,
        now=current,
    )
    if runtime_blockers:
        raise ExecutionError("; ".join(runtime_blockers))
    lease = _lease_for_update(db, organization_id)
    if lease is None:
        raise ExecutionError("Execution lease отсутствует")
    if lease.status != ExecutionLeaseStatus.ACTIVE:
        raise ExecutionError("Execution lease находится в режиме draining")
    if lease.active_site_key != settings.execution_site_key:
        raise ExecutionError("Текущая площадка больше не является active")
    if lease.holder_worker_id != worker_id:
        raise ExecutionError("Execution lease передан другому worker")
    if fence_epoch is None or int(lease.epoch) != int(fence_epoch):
        raise ExecutionError("Fencing epoch задания устарел")
    expires = aware_utc(lease.lease_expires_at)
    if expires is None or expires <= current:
        raise ExecutionError("Execution lease истёк до Telegram-вызова")
    # Do not mutate the row here. SELECT FOR UPDATE itself is the fence on
    # PostgreSQL and remains held until the delivery transaction commits. The
    # lease was renewed during claim; avoiding an extra write also permits the
    # attempt ledger to persist NETWORK_STARTED in an independent transaction
    # during SQLite/WAL tests.
    return lease


def scheduler_site_allowed(
    db: Session,
    *,
    organization_id: str,
    settings: Settings,
    now: datetime | None = None,
) -> ExecutionGateDecision:
    """Выполнить операцию scheduler site allowed. Аргументы интерпретируются в контексте модуля,
    результат возвращается вызывающему коду.
    """
    if not settings.execution_fencing_required:
        return ExecutionGateDecision(
            True, "EXECUTION_FENCING_DISABLED", "Execution fencing отключён"
        )
    # The scheduler may run before the delivery worker has emitted its first
    # heartbeat after process start. Register the local runtime here so the
    # gate evaluates real evidence instead of treating a healthy fresh process
    # as an unknown site.
    upsert_site_heartbeat(
        db,
        organization_id=organization_id,
        settings=settings,
        worker_id=f"scheduler:{settings.execution_site_key}",
        details={"scheduler": True},
        now=now,
    )
    runtime_blockers = _local_runtime_blockers(
        db,
        organization_id=organization_id,
        settings=settings,
        now=now,
    )
    if runtime_blockers:
        return ExecutionGateDecision(
            False,
            "EXECUTION_RUNTIME_MISMATCH",
            "; ".join(runtime_blockers),
        )
    lease = get_or_create_execution_lease(
        db,
        organization_id=organization_id,
        settings=settings,
        now=now,
    )
    if lease.status != ExecutionLeaseStatus.ACTIVE:
        return ExecutionGateDecision(
            False, "EXECUTION_LEASE_DRAINING", "Площадка находится в draining", lease
        )
    if lease.active_site_key != settings.execution_site_key:
        return ExecutionGateDecision(
            False,
            "EXECUTION_SITE_STANDBY",
            f"Scheduler разрешён только на площадке {lease.active_site_key}",
            lease,
        )
    return ExecutionGateDecision(True, "EXECUTION_SITE_ACTIVE", "Текущая площадка active", lease)


def renew_owned_leases(
    db: Session,
    *,
    settings: Settings,
    worker_id: str,
    now: datetime | None = None,
) -> int:
    """Выполнить операцию renew owned leases. Аргументы интерпретируются в контексте модуля,
    результат возвращается вызывающему коду.
    """
    if not settings.execution_fencing_required:
        return 0
    current = _now(now)
    leases = list(
        db.scalars(
            select(ExecutionLease).where(
                ExecutionLease.active_site_key == settings.execution_site_key,
                ExecutionLease.holder_worker_id == worker_id,
                ExecutionLease.status == ExecutionLeaseStatus.ACTIVE,
            )
        ).all()
    )
    for lease in leases:
        lease.last_renewed_at = current
        lease.lease_expires_at = current + timedelta(seconds=settings.execution_lease_ttl_seconds)
    return len(leases)


class ExecutionCoordinator:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        settings: Settings,
        *,
        worker_id: str,
    ):
        """Инициализировать ExecutionCoordinator with its explicit dependencies. Сохраняется только
        состояние, необходимое последующим операциям.
        """
        self.session_factory = session_factory
        self.settings = settings
        self.worker_id = worker_id

    def heartbeat(self, details: dict[str, Any] | None = None) -> dict[str, int]:
        """Выполнить операцию heartbeat класса ExecutionCoordinator. Аргументы интерпретируются в
        контексте модуля, результат возвращается вызывающему коду.
        """
        with self.session_factory() as db:
            organization_ids = list(db.scalars(select(Organization.id)).all())
            for organization_id in organization_ids:
                upsert_site_heartbeat(
                    db,
                    organization_id=organization_id,
                    settings=self.settings,
                    worker_id=self.worker_id,
                    details=details,
                )
            renewed = renew_owned_leases(
                db,
                settings=self.settings,
                worker_id=self.worker_id,
            )
            db.commit()
        return {"sites": len(organization_ids), "leases": renewed}


def _site_for_update(
    db: Session,
    *,
    organization_id: str,
    site_key: str,
) -> ExecutionSite | None:
    """Реализовать внутренний этап site for update step. Вспомогательная функция сохраняет
    детерминированность и тестируемость процесса.
    """
    return db.scalar(
        select(ExecutionSite)
        .where(
            ExecutionSite.organization_id == organization_id,
            ExecutionSite.site_key == site_key,
        )
        .with_for_update()
    )


def request_failover(
    db: Session,
    *,
    organization_id: str,
    target_site_key: str,
    reason: str,
    actor: User,
    settings: Settings,
    now: datetime | None = None,
) -> FailoverRequest:
    """Выполнить операцию request failover. Аргументы интерпретируются в контексте модуля,
    результат возвращается вызывающему коду.
    """
    current = _now(now)
    target_key = target_site_key.strip().lower()
    lease = get_or_create_execution_lease(
        db,
        organization_id=organization_id,
        settings=settings,
        now=current,
        for_update=True,
    )
    if lease.status != ExecutionLeaseStatus.ACTIVE:
        raise ExecutionError("Уже выполняется draining или failover")
    if lease.active_site_key == target_key:
        raise ExecutionError("Целевая площадка уже является active")
    open_request = db.scalar(
        select(FailoverRequest).where(
            FailoverRequest.organization_id == organization_id,
            FailoverRequest.status == FailoverRequestStatus.REQUESTED,
        )
    )
    if open_request is not None:
        raise ExecutionError("Для организации уже открыт failover request")
    target = _site_for_update(db, organization_id=organization_id, site_key=target_key)
    if target is None or not site_is_fresh(target, settings, now=current):
        raise ExecutionError("Целевая площадка не зарегистрирована или её heartbeat устарел")
    runtime_blockers = _runtime_pair_blockers(
        db,
        organization_id=organization_id,
        source_site_key=lease.active_site_key,
        target_site_key=target_key,
        settings=settings,
        now=current,
    )
    if runtime_blockers:
        raise ExecutionError("; ".join(runtime_blockers))

    item = FailoverRequest(
        organization_id=organization_id,
        source_site_key=lease.active_site_key,
        target_site_key=target_key,
        source_epoch=int(lease.epoch),
        reason=reason.strip(),
        status=FailoverRequestStatus.REQUESTED,
        requested_by_id=actor.id,
        requested_at=current,
    )
    db.add(item)
    lease.status = ExecutionLeaseStatus.DRAINING
    lease.drain_reason = f"Failover на {target_key}: {reason.strip()}"
    lease.drain_started_at = current
    db.flush()
    write_audit(
        db,
        actor=actor,
        action="execution.failover_requested",
        entity_type="failover_request",
        entity_id=item.id,
        severity=SafetySeverity.CRITICAL,
        details={
            "source_site_key": item.source_site_key,
            "target_site_key": item.target_site_key,
            "source_epoch": item.source_epoch,
        },
    )
    create_notification(
        db,
        settings=settings,
        organization_id=organization_id,
        event_type="execution.failover_requested",
        title="Запрошено переключение active-площадки",
        message=(
            f"Публикации переведены в draining. Требуется независимое подтверждение "
            f"переключения {item.source_site_key} → {item.target_site_key}."
        ),
        severity=SafetySeverity.CRITICAL,
        entity_type="failover_request",
        entity_id=item.id,
        dedup_key=f"execution-failover:{item.id}",
        details={"source": item.source_site_key, "target": item.target_site_key},
    )
    return item


def _failover_blockers(
    db: Session,
    *,
    organization_id: str,
) -> list[str]:
    """Реализовать внутренний этап failover blockers step. Вспомогательная функция сохраняет
    детерминированность и тестируемость процесса.
    """
    processing = int(
        db.scalar(
            select(func.count(DeliveryJob.id)).where(
                DeliveryJob.organization_id == organization_id,
                DeliveryJob.status == JobStatus.PROCESSING,
            )
        )
        or 0
    )
    uncertain = int(
        db.scalar(
            select(func.count(DeliveryJob.id)).where(
                DeliveryJob.organization_id == organization_id,
                DeliveryJob.status == JobStatus.WAITING_REVIEW,
                DeliveryJob.error_code.in_(
                    ["DELIVERY_RESULT_UNCERTAIN", "WORKER_CRASH_DURING_SEND"]
                ),
            )
        )
        or 0
    )
    blockers: list[str] = []
    if processing:
        blockers.append(f"Есть активные Telegram-вызовы: {processing}")
    if uncertain:
        blockers.append(f"Есть несверенные неопределённые доставки: {uncertain}")
    return blockers


def approve_failover(
    db: Session,
    *,
    request_id: str,
    confirmation: str,
    actor: User,
    settings: Settings,
    now: datetime | None = None,
) -> FailoverRequest:
    """Выполнить операцию approve failover. Аргументы интерпретируются в контексте модуля,
    результат возвращается вызывающему коду.
    """
    current = _now(now)
    item = db.scalar(
        select(FailoverRequest)
        .where(
            FailoverRequest.id == request_id,
            FailoverRequest.organization_id == actor.organization_id,
        )
        .with_for_update()
    )
    if item is None:
        raise ExecutionError("Failover request не найден")
    if item.status != FailoverRequestStatus.REQUESTED:
        raise ExecutionError("Failover request уже завершён")
    if settings.execution_require_distinct_failover_approver and item.requested_by_id == actor.id:
        raise ExecutionError("Автор failover request не может сам выполнить переключение")
    expected = f"ПЕРЕКЛЮЧИТЬ НА {item.target_site_key}"
    if confirmation.strip() != expected:
        raise ExecutionError(f"Введите точную фразу: {expected}")

    lease = _lease_for_update(db, actor.organization_id)
    if lease is None:
        raise ExecutionError("Execution lease отсутствует")
    if lease.status != ExecutionLeaseStatus.DRAINING:
        raise ExecutionError("Execution lease больше не находится в draining")
    if lease.active_site_key != item.source_site_key or int(lease.epoch) != int(item.source_epoch):
        raise ExecutionError("Execution lease изменился после создания запроса")
    target = _site_for_update(
        db,
        organization_id=actor.organization_id,
        site_key=item.target_site_key,
    )
    blockers = _failover_blockers(db, organization_id=actor.organization_id)
    if target is None or not site_is_fresh(target, settings, now=current):
        blockers.append("Heartbeat целевой площадки отсутствует или устарел")
    blockers.extend(
        _runtime_pair_blockers(
            db,
            organization_id=actor.organization_id,
            source_site_key=item.source_site_key,
            target_site_key=item.target_site_key,
            settings=settings,
            now=current,
        )
    )
    if blockers:
        # Leave the ORM object dirty so the API layer can persist the blocker
        # snapshot while still returning a conflict response.
        item.blockers = blockers
        raise ExecutionError("; ".join(blockers))

    lease.epoch = int(lease.epoch) + 1
    lease.active_site_key = item.target_site_key
    lease.holder_worker_id = None
    lease.lease_expires_at = current
    lease.last_renewed_at = None
    lease.status = ExecutionLeaseStatus.ACTIVE
    lease.drain_reason = None
    lease.drain_started_at = None

    item.status = FailoverRequestStatus.COMPLETED
    item.approved_by_id = actor.id
    item.approved_at = current
    item.completed_at = current
    item.target_epoch = int(lease.epoch)
    item.blockers = []
    write_audit(
        db,
        actor=actor,
        action="execution.failover_completed",
        entity_type="failover_request",
        entity_id=item.id,
        severity=SafetySeverity.CRITICAL,
        details={
            "source_site_key": item.source_site_key,
            "target_site_key": item.target_site_key,
            "source_epoch": item.source_epoch,
            "target_epoch": item.target_epoch,
        },
    )
    create_notification(
        db,
        settings=settings,
        organization_id=actor.organization_id,
        event_type="execution.failover_completed",
        title="Active-площадка переключена",
        message=(
            f"Execution epoch изменён {item.source_epoch} → {item.target_epoch}. "
            f"Active site: {item.target_site_key}."
        ),
        severity=SafetySeverity.CRITICAL,
        entity_type="failover_request",
        entity_id=item.id,
        dedup_key=f"execution-failover-completed:{item.id}",
        details={"target_site_key": item.target_site_key, "target_epoch": item.target_epoch},
    )
    return item


def cancel_failover(
    db: Session,
    *,
    request_id: str,
    actor: User,
    settings: Settings,
    now: datetime | None = None,
) -> FailoverRequest:
    """Безопасно выполнить cancel failover. Зависимое состояние и видимые в аудите последствия
    обрабатываются согласованно.
    """
    current = _now(now)
    item = db.scalar(
        select(FailoverRequest)
        .where(
            FailoverRequest.id == request_id,
            FailoverRequest.organization_id == actor.organization_id,
        )
        .with_for_update()
    )
    if item is None:
        raise ExecutionError("Failover request не найден")
    if item.status != FailoverRequestStatus.REQUESTED:
        raise ExecutionError("Можно отменить только открытый failover request")
    lease = _lease_for_update(db, actor.organization_id)
    if lease is None:
        raise ExecutionError("Execution lease отсутствует")
    if (
        lease.status == ExecutionLeaseStatus.DRAINING
        and lease.active_site_key == item.source_site_key
        and int(lease.epoch) == int(item.source_epoch)
    ):
        lease.status = ExecutionLeaseStatus.ACTIVE
        lease.drain_reason = None
        lease.drain_started_at = None
    item.status = FailoverRequestStatus.CANCELLED
    item.cancelled_at = current
    write_audit(
        db,
        actor=actor,
        action="execution.failover_cancelled",
        entity_type="failover_request",
        entity_id=item.id,
        severity=SafetySeverity.WARNING,
        details={"source_site_key": item.source_site_key, "target_site_key": item.target_site_key},
    )
    create_notification(
        db,
        settings=settings,
        organization_id=actor.organization_id,
        event_type="execution.failover_cancelled",
        title="Переключение площадки отменено",
        message=f"Площадка {item.source_site_key} возвращена в active-режим.",
        severity=SafetySeverity.WARNING,
        entity_type="failover_request",
        entity_id=item.id,
        dedup_key=f"execution-failover-cancelled:{item.id}",
        details={},
    )
    return item


def invalidate_reconciled_delivery_fence(
    db: Session,
    *,
    job: DeliveryJob,
    attempt: DeliveryAttempt | None,
    now: datetime | None = None,
) -> int | None:
    """Инвалидировать the worker epoch once an operator reconciles its result. PostgreSQL blocks
    this row lock while a real Telegram request still owns the execution fence. Once
    reconciliation is possible, advancing the epoch prevents the previous process token from
    becoming valid again and allows a newly started worker to claim the organization
    immediately.
    """

    if attempt is None:
        return None
    lease = _lease_for_update(db, job.organization_id)
    if lease is None:
        return None
    if lease.holder_worker_id != attempt.worker_id or int(lease.epoch) != int(attempt.fence_epoch):
        return None
    lease.epoch = int(lease.epoch) + 1
    lease.holder_worker_id = None
    lease.lease_expires_at = _now(now)
    lease.last_renewed_at = None
    return int(lease.epoch)


def attempt_needs_manual_reconciliation(attempt: DeliveryAttempt) -> bool:
    """Выполнить операцию attempt needs manual reconciliation. Аргументы интерпретируются в
    контексте модуля, результат возвращается вызывающему коду.
    """
    return attempt.status in {
        DeliveryAttemptStatus.NETWORK_STARTED,
        DeliveryAttemptStatus.UNCERTAIN,
    }
