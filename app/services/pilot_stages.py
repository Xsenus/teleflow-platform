from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import Settings
from app.enums import (
    CampaignStatus,
    ConnectionStatus,
    JobStatus,
    NotificationStatus,
    OrganizationStatus,
    PermissionStatus,
    PilotCanaryStatus,
    PilotStage,
    ReadinessStatus,
    RunStatus,
    SafetySeverity,
)
from app.models import (
    Campaign,
    CampaignRun,
    DeliveryJob,
    Destination,
    Notification,
    Organization,
    PilotCanaryAttempt,
    PilotStageAssessment,
    TelegramConnection,
    User,
    WorkerHeartbeat,
    utcnow,
)
from app.security import aware_utc
from app.services.destination_validation import validation_is_fresh

STAGE_ORDER: tuple[PilotStage, ...] = (
    PilotStage.LOCAL,
    PilotStage.SERVICE,
    PilotStage.FIVE,
    PilotStage.TWENTY,
    PilotStage.FIFTY,
    PilotStage.HUNDRED,
)
STAGE_LIMITS: dict[PilotStage, int] = {
    PilotStage.LOCAL: 0,
    PilotStage.SERVICE: 1,
    PilotStage.FIVE: 5,
    PilotStage.TWENTY: 20,
    PilotStage.FIFTY: 50,
    PilotStage.HUNDRED: 100,
}
STAGE_LABELS: dict[PilotStage, str] = {
    PilotStage.LOCAL: "Локальный fake mode",
    PilotStage.SERVICE: "1 служебная группа",
    PilotStage.FIVE: "5 разрешённых групп",
    PilotStage.TWENTY: "20 разрешённых групп",
    PilotStage.FIFTY: "50 разрешённых групп",
    PilotStage.HUNDRED: "100 разрешённых групп",
}
RUN_EVIDENCE_REQUIRED: dict[PilotStage, int] = {
    PilotStage.LOCAL: 0,
    PilotStage.SERVICE: 0,
    PilotStage.FIVE: 1,
    PilotStage.TWENTY: 5,
    PilotStage.FIFTY: 20,
    PilotStage.HUNDRED: 50,
}


def _iso(value: datetime | None) -> str | None:
    """Преобразовать возможное время в нормализованную UTC ISO-строку."""
    normalized = aware_utc(value)
    return normalized.isoformat() if normalized is not None else None


def stage_index(stage: PilotStage) -> int:
    """Выполнить операцию stage index. Аргументы интерпретируются в контексте модуля, результат
    возвращается вызывающему коду.
    """
    return STAGE_ORDER.index(stage)


def next_stage(stage: PilotStage) -> PilotStage | None:
    """Выполнить операцию next stage. Аргументы интерпретируются в контексте модуля, результат
    возвращается вызывающему коду.
    """
    index = stage_index(stage)
    return STAGE_ORDER[index + 1] if index + 1 < len(STAGE_ORDER) else None


def stage_limit(stage: PilotStage, settings: Settings | None = None) -> int:
    # Local mode is intentionally non-live. Fake transport may still exercise
    # an arbitrary route locally because no Telegram request is made.
    """Выполнить операцию stage limit. Аргументы интерпретируются в контексте модуля, результат
    возвращается вызывающему коду.
    """
    if stage == PilotStage.LOCAL and settings and settings.telegram_fake_mode:
        return 100
    return STAGE_LIMITS[stage]


def active_campaign_destinations(campaign: Campaign) -> int:
    """Выполнить операцию active campaign destinations. Аргументы интерпретируются в контексте
    модуля, результат возвращается вызывающему коду.
    """
    return sum(1 for link in campaign.destinations if link.enabled and link.destination.enabled)


def campaign_stage_blocker(
    organization: Organization,
    campaign: Campaign,
    settings: Settings,
) -> str | None:
    """Выполнить операцию campaign stage blocker. Аргументы интерпретируются в контексте модуля,
    результат возвращается вызывающему коду.
    """
    if not settings.pilot_stage_enforcement_required:
        return None
    count = active_campaign_destinations(campaign)
    limit = stage_limit(organization.pilot_stage, settings)
    if organization.pilot_stage == PilotStage.LOCAL and not settings.telegram_fake_mode:
        return (
            "Организация находится на локальном этапе. Перед реальной публикацией "
            "пройдите служебную canary-проверку и повысьте этап пилота"
        )
    if count > limit:
        return (
            f"Текущий этап «{STAGE_LABELS[organization.pilot_stage]}» разрешает "
            f"не более {limit} назначений за запуск; в кампании {count}"
        )
    return None


