from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.audit import write_audit
from app.config import Settings
from app.enums import (
    IncidentEventType,
    IncidentSource,
    IncidentStatus,
    JobStatus,
    OrganizationStatus,
    ReadinessStatus,
    SafetySeverity,
    SLOAssessmentSource,
)
from app.models import (
    DeliveryJob,
    Incident,
    IncidentEvent,
    Organization,
    SLOAssessment,
    SLOPolicy,
    User,
    WorkerHeartbeat,
    utcnow,
)
from app.security import aware_utc
from app.services.notifications import create_notification

logger = logging.getLogger(__name__)


class OperationsError(RuntimeError):
    pass


@dataclass(frozen=True)
class SLOGateDecision:
    allowed: bool
    message: str
    assessment: SLOAssessment | None = None


def _canonical_json(value: Any) -> bytes:
    """Реализовать внутренний этап canonical json step. Вспомогательная функция сохраняет
    детерминированность и тестируемость процесса.
    """
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")


def _sha256_json(value: Any) -> str:
    """Вычислить sha256 json. Канонический ввод обеспечивает детерминированное сравнение
    целостности между процессами.
    """
    return hashlib.sha256(_canonical_json(value)).hexdigest()


def policy_snapshot(policy: SLOPolicy) -> dict[str, Any]:
    """Выполнить операцию policy snapshot. Аргументы интерпретируются в контексте модуля, результат
    возвращается вызывающему коду.
    """
    return {
        "enabled": bool(policy.enabled),
        "evaluation_window_hours": int(policy.evaluation_window_hours),
        "delivery_success_target_bps": int(policy.delivery_success_target_bps),
        "minimum_delivery_sample_size": int(policy.minimum_delivery_sample_size),
        "max_queue_age_seconds": int(policy.max_queue_age_seconds),
        "max_worker_heartbeat_age_seconds": int(policy.max_worker_heartbeat_age_seconds),
        "max_unresolved_delivery_reviews": int(policy.max_unresolved_delivery_reviews),
        "max_open_critical_incidents": int(policy.max_open_critical_incidents),
        "error_budget_warning_percent": int(policy.error_budget_warning_percent),
        "error_budget_critical_percent": int(policy.error_budget_critical_percent),
        "assessment_ttl_minutes": int(policy.assessment_ttl_minutes),
        "gate_publishing": bool(policy.gate_publishing),
        "gate_changes": bool(policy.gate_changes),
        "auto_create_incidents": bool(policy.auto_create_incidents),
        "auto_resolve_incidents": bool(policy.auto_resolve_incidents),
        "suppress_incidents_during_maintenance": bool(policy.suppress_incidents_during_maintenance),
    }


def policy_sha256(policy: SLOPolicy) -> str:
    """Вычислить policy sha256. Канонический ввод обеспечивает детерминированное сравнение
    целостности между процессами.
    """
    return _sha256_json(policy_snapshot(policy))


def get_or_create_slo_policy(
    db: Session,
    *,
    organization_id: str,
    settings: Settings,
    user: User | None = None,
) -> SLOPolicy:
    """Прочитать or create slo policy. Значение возвращается без несвязанных изменений состояния."""
    policy = db.scalar(select(SLOPolicy).where(SLOPolicy.organization_id == organization_id))
    if policy is not None:
        return policy

    # Serialize first creation through the tenant row. This prevents two API or
    # worker transactions from racing into the organization-level unique key.
    organization = db.scalar(
        select(Organization).where(Organization.id == organization_id).with_for_update()
    )
    if organization is None:
        raise OperationsError("Организация не найдена")
    policy = db.scalar(select(SLOPolicy).where(SLOPolicy.organization_id == organization_id))
    if policy is not None:
        return policy

    policy = SLOPolicy(
        organization_id=organization_id,
        enabled=True,
        evaluation_window_hours=settings.slo_default_evaluation_window_hours,
        delivery_success_target_bps=settings.slo_default_delivery_success_target_bps,
        minimum_delivery_sample_size=settings.slo_default_minimum_delivery_sample_size,
        max_queue_age_seconds=settings.slo_default_max_queue_age_seconds,
        max_worker_heartbeat_age_seconds=settings.slo_default_max_worker_heartbeat_age_seconds,
        max_unresolved_delivery_reviews=settings.slo_default_max_unresolved_delivery_reviews,
        max_open_critical_incidents=settings.slo_default_max_open_critical_incidents,
        error_budget_warning_percent=settings.slo_default_error_budget_warning_percent,
        error_budget_critical_percent=settings.slo_default_error_budget_critical_percent,
        assessment_ttl_minutes=settings.slo_assessment_ttl_minutes,
        gate_publishing=settings.slo_gate_required,
        gate_changes=settings.slo_gate_required,
        auto_create_incidents=True,
        auto_resolve_incidents=True,
        suppress_incidents_during_maintenance=True,
        created_by_id=user.id if user else None,
        updated_by_id=user.id if user else None,
    )
    db.add(policy)
    db.flush()
    return policy


