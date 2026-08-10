from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import Settings
from app.enums import (
    CapacityAssessmentSource,
    ConnectionKind,
    ConnectionStatus,
    DeliveryAttemptStatus,
    JobStatus,
    ReadinessStatus,
    RunStatus,
)
from app.models import (
    CampaignRun,
    CapacityAssessment,
    CapacityPolicy,
    DeliveryAttempt,
    DeliveryJob,
    Organization,
    TelegramConnection,
    User,
    utcnow,
)
from app.security import aware_utc

ACTIVE_JOB_STATUSES = (
    JobStatus.HELD,
    JobStatus.PENDING,
    JobStatus.PROCESSING,
    JobStatus.RETRY,
    JobStatus.WAITING_REVIEW,
)
READY_JOB_STATUSES = (
    JobStatus.PENDING,
    JobStatus.PROCESSING,
    JobStatus.RETRY,
)
ACTIVE_RUN_STATUSES = (
    RunStatus.QUEUED,
    RunStatus.RUNNING,
    RunStatus.AWAITING_CHECKPOINT,
)


class CapacityError(RuntimeError):
    """Raised when a capacity policy or transition violates a hard invariant."""


@dataclass(frozen=True)
class CapacityGateDecision:
    """Decision returned before queue admission, batch release or network dispatch."""

    allowed: bool
    code: str
    message: str
    defer_until: datetime | None = None
    assessment: CapacityAssessment | None = None


@dataclass(frozen=True)
class CapacitySnapshot:
    """Current and projected queue measurements used by every capacity gate."""

    active_jobs: int
    ready_jobs: int
    held_jobs: int
    processing_jobs: int
    waiting_review_jobs: int
    active_runs: int
    network_starts_last_minute: int
    network_starts_last_hour: int
    estimated_drain_seconds: int
    queue_utilization_percent: int
    projected_jobs: int
    projected_ready_jobs: int
    projected_runs: int
    latest_due_at: str | None
    effective_dispatches_per_minute: float

    def to_dict(self) -> dict[str, Any]:
        """Вернуть a JSON-safe copy while preserving the immutable snapshot."""

        return asdict(self)


def _canonical_sha256(payload: object) -> str:
    """Вернуть a deterministic SHA-256 класса JSON-compatible capacity evidence."""

    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def capacity_policy_payload(policy: CapacityPolicy) -> dict[str, Any]:
    """Сериализовать only policy fields that influence capacity decisions."""

    return {
        "enabled": bool(policy.enabled),
        "max_active_jobs": int(policy.max_active_jobs),
        "max_ready_jobs": int(policy.max_ready_jobs),
        "max_processing_jobs": int(policy.max_processing_jobs),
        "max_active_runs": int(policy.max_active_runs),
        "max_jobs_per_run": int(policy.max_jobs_per_run),
        "max_network_starts_per_minute": int(policy.max_network_starts_per_minute),
        "max_network_starts_per_hour": int(policy.max_network_starts_per_hour),
        "max_estimated_drain_seconds": int(policy.max_estimated_drain_seconds),
        "warning_utilization_percent": int(policy.warning_utilization_percent),
        "admission_block_utilization_percent": int(policy.admission_block_utilization_percent),
        "assessment_ttl_minutes": int(policy.assessment_ttl_minutes),
        "gate_admission": bool(policy.gate_admission),
        "gate_dispatch": bool(policy.gate_dispatch),
    }


def capacity_policy_sha256(policy: CapacityPolicy) -> str:
    """Вычислить fingerprint для the effective capacity policy for stale-assessment detection."""

    return _canonical_sha256(capacity_policy_payload(policy))


def _validate_policy(policy: CapacityPolicy, settings: Settings) -> None:
    """Проверить cross-field rules not expressible by individual API field bounds."""

    if not (policy.max_processing_jobs <= policy.max_ready_jobs <= policy.max_active_jobs):
        raise CapacityError("Лимиты должны соблюдать processing jobs ≤ ready jobs ≤ active jobs")
    if policy.max_jobs_per_run > policy.max_active_jobs:
        raise CapacityError("Лимит заданий одного запуска превышает общий лимит очереди")
    if policy.max_network_starts_per_hour < policy.max_network_starts_per_minute:
        raise CapacityError("Часовой сетевой лимит не может быть меньше минутного")
    if not (policy.warning_utilization_percent < policy.admission_block_utilization_percent <= 100):
        raise CapacityError("Порог предупреждения должен быть меньше порога блокировки admission")
    if settings.is_production and settings.capacity_assurance_required:
        if not policy.enabled:
            raise CapacityError("В production capacity policy нельзя отключить")
        if not policy.gate_admission or not policy.gate_dispatch:
            raise CapacityError("В production capacity policy должна защищать admission и dispatch")


