"""Controlled continuity drills, failback evidence and compliance checks.

A live drill intentionally reuses the production failover service so the same
fencing epoch, draining state and four-eyes approval rules are exercised.  A
simulation never mutates the execution lease and records explicit evidence
that no network call or lease mutation was performed.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit import write_audit
from app.config import Settings
from app.enums import (
    ContinuityDrillEventType,
    ContinuityDrillMode,
    ContinuityDrillStatus,
    ExecutionLeaseStatus,
    FailoverRequestStatus,
    SafetySeverity,
)
from app.models import (
    ContinuityDrill,
    ContinuityDrillEvent,
    ContinuityPolicy,
    ExecutionLease,
    ExecutionSite,
    FailoverRequest,
    Organization,
    User,
)
from app.security import aware_utc
from app.services.execution import (
    ExecutionError,
    cancel_failover,
    get_or_create_execution_lease,
    request_failover,
    site_is_fresh,
)
from app.services.notifications import create_notification
from app.services.runtime_evidence import compare_site_runtime, site_runtime_payload

logger = logging.getLogger(__name__)


class ContinuityError(RuntimeError):
    """Raised when a continuity transition violates a safety invariant."""


@dataclass(slots=True)
class ContinuityCompliance:
    """Current organization-level continuity assurance decision."""

    compliant: bool
    required: bool
    blockers: list[str]
    warnings: list[str]
    policy: ContinuityPolicy
    latest_drill: ContinuityDrill | None
    runtime_snapshot: dict[str, Any]


@dataclass(slots=True)
class ContinuitySyncSummary:
    """Result of one background projection pass over unfinished live drills."""

    scanned: int = 0
    updated: int = 0
    errors: int = 0


def _now(value: datetime | None = None) -> datetime:
    """Нормализовать an optional timestamp to an aware UTC value."""

    return aware_utc(value) or datetime.now(UTC)


def _iso_or_none(value: datetime | None) -> str | None:
    """Сериализовать an optional timestamp without inventing a replacement time."""

    normalized = aware_utc(value)
    return normalized.isoformat() if normalized is not None else None


def _canonical_sha256(payload: Any) -> str:
    """Хешировать JSON-compatible evidence using a canonical representation."""

    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def policy_snapshot(policy: ContinuityPolicy) -> dict[str, Any]:
    """Вернуть the immutable policy fields bound to a drill."""

    return {
        "enabled": bool(policy.enabled),
        "require_live_drill": bool(policy.require_live_drill),
        "max_rto_seconds": int(policy.max_rto_seconds),
        "evidence_valid_days": int(policy.evidence_valid_days),
        "require_distinct_signoff": bool(policy.require_distinct_signoff),
    }


def policy_sha256(policy: ContinuityPolicy) -> str:
    """Вычислить the fingerprint used to invalidate stale drill evidence."""

    return _canonical_sha256(policy_snapshot(policy))


def get_or_create_policy(
    db: Session,
    *,
    organization_id: str,
    settings: Settings,
    actor_id: str | None = None,
) -> ContinuityPolicy:
    """Загрузить the tenant policy or create safe defaults exactly once. The organization row is
    locked before the second lookup. PostgreSQL then serializes concurrent first requests and
    prevents duplicate unique rows.
    """

    policy = db.scalar(
        select(ContinuityPolicy).where(ContinuityPolicy.organization_id == organization_id)
    )
    if policy is not None:
        return policy
    organization = db.scalar(
        select(Organization).where(Organization.id == organization_id).with_for_update()
    )
    if organization is None:
        raise ContinuityError("Организация для continuity policy не найдена")
    policy = db.scalar(
        select(ContinuityPolicy).where(ContinuityPolicy.organization_id == organization_id)
    )
    if policy is not None:
        return policy
    policy = ContinuityPolicy(
        organization_id=organization_id,
        enabled=settings.continuity_assurance_required,
        require_live_drill=settings.continuity_default_require_live_drill,
        max_rto_seconds=settings.continuity_default_max_rto_seconds,
        evidence_valid_days=settings.continuity_default_evidence_valid_days,
        require_distinct_signoff=settings.continuity_require_distinct_signoff,
        created_by_id=actor_id,
        updated_by_id=actor_id,
    )
    db.add(policy)
    db.flush()
    return policy


def _event_payload(
    *,
    organization_id: str,
    drill_id: str,
    sequence: int,
    event_type: ContinuityDrillEventType,
    actor_user_id: str | None,
    payload: dict[str, Any],
    previous_hash: str,
    created_at: datetime,
) -> dict[str, Any]:
    """Построить the exact data protected by a continuity event hash."""

    return {
        "organization_id": organization_id,
        "drill_id": drill_id,
        "sequence": sequence,
        "event_type": event_type.value,
        "actor_user_id": actor_user_id,
        "payload": payload,
        "previous_hash": previous_hash,
        "created_at": created_at.isoformat(),
    }


def append_event(
    db: Session,
    *,
    drill: ContinuityDrill,
    event_type: ContinuityDrillEventType,
    actor: User | None,
    payload: dict[str, Any] | None = None,
    now: datetime | None = None,
) -> ContinuityDrillEvent:
    """Добавить one hash-linked event while the drill row is transaction-locked."""

    current = _now(now)
    last = db.scalar(
        select(ContinuityDrillEvent)
        .where(ContinuityDrillEvent.drill_id == drill.id)
        .order_by(ContinuityDrillEvent.sequence.desc())
        .limit(1)
    )
    sequence = int(last.sequence) + 1 if last else 1
    previous_hash = last.event_hash if last else "0" * 64
    event_payload = payload or {}
    actor_id = actor.id if actor else None
    digest = _canonical_sha256(
        _event_payload(
            organization_id=drill.organization_id,
            drill_id=drill.id,
            sequence=sequence,
            event_type=event_type,
            actor_user_id=actor_id,
            payload=event_payload,
            previous_hash=previous_hash,
            created_at=current,
        )
    )
    event = ContinuityDrillEvent(
        organization_id=drill.organization_id,
        drill_id=drill.id,
        sequence=sequence,
        event_type=event_type,
        actor_user_id=actor_id,
        payload=event_payload,
        previous_hash=previous_hash,
        event_hash=digest,
        created_at=current,
    )
    db.add(event)
    db.flush()
    return event


def verify_event_chain(db: Session, *, drill_id: str) -> dict[str, Any]:
    """Пересчитать every event hash and report the first integrity failure. The drill tenant is
    authoritative, so moving event rows to another tenant cannot be hidden by recalculating the
    individual hashes.
    """

    drill = db.get(ContinuityDrill, drill_id)
    if drill is None:
        return {
            "valid": False,
            "checked_events": 0,
            "last_hash": None,
            "error": "История continuity-учения пуста: drill не найден",
        }
    events = list(
        db.scalars(
            select(ContinuityDrillEvent)
            .where(ContinuityDrillEvent.drill_id == drill_id)
            .order_by(ContinuityDrillEvent.sequence)
        ).all()
    )
    if not events:
        return {
            "valid": False,
            "checked_events": 0,
            "last_hash": None,
            "error": "История continuity-учения пуста",
        }
    previous_hash = "0" * 64
    expected_sequence = 1
    for event in events:
        if event.organization_id != drill.organization_id:
            return {
                "valid": False,
                "checked_events": expected_sequence - 1,
                "error": f"Событие {event.sequence} принадлежит другой организации",
            }
        if int(event.sequence) != expected_sequence:
            return {
                "valid": False,
                "checked_events": expected_sequence - 1,
                "error": f"Ожидалась последовательность {expected_sequence}",
            }
        if event.previous_hash != previous_hash:
            return {
                "valid": False,
                "checked_events": expected_sequence - 1,
                "error": f"Нарушена ссылка previous_hash у события {event.sequence}",
            }
        calculated = _canonical_sha256(
            _event_payload(
                organization_id=event.organization_id,
                drill_id=event.drill_id,
                sequence=int(event.sequence),
                event_type=event.event_type,
                actor_user_id=event.actor_user_id,
                payload=event.payload,
                previous_hash=event.previous_hash,
                created_at=_now(event.created_at),
            )
        )
        if calculated != event.event_hash:
            return {
                "valid": False,
                "checked_events": expected_sequence - 1,
                "error": f"Hash события {event.sequence} не совпадает",
            }
        previous_hash = event.event_hash
        expected_sequence += 1
    return {
        "valid": True,
        "checked_events": len(events),
        "last_hash": previous_hash,
        "error": None,
    }


def _site_for_snapshot(db: Session, *, organization_id: str, site_key: str) -> ExecutionSite | None:
    """Загрузить runtime evidence without violating the lease-first lock order."""

    return db.scalar(
        select(ExecutionSite).where(
            ExecutionSite.organization_id == organization_id,
            ExecutionSite.site_key == site_key,
        )
    )


def runtime_pair_snapshot(
    db: Session,
    *,
    organization_id: str,
    source_site_key: str,
    target_site_key: str,
    settings: Settings,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Проверить and snapshot the active/standby runtime pair."""

    current = _now(now)
    source = _site_for_snapshot(db, organization_id=organization_id, site_key=source_site_key)
    target = _site_for_snapshot(db, organization_id=organization_id, site_key=target_site_key)
    blockers: list[str] = []
    if source is None or not site_is_fresh(source, settings, now=current):
        blockers.append("Heartbeat исходной площадки отсутствует или устарел")
    if target is None or not site_is_fresh(target, settings, now=current):
        blockers.append("Heartbeat целевой площадки отсутствует или устарел")
    if blockers:
        raise ContinuityError("; ".join(blockers))
    assert source is not None and target is not None
    compatibility = compare_site_runtime(source, target)
    if not compatibility.allowed:
        raise ContinuityError("; ".join(compatibility.blockers))
    return {
        "source": compatibility.source,
        "target": compatibility.target,
        "compatibility": {
            "allowed": compatibility.allowed,
            "blockers": compatibility.blockers,
            "warnings": compatibility.warnings,
        },
    }


