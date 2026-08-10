from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import Settings
from app.enums import (
    ConnectionStatus,
    OrganizationStatus,
    PreflightStatus,
    ReadinessStatus,
    RolloutMode,
    UserRole,
)
from app.models import (
    Campaign,
    Organization,
    PilotReadinessReport,
    User,
    WorkerHeartbeat,
    utcnow,
)
from app.security import aware_utc
from app.services.approvals import approval_is_current, campaign_fingerprint
from app.services.blackouts import evaluate_blackouts
from app.services.destination_validation import validation_is_fresh
from app.services.pilot_stages import campaign_stage_blocker
from app.services.preflight import create_preflight_report
from app.services.storage import StorageService


def _check(
    checks: list[dict[str, Any]],
    *,
    code: str,
    title: str,
    status: str,
    message: str,
    entity_type: str | None = None,
    entity_id: str | None = None,
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
            "entity_type": entity_type,
            "entity_id": entity_id,
            "details": details or {},
        }
    )


def _collect_messages(checks: list[dict[str, Any]], status: str) -> list[str]:
    """Реализовать внутренний этап collect messages step. Вспомогательная функция сохраняет
    детерминированность и тестируемость процесса.
    """
    return [f"{item['title']}: {item['message']}" for item in checks if item["status"] == status]