def update_slo_policy(
    db: Session,
    *,
    policy: SLOPolicy,
    values: dict[str, Any],
    user: User,
    settings: Settings,
) -> SLOPolicy:
    """Обновить slo policy. Переход применяется только после проверки его предусловий."""
    for field, value in values.items():
        if value is not None:
            setattr(policy, field, value)
    if policy.error_budget_warning_percent >= policy.error_budget_critical_percent:
        raise OperationsError("Порог предупреждения error budget должен быть меньше критического")
    if settings.is_production and settings.slo_gate_required:
        if not policy.enabled:
            raise OperationsError("В production SLO policy нельзя отключить")
        if not policy.gate_publishing or not policy.gate_changes:
            raise OperationsError("В production SLO gate должен защищать публикации и изменения")
    policy.updated_by_id = user.id
    db.flush()
    return policy


def latest_slo_assessment(db: Session, *, organization_id: str) -> SLOAssessment | None:
    """Выполнить операцию latest slo assessment. Аргументы интерпретируются в контексте модуля,
    результат возвращается вызывающему коду.
    """
    return db.scalar(
        select(SLOAssessment)
        .where(SLOAssessment.organization_id == organization_id)
        .order_by(SLOAssessment.created_at.desc(), SLOAssessment.id.desc())
        .limit(1)
    )


def assessment_is_current(
    assessment: SLOAssessment | None,
    policy: SLOPolicy | None,
    *,
    now: datetime | None = None,
) -> bool:
    """Выполнить операцию assessment is current. Аргументы интерпретируются в контексте модуля,
    результат возвращается вызывающему коду.
    """
    if assessment is None or policy is None:
        return False
    now = aware_utc(now) or utcnow()
    expires_at = aware_utc(assessment.expires_at)
    return bool(
        expires_at and expires_at > now and assessment.policy_sha256 == policy_sha256(policy)
    )


def _count(
    db: Session,
    model_column: Any,
    *conditions: Any,
) -> int:
    """Реализовать внутренний этап count step. Вспомогательная функция сохраняет
    детерминированность и тестируемость процесса.
    """
    return int(db.scalar(select(func.count(model_column)).where(*conditions)) or 0)


def _last_worker_heartbeat(db: Session) -> WorkerHeartbeat | None:
    """Реализовать внутренний этап last worker heartbeat step. Вспомогательная функция сохраняет
    детерминированность и тестируемость процесса.
    """
    return db.scalar(select(WorkerHeartbeat).order_by(WorkerHeartbeat.last_seen_at.desc()).limit(1))


def append_incident_event(
    db: Session,
    *,
    incident: Incident,
    event_type: IncidentEventType,
    message: str | None,
    actor: User | None,
    payload: dict[str, Any] | None = None,
) -> IncidentEvent:
    """Создать incident event. Перед сохранением или возвратом нового значения проверяются
    связанные инварианты.
    """
    item = IncidentEvent(
        organization_id=incident.organization_id,
        incident_id=incident.id,
        event_type=event_type,
        message=(message or "")[:8000] or None,
        payload=payload or {},
        actor_user_id=actor.id if actor else None,
    )
    db.add(item)
    db.flush()
    return item