def get_or_create_capacity_policy(
    db: Session,
    *,
    organization_id: str,
    settings: Settings,
    user: User | None = None,
) -> CapacityPolicy:
    """Вернуть the tenant policy, serializing first creation through the organization row."""

    policy = db.scalar(
        select(CapacityPolicy).where(CapacityPolicy.organization_id == organization_id)
    )
    if policy is not None:
        return policy

    organization = db.scalar(
        select(Organization).where(Organization.id == organization_id).with_for_update()
    )
    if organization is None:
        raise CapacityError("Организация не найдена")
    policy = db.scalar(
        select(CapacityPolicy).where(CapacityPolicy.organization_id == organization_id)
    )
    if policy is not None:
        return policy

    policy = CapacityPolicy(
        organization_id=organization_id,
        enabled=True,
        max_active_jobs=settings.capacity_default_max_active_jobs,
        max_ready_jobs=settings.capacity_default_max_ready_jobs,
        max_processing_jobs=settings.capacity_default_max_processing_jobs,
        max_active_runs=settings.capacity_default_max_active_runs,
        max_jobs_per_run=settings.capacity_default_max_jobs_per_run,
        max_network_starts_per_minute=(settings.capacity_default_max_network_starts_per_minute),
        max_network_starts_per_hour=(settings.capacity_default_max_network_starts_per_hour),
        max_estimated_drain_seconds=(settings.capacity_default_max_estimated_drain_seconds),
        warning_utilization_percent=(settings.capacity_default_warning_utilization_percent),
        admission_block_utilization_percent=(
            settings.capacity_default_admission_block_utilization_percent
        ),
        assessment_ttl_minutes=settings.capacity_assessment_ttl_minutes,
        gate_admission=settings.capacity_assurance_required,
        gate_dispatch=settings.capacity_assurance_required,
        created_by_id=user.id if user else None,
        updated_by_id=user.id if user else None,
    )
    _validate_policy(policy, settings)
    db.add(policy)
    db.flush()
    return policy


def lock_capacity_policy(
    db: Session,
    *,
    organization_id: str,
    settings: Settings,
) -> CapacityPolicy:
    """Заблокировать the tenant policy so concurrent admissions cannot overbook queue capacity."""

    get_or_create_capacity_policy(
        db,
        organization_id=organization_id,
        settings=settings,
    )
    policy = db.scalar(
        select(CapacityPolicy)
        .where(CapacityPolicy.organization_id == organization_id)
        .with_for_update()
    )
    if policy is None:
        raise CapacityError("Capacity policy не найдена после инициализации")
    return policy


def update_capacity_policy(
    db: Session,
    *,
    policy: CapacityPolicy,
    values: dict[str, Any],
    user: User,
    settings: Settings,
) -> CapacityPolicy:
    """Применить operator changes and reject an internally inconsistent policy atomically."""

    for field, value in values.items():
        if value is not None:
            setattr(policy, field, value)
    _validate_policy(policy, settings)
    policy.updated_by_id = user.id
    db.flush()
    return policy


def latest_capacity_assessment(
    db: Session,
    *,
    organization_id: str,
) -> CapacityAssessment | None:
    """Вернуть the newest immutable capacity assessment for one organization."""

    return db.scalar(
        select(CapacityAssessment)
        .where(CapacityAssessment.organization_id == organization_id)
        .order_by(CapacityAssessment.created_at.desc(), CapacityAssessment.id.desc())
        .limit(1)
    )


def capacity_assessment_is_current(
    assessment: CapacityAssessment | None,
    policy: CapacityPolicy | None,
    *,
    now: datetime | None = None,
) -> bool:
    """Проверить assessment TTL and policy fingerprint without mutating stored evidence."""

    if assessment is None or policy is None:
        return False
    current_time = aware_utc(now) or utcnow()
    expires_at = aware_utc(assessment.expires_at)
    return bool(
        expires_at
        and expires_at > current_time
        and assessment.policy_sha256 == capacity_policy_sha256(policy)
    )


def _job_status_counts(db: Session, organization_id: str) -> dict[JobStatus, int]:
    """Подсчитать queue states in one grouped query to keep every decision internally consistent."""

    return {
        status: int(count)
        for status, count in db.execute(
            select(DeliveryJob.status, func.count(DeliveryJob.id))
            .where(DeliveryJob.organization_id == organization_id)
            .group_by(DeliveryJob.status)
        ).all()
    }


def _active_run_count(db: Session, organization_id: str) -> int:
    """Подсчитать runs that can still create or release delivery work."""

    return int(
        db.scalar(
            select(func.count(CampaignRun.id)).where(
                CampaignRun.organization_id == organization_id,
                CampaignRun.status.in_(ACTIVE_RUN_STATUSES),
            )
        )
        or 0
    )