def _lock_drill(db: Session, *, drill_id: str, organization_id: str) -> ContinuityDrill:
    """Загрузить one tenant drill under a row lock or raise a domain error."""

    drill = db.scalar(
        select(ContinuityDrill)
        .where(
            ContinuityDrill.id == drill_id,
            ContinuityDrill.organization_id == organization_id,
        )
        .with_for_update()
    )
    if drill is None:
        raise ContinuityError("Continuity drill не найден")
    return drill


def _assert_policy_current(
    db: Session, *, drill: ContinuityDrill, settings: Settings
) -> ContinuityPolicy:
    """Убедиться, что the drill still uses the organization's current policy."""

    policy = get_or_create_policy(db, organization_id=drill.organization_id, settings=settings)
    if drill.policy_sha256 != policy_sha256(policy):
        raise ContinuityError("Политика continuity изменилась после создания учения")
    return policy


def create_drill(
    db: Session,
    *,
    mode: ContinuityDrillMode,
    target_site_key: str,
    actor: User,
    settings: Settings,
    now: datetime | None = None,
) -> ContinuityDrill:
    """Создать a draft drill bound to the current lease and policy fingerprint."""

    current = _now(now)
    lease = get_or_create_execution_lease(
        db,
        organization_id=actor.organization_id,
        settings=settings,
        now=current,
        for_update=True,
    )
    target_key = target_site_key.strip().lower()
    if target_key == lease.active_site_key:
        raise ContinuityError("Целевая площадка уже является active")
    existing = db.scalar(
        select(ContinuityDrill).where(
            ContinuityDrill.organization_id == actor.organization_id,
            ContinuityDrill.status.in_(
                [
                    ContinuityDrillStatus.DRAFT,
                    ContinuityDrillStatus.RUNNING,
                    ContinuityDrillStatus.AWAITING_FAILBACK,
                    ContinuityDrillStatus.AWAITING_SIGNOFF,
                ]
            ),
        )
    )
    if existing is not None:
        raise ContinuityError("Сначала завершите или отмените текущее continuity-учение")
    policy = get_or_create_policy(
        db,
        organization_id=actor.organization_id,
        settings=settings,
        actor_id=actor.id,
    )
    runtime = runtime_pair_snapshot(
        db,
        organization_id=actor.organization_id,
        source_site_key=lease.active_site_key,
        target_site_key=target_key,
        settings=settings,
        now=current,
    )
    drill = ContinuityDrill(
        organization_id=actor.organization_id,
        mode=mode,
        status=ContinuityDrillStatus.DRAFT,
        source_site_key=lease.active_site_key,
        target_site_key=target_key,
        source_epoch=int(lease.epoch),
        policy_snapshot=policy_snapshot(policy),
        policy_sha256=policy_sha256(policy),
        runtime_snapshot=runtime,
        created_by_id=actor.id,
    )
    db.add(drill)
    db.flush()
    append_event(
        db,
        drill=drill,
        event_type=ContinuityDrillEventType.CREATED,
        actor=actor,
        payload={
            "mode": mode.value,
            "source_site_key": drill.source_site_key,
            "target_site_key": drill.target_site_key,
            "source_epoch": drill.source_epoch,
            "policy_sha256": drill.policy_sha256,
        },
        now=current,
    )
    write_audit(
        db,
        actor=actor,
        action="continuity.drill_created",
        entity_type="continuity_drill",
        entity_id=drill.id,
        severity=SafetySeverity.WARNING,
        details={"mode": mode.value, "target_site_key": target_key},
    )
    return drill


