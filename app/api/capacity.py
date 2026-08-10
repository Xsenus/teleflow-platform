from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit import write_audit
from app.config import Settings
from app.database import get_db
from app.dependencies import get_app_settings, get_current_user, require_roles
from app.enums import CapacityAssessmentSource, SafetySeverity, UserRole
from app.models import CapacityAssessment, User
from app.schemas import (
    CapacityAssessmentRead,
    CapacityOverviewRead,
    CapacityPolicyPatch,
    CapacityPolicyRead,
)
from app.services.capacity import (
    CapacityError,
    capacity_admission_decision,
    capacity_assessment_is_current,
    collect_capacity_snapshot,
    create_capacity_assessment,
    get_or_create_capacity_policy,
    latest_capacity_assessment,
    lock_capacity_policy,
    update_capacity_policy,
)

router = APIRouter(prefix="/capacity", tags=["capacity"])


@router.get("/overview", response_model=CapacityOverviewRead)
def get_capacity_overview(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
):
    """Вернуть current queue pressure without admitting or dispatching any Telegram work."""

    policy = get_or_create_capacity_policy(
        db,
        organization_id=user.organization_id,
        settings=settings,
        user=user,
    )
    snapshot = collect_capacity_snapshot(
        db,
        organization_id=user.organization_id,
        policy=policy,
        settings=settings,
    )
    latest = latest_capacity_assessment(db, organization_id=user.organization_id)
    decision = capacity_admission_decision(
        db,
        organization_id=user.organization_id,
        settings=settings,
        incoming_jobs=0,
        incoming_ready_jobs=0,
        incoming_runs=0,
        incoming_connection_id=None,
        incoming_due_span_seconds=0,
        persist_assessment=False,
    )
    db.commit()
    db.refresh(policy)
    return CapacityOverviewRead(
        policy=CapacityPolicyRead.model_validate(policy),
        latest_assessment=(
            CapacityAssessmentRead.model_validate(latest) if latest is not None else None
        ),
        assessment_current=capacity_assessment_is_current(latest, policy),
        admission_allowed=decision.allowed,
        admission_code=decision.code,
        admission_message=decision.message,
        metrics=snapshot.to_dict(),
    )


@router.get("/policy", response_model=CapacityPolicyRead)
def get_capacity_policy(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
):
    """Вернуть the tenant capacity policy, creating safe defaults on first access."""

    policy = get_or_create_capacity_policy(
        db,
        organization_id=user.organization_id,
        settings=settings,
        user=user,
    )
    db.commit()
    db.refresh(policy)
    return policy


@router.patch("/policy", response_model=CapacityPolicyRead)
def patch_capacity_policy(
    payload: CapacityPolicyPatch,
    request: Request,
    user: User = Depends(require_roles(UserRole.OWNER, UserRole.ADMIN)),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
):
    """Обновить capacity limits atomically after validating all cross-field invariants."""

    policy = lock_capacity_policy(
        db,
        organization_id=user.organization_id,
        settings=settings,
    )
    try:
        update_capacity_policy(
            db,
            policy=policy,
            values=payload.model_dump(exclude_unset=True),
            user=user,
            settings=settings,
        )
    except CapacityError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    write_audit(
        db,
        actor=user,
        action="capacity.policy_updated",
        entity_type="capacity_policy",
        entity_id=policy.id,
        severity=SafetySeverity.WARNING,
        details={
            "enabled": policy.enabled,
            "max_active_jobs": policy.max_active_jobs,
            "max_ready_jobs": policy.max_ready_jobs,
            "max_processing_jobs": policy.max_processing_jobs,
            "max_active_runs": policy.max_active_runs,
            "max_jobs_per_run": policy.max_jobs_per_run,
            "max_network_starts_per_minute": (policy.max_network_starts_per_minute),
            "max_network_starts_per_hour": policy.max_network_starts_per_hour,
            "gate_admission": policy.gate_admission,
            "gate_dispatch": policy.gate_dispatch,
        },
        request=request,
    )
    db.commit()
    db.refresh(policy)
    return policy


@router.get("/assessments", response_model=list[CapacityAssessmentRead])
def list_capacity_assessments(
    limit: int = Query(default=100, ge=1, le=500),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Перечислить immutable capacity assessments for the authenticated organization."""

    return list(
        db.scalars(
            select(CapacityAssessment)
            .where(CapacityAssessment.organization_id == user.organization_id)
            .order_by(
                CapacityAssessment.created_at.desc(),
                CapacityAssessment.id.desc(),
            )
            .limit(limit)
        ).all()
    )


@router.post("/assessments", response_model=CapacityAssessmentRead, status_code=201)
def create_manual_capacity_assessment(
    request: Request,
    user: User = Depends(require_roles(UserRole.OWNER, UserRole.ADMIN, UserRole.OPERATOR)),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
):
    """Сохранить an operator-requested snapshot without changing queue state."""

    try:
        item = create_capacity_assessment(
            db,
            organization_id=user.organization_id,
            settings=settings,
            source=CapacityAssessmentSource.MANUAL,
            user=user,
        )
    except CapacityError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    write_audit(
        db,
        actor=user,
        action="capacity.assessment_created",
        entity_type="capacity_assessment",
        entity_id=item.id,
        severity=(SafetySeverity.WARNING if item.blockers else SafetySeverity.INFO),
        details={
            "status": item.status.value,
            "active_jobs": item.active_jobs,
            "ready_jobs": item.ready_jobs,
            "estimated_drain_seconds": item.estimated_drain_seconds,
            "blockers": item.blockers,
        },
        request=request,
    )
    db.commit()
    db.refresh(item)
    return item
