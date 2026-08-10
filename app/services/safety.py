from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import Settings
from app.enums import (
    CampaignStatus,
    ConnectionKind,
    ConnectionStatus,
    JobStatus,
    PermissionStatus,
)
from app.models import Campaign, DeliveryJob, Destination, Organization, TelegramConnection
from app.security import aware_utc
from app.services.approvals import approval_is_current
from app.services.blackouts import evaluate_blackouts
from app.services.capacity import capacity_dispatch_decision
from app.services.content_guard import find_recent_duplicate
from app.services.destination_validation import validation_is_fresh
from app.services.destination_windows import evaluate_destination_window
from app.services.execution import execution_gate_decision
from app.services.operations import slo_gate_decision
from app.services.pilot_stages import campaign_stage_blocker


@dataclass(frozen=True)
class SafetyDecision:
    allowed: bool
    reason: str | None = None
    code: str | None = None
    defer_until: datetime | None = None
    requires_review: bool = False

    def to_dict(self) -> dict[str, object]:
        """Преобразовать to dict класса SafetyDecision without changing the source object."""
        payload = asdict(self)
        if self.defer_until:
            payload["defer_until"] = self.defer_until.isoformat()
        return payload


def hard_min_interval(connection: TelegramConnection, settings: Settings) -> int:
    """Выполнить операцию hard min interval. Аргументы интерпретируются в контексте модуля,
    результат возвращается вызывающему коду.
    """
    if connection.kind == ConnectionKind.USER:
        return settings.user_hard_min_interval_seconds
    return settings.bot_hard_min_interval_seconds


def validate_campaign(campaign: Campaign, settings: Settings) -> tuple[list[str], list[str]]:
    """Проверить campaign. Некорректные данные или состояние отклоняются до побочного эффекта."""
    blockers: list[str] = []
    warnings: list[str] = []
    connection = campaign.connection

    if connection.status != ConnectionStatus.ACTIVE:
        blockers.append("Telegram-подключение не активно")
    if not campaign.template.is_active:
        blockers.append("Шаблон сообщения отключён")
    if campaign.secondary_template_id:
        if campaign.secondary_template is None:
            blockers.append("Шаблон варианта B не найден")
        elif not campaign.secondary_template.is_active:
            blockers.append("Шаблон варианта B отключён")
        if not 1 <= (campaign.secondary_template_weight or 0) <= 99:
            blockers.append("Некорректный вес варианта B")
    if not campaign.destinations:
        blockers.append("Не выбрано ни одного назначения")

    minimum = max(connection.min_interval_seconds, hard_min_interval(connection, settings))
    if campaign.spacing_seconds < minimum:
        blockers.append(f"Интервал кампании должен быть не меньше {minimum} секунд")
    if connection.daily_cap > settings.global_hard_daily_cap:
        blockers.append("Дневной лимит подключения превышает системный предел")

    active_links = [link for link in campaign.destinations if link.enabled]
    if campaign.destinations and not active_links:
        blockers.append("Все назначения кампании отключены")
    if len(active_links) > connection.daily_cap:
        blockers.append("Количество назначений в одном проходе превышает дневной лимит подключения")

    for link in active_links:
        destination = link.destination
        if destination.connection_id != campaign.connection_id:
            blockers.append(f"Назначение «{destination.title}» относится к другому подключению")
        if not destination.enabled:
            blockers.append(f"Назначение «{destination.title}» отключено")
        if destination.permission_status != PermissionStatus.CONFIRMED:
            blockers.append(f"Для «{destination.title}» не подтверждено разрешение на публикацию")
        if not destination.validated:
            blockers.append(f"«{destination.title}» не прошло проверку через Telegram")
        elif not validation_is_fresh(destination, settings):
            blockers.append(f"Для «{destination.title}» устарела проверка доступа Telegram")
        if not destination.permission_note and not destination.rules_url:
            blockers.append(f"Для «{destination.title}» не зафиксировано основание разрешения")
        permission_expires_at = aware_utc(destination.permission_expires_at)
        if permission_expires_at and permission_expires_at <= datetime.now(UTC):
            blockers.append(f"Для «{destination.title}» истёк срок разрешения")

    if connection.kind == ConnectionKind.USER and not campaign.manual_approval_required:
        blockers.append("Кампании пользовательского аккаунта требуют ручного утверждения")
    if connection.kind == ConnectionKind.USER and connection.destination_cooldown_minutes < 60:
        blockers.append(
            "Для пользовательского аккаунта cooldown назначения не может быть меньше часа"
        )
    if connection.destination_cooldown_minutes < 1:
        blockers.append("Cooldown назначения должен быть положительным")

    return list(dict.fromkeys(blockers)), list(dict.fromkeys(warnings))