def _network_start_count(
    db: Session,
    *,
    organization_id: str,
    since: datetime,
) -> int:
    """Подсчитать actual Telegram network starts rather than retries that never reached the
    gateway.
    """

    return int(
        db.scalar(
            select(func.count(DeliveryAttempt.id)).where(
                DeliveryAttempt.organization_id == organization_id,
                DeliveryAttempt.network_started_at.is_not(None),
                DeliveryAttempt.network_started_at >= since,
                DeliveryAttempt.status.in_(
                    [
                        DeliveryAttemptStatus.NETWORK_STARTED,
                        DeliveryAttemptStatus.SENT,
                        DeliveryAttemptStatus.FAILED,
                        DeliveryAttemptStatus.UNCERTAIN,
                        DeliveryAttemptStatus.RECONCILED_NOT_SENT,
                        DeliveryAttemptStatus.RECONCILED_SKIPPED,
                    ]
                ),
            )
        )
        or 0
    )


def _rate_window_delay_seconds(
    db: Session,
    *,
    organization_id: str,
    policy: CapacityPolicy,
    now: datetime,
) -> int:
    """Вернуть the conservative wait until at least one tenant dispatch slot reopens. Admission may
    still accept bounded future work while an immediate minute or hour window is full. Adding
    this delay to the drain estimate prevents that admission from pretending the queue can start
    draining immediately.
    """

    waits: list[int] = []
    for window, limit in (
        (timedelta(minutes=1), policy.max_network_starts_per_minute),
        (timedelta(hours=1), policy.max_network_starts_per_hour),
    ):
        since = now - window
        count = _network_start_count(
            db,
            organization_id=organization_id,
            since=since,
        )
        if count < limit:
            continue
        oldest = _oldest_network_start(
            db,
            organization_id=organization_id,
            since=since,
        )
        if oldest is None:
            waits.append(math.ceil(window.total_seconds()))
            continue
        waits.append(max(0, math.ceil((oldest + window - now).total_seconds())))
    return max(waits, default=0)


def _connection_interval_seconds(connection: TelegramConnection, settings: Settings) -> int:
    """Вернуть the hard effective interval for conservative drain-rate estimation."""

    hard_minimum = (
        settings.user_hard_min_interval_seconds
        if connection.kind == ConnectionKind.USER
        else settings.bot_hard_min_interval_seconds
    )
    return max(1, int(connection.min_interval_seconds), int(hard_minimum))


def _estimated_drain_seconds(
    db: Session,
    *,
    organization_id: str,
    policy: CapacityPolicy,
    settings: Settings,
    now: datetime,
    projected_active_jobs: int,
    incoming_connection_id: str | None,
    incoming_jobs: int,
    incoming_due_span_seconds: int,
) -> tuple[int, float, datetime | None]:
    """Оценить queue drain time from connection intervals, aggregate caps and due-time horizon."""

    counts_by_connection = {
        connection_id: int(count)
        for connection_id, count in db.execute(
            select(DeliveryJob.connection_id, func.count(DeliveryJob.id))
            .where(
                DeliveryJob.organization_id == organization_id,
                DeliveryJob.status.in_(ACTIVE_JOB_STATUSES),
            )
            .group_by(DeliveryJob.connection_id)
        ).all()
    }
    if incoming_connection_id and incoming_jobs:
        counts_by_connection[incoming_connection_id] = (
            counts_by_connection.get(incoming_connection_id, 0) + incoming_jobs
        )

    connection_ids = list(counts_by_connection)
    connections = (
        list(
            db.scalars(
                select(TelegramConnection).where(
                    TelegramConnection.organization_id == organization_id,
                    TelegramConnection.id.in_(connection_ids),
                    TelegramConnection.status == ConnectionStatus.ACTIVE,
                )
            ).all()
        )
        if connection_ids
        else []
    )
    aggregate_rate_per_second = sum(
        1.0 / _connection_interval_seconds(connection, settings) for connection in connections
    )
    aggregate_rate_per_second = min(
        aggregate_rate_per_second,
        policy.max_network_starts_per_minute / 60.0,
        policy.max_network_starts_per_hour / 3600.0,
    )
    if projected_active_jobs <= 0:
        service_seconds = 0
    elif aggregate_rate_per_second <= 0:
        service_seconds = policy.max_estimated_drain_seconds + 1
    else:
        service_seconds = math.ceil(projected_active_jobs / aggregate_rate_per_second)

    latest_due_at = aware_utc(
        db.scalar(
            select(func.max(DeliveryJob.due_at)).where(
                DeliveryJob.organization_id == organization_id,
                DeliveryJob.status.in_(ACTIVE_JOB_STATUSES),
            )
        )
    )
    projected_latest_due = now + timedelta(seconds=max(0, incoming_due_span_seconds))
    if incoming_jobs and (latest_due_at is None or projected_latest_due > latest_due_at):
        latest_due_at = projected_latest_due
    due_horizon_seconds = (
        max(0, math.ceil((latest_due_at - now).total_seconds())) if latest_due_at else 0
    )
    rate_window_delay = (
        _rate_window_delay_seconds(
            db,
            organization_id=organization_id,
            policy=policy,
            now=now,
        )
        if projected_active_jobs > 0
        else 0
    )
    estimate = max(rate_window_delay + service_seconds, due_horizon_seconds)
    return estimate, round(aggregate_rate_per_second * 60.0, 3), latest_due_at


