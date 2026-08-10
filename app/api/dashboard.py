from __future__ import annotations

from datetime import timedelta

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import get_current_user
from app.enums import (
    CampaignApprovalStatus,
    CampaignStatus,
    CandidateStatus,
    ConnectionStatus,
    ConversationStatus,
    JobStatus,
    NotificationStatus,
    OutboxStatus,
    PermissionStatus,
    SafetySeverity,
)
from app.models import (
    AuditLog,
    Campaign,
    CampaignApprovalRequest,
    CandidateProfile,
    Conversation,
    DeliveryJob,
    Destination,
    Notification,
    Organization,
    OutboxEvent,
    TelegramConnection,
    User,
    WorkerHeartbeat,
    utcnow,
)
from app.schemas import AuditLogRead, DashboardSummary
from app.security import aware_utc

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


@router.get("/summary", response_model=DashboardSummary)
def summary(
    user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> DashboardSummary:
    """Выполнить операцию summary. Аргументы интерпретируются в контексте модуля, результат
    возвращается вызывающему коду.
    """
    org_id = user.organization_id
    now = utcnow()
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    heartbeat = db.scalar(
        select(WorkerHeartbeat).order_by(WorkerHeartbeat.last_seen_at.desc()).limit(1)
    )
    last_seen = aware_utc(heartbeat.last_seen_at) if heartbeat else None
    organization = db.get(Organization, org_id)
    recent = list(
        db.scalars(
            select(AuditLog)
            .where(
                AuditLog.organization_id == org_id,
                AuditLog.severity.in_([SafetySeverity.WARNING, SafetySeverity.CRITICAL]),
            )
            .order_by(AuditLog.created_at.desc())
            .limit(8)
        ).all()
    )
    return DashboardSummary(
        connections_total=db.scalar(
            select(func.count(TelegramConnection.id)).where(
                TelegramConnection.organization_id == org_id
            )
        )
        or 0,
        connections_active=db.scalar(
            select(func.count(TelegramConnection.id)).where(
                TelegramConnection.organization_id == org_id,
                TelegramConnection.status == ConnectionStatus.ACTIVE,
            )
        )
        or 0,
        destinations_total=db.scalar(
            select(func.count(Destination.id)).where(Destination.organization_id == org_id)
        )
        or 0,
        destinations_confirmed=db.scalar(
            select(func.count(Destination.id)).where(
                Destination.organization_id == org_id,
                Destination.permission_status == PermissionStatus.CONFIRMED,
            )
        )
        or 0,
        campaigns_active=db.scalar(
            select(func.count(Campaign.id)).where(
                Campaign.organization_id == org_id,
                Campaign.status.in_([CampaignStatus.SCHEDULED, CampaignStatus.RUNNING]),
            )
        )
        or 0,
        jobs_pending=db.scalar(
            select(func.count(DeliveryJob.id)).where(
                DeliveryJob.organization_id == org_id,
                DeliveryJob.status.in_([JobStatus.PENDING, JobStatus.RETRY]),
            )
        )
        or 0,
        jobs_sent_today=db.scalar(
            select(func.count(DeliveryJob.id)).where(
                DeliveryJob.organization_id == org_id,
                DeliveryJob.status == JobStatus.SENT,
                DeliveryJob.finished_at >= day_start,
            )
        )
        or 0,
        jobs_failed_today=db.scalar(
            select(func.count(DeliveryJob.id)).where(
                DeliveryJob.organization_id == org_id,
                DeliveryJob.status == JobStatus.FAILED,
                DeliveryJob.finished_at >= day_start,
            )
        )
        or 0,
        worker_online=bool(last_seen and last_seen >= now - timedelta(seconds=30)),
        worker_last_seen_at=last_seen,
        conversations_open=db.scalar(
            select(func.count(Conversation.id)).where(
                Conversation.organization_id == org_id,
                Conversation.status.in_(
                    [
                        ConversationStatus.NEW,
                        ConversationStatus.AI_ACTIVE,
                        ConversationStatus.HUMAN_HANDOFF,
                    ]
                ),
            )
        )
        or 0,
        candidates_ready=db.scalar(
            select(func.count(CandidateProfile.id)).where(
                CandidateProfile.organization_id == org_id,
                CandidateProfile.status == CandidateStatus.READY_FOR_REVIEW,
            )
        )
        or 0,
        outbox_pending=db.scalar(
            select(func.count(OutboxEvent.id)).where(
                OutboxEvent.organization_id == org_id,
                OutboxEvent.status.in_([OutboxStatus.PENDING, OutboxStatus.RETRY]),
            )
        )
        or 0,
        publishing_paused=bool(organization and organization.publishing_paused),
        publishing_pause_reason=(organization.publishing_pause_reason if organization else None),
        notifications_unread=db.scalar(
            select(func.count(Notification.id)).where(
                Notification.organization_id == org_id,
                Notification.status == NotificationStatus.UNREAD,
            )
        )
        or 0,
        approvals_pending=db.scalar(
            select(func.count(CampaignApprovalRequest.id)).where(
                CampaignApprovalRequest.organization_id == org_id,
                CampaignApprovalRequest.status == CampaignApprovalStatus.PENDING,
                CampaignApprovalRequest.expires_at > now,
            )
        )
        or 0,
        recent_safety_events=[AuditLogRead.model_validate(item) for item in recent],
    )