def _simulation_evidence(drill: ContinuityDrill, completed_at: datetime) -> dict[str, Any]:
    """Создать evidence proving that simulation did not touch Telegram or the lease."""

    return {
        "schema_version": 1,
        "drill_id": drill.id,
        "mode": drill.mode.value,
        "source_site_key": drill.source_site_key,
        "target_site_key": drill.target_site_key,
        "source_epoch": drill.source_epoch,
        "policy_sha256": drill.policy_sha256,
        "runtime_snapshot": drill.runtime_snapshot,
        "started_at": _iso_or_none(drill.started_at),
        "completed_at": completed_at.isoformat(),
        "simulation": {
            "network_calls": 0,
            "lease_mutated": False,
            "failover_requests_created": 0,
        },
    }


def _live_evidence(drill: ContinuityDrill, completed_at: datetime) -> dict[str, Any]:
    """Создать immutable evidence for a completed failover/failback cycle."""

    return {
        "schema_version": 1,
        "drill_id": drill.id,
        "mode": drill.mode.value,
        "source_site_key": drill.source_site_key,
        "target_site_key": drill.target_site_key,
        "source_epoch": drill.source_epoch,
        "target_epoch": drill.target_epoch,
        "return_epoch": drill.return_epoch,
        "failover_request_id": drill.failover_request_id,
        "failback_request_id": drill.failback_request_id,
        "policy_sha256": drill.policy_sha256,
        "runtime_snapshot": drill.runtime_snapshot,
        "started_at": _iso_or_none(drill.started_at),
        "target_active_at": _iso_or_none(drill.target_active_at),
        "primary_restored_at": _iso_or_none(drill.primary_restored_at),
        "completed_at": completed_at.isoformat(),
        "rto_seconds": drill.rto_seconds,
        "network_calls": "performed through fenced production failover service",
        "lease_mutated": True,
    }


def start_drill(
    db: Session,
    *,
    drill_id: str,
    actor: User,
    settings: Settings,
    now: datetime | None = None,
) -> ContinuityDrill:
    """Запустить a simulation or create the first live failover request."""

    current = _now(now)
    drill = _lock_drill(db, drill_id=drill_id, organization_id=actor.organization_id)
    if drill.status != ContinuityDrillStatus.DRAFT:
        raise ContinuityError("Запустить можно только draft-учение")
    _assert_policy_current(db, drill=drill, settings=settings)
    lease = get_or_create_execution_lease(
        db,
        organization_id=actor.organization_id,
        settings=settings,
        now=current,
        for_update=True,
    )
    if lease.status != ExecutionLeaseStatus.ACTIVE:
        raise ContinuityError("Execution lease находится в draining")
    if lease.active_site_key != drill.source_site_key or int(lease.epoch) != int(
        drill.source_epoch
    ):
        raise ContinuityError("Active-площадка или epoch изменились после создания учения")
    drill.runtime_snapshot = runtime_pair_snapshot(
        db,
        organization_id=actor.organization_id,
        source_site_key=drill.source_site_key,
        target_site_key=drill.target_site_key,
        settings=settings,
        now=current,
    )
    drill.started_at = current
    if drill.mode == ContinuityDrillMode.SIMULATION:
        drill.completed_at = current
        drill.status = ContinuityDrillStatus.AWAITING_SIGNOFF
        evidence = _simulation_evidence(drill, current)
        drill.evidence_payload = evidence
        drill.evidence_sha256 = _canonical_sha256(evidence)
        append_event(
            db,
            drill=drill,
            event_type=ContinuityDrillEventType.SIMULATION_COMPLETED,
            actor=actor,
            payload={
                "network_calls": 0,
                "lease_mutated": False,
                "evidence_sha256": drill.evidence_sha256,
            },
            now=current,
        )
    else:
        try:
            request = request_failover(
                db,
                organization_id=actor.organization_id,
                target_site_key=drill.target_site_key,
                reason=f"Continuity drill {drill.id}: failover",
                actor=actor,
                settings=settings,
                now=current,
            )
        except ExecutionError as exc:
            raise ContinuityError(str(exc)) from exc
        drill.failover_request_id = request.id
        drill.status = ContinuityDrillStatus.RUNNING
        append_event(
            db,
            drill=drill,
            event_type=ContinuityDrillEventType.FAILOVER_REQUESTED,
            actor=actor,
            payload={"failover_request_id": request.id, "source_epoch": request.source_epoch},
            now=current,
        )
    write_audit(
        db,
        actor=actor,
        action="continuity.drill_started",
        entity_type="continuity_drill",
        entity_id=drill.id,
        severity=SafetySeverity.CRITICAL
        if drill.mode == ContinuityDrillMode.LIVE
        else SafetySeverity.WARNING,
        details={"mode": drill.mode.value, "status": drill.status.value},
    )
    return drill