def collect_capacity_snapshot(
    db: Session,
    *,
    organization_id: str,
    policy: CapacityPolicy,
    settings: Settings,
    now: datetime | None = None,
    incoming_jobs: int = 0,
    incoming_ready_jobs: int = 0,
    incoming_runs: int = 0,
    incoming_connection_id: str | None = None,
    incoming_due_span_seconds: int = 0,
) -> CapacitySnapshot:
    """Собрать current queue pressure and an explicit projected admission delta."""

    current_time = aware_utc(now) or utcnow()
    counts = _job_status_counts(db, organization_id)
    active_jobs = sum(int(counts.get(status, 0)) for status in ACTIVE_JOB_STATUSES)
    ready_jobs = sum(int(counts.get(status, 0)) for status in READY_JOB_STATUSES)
    held_jobs = int(counts.get(JobStatus.HELD, 0))
    processing_jobs = int(counts.get(JobStatus.PROCESSING, 0))
    waiting_review_jobs = int(counts.get(JobStatus.WAITING_REVIEW, 0))
    active_runs = _active_run_count(db, organization_id)
    projected_jobs = active_jobs + max(0, incoming_jobs)
    projected_ready_jobs = ready_jobs + max(0, incoming_ready_jobs)
    projected_runs = active_runs + max(0, incoming_runs)
    estimated_drain, effective_per_minute, latest_due_at = _estimated_drain_seconds(
        db,
        organization_id=organization_id,
        policy=policy,
        settings=settings,
        now=current_time,
        projected_active_jobs=projected_jobs,
        incoming_connection_id=incoming_connection_id,
        incoming_jobs=max(0, incoming_jobs),
        incoming_due_span_seconds=max(0, incoming_due_span_seconds),
    )
    # Admission pressure is governed by the most saturated structural queue
    # dimension. Using only the broad active-job limit would hide a nearly full
    # ready queue or run ledger behind a much larger total-queue allowance.
    utilization = max(
        math.ceil((projected_jobs / max(1, policy.max_active_jobs)) * 100),
        math.ceil((projected_ready_jobs / max(1, policy.max_ready_jobs)) * 100),
        math.ceil((projected_runs / max(1, policy.max_active_runs)) * 100),
    )
    return CapacitySnapshot(
        active_jobs=active_jobs,
        ready_jobs=ready_jobs,
        held_jobs=held_jobs,
        processing_jobs=processing_jobs,
        waiting_review_jobs=waiting_review_jobs,
        active_runs=active_runs,
        network_starts_last_minute=_network_start_count(
            db,
            organization_id=organization_id,
            since=current_time - timedelta(minutes=1),
        ),
        network_starts_last_hour=_network_start_count(
            db,
            organization_id=organization_id,
            since=current_time - timedelta(hours=1),
        ),
        estimated_drain_seconds=estimated_drain,
        queue_utilization_percent=utilization,
        projected_jobs=projected_jobs,
        projected_ready_jobs=projected_ready_jobs,
        projected_runs=projected_runs,
        latest_due_at=latest_due_at.isoformat() if latest_due_at else None,
        effective_dispatches_per_minute=effective_per_minute,
    )


def _check(
    checks: list[dict[str, Any]],
    *,
    code: str,
    status: str,
    message: str,
    actual: int | float,
    limit: int | float,
) -> None:
    """Добавить one normalized check used by API, audit and tests."""

    checks.append(
        {
            "code": code,
            "status": status,
            "message": message,
            "actual": actual,
            "limit": limit,
        }
    )


