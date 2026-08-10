from __future__ import annotations

from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import update
from sqlalchemy.orm import Session

from app.audit import write_audit
from app.config import Settings
from app.database import get_db
from app.dependencies import get_app_settings, get_current_organization, require_roles
from app.enums import JobStatus, SafetySeverity, UserRole
from app.models import DeliveryJob, Organization, User, utcnow
from app.schemas import (
    OrganizationPatch,
    OrganizationRead,
    PublishingPauseRequest,
    PublishingResumeRequest,
)
from app.services.notifications import create_notification
from app.services.outbox import enqueue_event

router = APIRouter(prefix="/organization", tags=["organization"])


@router.get("", response_model=OrganizationRead)
def get_organization(
    organization: Organization = Depends(get_current_organization),
) -> Organization:
    """Прочитать organization. Значение возвращается без несвязанных изменений состояния."""
    return organization


@router.patch("", response_model=OrganizationRead)
def patch_organization(
    payload: OrganizationPatch,
    request: Request,
    user: User = Depends(require_roles(UserRole.OWNER, UserRole.ADMIN)),
    organization: Organization = Depends(get_current_organization),
    db: Session = Depends(get_db),
) -> Organization:
    """Обновить organization. Переход применяется только после проверки его предусловий."""
    values = payload.model_dump(exclude_unset=True)
    if "timezone_name" in values:
        try:
            ZoneInfo(values["timezone_name"])
        except ZoneInfoNotFoundError as exc:
            raise HTTPException(status_code=422, detail="Неизвестный часовой пояс IANA") from exc
    if "high_risk_required_approvals" in values and values["high_risk_required_approvals"] > max(
        1,
        len(
            [
                item
                for item in organization.users
                if item.is_active and item.role in {UserRole.OWNER, UserRole.ADMIN}
            ]
        ),
    ):
        raise HTTPException(
            status_code=422,
            detail="Число обязательных утверждений превышает количество активных владельцев и администраторов",
        )
    for key, value in values.items():
        setattr(organization, key, value)
    write_audit(
        db,
        actor=user,
        action="organization.updated",
        entity_type="organization",
        entity_id=organization.id,
        details={"fields": sorted(values)},
        request=request,
    )
    db.commit()
    db.refresh(organization)
    return organization


@router.post("/publishing/pause", response_model=OrganizationRead)
def pause_publishing(
    payload: PublishingPauseRequest,
    request: Request,
    user: User = Depends(require_roles(UserRole.OWNER, UserRole.ADMIN)),
    organization: Organization = Depends(get_current_organization),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
) -> Organization:
    """Выполнить операцию pause publishing. Аргументы интерпретируются в контексте модуля,
    результат возвращается вызывающему коду.
    """
    now = utcnow()
    organization.publishing_paused = True
    organization.publishing_pause_reason = payload.reason.strip()
    organization.publishing_paused_at = now
    organization.publishing_paused_by_id = user.id

    # Jobs that have not started are held for an explicit resume. A currently
    # processing network request cannot be recalled, which is documented in UI.
    result = db.execute(
        update(DeliveryJob)
        .where(
            DeliveryJob.organization_id == organization.id,
            DeliveryJob.status.in_([JobStatus.PENDING, JobStatus.RETRY]),
        )
        .values(
            status=JobStatus.WAITING_REVIEW,
            error_code="ORG_EMERGENCY_STOP",
            error_message="Публикации приостановлены на уровне организации",
            locked_at=None,
            locked_by=None,
        )
    )
    held_jobs = int(getattr(result, "rowcount", 0) or 0)
    write_audit(
        db,
        actor=user,
        action="safety.organization_publishing_paused",
        entity_type="organization",
        entity_id=organization.id,
        severity=SafetySeverity.CRITICAL,
        details={"reason": payload.reason, "held_jobs": held_jobs},
        request=request,
    )
    create_notification(
        db,
        settings=settings,
        organization_id=organization.id,
        event_type="safety.organization_publishing_paused",
        title="Аварийная остановка публикаций",
        message=f"{user.display_name} остановил все новые публикации. Причина: {payload.reason}",
        severity=SafetySeverity.CRITICAL,
        entity_type="organization",
        entity_id=organization.id,
        dedup_key=f"org-publishing-paused:{organization.id}",
        details={"held_jobs": held_jobs},
    )
    enqueue_event(
        db,
        organization_id=organization.id,
        event_type="publishing.paused",
        aggregate_type="organization",
        aggregate_id=organization.id,
        payload={
            "reason": payload.reason,
            "held_jobs": held_jobs,
            "paused_at": now.isoformat(),
            "paused_by_id": user.id,
        },
    )
    db.commit()
    db.refresh(organization)
    return organization


@router.post("/publishing/resume", response_model=OrganizationRead)
def resume_publishing(
    payload: PublishingResumeRequest,
    request: Request,
    user: User = Depends(require_roles(UserRole.OWNER, UserRole.ADMIN)),
    organization: Organization = Depends(get_current_organization),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
) -> Organization:
    """Выполнить операцию resume publishing. Аргументы интерпретируются в контексте модуля,
    результат возвращается вызывающему коду.
    """
    if not organization.publishing_paused:
        raise HTTPException(status_code=409, detail="Публикации уже разрешены")
    now = utcnow()
    previous_reason = organization.publishing_pause_reason
    organization.publishing_paused = False
    organization.publishing_pause_reason = None
    organization.publishing_paused_at = None
    organization.publishing_paused_by_id = None

    result = db.execute(
        update(DeliveryJob)
        .where(
            DeliveryJob.organization_id == organization.id,
            DeliveryJob.status == JobStatus.WAITING_REVIEW,
            DeliveryJob.error_code == "ORG_EMERGENCY_STOP",
        )
        .values(
            status=JobStatus.PENDING,
            due_at=now,
            error_code=None,
            error_message=None,
            safety_decision=None,
            locked_at=None,
            locked_by=None,
        )
    )
    released_jobs = int(getattr(result, "rowcount", 0) or 0)
    write_audit(
        db,
        actor=user,
        action="safety.organization_publishing_resumed",
        entity_type="organization",
        entity_id=organization.id,
        severity=SafetySeverity.WARNING,
        details={
            "previous_reason": previous_reason,
            "note": payload.note,
            "released_jobs": released_jobs,
        },
        request=request,
    )
    create_notification(
        db,
        settings=settings,
        organization_id=organization.id,
        event_type="safety.organization_publishing_resumed",
        title="Публикации возобновлены",
        message=f"{user.display_name} возобновил очередь публикаций.",
        severity=SafetySeverity.WARNING,
        entity_type="organization",
        entity_id=organization.id,
        dedup_key=f"org-publishing-resumed:{organization.id}",
        details={"released_jobs": released_jobs, "note": payload.note},
    )
    enqueue_event(
        db,
        organization_id=organization.id,
        event_type="publishing.resumed",
        aggregate_type="organization",
        aggregate_id=organization.id,
        payload={
            "note": payload.note,
            "released_jobs": released_jobs,
            "resumed_at": now.isoformat(),
            "resumed_by_id": user.id,
        },
    )
    db.commit()
    db.refresh(organization)
    return organization