def _request_mismatch(
    request: FailoverRequest, drill: ContinuityDrill, *, failback: bool
) -> list[str]:
    """Проверить the route and epochs of a completed failover request."""

    blockers: list[str] = []
    expected_source = drill.target_site_key if failback else drill.source_site_key
    expected_target = drill.source_site_key if failback else drill.target_site_key
    expected_epoch = drill.target_epoch if failback else drill.source_epoch
    if request.organization_id != drill.organization_id:
        blockers.append("Failover request принадлежит другой организации")
    if request.source_site_key != expected_source:
        blockers.append("Исходная площадка failover request не совпадает с учением")
    if request.target_site_key != expected_target:
        blockers.append("Целевая площадка failover request не совпадает с учением")
    if expected_epoch is None or int(request.source_epoch) != int(expected_epoch):
        blockers.append("Исходный execution epoch failover request не совпадает с учением")
    if request.target_epoch is None or int(request.target_epoch) <= int(request.source_epoch):
        blockers.append("Failover request не содержит монотонный target epoch")
    return blockers


def _mark_failed(
    db: Session,
    *,
    drill: ContinuityDrill,
    reason: str,
    actor: User | None,
    now: datetime,
) -> ContinuityDrill:
    """Сохранить a terminal failure and append exactly one failure event."""

    drill.status = ContinuityDrillStatus.FAILED
    drill.completed_at = now
    drill.failure_reason = reason
    append_event(
        db,
        drill=drill,
        event_type=ContinuityDrillEventType.FAILED,
        actor=actor,
        payload={"reason": reason},
        now=now,
    )
    return drill


def synchronize_drill(
    db: Session,
    *,
    drill: ContinuityDrill,
    settings: Settings,
    actor: User | None = None,
    now: datetime | None = None,
) -> ContinuityDrill:
    """Отразить completed failover requests into the drill state machine."""

    current = _now(now)
    locked = db.scalar(
        select(ContinuityDrill)
        .where(
            ContinuityDrill.id == drill.id,
            ContinuityDrill.organization_id == drill.organization_id,
        )
        .with_for_update()
    )
    if locked is None:
        raise ContinuityError("Continuity drill не найден")
    drill = locked
    if drill.mode != ContinuityDrillMode.LIVE or drill.status in {
        ContinuityDrillStatus.PASSED,
        ContinuityDrillStatus.FAILED,
        ContinuityDrillStatus.CANCELLED,
        ContinuityDrillStatus.INVALIDATED,
    }:
        return drill

    if drill.failover_request_id and drill.target_active_at is None:
        first = db.get(FailoverRequest, drill.failover_request_id)
        if first and first.status == FailoverRequestStatus.COMPLETED:
            mismatch = _request_mismatch(first, drill, failback=False)
            lease = db.scalar(
                select(ExecutionLease)
                .where(ExecutionLease.organization_id == drill.organization_id)
                .with_for_update()
            )
            if lease is None:
                mismatch.append("Execution lease отсутствует после failover")
            else:
                if lease.status != ExecutionLeaseStatus.ACTIVE:
                    mismatch.append("Execution lease не active после failover")
                if lease.active_site_key != drill.target_site_key:
                    mismatch.append("Active-площадка не совпадает с target учения")
                if first.target_epoch is None or int(lease.epoch) != int(first.target_epoch):
                    mismatch.append("Execution lease epoch не совпадает с failover request")
            if mismatch:
                return _mark_failed(
                    db, drill=drill, reason="; ".join(mismatch), actor=actor, now=current
                )
            drill.target_epoch = first.target_epoch
            drill.target_active_at = aware_utc(first.completed_at) or current
            drill.status = ContinuityDrillStatus.AWAITING_FAILBACK
            append_event(
                db,
                drill=drill,
                event_type=ContinuityDrillEventType.TARGET_ACTIVE,
                actor=actor,
                payload={
                    "site_key": drill.target_site_key,
                    "target_epoch": drill.target_epoch,
                    "failover_request_id": first.id,
                },
                now=drill.target_active_at,
            )
        elif first and first.status in {
            FailoverRequestStatus.CANCELLED,
            FailoverRequestStatus.FAILED,
        }:
            event = (
                ContinuityDrillEventType.CANCELLED
                if first.status == FailoverRequestStatus.CANCELLED
                else ContinuityDrillEventType.FAILED
            )
            drill.status = (
                ContinuityDrillStatus.CANCELLED
                if first.status == FailoverRequestStatus.CANCELLED
                else ContinuityDrillStatus.FAILED
            )
            drill.completed_at = current
            drill.failure_reason = f"Failover request завершён со статусом {first.status.value}"
            append_event(
                db,
                drill=drill,
                event_type=event,
                actor=actor,
                payload={"request_id": first.id, "reason": drill.failure_reason},
                now=current,
            )
            return drill

    if drill.failback_request_id and drill.primary_restored_at is None:
        second = db.get(FailoverRequest, drill.failback_request_id)
        if second and second.status == FailoverRequestStatus.COMPLETED:
            mismatch = _request_mismatch(second, drill, failback=True)
            lease = db.scalar(
                select(ExecutionLease)
                .where(ExecutionLease.organization_id == drill.organization_id)
                .with_for_update()
            )
            if lease is None:
                mismatch.append("Execution lease отсутствует после failback")
            else:
                if lease.status != ExecutionLeaseStatus.ACTIVE:
                    mismatch.append("Execution lease не active после failback")
                if lease.active_site_key != drill.source_site_key:
                    mismatch.append("Active-площадка не вернулась на source учения")
                if second.target_epoch is None or int(lease.epoch) != int(second.target_epoch):
                    mismatch.append("Execution lease epoch не совпадает с failback request")
            if mismatch:
                return _mark_failed(
                    db, drill=drill, reason="; ".join(mismatch), actor=actor, now=current
                )
            drill.return_epoch = second.target_epoch
            drill.primary_restored_at = aware_utc(second.completed_at) or current
            drill.completed_at = drill.primary_restored_at
            drill.rto_seconds = max(
                int((drill.primary_restored_at - _now(drill.started_at)).total_seconds()), 0
            )
            drill.status = ContinuityDrillStatus.AWAITING_SIGNOFF
            evidence = _live_evidence(drill, drill.primary_restored_at)
            drill.evidence_payload = evidence
            drill.evidence_sha256 = _canonical_sha256(evidence)
            append_event(
                db,
                drill=drill,
                event_type=ContinuityDrillEventType.PRIMARY_RESTORED,
                actor=actor,
                payload={
                    "site_key": drill.source_site_key,
                    "return_epoch": drill.return_epoch,
                    "rto_seconds": drill.rto_seconds,
                    "evidence_sha256": drill.evidence_sha256,
                },
                now=drill.primary_restored_at,
            )
        elif second and second.status in {
            FailoverRequestStatus.CANCELLED,
            FailoverRequestStatus.FAILED,
        }:
            event = (
                ContinuityDrillEventType.CANCELLED
                if second.status == FailoverRequestStatus.CANCELLED
                else ContinuityDrillEventType.FAILED
            )
            drill.status = (
                ContinuityDrillStatus.CANCELLED
                if second.status == FailoverRequestStatus.CANCELLED
                else ContinuityDrillStatus.FAILED
            )
            drill.completed_at = current
            drill.failure_reason = f"Failback request завершён со статусом {second.status.value}"
            append_event(
                db,
                drill=drill,
                event_type=event,
                actor=actor,
                payload={"request_id": second.id, "reason": drill.failure_reason},
                now=current,
            )
    return drill