def evaluate_snapshot(
    snapshot: CapacitySnapshot,
    policy: CapacityPolicy,
    *,
    incoming_jobs: int,
) -> tuple[ReadinessStatus, list[dict[str, Any]], list[str], list[str]]:
    """Классифицировать a snapshot using deterministic hard blockers and early warnings."""

    checks: list[dict[str, Any]] = []
    blockers: list[str] = []
    warnings: list[str] = []

    def add_result(
        code: str,
        actual: int,
        limit: int,
        blocked_message: str,
        warning_message: str | None = None,
        warning_at: int | None = None,
        block_at_limit: bool = False,
    ) -> None:
        """Записать one bounded metric and mirror its operator-facing consequence."""

        blocked = actual >= limit if block_at_limit else actual > limit
        if blocked:
            _check(
                checks,
                code=code,
                status="blocked",
                message=blocked_message,
                actual=actual,
                limit=limit,
            )
            blockers.append(blocked_message)
        elif warning_at is not None and actual >= warning_at:
            message = warning_message or blocked_message
            _check(
                checks,
                code=code,
                status="warning",
                message=message,
                actual=actual,
                limit=limit,
            )
            warnings.append(message)
        else:
            _check(
                checks,
                code=code,
                status="passed",
                message="Показатель находится в допустимом диапазоне",
                actual=actual,
                limit=limit,
            )

    add_result(
        "active_jobs",
        snapshot.projected_jobs,
        policy.max_active_jobs,
        "Общее количество активных заданий превышает лимит очереди",
        "Очередь приближается к общему лимиту активных заданий",
        max(1, math.ceil(policy.max_active_jobs * policy.warning_utilization_percent / 100)),
    )
    add_result(
        "ready_jobs",
        snapshot.projected_ready_jobs,
        policy.max_ready_jobs,
        "Количество готовых к обработке заданий превышает лимит backpressure",
        "Готовая очередь приближается к лимиту backpressure",
        max(1, math.ceil(policy.max_ready_jobs * policy.warning_utilization_percent / 100)),
    )
    add_result(
        "processing_jobs",
        snapshot.processing_jobs,
        policy.max_processing_jobs,
        "Одновременно обрабатывается больше заданий, чем допускает policy",
    )
    add_result(
        "active_runs",
        snapshot.projected_runs,
        policy.max_active_runs,
        "Количество незавершённых запусков превышает лимит",
        "Количество незавершённых запусков приближается к лимиту",
        max(1, math.ceil(policy.max_active_runs * policy.warning_utilization_percent / 100)),
    )
    add_result(
        "network_starts_minute",
        snapshot.network_starts_last_minute,
        policy.max_network_starts_per_minute,
        "Минутный лимит фактически начатых Telegram-запросов исчерпан",
        block_at_limit=True,
    )
    add_result(
        "network_starts_hour",
        snapshot.network_starts_last_hour,
        policy.max_network_starts_per_hour,
        "Часовой лимит фактически начатых Telegram-запросов исчерпан",
        block_at_limit=True,
    )
    add_result(
        "estimated_drain",
        snapshot.estimated_drain_seconds,
        policy.max_estimated_drain_seconds,
        "Прогноз времени полного опустошения очереди превышает лимит",
        "Прогноз времени обработки очереди приближается к лимиту",
        math.floor(policy.max_estimated_drain_seconds * 0.8),
    )

    if incoming_jobs > policy.max_jobs_per_run:
        message = "Количество заданий одного запуска превышает capacity policy"
        _check(
            checks,
            code="jobs_per_run",
            status="blocked",
            message=message,
            actual=incoming_jobs,
            limit=policy.max_jobs_per_run,
        )
        blockers.append(message)
    else:
        _check(
            checks,
            code="jobs_per_run",
            status="passed",
            message="Размер запуска находится в допустимом диапазоне",
            actual=incoming_jobs,
            limit=policy.max_jobs_per_run,
        )

    if snapshot.queue_utilization_percent >= policy.admission_block_utilization_percent:
        message = (
            "Projected utilization достиг порога admission backpressure: "
            f"{snapshot.queue_utilization_percent}%"
        )
        _check(
            checks,
            code="queue_utilization",
            status="blocked",
            message=message,
            actual=snapshot.queue_utilization_percent,
            limit=policy.admission_block_utilization_percent,
        )
        blockers.append(message)
    elif snapshot.queue_utilization_percent >= policy.warning_utilization_percent:
        message = f"Очередь заполнена на {snapshot.queue_utilization_percent}%"
        _check(
            checks,
            code="queue_utilization",
            status="warning",
            message=message,
            actual=snapshot.queue_utilization_percent,
            limit=policy.admission_block_utilization_percent,
        )
        warnings.append(message)
    else:
        _check(
            checks,
            code="queue_utilization",
            status="passed",
            message="Заполнение очереди ниже предупреждающего порога",
            actual=snapshot.queue_utilization_percent,
            limit=policy.admission_block_utilization_percent,
        )

    if snapshot.waiting_review_jobs:
        warnings.append(f"Ручной сверки ожидают заданий: {snapshot.waiting_review_jobs}")

    blockers = list(dict.fromkeys(blockers))
    warnings = list(dict.fromkeys(warnings))
    status = (
        ReadinessStatus.BLOCKED
        if blockers
        else ReadinessStatus.WARNING
        if warnings
        else ReadinessStatus.PASSED
    )
    return status, checks, blockers, warnings