def create_incident(
    db: Session,
    *,
    organization_id: str,
    title: str,
    summary: str,
    severity: SafetySeverity,
    source: IncidentSource,
    actor: User | None,
    settings: Settings,
    impact: str | None = None,
    owner_user_id: str | None = None,
    started_at: datetime | None = None,
    dedup_key: str | None = None,
    linked_slo_assessment_id: str | None = None,
    metadata_payload: dict[str, Any] | None = None,
    notify: bool = True,
) -> Incident:
    """Создать incident. Перед сохранением или возвратом нового значения проверяются связанные
    инварианты.
    """
    if owner_user_id:
        owner = db.scalar(
            select(User).where(
                User.id == owner_user_id,
                User.organization_id == organization_id,
                User.is_active.is_(True),
            )
        )
        if owner is None:
            raise OperationsError("Ответственный пользователь не найден")
    now = utcnow()
    item = Incident(
        organization_id=organization_id,
        status=IncidentStatus.OPEN,
        severity=severity,
        source=source,
        title=title.strip()[:220],
        summary=summary.strip()[:8000],
        impact=impact.strip()[:8000] if impact else None,
        dedup_key=dedup_key[:240] if dedup_key else None,
        linked_slo_assessment_id=linked_slo_assessment_id,
        owner_user_id=owner_user_id,
        metadata_payload=metadata_payload or {},
        detected_at=now,
        started_at=aware_utc(started_at) or now,
        created_by_id=actor.id if actor else None,
    )
    db.add(item)
    db.flush()
    append_incident_event(
        db,
        incident=item,
        event_type=IncidentEventType.CREATED,
        message=item.summary,
        actor=actor,
        payload={"severity": severity.value, "source": source.value},
    )
    write_audit(
        db,
        actor=actor,
        organization_id=organization_id,
        action="incident.created",
        entity_type="incident",
        entity_id=item.id,
        severity=severity,
        details={
            "source": source.value,
            "severity": severity.value,
            "dedup_key": dedup_key,
            "linked_slo_assessment_id": linked_slo_assessment_id,
        },
    )
    if notify:
        create_notification(
            db,
            settings=settings,
            organization_id=organization_id,
            event_type="incident.created",
            title=f"Инцидент: {item.title}",
            message=item.summary,
            severity=severity,
            entity_type="incident",
            entity_id=item.id,
            dedup_key=f"incident-created:{item.dedup_key or item.id}",
            details={"source": source.value, "status": item.status.value},
        )
    return item


def _active_incident_statuses() -> tuple[IncidentStatus, ...]:
    """Реализовать внутренний этап active incident statuses step. Вспомогательная функция сохраняет
    детерминированность и тестируемость процесса.
    """
    return (
        IncidentStatus.OPEN,
        IncidentStatus.ACKNOWLEDGED,
        IncidentStatus.MITIGATING,
    )


def _sync_slo_incidents(
    db: Session,
    *,
    organization: Organization,
    policy: SLOPolicy,
    assessment: SLOAssessment,
    actor: User | None,
    settings: Settings,
) -> None:
    """Реализовать внутренний этап sync slo incidents step. Вспомогательная функция сохраняет
    детерминированность и тестируемость процесса.
    """
    creation_suppressed = bool(
        organization.maintenance_mode and policy.suppress_incidents_during_maintenance
    )

    blocked_checks = {
        str(check.get("code")): check
        for check in assessment.checks
        if check.get("status") == "blocked" and check.get("code")
    }
    if policy.auto_create_incidents and not creation_suppressed:
        checks_to_create = list(blocked_checks.items())
    else:
        checks_to_create = []

    for code, check in checks_to_create:
        dedup_key = f"slo:{code}"
        existing = db.scalar(
            select(Incident)
            .where(
                Incident.organization_id == organization.id,
                Incident.dedup_key == dedup_key,
                Incident.status.in_(_active_incident_statuses()),
            )
            .order_by(Incident.created_at.desc())
            .limit(1)
            .with_for_update()
        )
        if existing:
            existing.linked_slo_assessment_id = assessment.id
            existing.metadata_payload = {
                **(existing.metadata_payload or {}),
                "latest_assessment_id": assessment.id,
                "latest_observed": check.get("observed"),
                "latest_limit": check.get("limit"),
            }
            continue
        create_incident(
            db,
            organization_id=organization.id,
            title=f"Нарушение SLO: {check.get('title') or code}",
            summary=str(check.get("message") or "Критический показатель SLO нарушен"),
            severity=SafetySeverity.CRITICAL,
            source=IncidentSource.SLO,
            actor=actor,
            settings=settings,
            dedup_key=dedup_key,
            linked_slo_assessment_id=assessment.id,
            metadata_payload={
                "check_code": code,
                "observed": check.get("observed"),
                "limit": check.get("limit"),
                "assessment_id": assessment.id,
            },
        )

    if not policy.auto_resolve_incidents:
        return
    active = list(
        db.scalars(
            select(Incident).where(
                Incident.organization_id == organization.id,
                Incident.source == IncidentSource.SLO,
                Incident.status.in_(_active_incident_statuses()),
                Incident.dedup_key.is_not(None),
            )
        ).all()
    )
    for incident in active:
        code = (incident.dedup_key or "").removeprefix("slo:")
        if code in blocked_checks:
            continue
        incident.status = IncidentStatus.RESOLVED
        incident.resolved_at = assessment.window_end
        incident.resolved_by_id = actor.id if actor else None
        incident.resolution_summary = f"Показатель восстановлен по SLO assessment {assessment.id}."
        append_incident_event(
            db,
            incident=incident,
            event_type=IncidentEventType.RESOLVED,
            message=incident.resolution_summary,
            actor=actor,
            payload={"assessment_id": assessment.id, "automatic": True},
        )
        write_audit(
            db,
            actor=actor,
            organization_id=organization.id,
            action="incident.auto_resolved",
            entity_type="incident",
            entity_id=incident.id,
            details={"assessment_id": assessment.id, "check_code": code},
        )
        create_notification(
            db,
            settings=settings,
            organization_id=organization.id,
            event_type="incident.auto_resolved",
            title=f"SLO восстановлен: {incident.title}",
            message=incident.resolution_summary,
            severity=SafetySeverity.INFO,
            entity_type="incident",
            entity_id=incident.id,
            dedup_key=f"incident-resolved:{incident.id}:{assessment.id}",
        )