def request_failback(
    db: Session,
    *,
    drill_id: str,
    actor: User,
    settings: Settings,
    now: datetime | None = None,
) -> ContinuityDrill:
    """Создать the managed return request after the standby becomes active."""

    current = _now(now)
    drill = _lock_drill(db, drill_id=drill_id, organization_id=actor.organization_id)
    drill = synchronize_drill(db, drill=drill, settings=settings, actor=actor, now=current)
    if drill.mode != ContinuityDrillMode.LIVE:
        raise ContinuityError("Failback доступен только для live-учения")
    if drill.status != ContinuityDrillStatus.AWAITING_FAILBACK:
        raise ContinuityError("Сначала завершите failover на резервную площадку")
    lease = db.scalar(
        select(ExecutionLease)
        .where(ExecutionLease.organization_id == actor.organization_id)
        .with_for_update()
    )
    if lease is None or lease.active_site_key != drill.target_site_key:
        raise ContinuityError("Резервная площадка не является active")
    if lease.status != ExecutionLeaseStatus.ACTIVE:
        raise ContinuityError("Execution lease находится в draining")
    if drill.target_epoch is None or int(lease.epoch) != int(drill.target_epoch):
        raise ContinuityError("Execution epoch резервной площадки изменился")
    try:
        request = request_failover(
            db,
            organization_id=actor.organization_id,
            target_site_key=drill.source_site_key,
            reason=f"Continuity drill {drill.id}: failback",
            actor=actor,
            settings=settings,
            now=current,
        )
    except ExecutionError as exc:
        raise ContinuityError(str(exc)) from exc
    drill.failback_request_id = request.id
    drill.failback_requested_at = current
    drill.status = ContinuityDrillStatus.RUNNING
    append_event(
        db,
        drill=drill,
        event_type=ContinuityDrillEventType.FAILBACK_REQUESTED,
        actor=actor,
        payload={"failback_request_id": request.id, "source_epoch": request.source_epoch},
        now=current,
    )
    write_audit(
        db,
        actor=actor,
        action="continuity.failback_requested",
        entity_type="continuity_drill",
        entity_id=drill.id,
        severity=SafetySeverity.CRITICAL,
        details={"failback_request_id": request.id},
    )
    return drill


def _evidence_semantic_blockers(drill: ContinuityDrill) -> list[str]:
    """Сравнить stored evidence with authoritative drill columns."""

    payload = drill.evidence_payload or {}
    blockers: list[str] = []
    common = {
        "schema_version": 1,
        "drill_id": drill.id,
        "mode": drill.mode.value,
        "source_site_key": drill.source_site_key,
        "target_site_key": drill.target_site_key,
        "source_epoch": drill.source_epoch,
        "policy_sha256": drill.policy_sha256,
        "runtime_snapshot": drill.runtime_snapshot,
        "started_at": _iso_or_none(drill.started_at),
        "completed_at": _iso_or_none(drill.completed_at),
    }
    for field, expected in common.items():
        if payload.get(field) != expected:
            blockers.append(f"Evidence-поле {field} не совпадает с continuity drill")
    if drill.mode == ContinuityDrillMode.SIMULATION:
        if payload.get("simulation") != {
            "network_calls": 0,
            "lease_mutated": False,
            "failover_requests_created": 0,
        }:
            blockers.append("Simulation evidence не подтверждает нулевое сетевое воздействие")
    else:
        live = {
            "target_epoch": drill.target_epoch,
            "return_epoch": drill.return_epoch,
            "failover_request_id": drill.failover_request_id,
            "failback_request_id": drill.failback_request_id,
            "rto_seconds": drill.rto_seconds,
            "lease_mutated": True,
            "target_active_at": _iso_or_none(drill.target_active_at),
            "primary_restored_at": _iso_or_none(drill.primary_restored_at),
        }
        for field, expected in live.items():
            if payload.get(field) != expected:
                blockers.append(f"Live evidence-поле {field} не совпадает с continuity drill")
        if payload.get("network_calls") != "performed through fenced production failover service":
            blockers.append("Live evidence не подтверждает использование fenced failover service")
    return blockers