def create_capacity_assessment(
    db: Session,
    *,
    organization_id: str,
    settings: Settings,
    source: CapacityAssessmentSource,
    user: User | None = None,
    now: datetime | None = None,
    incoming_jobs: int = 0,
    incoming_ready_jobs: int = 0,
    incoming_runs: int = 0,
    incoming_connection_id: str | None = None,
    incoming_due_span_seconds: int = 0,
    policy: CapacityPolicy | None = None,
) -> CapacityAssessment:
    """Сохранить an immutable current/projected capacity assessment."""

    current_time = aware_utc(now) or utcnow()
    policy = policy or get_or_create_capacity_policy(
        db,
        organization_id=organization_id,
        settings=settings,
        user=user,
    )
    snapshot = collect_capacity_snapshot(
        db,
        organization_id=organization_id,
        policy=policy,
        settings=settings,
        now=current_time,
        incoming_jobs=incoming_jobs,
        incoming_ready_jobs=incoming_ready_jobs,
        incoming_runs=incoming_runs,
        incoming_connection_id=incoming_connection_id,
        incoming_due_span_seconds=incoming_due_span_seconds,
    )
    status, checks, blockers, warnings = evaluate_snapshot(
        snapshot,
        policy,
        incoming_jobs=max(0, incoming_jobs),
    )
    policy_snapshot = capacity_policy_payload(policy)
    policy_hash = _canonical_sha256(policy_snapshot)
    metrics = snapshot.to_dict()
    fingerprint = _canonical_sha256(
        {
            "organization_id": organization_id,
            "source": source.value,
            "policy_sha256": policy_hash,
            "metrics": metrics,
            "checks": checks,
            "status": status.value,
        }
    )
    assessment = CapacityAssessment(
        organization_id=organization_id,
        source=source,
        status=status,
        policy_snapshot=policy_snapshot,
        policy_sha256=policy_hash,
        metrics=metrics,
        checks=checks,
        blockers=blockers,
        warnings=warnings,
        active_jobs=snapshot.active_jobs,
        ready_jobs=snapshot.ready_jobs,
        held_jobs=snapshot.held_jobs,
        processing_jobs=snapshot.processing_jobs,
        waiting_review_jobs=snapshot.waiting_review_jobs,
        active_runs=snapshot.active_runs,
        network_starts_last_minute=snapshot.network_starts_last_minute,
        network_starts_last_hour=snapshot.network_starts_last_hour,
        estimated_drain_seconds=snapshot.estimated_drain_seconds,
        queue_utilization_percent=snapshot.queue_utilization_percent,
        projected_jobs=snapshot.projected_jobs,
        projected_ready_jobs=snapshot.projected_ready_jobs,
        projected_runs=snapshot.projected_runs,
        fingerprint=fingerprint,
        created_by_id=user.id if user else None,
        expires_at=current_time + timedelta(minutes=policy.assessment_ttl_minutes),
        created_at=current_time,
    )
    db.add(assessment)
    db.flush()
    return assessment


def capacity_admission_decision(
    db: Session,
    *,
    organization_id: str,
    settings: Settings,
    incoming_jobs: int,
    incoming_ready_jobs: int,
    incoming_runs: int,
    incoming_connection_id: str | None,
    incoming_due_span_seconds: int,
    source: CapacityAssessmentSource = CapacityAssessmentSource.ADMISSION,
    user: User | None = None,
    now: datetime | None = None,
    persist_assessment: bool = True,
) -> CapacityGateDecision:
    """Сериализовать queue admission and reject a projected overload before jobs are created."""

    policy = lock_capacity_policy(
        db,
        organization_id=organization_id,
        settings=settings,
    )
    assessment = (
        create_capacity_assessment(
            db,
            organization_id=organization_id,
            settings=settings,
            source=source,
            user=user,
            now=now,
            incoming_jobs=max(0, incoming_jobs),
            incoming_ready_jobs=max(0, incoming_ready_jobs),
            incoming_runs=max(0, incoming_runs),
            incoming_connection_id=incoming_connection_id,
            incoming_due_span_seconds=max(0, incoming_due_span_seconds),
            policy=policy,
        )
        if persist_assessment
        else None
    )
    if assessment is None:
        snapshot = collect_capacity_snapshot(
            db,
            organization_id=organization_id,
            policy=policy,
            settings=settings,
            now=now,
            incoming_jobs=max(0, incoming_jobs),
            incoming_ready_jobs=max(0, incoming_ready_jobs),
            incoming_runs=max(0, incoming_runs),
            incoming_connection_id=incoming_connection_id,
            incoming_due_span_seconds=max(0, incoming_due_span_seconds),
        )
        status, checks, _blockers, _warnings = evaluate_snapshot(
            snapshot,
            policy,
            incoming_jobs=max(0, incoming_jobs),
        )
    else:
        status = assessment.status
        checks = assessment.checks

    # Minute/hour exhaustion is a dispatch condition, not by itself a reason
    # to reject bounded future queue work. The drain estimate above includes
    # the required window wait, while Safety Engine still blocks the immediate
    # Telegram call. All structural queue, run, concurrency and drain blockers
    # remain hard admission failures.
    transient_dispatch_codes = {"network_starts_minute", "network_starts_hour"}
    admission_blockers = [
        str(check.get("message") or "Capacity admission заблокирован")
        for check in checks
        if check.get("status") == "blocked" and check.get("code") not in transient_dispatch_codes
    ]

    enforced = bool(
        settings.capacity_assurance_required or (policy.enabled and policy.gate_admission)
    )
    if admission_blockers and enforced:
        message = "; ".join(admission_blockers[:3]) or "Capacity admission заблокирован"
        return CapacityGateDecision(
            False,
            "CAPACITY_ADMISSION_BLOCKED",
            message,
            assessment=assessment,
        )
    if admission_blockers:
        return CapacityGateDecision(
            True,
            "CAPACITY_MONITOR_ONLY",
            "Перегрузка обнаружена, но admission policy работает в режиме наблюдения",
            assessment=assessment,
        )
    if status == ReadinessStatus.BLOCKED:
        return CapacityGateDecision(
            True,
            "CAPACITY_ADMISSION_ALLOWED_DISPATCH_DEFERRED",
            "Очередь допустима, но немедленный dispatch ожидает освобождения сетевого окна",
            assessment=assessment,
        )
    return CapacityGateDecision(
        True,
        "CAPACITY_ADMISSION_ALLOWED",
        "Projected queue load находится в допустимом диапазоне",
        assessment=assessment,
    )