def _check(
    checks: list[dict[str, Any]],
    *,
    code: str,
    title: str,
    status: str,
    message: str,
    details: dict[str, Any] | None = None,
) -> None:
    """Реализовать внутренний этап check step. Вспомогательная функция сохраняет
    детерминированность и тестируемость процесса.
    """
    checks.append(
        {
            "code": code,
            "title": title,
            "status": status,
            "message": message,
            "details": details or {},
        }
    )


def _connection_is_healthy(
    connection: TelegramConnection | None,
    settings: Settings,
    now: datetime,
) -> bool:
    """Реализовать внутренний этап connection is healthy step. Вспомогательная функция сохраняет
    детерминированность и тестируемость процесса.
    """
    if connection is None or connection.status != ConnectionStatus.ACTIVE:
        return False
    if not connection.credentials_enc:
        return False
    checked_at = aware_utc(connection.last_checked_at)
    if checked_at is None or checked_at <= now - timedelta(
        hours=settings.connection_health_ttl_hours
    ):
        return False
    blocked_until = aware_utc(connection.flood_blocked_until)
    if blocked_until and blocked_until > now:
        return False
    return connection.last_error_code not in {
        "ANTI_SPAM_RESTRICTION",
        "TELEGRAM_AUTH_INVALID",
    }


def _worker_heartbeat_is_fresh(
    db: Session,
    settings: Settings,
    now: datetime,
) -> tuple[WorkerHeartbeat | None, bool]:
    """Реализовать внутренний этап worker heartbeat is fresh step. Вспомогательная функция
    сохраняет детерминированность и тестируемость процесса.
    """
    heartbeat = db.scalar(
        select(WorkerHeartbeat).order_by(WorkerHeartbeat.last_seen_at.desc()).limit(1)
    )
    heartbeat_at = aware_utc(heartbeat.last_seen_at) if heartbeat else None
    return heartbeat, bool(
        heartbeat_at
        and heartbeat_at > now - timedelta(seconds=settings.worker_readiness_max_age_seconds)
    )


def _critical_unacknowledged_count(db: Session, organization_id: str) -> int:
    """Реализовать внутренний этап critical unacknowledged count step. Вспомогательная функция
    сохраняет детерминированность и тестируемость процесса.
    """
    return int(
        db.scalar(
            select(func.count(Notification.id)).where(
                Notification.organization_id == organization_id,
                Notification.severity == SafetySeverity.CRITICAL,
                Notification.status != NotificationStatus.ACKNOWLEDGED,
            )
        )
        or 0
    )


def _ready_destinations(
    db: Session,
    organization_id: str,
    settings: Settings,
    now: datetime,
) -> list[Destination]:
    """Реализовать внутренний этап ready destinations step. Вспомогательная функция сохраняет
    детерминированность и тестируемость процесса.
    """
    connections = {
        item.id: item
        for item in db.scalars(
            select(TelegramConnection).where(TelegramConnection.organization_id == organization_id)
        ).all()
    }
    rows = list(
        db.scalars(
            select(Destination).where(
                Destination.organization_id == organization_id,
                Destination.enabled.is_(True),
                Destination.permission_status == PermissionStatus.CONFIRMED,
            )
        ).all()
    )
    ready: list[Destination] = []
    for destination in rows:
        if not _connection_is_healthy(connections.get(destination.connection_id), settings, now):
            continue
        expiry = aware_utc(destination.permission_expires_at)
        if expiry and expiry <= now:
            continue
        if not destination.permission_note and not destination.rules_url:
            continue
        if not validation_is_fresh(destination, settings, now=now):
            continue
        ready.append(destination)
    return ready