def evaluate_slo(
    db: Session,
    *,
    organization_id: str,
    settings: Settings,
    source: SLOAssessmentSource,
    user: User | None = None,
    now: datetime | None = None,
) -> SLOAssessment:
    """Выполнить операцию evaluate slo. Аргументы интерпретируются в контексте модуля, результат
    возвращается вызывающему коду.
    """
    now = aware_utc(now) or utcnow()
    organization = db.get(Organization, organization_id)
    if organization is None:
        raise OperationsError("Организация не найдена")
    policy = get_or_create_slo_policy(
        db,
        organization_id=organization_id,
        settings=settings,
        user=user,
    )
    # One assessment per organization is calculated at a time. Besides making
    # the latest snapshot deterministic, this prevents concurrent API/worker
    # evaluations from creating duplicate automatic incidents.
    policy = (
        db.scalar(select(SLOPolicy).where(SLOPolicy.id == policy.id).with_for_update()) or policy
    )
    window_start = now - timedelta(hours=policy.evaluation_window_hours)

    terminal_time = func.coalesce(DeliveryJob.finished_at, DeliveryJob.created_at)
    sent = _count(
        db,
        DeliveryJob.id,
        DeliveryJob.organization_id == organization_id,
        DeliveryJob.status == JobStatus.SENT,
        terminal_time >= window_start,
        terminal_time <= now,
    )
    failed = _count(
        db,
        DeliveryJob.id,
        DeliveryJob.organization_id == organization_id,
        DeliveryJob.status == JobStatus.FAILED,
        terminal_time >= window_start,
        terminal_time <= now,
    )
    uncertain = _count(
        db,
        DeliveryJob.id,
        DeliveryJob.organization_id == organization_id,
        DeliveryJob.status == JobStatus.WAITING_REVIEW,
        terminal_time >= window_start,
        terminal_time <= now,
    )
    eligible = sent + failed + uncertain
    success_rate_bps = round(sent * 10000 / eligible) if eligible else None

    allowed_failure_rate_bps = max(10000 - policy.delivery_success_target_bps, 0)
    observed_failures = failed + uncertain
    if eligible == 0:
        error_budget_consumed_bps: int | None = None
    elif allowed_failure_rate_bps == 0:
        error_budget_consumed_bps = 0 if observed_failures == 0 else 1_000_000
    else:
        allowed_failure_count = eligible * allowed_failure_rate_bps / 10000
        error_budget_consumed_bps = round(
            observed_failures / max(allowed_failure_count, 0.000001) * 10000
        )

    queue_statuses = [JobStatus.PENDING, JobStatus.RETRY, JobStatus.PROCESSING]
    oldest_due_at = db.scalar(
        select(func.min(DeliveryJob.due_at)).where(
            DeliveryJob.organization_id == organization_id,
            DeliveryJob.status.in_(queue_statuses),
        )
    )
    oldest_due = aware_utc(oldest_due_at)
    oldest_queue_age_seconds = max(int((now - oldest_due).total_seconds()), 0) if oldest_due else 0

    unresolved_reviews = _count(
        db,
        DeliveryJob.id,
        DeliveryJob.organization_id == organization_id,
        DeliveryJob.status == JobStatus.WAITING_REVIEW,
    )
    open_critical_incidents = _count(
        db,
        Incident.id,
        Incident.organization_id == organization_id,
        Incident.severity == SafetySeverity.CRITICAL,
        Incident.source != IncidentSource.SLO,
        Incident.status.in_(_active_incident_statuses()),
    )
    heartbeat = _last_worker_heartbeat(db)
    heartbeat_time = aware_utc(heartbeat.last_seen_at) if heartbeat else None
    worker_age_seconds = (
        max(int((now - heartbeat_time).total_seconds()), 0) if heartbeat_time else None
    )

    checks: list[dict[str, Any]] = []
    blockers: list[str] = []
    warnings: list[str] = []

    def add_check(
        code: str,
        title: str,
        status: str,
        message: str,
        *,
        observed: Any = None,
        limit: Any = None,
    ) -> None:
        """Создать check. Перед сохранением или возвратом нового значения проверяются связанные
        инварианты.
        """
        checks.append(
            {
                "code": code,
                "title": title,
                "status": status,
                "message": message,
                "observed": observed,
                "limit": limit,
            }
        )
        if status == "blocked":
            blockers.append(message)
        elif status == "warning":
            warnings.append(message)

    if not policy.enabled:
        add_check(
            "policy_disabled",
            "Политика SLO",
            "warning",
            "Политика SLO отключена; показатели рассчитаны только для наблюдения.",
        )

    if eligible < policy.minimum_delivery_sample_size:
        add_check(
            "delivery_sample",
            "Объём выборки доставки",
            "warning",
            (
                f"Недостаточно завершённых доставок для устойчивой оценки: {eligible} из "
                f"{policy.minimum_delivery_sample_size}."
            ),
            observed=eligible,
            limit=policy.minimum_delivery_sample_size,
        )
    else:
        add_check(
            "delivery_sample",
            "Объём выборки доставки",
            "passed",
            f"Выборка содержит {eligible} завершённых доставок.",
            observed=eligible,
            limit=policy.minimum_delivery_sample_size,
        )

    if error_budget_consumed_bps is None:
        budget_status = "warning"
        budget_message = "Error budget не рассчитан: в окне нет завершённых доставок."
    elif error_budget_consumed_bps >= policy.error_budget_critical_percent * 100:
        budget_status = "blocked"
        budget_message = (
            "Критический расход error budget: "
            f"{error_budget_consumed_bps / 100:.2f}% при пороге "
            f"{policy.error_budget_critical_percent}%."
        )
    elif error_budget_consumed_bps >= policy.error_budget_warning_percent * 100:
        budget_status = "warning"
        budget_message = (
            "Повышенный расход error budget: "
            f"{error_budget_consumed_bps / 100:.2f}% при пороге предупреждения "
            f"{policy.error_budget_warning_percent}%."
        )
    else:
        budget_status = "passed"
        budget_message = f"Расход error budget: {error_budget_consumed_bps / 100:.2f}%."
    add_check(
        "delivery_error_budget",
        "Надёжность доставки",
        budget_status,
        budget_message,
        observed=error_budget_consumed_bps,
        limit=policy.error_budget_critical_percent * 100,
    )

    queue_status = (
        "blocked" if oldest_queue_age_seconds > policy.max_queue_age_seconds else "passed"
    )
    add_check(
        "queue_age",
        "Возраст очереди",
        queue_status,
        (
            f"Самое старое задание ожидает {oldest_queue_age_seconds} секунд."
            if queue_status == "passed"
            else (
                f"Очередь задержана на {oldest_queue_age_seconds} секунд при лимите "
                f"{policy.max_queue_age_seconds}."
            )
        ),
        observed=oldest_queue_age_seconds,
        limit=policy.max_queue_age_seconds,
    )

    if worker_age_seconds is None:
        worker_status = "blocked"
        worker_message = "Heartbeat worker отсутствует."
    elif worker_age_seconds > policy.max_worker_heartbeat_age_seconds:
        worker_status = "blocked"
        worker_message = (
            f"Heartbeat worker устарел: {worker_age_seconds} секунд при лимите "
            f"{policy.max_worker_heartbeat_age_seconds}."
        )
    else:
        worker_status = "passed"
        worker_message = f"Worker отвечает; heartbeat {worker_age_seconds} секунд назад."
    add_check(
        "worker_heartbeat",
        "Worker",
        worker_status,
        worker_message,
        observed=worker_age_seconds,
        limit=policy.max_worker_heartbeat_age_seconds,
    )

    reviews_status = (
        "blocked" if unresolved_reviews > policy.max_unresolved_delivery_reviews else "passed"
    )
    add_check(
        "unresolved_delivery_reviews",
        "Неоднозначные доставки",
        reviews_status,
        (
            f"Нерешённых доставок: {unresolved_reviews}."
            if reviews_status == "passed"
            else (
                f"Нерешённых доставок {unresolved_reviews}, допустимо "
                f"{policy.max_unresolved_delivery_reviews}."
            )
        ),
        observed=unresolved_reviews,
        limit=policy.max_unresolved_delivery_reviews,
    )

    incident_status = (
        "blocked" if open_critical_incidents > policy.max_open_critical_incidents else "passed"
    )
    add_check(
        "open_critical_incidents",
        "Критические инциденты",
        incident_status,
        (
            f"Открытых критических инцидентов: {open_critical_incidents}."
            if incident_status == "passed"
            else (
                f"Открытых критических инцидентов {open_critical_incidents}, допустимо "
                f"{policy.max_open_critical_incidents}."
            )
        ),
        observed=open_critical_incidents,
        limit=policy.max_open_critical_incidents,
    )

    status = (
        ReadinessStatus.BLOCKED
        if blockers
        else ReadinessStatus.WARNING
        if warnings
        else ReadinessStatus.PASSED
    )
    snapshot = policy_snapshot(policy)
    snapshot_sha = _sha256_json(snapshot)
    metrics = {
        "window_hours": policy.evaluation_window_hours,
        "sent": sent,
        "failed": failed,
        "uncertain": uncertain,
        "eligible": eligible,
        "delivery_success_rate_bps": success_rate_bps,
        "delivery_success_target_bps": policy.delivery_success_target_bps,
        "error_budget_consumed_bps": error_budget_consumed_bps,
        "oldest_queue_age_seconds": oldest_queue_age_seconds,
        "worker_heartbeat_age_seconds": worker_age_seconds,
        "worker_id": heartbeat.worker_id if heartbeat else None,
        "unresolved_delivery_reviews": unresolved_reviews,
        "open_critical_incidents": open_critical_incidents,
    }
    fingerprint_payload = {
        "organization_id": organization_id,
        "source": source.value,
        "window_start": window_start.isoformat(),
        "window_end": now.isoformat(),
        "policy_sha256": snapshot_sha,
        "metrics": metrics,
        "checks": checks,
    }
    assessment = SLOAssessment(
        organization_id=organization_id,
        source=source,
        status=status,
        window_start=window_start,
        window_end=now,
        policy_snapshot=snapshot,
        policy_sha256=snapshot_sha,
        metrics=metrics,
        checks=checks,
        blockers=list(dict.fromkeys(blockers)),
        warnings=list(dict.fromkeys(warnings)),
        eligible_deliveries=eligible,
        successful_deliveries=sent,
        failed_deliveries=failed,
        uncertain_deliveries=uncertain,
        delivery_success_rate_bps=success_rate_bps,
        error_budget_consumed_bps=error_budget_consumed_bps,
        oldest_queue_age_seconds=oldest_queue_age_seconds,
        worker_heartbeat_age_seconds=worker_age_seconds,
        open_critical_incidents=open_critical_incidents,
        unresolved_delivery_reviews=unresolved_reviews,
        fingerprint=_sha256_json(fingerprint_payload),
        created_by_id=user.id if user else None,
        expires_at=now + timedelta(minutes=policy.assessment_ttl_minutes),
    )
    db.add(assessment)
    db.flush()

    write_audit(
        db,
        actor=user,
        organization_id=organization_id,
        action="slo.assessed",
        entity_type="slo_assessment",
        entity_id=assessment.id,
        severity=(
            SafetySeverity.CRITICAL
            if status == ReadinessStatus.BLOCKED
            else SafetySeverity.WARNING
            if status == ReadinessStatus.WARNING
            else SafetySeverity.INFO
        ),
        details={
            "source": source.value,
            "status": status.value,
            "fingerprint": assessment.fingerprint,
            "blockers": assessment.blockers,
            "warnings": assessment.warnings,
        },
    )
    _sync_slo_incidents(
        db,
        organization=organization,
        policy=policy,
        assessment=assessment,
        actor=user,
        settings=settings,
    )
    return assessment


