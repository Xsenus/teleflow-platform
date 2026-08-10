from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.enums import PermissionStatus, PreflightStatus, RolloutMode
from app.models import Campaign, CampaignPreflightReport, Organization, User, utcnow
from app.security import aware_utc
from app.services.approvals import campaign_fingerprint
from app.services.blackouts import evaluate_blackouts
from app.services.campaign_variants import select_campaign_template
from app.services.content_guard import content_fingerprint, find_recent_duplicate
from app.services.destination_validation import (
    effective_validation_expiry,
    validation_is_fresh,
)
from app.services.destination_windows import evaluate_destination_window
from app.services.pilot_stages import campaign_stage_blocker
from app.services.safety import validate_campaign
from app.services.storage import StorageService


def _estimated_start(campaign: Campaign, now: datetime) -> datetime:
    """Реализовать внутренний этап estimated start step. Вспомогательная функция сохраняет
    детерминированность и тестируемость процесса.
    """
    candidate = aware_utc(campaign.next_run_at) or aware_utc(campaign.schedule_at) or now
    return max(candidate, now)


def _batch_number(campaign: Campaign, position: int) -> int:
    """Реализовать внутренний этап batch number step. Вспомогательная функция сохраняет
    детерминированность и тестируемость процесса.
    """
    if campaign.rollout_mode != RolloutMode.STAGED:
        return 1
    return (position // max(1, campaign.rollout_batch_size)) + 1


def _append_unique(target: list[str], value: str) -> None:
    """Реализовать внутренний этап append unique step. Вспомогательная функция сохраняет
    детерминированность и тестируемость процесса.
    """
    if value not in target:
        target.append(value)


def create_preflight_report(
    db: Session,
    *,
    campaign: Campaign,
    created_by: User,
    settings: Settings,
    storage: StorageService | None = None,
    now: datetime | None = None,
) -> CampaignPreflightReport:
    """Сохранить a reviewable snapshot before campaign approval. Preflight is intentionally
    conservative: it checks only deterministic local state and previously observed delivery
    history. Telegram permissions are still re-evaluated by the gateway/Safety Engine at
    delivery time.
    """

    now = aware_utc(now) or utcnow()
    storage = storage or StorageService(settings)
    blockers, warnings = validate_campaign(campaign, settings)
    organization = db.get(Organization, campaign.organization_id)
    if organization is None:
        blockers.append("Организация кампании не найдена")
    else:
        stage_blocker = campaign_stage_blocker(organization, campaign, settings)
        if stage_blocker:
            blockers.append(stage_blocker)
    global_blockers = list(blockers)
    global_warnings = list(warnings)
    items: list[dict[str, Any]] = []
    start = _estimated_start(campaign, now)
    active_links = [
        link
        for link in sorted(campaign.destinations, key=lambda item: (item.position, item.id))
        if link.enabled
    ]

    for position, link in enumerate(active_links):
        destination = link.destination
        template = select_campaign_template(campaign, destination.id)
        body = link.custom_body or template.body
        media_sha = template.media_asset.sha256 if template.media_asset else None
        fingerprint = content_fingerprint(
            body=body,
            parse_mode=template.parse_mode,
            media_sha256=media_sha,
            link_preview=template.link_preview,
        )
        due_at = start + timedelta(seconds=position * campaign.spacing_seconds)
        item_blockers: list[str] = []
        item_warnings: list[str] = []

        if not destination.enabled:
            item_blockers.append("Назначение отключено")
        if destination.permission_status != PermissionStatus.CONFIRMED:
            item_blockers.append("Разрешение на публикацию не подтверждено")
        if not destination.permission_note and not destination.rules_url:
            item_blockers.append("Отсутствует основание разрешения")
        validation_expires_at = effective_validation_expiry(destination, settings)
        if not destination.validated:
            item_blockers.append("Назначение не прошло проверку через Telegram")
        elif not validation_is_fresh(destination, settings, now=now):
            item_blockers.append("Проверка доступа Telegram устарела")

        expires_at = aware_utc(destination.permission_expires_at)
        if expires_at:
            if expires_at <= now:
                item_blockers.append("Срок подтверждённого разрешения истёк")
            elif expires_at <= now + timedelta(days=settings.permission_expiry_warning_days):
                item_warnings.append(
                    f"Разрешение истекает {expires_at.astimezone(UTC).isoformat()}"
                )

        window = evaluate_destination_window(
            destination,
            fallback_timezone=campaign.timezone_name,
            now=due_at,
        )
        if not window.allowed:
            item_warnings.append("Расчётное время будет перенесено в разрешённое локальное окно")
        blackout = evaluate_blackouts(
            db,
            organization_id=campaign.organization_id,
            connection_id=campaign.connection_id,
            destination_id=destination.id,
            at=due_at,
        )
        if blackout.active:
            item_warnings.append("Расчётное время попадает в операционный запрет публикаций")
        next_allowed = aware_utc(destination.next_allowed_at)
        if next_allowed and next_allowed > due_at:
            item_warnings.append(
                f"Действует cooldown до {next_allowed.astimezone(UTC).isoformat()}"
            )

        duplicate = find_recent_duplicate(
            db,
            organization_id=campaign.organization_id,
            destination_id=destination.id,
            fingerprint=fingerprint,
            lookback_minutes=campaign.duplicate_guard_minutes,
            now=now,
        )
        if duplicate:
            item_blockers.append(
                "Такой же снимок сообщения уже отправлялся в период duplicate guard"
            )

        if template.media_asset:
            key = template.media_asset.storage_key or template.media_asset.relative_path
            if not storage.exists(key):
                item_blockers.append("Медиафайл отсутствует в хранилище")

        for text in item_blockers:
            _append_unique(global_blockers, f"{destination.title}: {text}")
        for text in item_warnings:
            _append_unique(global_warnings, f"{destination.title}: {text}")

        items.append(
            {
                "destination_id": destination.id,
                "destination_title": destination.title,
                "due_at": due_at.isoformat(),
                "batch_number": _batch_number(campaign, position),
                "content_fingerprint": fingerprint,
                "blockers": item_blockers,
                "warnings": item_warnings,
                "permission_expires_at": expires_at.isoformat() if expires_at else None,
                "validation_expires_at": (
                    validation_expires_at.isoformat() if validation_expires_at else None
                ),
                "blackout_until": (
                    blackout.defer_until.isoformat() if blackout.defer_until else None
                ),
                "duplicate_job_id": duplicate.id if duplicate else None,
            }
        )

    if global_blockers:
        status = PreflightStatus.BLOCKED
    elif global_warnings:
        status = PreflightStatus.WARNING
    else:
        status = PreflightStatus.PASSED

    total_batches = (
        math.ceil(len(active_links) / max(1, campaign.rollout_batch_size))
        if campaign.rollout_mode == RolloutMode.STAGED and active_links
        else 1
    )
    report = CampaignPreflightReport(
        organization_id=campaign.organization_id,
        campaign_id=campaign.id,
        fingerprint=campaign_fingerprint(campaign),
        status=status,
        blockers=global_blockers,
        warnings=global_warnings,
        items=items,
        summary={
            "destinations": len(active_links),
            "blocked_destinations": sum(bool(item["blockers"]) for item in items),
            "warning_destinations": sum(bool(item["warnings"]) for item in items),
            "rollout_mode": campaign.rollout_mode.value,
            "total_batches": total_batches,
            "duplicate_guard_minutes": campaign.duplicate_guard_minutes,
            "pilot_stage": organization.pilot_stage.value if organization else None,
            "pilot_stage_enforced": settings.pilot_stage_enforcement_required,
        },
        created_by_id=created_by.id,
        created_at=now,
        expires_at=now + timedelta(minutes=settings.preflight_ttl_minutes),
    )
    db.add(report)
    db.flush()
    return report


def latest_preflight_report(
    db: Session,
    *,
    campaign: Campaign,
) -> CampaignPreflightReport | None:
    """Выполнить операцию latest preflight report. Аргументы интерпретируются в контексте модуля,
    результат возвращается вызывающему коду.
    """
    return db.scalar(
        select(CampaignPreflightReport)
        .where(
            CampaignPreflightReport.organization_id == campaign.organization_id,
            CampaignPreflightReport.campaign_id == campaign.id,
        )
        .order_by(CampaignPreflightReport.created_at.desc())
        .limit(1)
    )


def preflight_is_current(report: CampaignPreflightReport | None, campaign: Campaign) -> bool:
    """Выполнить операцию preflight is current. Аргументы интерпретируются в контексте модуля,
    результат возвращается вызывающему коду.
    """
    if report is None:
        return False
    if report.status not in {PreflightStatus.PASSED, PreflightStatus.WARNING}:
        return False
    expires_at = aware_utc(report.expires_at)
    if expires_at is None or expires_at <= utcnow():
        return False
    return report.fingerprint == campaign_fingerprint(campaign)