def _latest_completed_run(
    db: Session,
    organization_id: str,
    settings: Settings,
    now: datetime,
) -> CampaignRun | None:
    """Реализовать внутренний этап latest completed run step. Вспомогательная функция сохраняет
    детерминированность и тестируемость процесса.
    """
    cutoff = now - timedelta(days=settings.pilot_run_evidence_max_age_days)
    real_delivery_exists = (
        select(DeliveryJob.id)
        .where(
            DeliveryJob.run_id == CampaignRun.id,
            DeliveryJob.status == JobStatus.SENT,
            DeliveryJob.telegram_message_id.is_not(None),
            ~DeliveryJob.telegram_message_id.like("fake-%"),
        )
        .exists()
    )
    return db.scalar(
        select(CampaignRun)
        .where(
            CampaignRun.organization_id == organization_id,
            CampaignRun.status.in_([RunStatus.COMPLETED, RunStatus.PARTIAL]),
            CampaignRun.finished_at.is_not(None),
            CampaignRun.finished_at >= cutoff,
            real_delivery_exists,
        )
        .order_by(CampaignRun.finished_at.desc())
        .limit(1)
    )


def _real_sent_count(db: Session, run_id: str | None) -> int:
    """Реализовать внутренний этап real sent count step. Вспомогательная функция сохраняет
    детерминированность и тестируемость процесса.
    """
    if not run_id:
        return 0
    return int(
        db.scalar(
            select(func.count(DeliveryJob.id)).where(
                DeliveryJob.run_id == run_id,
                DeliveryJob.status == JobStatus.SENT,
                DeliveryJob.telegram_message_id.is_not(None),
                ~DeliveryJob.telegram_message_id.like("fake-%"),
            )
        )
        or 0
    )


def latest_successful_real_canary(
    db: Session,
    organization_id: str,
    settings: Settings,
    *,
    now: datetime | None = None,
) -> PilotCanaryAttempt | None:
    """Выполнить операцию latest successful real canary. Аргументы интерпретируются в контексте
    модуля, результат возвращается вызывающему коду.
    """
    now = aware_utc(now) or utcnow()
    cutoff = now - timedelta(hours=settings.pilot_canary_valid_hours)
    return db.scalar(
        select(PilotCanaryAttempt)
        .where(
            PilotCanaryAttempt.organization_id == organization_id,
            PilotCanaryAttempt.status == PilotCanaryStatus.SENT,
            PilotCanaryAttempt.is_fake.is_(False),
            PilotCanaryAttempt.completed_at >= cutoff,
        )
        .order_by(PilotCanaryAttempt.completed_at.desc())
        .limit(1)
    )