def create_pilot_readiness_report(
    db: Session,
    *,
    campaign: Campaign,
    created_by: User,
    settings: Settings,
    storage: StorageService | None = None,
    now: datetime | None = None,
) -> PilotReadinessReport:
    """Создать pilot readiness report. Перед сохранением или возвратом нового значения проверяются
    связанные инварианты.
    """
    now = aware_utc(now) or utcnow()
    organization = db.get(Organization, campaign.organization_id)
    if organization is None:
        raise ValueError("Организация кампании не найдена")
    connection = campaign.connection
    active_links = [link for link in campaign.destinations if link.enabled]
    checks: list[dict[str, Any]] = []

    _check(
        checks,
        code="ORG_ACTIVE",
        title="Организация",
        status="passed" if organization.status == OrganizationStatus.ACTIVE else "blocked",
        message=(
            "Организация активна"
            if organization.status == OrganizationStatus.ACTIVE
            else f"Статус организации: {organization.status.value}"
        ),
        entity_type="organization",
        entity_id=organization.id,
    )
    _check(
        checks,
        code="PUBLISHING_SWITCH",
        title="Глобальный переключатель",
        status="blocked" if organization.publishing_paused else "passed",
        message=(
            (organization.publishing_pause_reason or "Публикации остановлены")
            if organization.publishing_paused
            else "Аварийная остановка не активна"
        ),
        entity_type="organization",
        entity_id=organization.id,
    )

    heartbeat = db.scalar(
        select(WorkerHeartbeat).order_by(WorkerHeartbeat.last_seen_at.desc()).limit(1)
    )
    heartbeat_seen_at = aware_utc(heartbeat.last_seen_at) if heartbeat else None
    heartbeat_fresh = bool(
        heartbeat
        and heartbeat_seen_at
        and heartbeat_seen_at > now - timedelta(seconds=settings.worker_readiness_max_age_seconds)
    )
    heartbeat_status = (
        "passed"
        if heartbeat_fresh
        else ("blocked" if settings.require_worker_for_readiness else "warning")
    )
    _check(
        checks,
        code="WORKER_HEARTBEAT",
        title="Worker",
        status=heartbeat_status,
        message=(
            f"Worker {heartbeat.worker_id} отвечает"
            if heartbeat_fresh and heartbeat
            else "Свежий heartbeat worker не найден"
        ),
        entity_type="worker",
        entity_id=heartbeat.worker_id if heartbeat else None,
        details={
            "last_seen_at": heartbeat.last_seen_at.isoformat() if heartbeat else None,
            "max_age_seconds": settings.worker_readiness_max_age_seconds,
        },
    )

    _check(
        checks,
        code="CONNECTION_ACTIVE",
        title="Telegram-подключение",
        status="passed" if connection.status == ConnectionStatus.ACTIVE else "blocked",
        message=(
            "Подключение активно"
            if connection.status == ConnectionStatus.ACTIVE
            else f"Статус подключения: {connection.status.value}"
        ),
        entity_type="telegram_connection",
        entity_id=connection.id,
    )
    if settings.telegram_fake_mode:
        _check(
            checks,
            code="TELEGRAM_FAKE_MODE",
            title="Telegram transport",
            status="warning",
            message="Включён fake mode; отчёт подтверждает только локальную готовность",
            entity_type="telegram_connection",
            entity_id=connection.id,
        )
    else:
        _check(
            checks,
            code="CONNECTION_CREDENTIALS",
            title="Credentials подключения",
            status="passed" if connection.credentials_enc else "blocked",
            message=(
                "Зашифрованные credentials сохранены"
                if connection.credentials_enc
                else "Credentials отсутствуют"
            ),
            entity_type="telegram_connection",
            entity_id=connection.id,
        )
        checked_at = aware_utc(connection.last_checked_at)
        fresh = bool(
            checked_at and checked_at > now - timedelta(hours=settings.connection_health_ttl_hours)
        )
        _check(
            checks,
            code="CONNECTION_HEALTH_FRESH",
            title="Проверка подключения",
            status="passed" if fresh else "blocked",
            message=(
                f"Проверено {checked_at.isoformat()}"
                if fresh and checked_at
                else "Подключение давно не проверялось через Telegram"
            ),
            entity_type="telegram_connection",
            entity_id=connection.id,
        )

    approved = approval_is_current(campaign)
    _check(
        checks,
        code="CAMPAIGN_APPROVAL",
        title="Утверждение кампании",
        status="passed" if approved else "blocked",
        message="Fingerprint утверждения актуален" if approved else "Нужно актуальное утверждение",
        entity_type="campaign",
        entity_id=campaign.id,
    )

    preflight = create_preflight_report(
        db,
        campaign=campaign,
        created_by=created_by,
        settings=settings,
        storage=storage or StorageService(settings),
        now=now,
    )
    preflight_status = (
        "blocked"
        if preflight.status == PreflightStatus.BLOCKED
        else "warning"
        if preflight.status == PreflightStatus.WARNING
        else "passed"
    )
    _check(
        checks,
        code="PREFLIGHT",
        title="Предзапусковая проверка",
        status=preflight_status,
        message=(
            "; ".join(preflight.blockers[:3])
            if preflight.blockers
            else "; ".join(preflight.warnings[:3])
            if preflight.warnings
            else "Локальные проверки пройдены"
        ),
        entity_type="campaign_preflight_report",
        entity_id=preflight.id,
        details={"status": preflight.status.value},
    )

    required_approvers = (
        organization.high_risk_required_approvals
        if len(active_links) >= organization.high_risk_destination_threshold
        else 1
    )
    approver_count = int(
        db.scalar(
            select(func.count(User.id)).where(
                User.organization_id == campaign.organization_id,
                User.is_active.is_(True),
                User.role.in_([UserRole.OWNER, UserRole.ADMIN]),
            )
        )
        or 0
    )
    _check(
        checks,
        code="APPROVER_CAPACITY",
        title="Независимые утверждающие",
        status="passed" if approver_count >= required_approvers else "blocked",
        message=f"Доступно {approver_count}, требуется {required_approvers}",
        entity_type="organization",
        entity_id=organization.id,
    )

    staged_required = len(active_links) > settings.pilot_staged_threshold
    staged_ok = not staged_required or campaign.rollout_mode == RolloutMode.STAGED
    _check(
        checks,
        code="STAGED_ROLLOUT",
        title="Пакетный ввод",
        status="passed" if staged_ok else "blocked",
        message=(
            "Пакетный режим соответствует размеру маршрута"
            if staged_ok
            else (
                f"Для маршрута более {settings.pilot_staged_threshold} назначений "
                "требуется staged rollout"
            )
        ),
        entity_type="campaign",
        entity_id=campaign.id,
        details={"destinations": len(active_links), "mode": campaign.rollout_mode.value},
    )

    stage_blocker = campaign_stage_blocker(organization, campaign, settings)
    _check(
        checks,
        code="PILOT_STAGE_CAPACITY",
        title="Этап масштабирования",
        status="blocked" if stage_blocker else "passed",
        message=(
            stage_blocker
            or f"Текущий этап: {organization.pilot_stage.value}; маршрут соответствует лимиту"
        ),
        entity_type="organization",
        entity_id=organization.id,
        details={
            "pilot_stage": organization.pilot_stage.value,
            "enforcement_required": settings.pilot_stage_enforcement_required,
            "destinations": len(active_links),
        },
    )

    stale_destinations = [
        link.destination.title
        for link in active_links
        if not validation_is_fresh(link.destination, settings, now=now)
    ]
    _check(
        checks,
        code="DESTINATION_VALIDATION",
        title="Проверка назначений",
        status="passed" if not stale_destinations else "blocked",
        message=(
            "Все назначения имеют свежую проверку Telegram"
            if not stale_destinations
            else "Требуют повторной проверки: " + ", ".join(stale_destinations[:10])
        ),
        entity_type="campaign",
        entity_id=campaign.id,
        details={"stale_destination_count": len(stale_destinations)},
    )

    active_blackout_titles: list[str] = []
    latest_blackout_end: datetime | None = None
    for link in active_links:
        decision = evaluate_blackouts(
            db,
            organization_id=campaign.organization_id,
            connection_id=campaign.connection_id,
            destination_id=link.destination.id,
            at=now,
        )
        if decision.active:
            active_blackout_titles.extend(item.title for item in decision.matches)
            if decision.defer_until and (
                latest_blackout_end is None or decision.defer_until > latest_blackout_end
            ):
                latest_blackout_end = decision.defer_until
    _check(
        checks,
        code="ACTIVE_BLACKOUTS",
        title="Операционный календарь",
        status="warning" if active_blackout_titles else "passed",
        message=(
            "Сейчас действует запрет: " + ", ".join(dict.fromkeys(active_blackout_titles))
            if active_blackout_titles
            else "Активных запретов для маршрута нет"
        ),
        entity_type="campaign",
        entity_id=campaign.id,
        details={
            "active_blackout_count": len(set(active_blackout_titles)),
            "defer_until": latest_blackout_end.isoformat() if latest_blackout_end else None,
        },
    )

    cap_ok = len(active_links) <= min(connection.daily_cap, settings.global_hard_daily_cap)
    _check(
        checks,
        code="DAILY_CAPACITY",
        title="Дневной лимит",
        status="passed" if cap_ok else "blocked",
        message=(
            "Маршрут помещается в дневной лимит"
            if cap_ok
            else "Количество назначений превышает эффективный дневной лимит"
        ),
        entity_type="telegram_connection",
        entity_id=connection.id,
        details={
            "destinations": len(active_links),
            "effective_cap": min(connection.daily_cap, settings.global_hard_daily_cap),
        },
    )

    admins_without_totp = int(
        db.scalar(
            select(func.count(User.id)).where(
                User.organization_id == campaign.organization_id,
                User.is_active.is_(True),
                User.role.in_([UserRole.OWNER, UserRole.ADMIN]),
                User.totp_enabled.is_(False),
            )
        )
        or 0
    )
    totp_status = (
        "passed" if admins_without_totp == 0 else "blocked" if settings.is_production else "warning"
    )
    _check(
        checks,
        code="ADMIN_TOTP",
        title="2FA администраторов",
        status=totp_status,
        message=(
            "У всех владельцев и администраторов включён TOTP"
            if admins_without_totp == 0
            else f"Без TOTP: {admins_without_totp}"
        ),
        entity_type="organization",
        entity_id=organization.id,
    )

    blockers = _collect_messages(checks, "blocked")
    warnings = _collect_messages(checks, "warning")
    status = (
        ReadinessStatus.BLOCKED
        if blockers
        else ReadinessStatus.WARNING
        if warnings
        else ReadinessStatus.PASSED
    )
    report = PilotReadinessReport(
        organization_id=campaign.organization_id,
        campaign_id=campaign.id,
        connection_id=campaign.connection_id,
        fingerprint=campaign_fingerprint(campaign),
        status=status,
        checks=checks,
        blockers=blockers,
        warnings=warnings,
        summary={
            "campaign_name": campaign.name,
            "campaign_status": campaign.status.value,
            "destinations": len(active_links),
            "passed_checks": sum(item["status"] == "passed" for item in checks),
            "warning_checks": len(warnings),
            "blocked_checks": len(blockers),
            "telegram_fake_mode": settings.telegram_fake_mode,
            "preflight_status": preflight.status.value,
            "pilot_stage": organization.pilot_stage.value,
            "pilot_stage_enforced": settings.pilot_stage_enforcement_required,
        },
        preflight_report_id=preflight.id,
        created_by_id=created_by.id,
        expires_at=now + timedelta(minutes=settings.readiness_ttl_minutes),
        created_at=now,
    )
    db.add(report)
    db.flush()
    return report


def latest_readiness_report(
    db: Session,
    *,
    campaign: Campaign,
) -> PilotReadinessReport | None:
    """Выполнить операцию latest readiness report. Аргументы интерпретируются в контексте модуля,
    результат возвращается вызывающему коду.
    """
    return db.scalar(
        select(PilotReadinessReport)
        .where(
            PilotReadinessReport.organization_id == campaign.organization_id,
            PilotReadinessReport.campaign_id == campaign.id,
        )
        .order_by(PilotReadinessReport.created_at.desc())
        .limit(1)
    )


def readiness_is_current(
    report: PilotReadinessReport | None,
    campaign: Campaign,
    *,
    now: datetime | None = None,
) -> bool:
    """Выполнить операцию readiness is current. Аргументы интерпретируются в контексте модуля,
    результат возвращается вызывающему коду.
    """
    if report is None or report.status == ReadinessStatus.BLOCKED:
        return False
    expires_at = aware_utc(report.expires_at)
    if expires_at is None or expires_at <= (aware_utc(now) or utcnow()):
        return False
    return report.fingerprint == campaign_fingerprint(campaign)