def slo_gate_decision(
    db: Session,
    *,
    organization_id: str,
    settings: Settings,
    gate: str,
    now: datetime | None = None,
) -> SLOGateDecision:
    """Выполнить операцию slo gate decision. Аргументы интерпретируются в контексте модуля,
    результат возвращается вызывающему коду.
    """
    now = aware_utc(now) or utcnow()
    policy = db.scalar(select(SLOPolicy).where(SLOPolicy.organization_id == organization_id))
    required_by_settings = bool(settings.slo_gate_required)
    required_by_policy = bool(
        policy and (policy.gate_publishing if gate == "publishing" else policy.gate_changes)
    )
    required = required_by_settings or required_by_policy
    if not required:
        return SLOGateDecision(True, "SLO gate не включён для этого контура")
    if policy is None:
        return SLOGateDecision(False, "Политика SLO не настроена")
    if not policy.enabled:
        return SLOGateDecision(False, "Политика SLO отключена")
    assessment = latest_slo_assessment(db, organization_id=organization_id)
    if not assessment_is_current(assessment, policy, now=now):
        return SLOGateDecision(
            False,
            "Актуальная SLO-оценка отсутствует или устарела",
            assessment,
        )
    if assessment and assessment.status == ReadinessStatus.BLOCKED:
        return SLOGateDecision(
            False,
            "SLO-оценка содержит блокирующие нарушения: " + "; ".join(assessment.blockers[:3]),
            assessment,
        )
    open_critical = _count(
        db,
        Incident.id,
        Incident.organization_id == organization_id,
        Incident.severity == SafetySeverity.CRITICAL,
        Incident.status.in_(_active_incident_statuses()),
    )
    if open_critical > policy.max_open_critical_incidents:
        return SLOGateDecision(
            False,
            (
                f"Открытых критических инцидентов {open_critical}, допустимо "
                f"{policy.max_open_critical_incidents}"
            ),
            assessment,
        )
    return SLOGateDecision(True, "SLO gate пройден", assessment)