def stage_state_fingerprint(
    db: Session,
    *,
    organization: Organization,
    requested_stage: PilotStage,
    settings: Settings,
    now: datetime | None = None,
) -> str:
    """Вычислить stage state fingerprint. Канонический ввод обеспечивает детерминированное
    сравнение целостности между процессами.
    """
    now = aware_utc(now) or utcnow()
    ready = _ready_destinations(db, organization.id, settings, now)
    connections = list(
        db.scalars(
            select(TelegramConnection)
            .where(TelegramConnection.organization_id == organization.id)
            .order_by(TelegramConnection.id)
        ).all()
    )
    canary = latest_successful_real_canary(db, organization.id, settings, now=now)
    run = _latest_completed_run(db, organization.id, settings, now)
    waiting_review = int(
        db.scalar(
            select(func.count(DeliveryJob.id)).where(
                DeliveryJob.organization_id == organization.id,
                DeliveryJob.status == JobStatus.WAITING_REVIEW,
            )
        )
        or 0
    )
    _, worker_heartbeat_fresh = _worker_heartbeat_is_fresh(db, settings, now)
    critical_unacknowledged = _critical_unacknowledged_count(db, organization.id)
    payload = {
        "organization_id": organization.id,
        "status": organization.status.value,
        "publishing_paused": organization.publishing_paused,
        "current_stage": organization.pilot_stage.value,
        "requested_stage": requested_stage.value,
        "telegram_fake_mode": settings.telegram_fake_mode,
        "ready_destinations": [
            {
                "id": item.id,
                "validated_at": _iso(item.validated_at),
                "validation_expires_at": _iso(item.validation_expires_at),
                "permission_expires_at": _iso(item.permission_expires_at),
            }
            for item in sorted(ready, key=lambda row: row.id)
        ],
        "connections": [
            {
                "id": item.id,
                "status": item.status.value,
                "last_checked_at": _iso(item.last_checked_at),
                "flood_blocked_until": _iso(item.flood_blocked_until),
                "last_error_code": item.last_error_code,
            }
            for item in connections
        ],
        "canary": {
            "id": canary.id,
            "completed_at": _iso(canary.completed_at),
        }
        if canary
        else None,
        "run": {
            "id": run.id,
            "sent_jobs": run.sent_jobs,
            "real_sent_jobs": _real_sent_count(db, run.id),
            "failed_jobs": run.failed_jobs,
            "total_jobs": run.total_jobs,
            "finished_at": _iso(run.finished_at),
        }
        if run
        else None,
        "waiting_review": waiting_review,
        "worker_heartbeat_fresh": worker_heartbeat_fresh,
        "critical_unacknowledged": critical_unacknowledged,
    }
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def create_stage_assessment(
    db: Session,
    *,
    organization: Organization,
    requested_stage: PilotStage,
    created_by: User,
    settings: Settings,
    now: datetime | None = None,
) -> PilotStageAssessment:
    """Создать stage assessment. Перед сохранением или возвратом нового значения проверяются
    связанные инварианты.
    """
    now = aware_utc(now) or utcnow()
    expected = next_stage(organization.pilot_stage)
    if expected is None:
        raise ValueError("Организация уже находится на максимальном этапе")
    if requested_stage != expected:
        raise ValueError(f"Можно перейти только на следующий этап: {STAGE_LABELS[expected]}")

    checks: list[dict[str, Any]] = []
    _check(
        checks,
        code="ORGANIZATION_ACTIVE",
        title="Организация",
        status="passed" if organization.status == OrganizationStatus.ACTIVE else "blocked",
        message=(
            "Организация активна"
            if organization.status == OrganizationStatus.ACTIVE
            else f"Статус организации: {organization.status.value}"
        ),
    )
    _check(
        checks,
        code="PUBLISHING_SWITCH",
        title="Аварийная остановка",
        status="blocked" if organization.publishing_paused else "passed",
        message=(
            organization.publishing_pause_reason or "Публикации остановлены"
            if organization.publishing_paused
            else "Публикации не остановлены"
        ),
    )
    _check(
        checks,
        code="LIVE_TRANSPORT",
        title="Telegram transport",
        status="blocked" if settings.telegram_fake_mode else "passed",
        message=(
            "Отключите fake mode и подключите реальный Telegram transport"
            if settings.telegram_fake_mode
            else "Используется реальный Telegram transport"
        ),
    )

    heartbeat, heartbeat_fresh = _worker_heartbeat_is_fresh(db, settings, now)
    _check(
        checks,
        code="WORKER_HEARTBEAT",
        title="Worker",
        status="passed" if heartbeat_fresh else "blocked",
        message=(
            f"Worker {heartbeat.worker_id} отвечает"
            if heartbeat_fresh and heartbeat
            else "Свежий heartbeat worker не найден"
        ),
    )

    active_connections = list(
        db.scalars(
            select(TelegramConnection).where(
                TelegramConnection.organization_id == organization.id,
                TelegramConnection.status == ConnectionStatus.ACTIVE,
            )
        ).all()
    )
    healthy_connections = [
        item for item in active_connections if _connection_is_healthy(item, settings, now)
    ]
    _check(
        checks,
        code="HEALTHY_CONNECTION",
        title="Telegram-подключение",
        status="passed" if healthy_connections else "blocked",
        message=(
            f"Готовых подключений: {len(healthy_connections)}"
            if healthy_connections
            else "Нет активного подключения со свежей проверкой"
        ),
    )

    ready = _ready_destinations(db, organization.id, settings, now)
    required_destinations = STAGE_LIMITS[requested_stage]
    _check(
        checks,
        code="READY_DESTINATIONS",
        title="Разрешённые назначения",
        status="passed" if len(ready) >= required_destinations else "blocked",
        message=f"Готово {len(ready)}, требуется {required_destinations}",
        details={"ready": len(ready), "required": required_destinations},
    )

    canary = latest_successful_real_canary(db, organization.id, settings, now=now)
    _check(
        checks,
        code="REAL_CANARY",
        title="Служебная canary-публикация",
        status="passed" if canary else "blocked",
        message=(
            f"Успешная реальная canary: {_iso(canary.completed_at)}"
            if canary and _iso(canary.completed_at)
            else "Нет свежей успешной реальной canary-публикации"
        ),
    )

    waiting_review = int(
        db.scalar(
            select(func.count(DeliveryJob.id)).where(
                DeliveryJob.organization_id == organization.id,
                DeliveryJob.status == JobStatus.WAITING_REVIEW,
            )
        )
        or 0
    )
    _check(
        checks,
        code="NO_UNCERTAIN_DELIVERIES",
        title="Неоднозначные доставки",
        status="passed" if waiting_review == 0 else "blocked",
        message=(
            "Нерешённых доставок нет"
            if waiting_review == 0
            else f"Требуют ручной сверки: {waiting_review}"
        ),
    )

    critical_unack = _critical_unacknowledged_count(db, organization.id)
    _check(
        checks,
        code="CRITICAL_NOTIFICATIONS",
        title="Критические уведомления",
        status="passed" if critical_unack == 0 else "blocked",
        message=(
            "Неподтверждённых критических уведомлений нет"
            if critical_unack == 0
            else f"Нужно подтвердить критические события: {critical_unack}"
        ),
    )

    required_sent = RUN_EVIDENCE_REQUIRED[requested_stage]
    run = _latest_completed_run(db, organization.id, settings, now)
    real_sent = _real_sent_count(db, run.id if run else None)
    if required_sent == 0:
        run_status = "passed"
        run_message = "Для служебного этапа достаточно canary-проверки"
    elif run is None:
        run_status = "blocked"
        run_message = (
            f"Нет завершённого реального пилотного запуска минимум на {required_sent} отправок"
        )
    else:
        denominator = max(real_sent + run.failed_jobs, 1)
        failure_percent = (run.failed_jobs * 100) / denominator
        run_ok = (
            real_sent >= required_sent and failure_percent <= settings.pilot_max_failure_percent
        )
        run_status = "passed" if run_ok else "blocked"
        run_message = (
            f"Последний реальный запуск: отправлено {real_sent}, ошибок {run.failed_jobs} "
            f"({failure_percent:.1f}%)"
        )
    _check(
        checks,
        code="RUN_EVIDENCE",
        title="Результат предыдущего этапа",
        status=run_status,
        message=run_message,
        details={
            "required_sent": required_sent,
            "run_id": run.id if run else None,
            "sent_jobs": run.sent_jobs if run else 0,
            "real_sent_jobs": real_sent,
            "failed_jobs": run.failed_jobs if run else 0,
        },
    )

    blockers = [
        f"{item['title']}: {item['message']}" for item in checks if item["status"] == "blocked"
    ]
    warnings = [
        f"{item['title']}: {item['message']}" for item in checks if item["status"] == "warning"
    ]
    status = (
        ReadinessStatus.BLOCKED
        if blockers
        else ReadinessStatus.WARNING
        if warnings
        else ReadinessStatus.PASSED
    )
    fingerprint = stage_state_fingerprint(
        db,
        organization=organization,
        requested_stage=requested_stage,
        settings=settings,
        now=now,
    )
    assessment = PilotStageAssessment(
        organization_id=organization.id,
        current_stage=organization.pilot_stage,
        requested_stage=requested_stage,
        status=status,
        checks=checks,
        blockers=blockers,
        warnings=warnings,
        summary={
            "current_stage": organization.pilot_stage.value,
            "requested_stage": requested_stage.value,
            "current_limit": STAGE_LIMITS[organization.pilot_stage],
            "requested_limit": STAGE_LIMITS[requested_stage],
            "ready_destinations": len(ready),
            "healthy_connections": len(healthy_connections),
            "waiting_review": waiting_review,
            "critical_unacknowledged": critical_unack,
            "passed_checks": sum(item["status"] == "passed" for item in checks),
            "warning_checks": sum(item["status"] == "warning" for item in checks),
            "blocked_checks": sum(item["status"] == "blocked" for item in checks),
            "real_canary_id": canary.id if canary else None,
            "evidence_run_id": run.id if run else None,
        },
        fingerprint=fingerprint,
        created_by_id=created_by.id,
        created_at=now,
        expires_at=now + timedelta(minutes=settings.pilot_stage_assessment_ttl_minutes),
    )
    db.add(assessment)
    db.flush()
    return assessment