def capacity_ready_release_decision(
    db: Session,
    *,
    organization_id: str,
    settings: Settings,
    incoming_ready_jobs: int,
    now: datetime | None = None,
) -> CapacityGateDecision:
    """Защитить staged batch release and manual retry from ready-queue overbooking."""

    policy = lock_capacity_policy(
        db,
        organization_id=organization_id,
        settings=settings,
    )
    snapshot = collect_capacity_snapshot(
        db,
        organization_id=organization_id,
        policy=policy,
        settings=settings,
        now=now,
        incoming_ready_jobs=max(0, incoming_ready_jobs),
    )
    enforced = bool(
        settings.capacity_assurance_required or (policy.enabled and policy.gate_admission)
    )
    if snapshot.projected_ready_jobs > policy.max_ready_jobs and enforced:
        return CapacityGateDecision(
            False,
            "CAPACITY_READY_QUEUE_BLOCKED",
            (
                "Раскрытие пакета превысит лимит готовой очереди: "
                f"{snapshot.projected_ready_jobs}/{policy.max_ready_jobs}"
            ),
        )
    return CapacityGateDecision(
        True,
        "CAPACITY_READY_QUEUE_ALLOWED",
        "Готовая очередь допускает новые задания",
    )


def _oldest_network_start(
    db: Session,
    *,
    organization_id: str,
    since: datetime,
) -> datetime | None:
    """Вернуть the oldest network start still contributing to a sliding window."""

    return aware_utc(
        db.scalar(
            select(func.min(DeliveryAttempt.network_started_at)).where(
                DeliveryAttempt.organization_id == organization_id,
                DeliveryAttempt.network_started_at.is_not(None),
                DeliveryAttempt.network_started_at >= since,
            )
        )
    )


def _prepared_reservation_count(
    db: Session,
    *,
    organization_id: str,
    since: datetime,
) -> int:
    """Подсчитать durable PREPARED attempts that reserve a dispatch slot before networking."""

    return int(
        db.scalar(
            select(func.count(DeliveryAttempt.id)).where(
                DeliveryAttempt.organization_id == organization_id,
                DeliveryAttempt.status == DeliveryAttemptStatus.PREPARED,
                DeliveryAttempt.prepared_at >= since,
            )
        )
        or 0
    )


def _oldest_prepared_reservation(
    db: Session,
    *,
    organization_id: str,
    since: datetime,
) -> datetime | None:
    """Вернуть the oldest durable PREPARED attempt still occupying a dispatch window."""

    return aware_utc(
        db.scalar(
            select(func.min(DeliveryAttempt.prepared_at)).where(
                DeliveryAttempt.organization_id == organization_id,
                DeliveryAttempt.status == DeliveryAttemptStatus.PREPARED,
                DeliveryAttempt.prepared_at >= since,
            )
        )
    )