def update_incident(
    db: Session,
    *,
    incident: Incident,
    actor: User,
    severity: SafetySeverity | None = None,
    owner_user_id: str | None = None,
    impact: str | None = None,
    root_cause: str | None = None,
    postmortem_url: str | None = None,
) -> Incident:
    """Обновить incident. Переход применяется только после проверки его предусловий."""
    if owner_user_id is not None:
        owner = db.scalar(
            select(User).where(
                User.id == owner_user_id,
                User.organization_id == incident.organization_id,
                User.is_active.is_(True),
            )
        )
        if owner is None:
            raise OperationsError("Ответственный пользователь не найден")
        if incident.owner_user_id != owner_user_id:
            incident.owner_user_id = owner_user_id
            append_incident_event(
                db,
                incident=incident,
                event_type=IncidentEventType.OWNER_ASSIGNED,
                message=f"Назначен ответственный: {owner.display_name}",
                actor=actor,
                payload={"owner_user_id": owner_user_id},
            )
    if severity is not None and severity != incident.severity:
        previous = incident.severity
        incident.severity = severity
        append_incident_event(
            db,
            incident=incident,
            event_type=IncidentEventType.SEVERITY_CHANGED,
            message=f"Уровень изменён: {previous.value} → {severity.value}",
            actor=actor,
            payload={"previous": previous.value, "current": severity.value},
        )
    if impact is not None:
        incident.impact = impact.strip()[:8000] or None
    if root_cause is not None:
        incident.root_cause = root_cause.strip()[:12000] or None
    if postmortem_url is not None:
        incident.postmortem_url = postmortem_url.strip()[:1000] or None
    write_audit(
        db,
        actor=actor,
        action="incident.updated",
        entity_type="incident",
        entity_id=incident.id,
        severity=incident.severity,
        details={
            "status": incident.status.value,
            "severity": incident.severity.value,
            "owner_user_id": incident.owner_user_id,
        },
    )
    db.flush()
    return incident