def _passed_semantic_blockers(db: Session, drill: ContinuityDrill) -> list[str]:
    """Проверить sign-off metadata and the terminal acceptance event."""

    blockers = _evidence_semantic_blockers(drill)
    if drill.signed_off_by_id is None or drill.signed_off_at is None:
        blockers.append("Continuity-evidence не имеет независимой приёмки")
    events = list(
        db.scalars(
            select(ContinuityDrillEvent)
            .where(ContinuityDrillEvent.drill_id == drill.id)
            .order_by(ContinuityDrillEvent.sequence)
        ).all()
    )
    if not events:
        blockers.append("История continuity-учения пуста")
        return blockers
    terminal = events[-1]
    if terminal.event_type != ContinuityDrillEventType.SIGNED_OFF:
        blockers.append("Последнее событие continuity-учения не является SIGNED_OFF")
    if terminal.actor_user_id != drill.signed_off_by_id:
        blockers.append("Подписавший пользователь не совпадает с terminal event")
    if terminal.payload.get("accepted") is not True:
        blockers.append("Terminal event не подтверждает принятие evidence")
    if terminal.payload.get("evidence_sha256") != drill.evidence_sha256:
        blockers.append("Terminal event связан с другим evidence SHA-256")
    if terminal.payload.get("verified_event_chain_last_hash") != terminal.previous_hash:
        blockers.append("Terminal event не подтверждает hash цепочки, проверенный до приёмки")
    return blockers


def signoff_drill(
    db: Session,
    *,
    drill_id: str,
    actor: User,
    settings: Settings,
    accepted: bool,
    note: str,
    now: datetime | None = None,
) -> ContinuityDrill:
    """Независимо accept or reject completed continuity evidence."""

    current = _now(now)
    drill = _lock_drill(db, drill_id=drill_id, organization_id=actor.organization_id)
    drill = synchronize_drill(db, drill=drill, settings=settings, actor=actor, now=current)
    if drill.status != ContinuityDrillStatus.AWAITING_SIGNOFF:
        raise ContinuityError("Учение ещё не готово к независимой приёмке")
    policy = _assert_policy_current(db, drill=drill, settings=settings)
    if (
        policy.require_distinct_signoff or settings.continuity_require_distinct_signoff
    ) and drill.created_by_id == actor.id:
        raise ContinuityError("Автор учения не может самостоятельно выполнить приёмку")
    if not drill.evidence_payload or not drill.evidence_sha256:
        raise ContinuityError("Evidence учения отсутствует")
    if _canonical_sha256(drill.evidence_payload) != drill.evidence_sha256:
        raise ContinuityError("Evidence SHA-256 не совпадает")
    semantic = _evidence_semantic_blockers(drill)
    if semantic:
        raise ContinuityError("; ".join(semantic))
    chain = verify_event_chain(db, drill_id=drill.id)
    if not chain["valid"]:
        raise ContinuityError(f"История учения повреждена: {chain['error']}")
    acceptance_blockers: list[str] = []
    if drill.mode == ContinuityDrillMode.SIMULATION and policy.require_live_drill:
        acceptance_blockers.append("Политика требует live-drill")
    if drill.rto_seconds is not None and drill.rto_seconds > policy.max_rto_seconds:
        acceptance_blockers.append(
            f"RTO {drill.rto_seconds} сек. превышает лимит {policy.max_rto_seconds} сек."
        )
    if accepted and acceptance_blockers:
        raise ContinuityError("; ".join(acceptance_blockers))
    drill.signed_off_by_id = actor.id
    drill.signed_off_at = current
    drill.signoff_note = note.strip()
    drill.completed_at = drill.completed_at or current
    if accepted:
        drill.status = ContinuityDrillStatus.PASSED
        drill.expires_at = current + timedelta(days=policy.evidence_valid_days)
        event_type = ContinuityDrillEventType.SIGNED_OFF
    else:
        drill.status = ContinuityDrillStatus.FAILED
        drill.failure_reason = note.strip() or "Учение отклонено при независимой приёмке"
        drill.expires_at = None
        event_type = ContinuityDrillEventType.FAILED
    append_event(
        db,
        drill=drill,
        event_type=event_type,
        actor=actor,
        payload={
            "accepted": accepted,
            "note": drill.signoff_note,
            "verified_event_chain_last_hash": chain.get("last_hash"),
            "evidence_sha256": drill.evidence_sha256,
            "rto_seconds": drill.rto_seconds,
            "expires_at": _iso_or_none(drill.expires_at),
        },
        now=current,
    )
    write_audit(
        db,
        actor=actor,
        action="continuity.drill_signed_off",
        entity_type="continuity_drill",
        entity_id=drill.id,
        severity=SafetySeverity.CRITICAL,
        details={"accepted": accepted, "mode": drill.mode.value, "rto_seconds": drill.rto_seconds},
    )
    create_notification(
        db,
        settings=settings,
        organization_id=actor.organization_id,
        event_type="continuity.drill_signed_off",
        title="Учение непрерывности принято" if accepted else "Учение непрерывности отклонено",
        message=f"Результат {drill.mode.value}; RTO: {drill.rto_seconds or 0} сек.; evidence: {drill.evidence_sha256}.",
        severity=SafetySeverity.INFO if accepted else SafetySeverity.CRITICAL,
        entity_type="continuity_drill",
        entity_id=drill.id,
        dedup_key=f"continuity-signoff:{drill.id}",
        details={"accepted": accepted},
    )
    return drill