def assessment_is_current(
    db: Session,
    *,
    organization: Organization,
    assessment: PilotStageAssessment,
    settings: Settings,
    now: datetime | None = None,
) -> bool:
    """Выполнить операцию assessment is current. Аргументы интерпретируются в контексте модуля,
    результат возвращается вызывающему коду.
    """
    now = aware_utc(now) or utcnow()
    if assessment.organization_id != organization.id:
        return False
    if assessment.current_stage != organization.pilot_stage:
        return False
    if assessment.status != ReadinessStatus.PASSED:
        return False
    expires_at = aware_utc(assessment.expires_at)
    if expires_at is None or expires_at <= now:
        return False
    expected = next_stage(organization.pilot_stage)
    if expected != assessment.requested_stage:
        return False
    return assessment.fingerprint == stage_state_fingerprint(
        db,
        organization=organization,
        requested_stage=assessment.requested_stage,
        settings=settings,
        now=now,
    )


def stage_confirmation(stage: PilotStage) -> str:
    """Выполнить операцию stage confirmation. Аргументы интерпретируются в контексте модуля,
    результат возвращается вызывающему коду.
    """
    return f"ПЕРЕЙТИ НА ЭТАП {STAGE_LIMITS[stage]}"


def lower_confirmation(stage: PilotStage) -> str:
    """Выполнить операцию lower confirmation. Аргументы интерпретируются в контексте модуля,
    результат возвращается вызывающему коду.
    """
    return f"СНИЗИТЬ ЭТАП ДО {STAGE_LIMITS[stage]}"