def transition_incident(
    db: Session,
    *,
    incident: Incident,
    action: str,
    note: str,
    actor: User,
    settings: Settings,
    owner_user_id: str | None = None,
    root_cause: str | None = None,
    postmortem_url: str | None = None,
) -> Incident:
    """Обновить incident. Переход применяется только после проверки его предусловий."""
    now = utcnow()
    note = note.strip()[:8000]
    if owner_user_id:
        update_incident(
            db,
            incident=incident,
            actor=actor,
            owner_user_id=owner_user_id,
        )

    if action == "acknowledge":
        if incident.status != IncidentStatus.OPEN:
            raise OperationsError("Подтвердить можно только открытый инцидент")
        incident.status = IncidentStatus.ACKNOWLEDGED
        incident.acknowledged_at = now
        incident.acknowledged_by_id = actor.id
        event_type = IncidentEventType.ACKNOWLEDGED
    elif action == "mitigate":
        if incident.status not in {IncidentStatus.OPEN, IncidentStatus.ACKNOWLEDGED}:
            raise OperationsError("Начать устранение можно только для активного инцидента")
        incident.status = IncidentStatus.MITIGATING
        incident.mitigating_at = now
        incident.mitigating_by_id = actor.id
        event_type = IncidentEventType.MITIGATION_STARTED
    elif action == "resolve":
        if incident.status not in _active_incident_statuses():
            raise OperationsError("Разрешить можно только активный инцидент")
        incident.status = IncidentStatus.RESOLVED
        incident.resolved_at = now
        incident.resolved_by_id = actor.id
        incident.resolution_summary = note
        if root_cause is not None:
            incident.root_cause = root_cause.strip()[:12000] or None
        if postmortem_url is not None:
            incident.postmortem_url = postmortem_url.strip()[:1000] or None
        event_type = IncidentEventType.RESOLVED
    elif action == "close":
        if incident.status != IncidentStatus.RESOLVED:
            raise OperationsError("Закрыть можно только разрешённый инцидент")
        incident.status = IncidentStatus.CLOSED
        incident.closed_at = now
        incident.closed_by_id = actor.id
        event_type = IncidentEventType.CLOSED
    elif action == "reopen":
        if incident.status not in {IncidentStatus.RESOLVED, IncidentStatus.CLOSED}:
            raise OperationsError("Повторно открыть можно только завершённый инцидент")
        incident.status = IncidentStatus.OPEN
        incident.resolved_at = None
        incident.resolved_by_id = None
        incident.closed_at = None
        incident.closed_by_id = None
        incident.resolution_summary = None
        event_type = IncidentEventType.REOPENED
    elif action == "comment":
        event_type = IncidentEventType.COMMENTED
    else:
        raise OperationsError("Неизвестное действие с инцидентом")

    append_incident_event(
        db,
        incident=incident,
        event_type=event_type,
        message=note,
        actor=actor,
        payload={"status": incident.status.value, "action": action},
    )
    write_audit(
        db,
        actor=actor,
        action=f"incident.{action}",
        entity_type="incident",
        entity_id=incident.id,
        severity=incident.severity,
        details={"status": incident.status.value},
    )
    if action in {"resolve", "close", "reopen"}:
        create_notification(
            db,
            settings=settings,
            organization_id=incident.organization_id,
            event_type=f"incident.{action}",
            title=f"Инцидент: {incident.title}",
            message=note,
            severity=(SafetySeverity.INFO if action in {"resolve", "close"} else incident.severity),
            entity_type="incident",
            entity_id=incident.id,
            dedup_key=f"incident-{action}:{incident.id}:{now.isoformat()}",
        )
    db.flush()
    return incident