def capacity_dispatch_decision(
    db: Session,
    *,
    organization_id: str,
    settings: Settings,
    now: datetime | None = None,
    reservation_attempt_id: str | None = None,
) -> CapacityGateDecision:
    """Применить dispatch limits, optionally counting a durable PREPARED reservation. A precheck
    has no reservation and blocks when the current queue already occupies the full budget. The
    final check runs after the current job and PREPARED attempt are committed; it therefore
    blocks only when the projected total is above the limit. Locking the policy row serializes
    concurrent reservations without holding the lock across the Telegram network call.
    """

    current_time = aware_utc(now) or utcnow()
    policy = lock_capacity_policy(
        db,
        organization_id=organization_id,
        settings=settings,
    )
    enforced = bool(
        settings.capacity_assurance_required or (policy.enabled and policy.gate_dispatch)
    )
    if not enforced:
        return CapacityGateDecision(
            True,
            "CAPACITY_DISPATCH_MONITOR_ONLY",
            "Dispatch policy работает в режиме наблюдения",
        )

    reservation_mode = reservation_attempt_id is not None
    if reservation_mode:
        reservation = db.scalar(
            select(DeliveryAttempt).where(
                DeliveryAttempt.id == reservation_attempt_id,
                DeliveryAttempt.organization_id == organization_id,
                DeliveryAttempt.status == DeliveryAttemptStatus.PREPARED,
            )
        )
        if reservation is None:
            return CapacityGateDecision(
                False,
                "CAPACITY_RESERVATION_MISSING",
                "Durable dispatch reservation отсутствует или уже завершена",
                defer_until=current_time + timedelta(seconds=settings.capacity_retry_delay_seconds),
            )

    counts = _job_status_counts(db, organization_id)
    processing_jobs = int(counts.get(JobStatus.PROCESSING, 0))
    processing_blocked = (
        processing_jobs > policy.max_processing_jobs
        if reservation_mode
        else processing_jobs >= policy.max_processing_jobs
    )
    if processing_blocked:
        return CapacityGateDecision(
            False,
            "CAPACITY_PROCESSING_LIMIT",
            (
                "Достигнут лимит одновременно обрабатываемых заданий: "
                f"{processing_jobs}/{policy.max_processing_jobs}"
            ),
            defer_until=current_time + timedelta(seconds=settings.capacity_retry_delay_seconds),
        )

    minute_since = current_time - timedelta(minutes=1)
    minute_count = _network_start_count(
        db,
        organization_id=organization_id,
        since=minute_since,
    )
    if reservation_mode:
        minute_count += _prepared_reservation_count(
            db,
            organization_id=organization_id,
            since=minute_since,
        )
    minute_blocked = (
        minute_count > policy.max_network_starts_per_minute
        if reservation_mode
        else minute_count >= policy.max_network_starts_per_minute
    )
    if minute_blocked:
        oldest = _oldest_network_start(
            db,
            organization_id=organization_id,
            since=minute_since,
        )
        oldest_reservation = (
            _oldest_prepared_reservation(
                db,
                organization_id=organization_id,
                since=minute_since,
            )
            if reservation_mode
            else None
        )
        if oldest is None or (oldest_reservation is not None and oldest_reservation < oldest):
            oldest = oldest_reservation
        return CapacityGateDecision(
            False,
            "CAPACITY_MINUTE_RATE_LIMIT",
            "Достигнут tenant-level минутный лимит Telegram-запросов",
            defer_until=(oldest + timedelta(minutes=1))
            if oldest
            else (current_time + timedelta(seconds=settings.capacity_retry_delay_seconds)),
        )

    hour_since = current_time - timedelta(hours=1)
    hour_count = _network_start_count(
        db,
        organization_id=organization_id,
        since=hour_since,
    )
    if reservation_mode:
        hour_count += _prepared_reservation_count(
            db,
            organization_id=organization_id,
            since=hour_since,
        )
    hour_blocked = (
        hour_count > policy.max_network_starts_per_hour
        if reservation_mode
        else hour_count >= policy.max_network_starts_per_hour
    )
    if hour_blocked:
        oldest = _oldest_network_start(
            db,
            organization_id=organization_id,
            since=hour_since,
        )
        oldest_reservation = (
            _oldest_prepared_reservation(
                db,
                organization_id=organization_id,
                since=hour_since,
            )
            if reservation_mode
            else None
        )
        if oldest is None or (oldest_reservation is not None and oldest_reservation < oldest):
            oldest = oldest_reservation
        return CapacityGateDecision(
            False,
            "CAPACITY_HOUR_RATE_LIMIT",
            "Достигнут tenant-level часовой лимит Telegram-запросов",
            defer_until=(oldest + timedelta(hours=1))
            if oldest
            else (current_time + timedelta(seconds=settings.capacity_retry_delay_seconds)),
        )

    return CapacityGateDecision(
        True,
        "CAPACITY_DISPATCH_ALLOWED",
        "Tenant-level dispatch budget доступен",
    )


def evaluate_due_capacity_policies(
    db: Session,
    *,
    settings: Settings,
    now: datetime | None = None,
) -> int:
    """Создать periodic immutable assessments without letting one tenant abort the worker cycle."""

    current_time = aware_utc(now) or utcnow()
    created = 0
    organization_ids = list(db.scalars(select(Organization.id).order_by(Organization.id)).all())
    for organization_id in organization_ids:
        try:
            with db.begin_nested():
                policy = get_or_create_capacity_policy(
                    db,
                    organization_id=organization_id,
                    settings=settings,
                )
                if not policy.enabled and not settings.capacity_assurance_required:
                    continue
                latest = latest_capacity_assessment(
                    db,
                    organization_id=organization_id,
                )
                due_before = current_time - timedelta(
                    minutes=settings.capacity_auto_evaluate_minutes
                )
                latest_created_at = aware_utc(latest.created_at) if latest is not None else None
                if (
                    latest is not None
                    and latest_created_at is not None
                    and latest_created_at > due_before
                    and capacity_assessment_is_current(latest, policy, now=current_time)
                ):
                    continue
                create_capacity_assessment(
                    db,
                    organization_id=organization_id,
                    settings=settings,
                    source=CapacityAssessmentSource.WORKER,
                    now=current_time,
                    policy=policy,
                )
                created += 1
        except Exception:
            # The caller logs the aggregate worker failure. A savepoint keeps
            # already evaluated organizations committed in the outer transaction.
            continue
    return created