def apply_stage(
    organization: Organization,
    *,
    stage: PilotStage,
    actor: User,
    note: str,
    now: datetime | None = None,
) -> None:
    """Выполнить операцию apply stage. Аргументы интерпретируются в контексте модуля, результат
    возвращается вызывающему коду.
    """
    organization.pilot_stage = stage
    organization.pilot_stage_updated_at = aware_utc(now) or utcnow()
    organization.pilot_stage_updated_by_id = actor.id
    organization.pilot_stage_note = note


def pause_campaigns_above_stage(
    db: Session,
    *,
    organization: Organization,
    settings: Settings,
    now: datetime | None = None,
) -> tuple[int, int]:
    """Выполнить операцию pause campaigns above stage. Аргументы интерпретируются в контексте
    модуля, результат возвращается вызывающему коду.
    """
    now = aware_utc(now) or utcnow()
    limit = stage_limit(organization.pilot_stage, settings)
    campaigns = list(
        db.scalars(
            select(Campaign).where(
                Campaign.organization_id == organization.id,
                Campaign.status.in_(
                    [
                        CampaignStatus.SCHEDULED,
                        CampaignStatus.RUNNING,
                    ]
                ),
            )
        ).all()
    )
    paused = 0
    held_jobs = 0
    for campaign in campaigns:
        if active_campaign_destinations(campaign) <= limit:
            continue
        campaign.status = CampaignStatus.PAUSED
        campaign.next_run_at = None
        paused += 1
        jobs = list(
            db.scalars(
                select(DeliveryJob).where(
                    DeliveryJob.organization_id == organization.id,
                    DeliveryJob.campaign_id == campaign.id,
                    DeliveryJob.status.in_([JobStatus.PENDING, JobStatus.RETRY, JobStatus.HELD]),
                )
            ).all()
        )
        for job in jobs:
            job.status = JobStatus.WAITING_REVIEW
            job.error_code = "PILOT_STAGE_LIMIT_REDUCED"
            job.error_message = f"Этап пилота снижен до {STAGE_LABELS[organization.pilot_stage]}"
            job.locked_at = None
            job.locked_by = None
            held_jobs += 1
    return paused, held_jobs