def evaluate_due_slo_policies(db: Session, *, settings: Settings) -> int:
    """Выполнить операцию evaluate due slo policies. Аргументы интерпретируются в контексте модуля,
    результат возвращается вызывающему коду.
    """
    now = utcnow()
    organization_ids = list(
        db.scalars(
            select(Organization.id).where(Organization.status == OrganizationStatus.ACTIVE)
        ).all()
    )
    processed = 0
    for organization_id in organization_ids:
        try:
            # A tenant-specific savepoint prevents a malformed tenant state from
            # suppressing SLO evidence for every other organization in the cycle.
            with db.begin_nested():
                policy = get_or_create_slo_policy(
                    db,
                    organization_id=organization_id,
                    settings=settings,
                )
                if not policy.enabled:
                    continue
                latest = latest_slo_assessment(db, organization_id=organization_id)
                latest_created = aware_utc(latest.created_at) if latest else None
                if latest_created and latest_created > now - timedelta(
                    minutes=settings.slo_auto_evaluate_minutes
                ):
                    continue
                evaluate_slo(
                    db,
                    organization_id=organization_id,
                    settings=settings,
                    source=SLOAssessmentSource.WORKER,
                    now=now,
                )
                processed += 1
        except Exception:
            logger.exception(
                "Automatic SLO evaluation failed for organization %s",
                organization_id,
            )
    return processed
