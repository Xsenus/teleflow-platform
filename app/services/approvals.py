from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.enums import (
    ApprovalDecision,
    CampaignApprovalStatus,
    CampaignStatus,
)
from app.models import (
    Campaign,
    CampaignApprovalDecision,
    CampaignApprovalRequest,
    Organization,
    User,
    utcnow,
)
from app.security import aware_utc


def _hash_text(value: str | None) -> str | None:
    """Вычислить hash text. Канонический ввод обеспечивает детерминированное сравнение целостности
    между процессами.
    """
    if value is None:
        return None
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _iso(value: object) -> str | None:
    """Реализовать внутренний этап iso step. Вспомогательная функция сохраняет детерминированность
    и тестируемость процесса.
    """
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        return str(value.isoformat())
    return str(value)


def campaign_fingerprint(campaign: Campaign) -> str:
    """Создать a stable digest of every field that an approver authorizes."""

    links: list[dict[str, Any]] = []
    for link in sorted(campaign.destinations, key=lambda item: (item.position, item.id)):
        destination = link.destination
        links.append(
            {
                "link_id": link.id,
                "position": link.position,
                "enabled": link.enabled,
                "custom_body_sha256": _hash_text(link.custom_body),
                "destination": {
                    "id": destination.id,
                    "connection_id": destination.connection_id,
                    "telegram_chat_id": destination.telegram_chat_id,
                    "username": destination.username,
                    "topic_id": destination.topic_id,
                    "kind": destination.kind.value,
                    "enabled": destination.enabled,
                    "validated": destination.validated,
                    "permission_status": destination.permission_status.value,
                    "permission_note_sha256": _hash_text(destination.permission_note),
                    "rules_url": destination.rules_url,
                    "permission_confirmed_at": _iso(destination.permission_confirmed_at),
                    "permission_reviewed_at": _iso(destination.permission_reviewed_at),
                    "permission_expires_at": _iso(destination.permission_expires_at),
                    "timezone_name": destination.timezone_name,
                    "allowed_weekdays": destination.allowed_weekdays,
                    "allowed_start_time": _iso(destination.allowed_start_time),
                    "allowed_end_time": _iso(destination.allowed_end_time),
                    "cooldown_minutes_override": destination.cooldown_minutes_override,
                },
            }
        )

    template = campaign.template
    secondary = campaign.secondary_template
    payload = {
        "campaign": {
            "id": campaign.id,
            "name": campaign.name,
            "connection_id": campaign.connection_id,
            "template_id": campaign.template_id,
            "secondary_template_id": campaign.secondary_template_id,
            "secondary_template_weight": campaign.secondary_template_weight,
            "schedule_type": campaign.schedule_type.value,
            "schedule_at": _iso(aware_utc(campaign.schedule_at)),
            "timezone_name": campaign.timezone_name,
            "weekdays": campaign.weekdays,
            "spacing_seconds": campaign.spacing_seconds,
            "rollout_mode": campaign.rollout_mode.value,
            "rollout_batch_size": campaign.rollout_batch_size,
            "rollout_pause_seconds": campaign.rollout_pause_seconds,
            "rollout_require_checkpoint": campaign.rollout_require_checkpoint,
            "rollout_failure_threshold_percent": campaign.rollout_failure_threshold_percent,
            "duplicate_guard_minutes": campaign.duplicate_guard_minutes,
            "end_at": _iso(aware_utc(campaign.end_at)),
            "manual_approval_required": campaign.manual_approval_required,
            "notes_sha256": _hash_text(campaign.notes),
        },
        "connection": {
            "id": campaign.connection.id,
            "kind": campaign.connection.kind.value,
            "min_interval_seconds": campaign.connection.min_interval_seconds,
            "daily_cap": campaign.connection.daily_cap,
            "destination_cooldown_minutes": campaign.connection.destination_cooldown_minutes,
            "require_manual_approval": campaign.connection.require_manual_approval,
            "stop_on_flood": campaign.connection.stop_on_flood,
        },
        "template_a": {
            "id": template.id,
            "revision": template.revision,
            "body_sha256": _hash_text(template.body),
            "parse_mode": template.parse_mode.value,
            "media_asset_id": template.media_asset_id,
            "link_preview": template.link_preview,
            "is_active": template.is_active,
        },
        "template_b": None
        if secondary is None
        else {
            "id": secondary.id,
            "revision": secondary.revision,
            "body_sha256": _hash_text(secondary.body),
            "parse_mode": secondary.parse_mode.value,
            "media_asset_id": secondary.media_asset_id,
            "link_preview": secondary.link_preview,
            "is_active": secondary.is_active,
        },
        "destinations": links,
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ApprovalPolicy:
    required_approvals: int
    require_distinct_requester: bool
    high_risk: bool


def resolve_approval_policy(campaign: Campaign, organization: Organization) -> ApprovalPolicy:
    """Прочитать approval policy. Значение возвращается без несвязанных изменений состояния."""
    destination_count = len(
        [link for link in campaign.destinations if link.enabled and link.destination.enabled]
    )
    threshold = max(1, int(organization.high_risk_destination_threshold or 1))
    high_risk = destination_count >= threshold
    required = max(1, int(organization.high_risk_required_approvals or 1)) if high_risk else 1
    return ApprovalPolicy(
        required_approvals=required,
        require_distinct_requester=bool(organization.require_distinct_campaign_approver),
        high_risk=high_risk,
    )


def invalidate_campaign_approval(
    db: Session,
    campaign: Campaign,
    *,
    reason: str,
) -> None:
    """Выполнить операцию invalidate campaign approval. Аргументы интерпретируются в контексте
    модуля, результат возвращается вызывающему коду.
    """
    now = utcnow()
    pending = list(
        db.scalars(
            select(CampaignApprovalRequest).where(
                CampaignApprovalRequest.campaign_id == campaign.id,
                CampaignApprovalRequest.organization_id == campaign.organization_id,
                CampaignApprovalRequest.status == CampaignApprovalStatus.PENDING,
            )
        ).all()
    )
    for item in pending:
        item.status = CampaignApprovalStatus.CANCELLED
        item.completed_at = now
        if not item.request_note:
            item.request_note = f"Отменено системой: {reason}"[:4000]
    campaign.approved_at = None
    campaign.approved_by_id = None
    campaign.approved_fingerprint = None
    campaign.active_approval_request_id = None
    campaign.next_run_at = None


def expire_approval_request(
    request: CampaignApprovalRequest,
    campaign: Campaign | None = None,
) -> bool:
    """Выполнить операцию expire approval request. Аргументы интерпретируются в контексте модуля,
    результат возвращается вызывающему коду.
    """
    now = utcnow()
    if request.status != CampaignApprovalStatus.PENDING:
        return False
    expires_at = aware_utc(request.expires_at)
    if expires_at and expires_at > now:
        return False
    request.status = CampaignApprovalStatus.EXPIRED
    request.completed_at = now
    if campaign and campaign.active_approval_request_id == request.id:
        campaign.active_approval_request_id = None
        campaign.approved_at = None
        campaign.approved_by_id = None
        campaign.approved_fingerprint = None
        campaign.next_run_at = None
        if campaign.status not in {
            CampaignStatus.CANCELLED,
            CampaignStatus.COMPLETED,
            CampaignStatus.FAILED,
        }:
            campaign.status = CampaignStatus.DRAFT
    return True


def submit_approval_request(
    db: Session,
    *,
    campaign: Campaign,
    organization: Organization,
    requested_by: User,
    note: str | None = None,
) -> CampaignApprovalRequest:
    """Выполнить операцию submit approval request. Аргументы интерпретируются в контексте модуля,
    результат возвращается вызывающему коду.
    """
    invalidate_campaign_approval(db, campaign, reason="создан новый запрос на утверждение")
    policy = resolve_approval_policy(campaign, organization)
    now = utcnow()
    fingerprint = campaign_fingerprint(campaign)
    item = CampaignApprovalRequest(
        organization_id=campaign.organization_id,
        campaign_id=campaign.id,
        fingerprint=fingerprint,
        status=CampaignApprovalStatus.PENDING,
        required_approvals=policy.required_approvals,
        require_distinct_requester=policy.require_distinct_requester,
        requested_by_id=requested_by.id,
        request_note=(note or None),
        expires_at=now + timedelta(hours=max(1, organization.approval_request_ttl_hours)),
        created_at=now,
    )
    db.add(item)
    db.flush()
    campaign.active_approval_request_id = item.id
    campaign.status = CampaignStatus.DRAFT
    return item


def decide_approval_request(
    db: Session,
    *,
    request: CampaignApprovalRequest,
    campaign: Campaign,
    user: User,
    decision: ApprovalDecision,
    note: str | None = None,
) -> tuple[CampaignApprovalRequest, CampaignApprovalDecision]:
    """Выполнить операцию decide approval request. Аргументы интерпретируются в контексте модуля,
    результат возвращается вызывающему коду.
    """
    if expire_approval_request(request, campaign):
        raise HTTPException(status_code=409, detail="Срок запроса на утверждение истёк")
    if request.status != CampaignApprovalStatus.PENDING:
        raise HTTPException(status_code=409, detail="Запрос уже завершён")
    if campaign.active_approval_request_id != request.id:
        raise HTTPException(status_code=409, detail="Запрос больше не является активным")
    current_fingerprint = campaign_fingerprint(campaign)
    if current_fingerprint != request.fingerprint:
        request.status = CampaignApprovalStatus.CANCELLED
        request.completed_at = utcnow()
        campaign.active_approval_request_id = None
        campaign.status = CampaignStatus.DRAFT
        raise HTTPException(
            status_code=409,
            detail="Кампания изменилась после отправки на утверждение; создайте новый запрос",
        )
    if request.require_distinct_requester and request.requested_by_id == user.id:
        raise HTTPException(
            status_code=409,
            detail="Автор запроса не может утвердить собственную кампанию",
        )
    exists = db.scalar(
        select(CampaignApprovalDecision.id).where(
            CampaignApprovalDecision.request_id == request.id,
            CampaignApprovalDecision.user_id == user.id,
        )
    )
    if exists:
        raise HTTPException(status_code=409, detail="Вы уже приняли решение по этому запросу")

    now = utcnow()
    item = CampaignApprovalDecision(
        organization_id=request.organization_id,
        request_id=request.id,
        user_id=user.id,
        decision=decision,
        note=note,
        created_at=now,
    )
    db.add(item)
    db.flush()

    if decision == ApprovalDecision.REJECT:
        request.status = CampaignApprovalStatus.REJECTED
        request.completed_at = now
        campaign.active_approval_request_id = None
        campaign.approved_at = None
        campaign.approved_by_id = None
        campaign.approved_fingerprint = None
        campaign.next_run_at = None
        campaign.status = CampaignStatus.DRAFT
        return request, item

    approvals = (
        db.scalar(
            select(func.count(CampaignApprovalDecision.id)).where(
                CampaignApprovalDecision.request_id == request.id,
                CampaignApprovalDecision.decision == ApprovalDecision.APPROVE,
            )
        )
        or 0
    )
    if approvals >= request.required_approvals:
        request.status = CampaignApprovalStatus.APPROVED
        request.completed_at = now
        campaign.approved_at = now
        campaign.approved_by_id = user.id
        campaign.approved_fingerprint = request.fingerprint
        campaign.status = CampaignStatus.SCHEDULED
        schedule_at = aware_utc(campaign.schedule_at) or now
        campaign.next_run_at = max(schedule_at, now)
    return request, item


def approval_is_current(campaign: Campaign) -> bool:
    """Выполнить операцию approval is current. Аргументы интерпретируются в контексте модуля,
    результат возвращается вызывающему коду.
    """
    if not campaign.approved_at or not campaign.approved_fingerprint:
        return False
    return campaign.approved_fingerprint == campaign_fingerprint(campaign)