def cancel_drill(
    db: Session,
    *,
    drill_id: str,
    actor: User,
    settings: Settings,
    now: datetime | None = None,
) -> ContinuityDrill:
    """Отменить an unfinished drill without stranding active on standby."""

    current = _now(now)
    drill = _lock_drill(db, drill_id=drill_id, organization_id=actor.organization_id)
    drill = synchronize_drill(db, drill=drill, settings=settings, actor=actor, now=current)
    lease = db.scalar(
        select(ExecutionLease)
        .where(ExecutionLease.organization_id == actor.organization_id)
        .with_for_update()
    )
    if (
        drill.mode == ContinuityDrillMode.LIVE
        and drill.primary_restored_at is None
        and (
            drill.target_active_at is not None
            or (lease is not None and lease.active_site_key == drill.target_site_key)
        )
    ):
        raise ContinuityError("После live-failover сначала выполните управляемый failback")
    if drill.status in {
        ContinuityDrillStatus.PASSED,
        ContinuityDrillStatus.FAILED,
        ContinuityDrillStatus.CANCELLED,
        ContinuityDrillStatus.INVALIDATED,
    }:
        raise ContinuityError("Завершённое учение нельзя отменить")
    for request_id in (drill.failback_request_id, drill.failover_request_id):
        if not request_id:
            continue
        request = db.get(FailoverRequest, request_id)
        if request and request.status == FailoverRequestStatus.REQUESTED:
            try:
                cancel_failover(
                    db, request_id=request.id, actor=actor, settings=settings, now=current
                )
            except ExecutionError as exc:
                raise ContinuityError(str(exc)) from exc
            break
    drill.status = ContinuityDrillStatus.CANCELLED
    drill.completed_at = current
    drill.failure_reason = "Отменено оператором"
    append_event(
        db,
        drill=drill,
        event_type=ContinuityDrillEventType.CANCELLED,
        actor=actor,
        payload={"reason": drill.failure_reason},
        now=current,
    )
    return drill


def update_policy(
    db: Session,
    *,
    actor: User,
    settings: Settings,
    enabled: bool,
    require_live_drill: bool,
    max_rto_seconds: int,
    evidence_valid_days: int,
    require_distinct_signoff: bool,
    now: datetime | None = None,
) -> ContinuityPolicy:
    """Обновить policy and invalidate unfinished evidence bound to the old hash."""

    current = _now(now)
    if settings.continuity_assurance_required and not enabled:
        raise ContinuityError("Обязательный continuity-gate нельзя отключить")
    if settings.is_production and not require_live_drill:
        raise ContinuityError("В production требуется подтверждённый live-drill")
    if settings.continuity_require_distinct_signoff and not require_distinct_signoff:
        raise ContinuityError("Настройка платформы требует независимую приёмку")
    unfinished = list(
        db.scalars(
            select(ContinuityDrill)
            .where(
                ContinuityDrill.organization_id == actor.organization_id,
                ContinuityDrill.status.in_(
                    [
                        ContinuityDrillStatus.DRAFT,
                        ContinuityDrillStatus.RUNNING,
                        ContinuityDrillStatus.AWAITING_FAILBACK,
                        ContinuityDrillStatus.AWAITING_SIGNOFF,
                    ]
                ),
            )
            .with_for_update()
        ).all()
    )
    lease = db.scalar(
        select(ExecutionLease)
        .where(ExecutionLease.organization_id == actor.organization_id)
        .with_for_update()
    )
    if any(
        drill.mode == ContinuityDrillMode.LIVE
        and drill.primary_restored_at is None
        and (
            drill.target_active_at is not None
            or (lease is not None and lease.active_site_key == drill.target_site_key)
        )
        for drill in unfinished
    ):
        raise ContinuityError("Нельзя менять политику, пока active lease не возвращён на source")
    policy = get_or_create_policy(
        db,
        organization_id=actor.organization_id,
        settings=settings,
        actor_id=actor.id,
    )
    old_hash = policy_sha256(policy)
    policy.enabled = enabled
    policy.require_live_drill = require_live_drill
    policy.max_rto_seconds = max_rto_seconds
    policy.evidence_valid_days = evidence_valid_days
    policy.require_distinct_signoff = require_distinct_signoff
    policy.updated_by_id = actor.id
    db.flush()
    new_hash = policy_sha256(policy)
    if new_hash != old_hash:
        for drill in unfinished:
            drill.status = ContinuityDrillStatus.INVALIDATED
            drill.completed_at = current
            drill.failure_reason = "Политика continuity изменена"
            append_event(
                db,
                drill=drill,
                event_type=ContinuityDrillEventType.INVALIDATED,
                actor=actor,
                payload={"old_policy_sha256": old_hash, "new_policy_sha256": new_hash},
                now=current,
            )
    write_audit(
        db,
        actor=actor,
        action="continuity.policy_updated",
        entity_type="continuity_policy",
        entity_id=policy.id,
        severity=SafetySeverity.WARNING,
        details={"old_policy_sha256": old_hash, "new_policy_sha256": new_hash},
    )
    return policy