def evaluate_delivery(
    db: Session,
    *,
    job: DeliveryJob,
    connection: TelegramConnection,
    destination: Destination,
    campaign: Campaign,
    settings: Settings,
    worker_id: str,
    fence_epoch: int | None,
    now: datetime,
) -> SafetyDecision:
    """Выполнить операцию evaluate delivery. Аргументы интерпретируются в контексте модуля,
    результат возвращается вызывающему коду.
    """
    now = aware_utc(now) or datetime.now(UTC)
    organization = db.get(Organization, job.organization_id)
    if organization is None:
        return SafetyDecision(
            False,
            "Организация задания не найдена",
            "ORGANIZATION_NOT_FOUND",
            requires_review=True,
        )
    if organization.publishing_paused:
        return SafetyDecision(
            False,
            organization.publishing_pause_reason
            or "Публикации приостановлены на уровне организации",
            "ORG_EMERGENCY_STOP",
            requires_review=True,
        )
    if organization.maintenance_mode:
        return SafetyDecision(
            False,
            organization.maintenance_reason or "Платформа находится в режиме обслуживания",
            "ORG_MAINTENANCE_MODE",
            requires_review=True,
        )
    execution_gate = execution_gate_decision(
        db,
        organization_id=job.organization_id,
        settings=settings,
        worker_id=worker_id,
        fence_epoch=fence_epoch,
        now=now,
    )
    if not execution_gate.allowed:
        return SafetyDecision(
            False,
            execution_gate.message,
            execution_gate.code,
            requires_review=execution_gate.code
            not in {"EXECUTION_LEASE_HELD", "EXECUTION_SITE_STANDBY"},
        )
    slo_gate = slo_gate_decision(
        db,
        organization_id=job.organization_id,
        settings=settings,
        gate="publishing",
        now=now,
    )
    if not slo_gate.allowed:
        return SafetyDecision(
            False,
            slo_gate.message,
            "SLO_GATE_BLOCKED",
            requires_review=True,
        )
    capacity_gate = capacity_dispatch_decision(
        db,
        organization_id=job.organization_id,
        settings=settings,
        now=now,
    )
    if not capacity_gate.allowed:
        return SafetyDecision(
            False,
            capacity_gate.message,
            capacity_gate.code,
            defer_until=capacity_gate.defer_until,
            requires_review=False,
        )
    stage_blocker = campaign_stage_blocker(organization, campaign, settings)
    if stage_blocker:
        return SafetyDecision(
            False,
            stage_blocker,
            "PILOT_STAGE_LIMIT",
            requires_review=True,
        )
    if not approval_is_current(campaign):
        return SafetyDecision(
            False,
            "Утверждение кампании отсутствует или не соответствует текущей версии",
            "CAMPAIGN_APPROVAL_STALE",
            requires_review=True,
        )
    if connection.status != ConnectionStatus.ACTIVE:
        return SafetyDecision(
            False,
            "Подключение приостановлено",
            "CONNECTION_NOT_ACTIVE",
            requires_review=True,
        )
    blocked_until = aware_utc(connection.flood_blocked_until)
    if blocked_until and blocked_until > now:
        return SafetyDecision(
            False,
            "Telegram потребовал паузу для подключения",
            "CONNECTION_FLOOD_BLOCKED",
            defer_until=blocked_until,
            requires_review=True,
        )
    if campaign.status not in {CampaignStatus.SCHEDULED, CampaignStatus.RUNNING}:
        return SafetyDecision(False, "Кампания не запущена", "CAMPAIGN_NOT_ACTIVE")
    if not destination.enabled:
        return SafetyDecision(
            False, "Назначение отключено", "DESTINATION_DISABLED", requires_review=True
        )
    if destination.permission_status != PermissionStatus.CONFIRMED:
        return SafetyDecision(
            False,
            "Нет подтверждённого разрешения на публикацию",
            "PERMISSION_NOT_CONFIRMED",
            requires_review=True,
        )
    if not destination.permission_note and not destination.rules_url:
        return SafetyDecision(
            False,
            "Не зафиксировано основание разрешения",
            "PERMISSION_EVIDENCE_MISSING",
            requires_review=True,
        )
    permission_expires_at = aware_utc(destination.permission_expires_at)
    if permission_expires_at and permission_expires_at <= now:
        return SafetyDecision(
            False,
            "Срок подтверждённого разрешения на публикацию истёк",
            "PERMISSION_EXPIRED",
            requires_review=True,
        )
    if not validation_is_fresh(destination, settings, now=now):
        return SafetyDecision(
            False,
            "Проверка доступа к назначению отсутствует или устарела",
            "DESTINATION_VALIDATION_STALE",
            requires_review=True,
        )

    blackout = evaluate_blackouts(
        db,
        organization_id=job.organization_id,
        connection_id=connection.id,
        destination_id=destination.id,
        at=now,
    )
    if blackout.active:
        return SafetyDecision(
            False,
            blackout.reason or "Действует операционный запрет публикаций",
            "PUBLISHING_BLACKOUT",
            defer_until=blackout.defer_until,
            requires_review=False,
        )

    duplicate = find_recent_duplicate(
        db,
        organization_id=job.organization_id,
        destination_id=destination.id,
        fingerprint=job.content_fingerprint,
        lookback_minutes=campaign.duplicate_guard_minutes,
        now=now,
        exclude_job_id=job.id,
    )
    if duplicate:
        return SafetyDecision(
            False,
            "Идентичный снимок сообщения уже отправлялся в эту группу в период duplicate guard",
            "DUPLICATE_CONTENT",
            requires_review=False,
        )

    window = evaluate_destination_window(
        destination,
        fallback_timezone=campaign.timezone_name,
        now=now,
    )
    if not window.allowed:
        return SafetyDecision(
            False,
            f"Публикация отложена до разрешённого локального окна ({window.timezone_name})",
            "DESTINATION_TIME_WINDOW",
            defer_until=window.defer_until,
        )

    next_allowed = aware_utc(destination.next_allowed_at)
    if next_allowed and next_allowed > now:
        return SafetyDecision(
            False,
            "Для группы действует индивидуальный cooldown",
            "DESTINATION_COOLDOWN",
            defer_until=next_allowed,
        )

    minimum = max(connection.min_interval_seconds, hard_min_interval(connection, settings))
    last_delivery = aware_utc(connection.last_delivery_at)
    if last_delivery:
        next_connection_send = last_delivery + timedelta(seconds=minimum)
        if next_connection_send > now:
            return SafetyDecision(
                False,
                "Соблюдается глобальный интервал подключения",
                "CONNECTION_INTERVAL",
                defer_until=next_connection_send,
            )

    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    sent_today = (
        db.scalar(
            select(func.count(DeliveryJob.id)).where(
                DeliveryJob.organization_id == job.organization_id,
                DeliveryJob.connection_id == connection.id,
                DeliveryJob.status == JobStatus.SENT,
                DeliveryJob.finished_at >= day_start,
            )
        )
        or 0
    )
    effective_cap = min(connection.daily_cap, settings.global_hard_daily_cap)
    if sent_today >= effective_cap:
        tomorrow = day_start + timedelta(days=1)
        return SafetyDecision(
            False,
            "Достигнут дневной лимит подключения",
            "DAILY_CAP_REACHED",
            defer_until=tomorrow,
        )

    global_sent_today = (
        db.scalar(
            select(func.count(DeliveryJob.id)).where(
                DeliveryJob.organization_id == job.organization_id,
                DeliveryJob.status == JobStatus.SENT,
                DeliveryJob.finished_at >= day_start,
            )
        )
        or 0
    )
    if global_sent_today >= settings.global_hard_daily_cap:
        return SafetyDecision(
            False,
            "Достигнут системный дневной предел отправок",
            "GLOBAL_DAILY_CAP_REACHED",
            defer_until=day_start + timedelta(days=1),
        )

    return SafetyDecision(True, code="ALLOWED")