def synchronize_open_drills(
    db: Session,
    *,
    settings: Settings,
    now: datetime | None = None,
) -> ContinuitySyncSummary:
    """Отразить completed failover requests for every unfinished live drill. Each drill runs inside
    its own database savepoint. A malformed drill or a transient row-level conflict therefore
    cannot roll back successful state transitions for another organization. This routine never
    creates a failover request and never calls Telegram; it only projects already completed
    execution requests into the continuity state machine.
    """

    current = _now(now)
    drill_ids = list(
        db.scalars(
            select(ContinuityDrill.id)
            .where(
                ContinuityDrill.mode == ContinuityDrillMode.LIVE,
                ContinuityDrill.status.in_(
                    [
                        ContinuityDrillStatus.RUNNING,
                        ContinuityDrillStatus.AWAITING_FAILBACK,
                    ]
                ),
            )
            .order_by(ContinuityDrill.created_at, ContinuityDrill.id)
        ).all()
    )
    summary = ContinuitySyncSummary(scanned=len(drill_ids))
    for drill_id in drill_ids:
        try:
            with db.begin_nested():
                drill = db.get(ContinuityDrill, drill_id)
                if drill is None:
                    continue
                before = (
                    drill.status,
                    aware_utc(drill.target_active_at),
                    aware_utc(drill.primary_restored_at),
                    drill.evidence_sha256,
                )
                synchronized = synchronize_drill(
                    db,
                    drill=drill,
                    settings=settings,
                    actor=None,
                    now=current,
                )
                after = (
                    synchronized.status,
                    aware_utc(synchronized.target_active_at),
                    aware_utc(synchronized.primary_restored_at),
                    synchronized.evidence_sha256,
                )
                if after != before:
                    summary.updated += 1
                    write_audit(
                        db,
                        organization_id=synchronized.organization_id,
                        action="continuity.drill_synchronized",
                        entity_type="continuity_drill",
                        entity_id=synchronized.id,
                        severity=SafetySeverity.WARNING,
                        details={
                            "previous_status": before[0].value,
                            "current_status": synchronized.status.value,
                        },
                    )
        except Exception:
            summary.errors += 1
            logger.exception("Continuity drill synchronization failed: %s", drill_id)
    return summary


def continuity_compliance(
    db: Session,
    *,
    organization_id: str,
    settings: Settings,
    now: datetime | None = None,
) -> ContinuityCompliance:
    """Оценить runtime compatibility and accepted continuity evidence."""

    current = _now(now)
    policy = get_or_create_policy(db, organization_id=organization_id, settings=settings)
    blockers: list[str] = []
    warnings: list[str] = []
    required = bool(settings.continuity_assurance_required or policy.enabled)
    if required and not policy.enabled:
        blockers.append("Continuity policy отключена при обязательном platform-gate")
    lease = get_or_create_execution_lease(
        db, organization_id=organization_id, settings=settings, now=current
    )
    sites = {
        site.site_key: site
        for site in db.scalars(
            select(ExecutionSite).where(ExecutionSite.organization_id == organization_id)
        ).all()
    }
    active = sites.get(lease.active_site_key)
    candidates = [
        site for key, site in sorted(sites.items()) if key != lease.active_site_key and site.enabled
    ]
    primary = sites.get(settings.execution_primary_site_key)
    if primary is not None and primary.site_key != lease.active_site_key and primary.enabled:
        candidates = [primary] + [site for site in candidates if site.id != primary.id]
    target: ExecutionSite | None = None
    diagnostics: list[str] = []
    for candidate in candidates:
        if not site_is_fresh(candidate, settings, now=current):
            diagnostics.append(f"Standby {candidate.site_key}: heartbeat отсутствует или устарел")
            continue
        if active is None:
            target = candidate
            break
        compatibility = compare_site_runtime(active, candidate)
        if compatibility.allowed:
            target = candidate
            warnings.extend(compatibility.warnings)
            break
        diagnostics.extend(
            f"Standby {candidate.site_key}: {item}" for item in compatibility.blockers
        )
    runtime_snapshot = {
        "active": site_runtime_payload(active),
        "standby": site_runtime_payload(target),
    }
    if required:
        if active is None or not site_is_fresh(active, settings, now=current):
            blockers.append("Active-площадка не имеет свежего heartbeat")
        if target is None:
            blockers.append("Standby-площадка не имеет свежего heartbeat")
            blockers.extend(diagnostics)
    latest = db.scalar(
        select(ContinuityDrill)
        .where(
            ContinuityDrill.organization_id == organization_id,
            ContinuityDrill.status == ContinuityDrillStatus.PASSED,
        )
        .order_by(ContinuityDrill.signed_off_at.desc(), ContinuityDrill.created_at.desc())
        .limit(1)
    )
    if required and latest is None:
        blockers.append("Нет принятого continuity-учения")
    if latest is not None:
        if latest.policy_sha256 != policy_sha256(policy):
            blockers.append("Последнее continuity-evidence создано по устаревшей политике")
        if policy.require_live_drill and latest.mode != ContinuityDrillMode.LIVE:
            blockers.append("Политика требует live-drill")
        expires = aware_utc(latest.expires_at)
        if expires is None or expires <= current:
            blockers.append("Continuity-evidence просрочено")
        if latest.rto_seconds is not None and latest.rto_seconds > policy.max_rto_seconds:
            blockers.append("Фактический RTO превышает политику")
        if not latest.evidence_payload or not latest.evidence_sha256:
            blockers.append("Continuity-evidence отсутствует")
        elif _canonical_sha256(latest.evidence_payload) != latest.evidence_sha256:
            blockers.append("Continuity-evidence повреждено")
        else:
            blockers.extend(_passed_semantic_blockers(db, latest))
        chain = verify_event_chain(db, drill_id=latest.id)
        if not chain["valid"]:
            blockers.append(f"История continuity-учения повреждена: {chain['error']}")
    if not required and not policy.enabled:
        warnings.append("Continuity policy отключена")
    return ContinuityCompliance(
        compliant=not blockers,
        required=required,
        blockers=blockers,
        warnings=warnings,
        policy=policy,
        latest_drill=latest,
        runtime_snapshot=runtime_snapshot,
    )
