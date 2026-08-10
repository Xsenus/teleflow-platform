"""HTTP API for continuity policy, drills, failback and evidence verification."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.dependencies import get_app_settings, get_current_user, get_db, require_roles
from app.enums import UserRole
from app.models import ContinuityDrill, ContinuityDrillEvent, User
from app.schemas import (
    ContinuityDrillCreate,
    ContinuityDrillEventRead,
    ContinuityDrillRead,
    ContinuityOverviewRead,
    ContinuityPolicyPatch,
    ContinuityPolicyRead,
    ContinuitySignoffRequest,
)
from app.services.continuity import (
    ContinuityError,
    cancel_drill,
    continuity_compliance,
    create_drill,
    get_or_create_policy,
    request_failback,
    signoff_drill,
    start_drill,
    synchronize_drill,
    update_policy,
    verify_event_chain,
)

router = APIRouter(prefix="/continuity", tags=["continuity"])


def _conflict(exc: ContinuityError) -> None:
    """Преобразовать domain safety conflicts to an HTTP 409 response."""

    raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/overview", response_model=ContinuityOverviewRead)
def get_continuity_overview(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
):
    """Вернуть current runtime compatibility and signed-evidence compliance."""

    result = continuity_compliance(
        db,
        organization_id=user.organization_id,
        settings=settings,
    )
    db.commit()
    return ContinuityOverviewRead(
        compliant=result.compliant,
        required=result.required,
        blockers=result.blockers,
        warnings=result.warnings,
        policy=result.policy,
        latest_drill=result.latest_drill,
        runtime_snapshot=result.runtime_snapshot,
    )


@router.get("/policy", response_model=ContinuityPolicyRead)
def get_continuity_policy(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
):
    """Прочитать or lazily create the tenant continuity policy."""

    policy = get_or_create_policy(
        db,
        organization_id=user.organization_id,
        settings=settings,
        actor_id=user.id,
    )
    db.commit()
    db.refresh(policy)
    return policy


@router.patch("/policy", response_model=ContinuityPolicyRead)
def patch_continuity_policy(
    payload: ContinuityPolicyPatch,
    user: User = Depends(require_roles(UserRole.OWNER, UserRole.ADMIN)),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
):
    """Изменить the policy and invalidate unfinished drills using old criteria."""

    try:
        policy = update_policy(
            db,
            actor=user,
            settings=settings,
            enabled=payload.enabled,
            require_live_drill=payload.require_live_drill,
            max_rto_seconds=payload.max_rto_seconds,
            evidence_valid_days=payload.evidence_valid_days,
            require_distinct_signoff=payload.require_distinct_signoff,
        )
    except ContinuityError as exc:
        db.rollback()
        _conflict(exc)
    db.commit()
    db.refresh(policy)
    return policy


@router.get("/drills", response_model=list[ContinuityDrillRead])
def list_continuity_drills(
    limit: int = Query(default=100, ge=1, le=500),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
):
    """Перечислить recent drills and project completed failover requests into state."""

    drills = list(
        db.scalars(
            select(ContinuityDrill)
            .where(ContinuityDrill.organization_id == user.organization_id)
            .order_by(ContinuityDrill.created_at.desc())
            .limit(limit)
        ).all()
    )
    for drill in drills:
        synchronize_drill(db, drill=drill, settings=settings, actor=None)
    db.commit()
    return drills


@router.post("/drills", response_model=ContinuityDrillRead, status_code=201)
def create_continuity_drill(
    payload: ContinuityDrillCreate,
    user: User = Depends(require_roles(UserRole.OWNER, UserRole.ADMIN)),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
):
    """Создать a draft tied to the current lease, policy and runtime pair."""

    try:
        drill = create_drill(
            db,
            mode=payload.mode,
            target_site_key=payload.target_site_key,
            actor=user,
            settings=settings,
        )
    except ContinuityError as exc:
        db.rollback()
        _conflict(exc)
    db.commit()
    db.refresh(drill)
    return drill


@router.get("/drills/{drill_id}", response_model=ContinuityDrillRead)
def get_continuity_drill(
    drill_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
):
    """Прочитать one tenant drill and synchronize linked execution requests."""

    drill = db.scalar(
        select(ContinuityDrill).where(
            ContinuityDrill.id == drill_id,
            ContinuityDrill.organization_id == user.organization_id,
        )
    )
    if drill is None:
        raise HTTPException(status_code=404, detail="Continuity drill не найден")
    drill = synchronize_drill(db, drill=drill, settings=settings, actor=None)
    db.commit()
    db.refresh(drill)
    return drill


@router.post("/drills/{drill_id}/start", response_model=ContinuityDrillRead)
def start_continuity_drill(
    drill_id: str,
    user: User = Depends(require_roles(UserRole.OWNER, UserRole.ADMIN)),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
):
    """Выполнить a zero-network simulation or request a live failover."""

    try:
        drill = start_drill(db, drill_id=drill_id, actor=user, settings=settings)
    except ContinuityError as exc:
        db.rollback()
        _conflict(exc)
    db.commit()
    db.refresh(drill)
    return drill


@router.post("/drills/{drill_id}/failback", response_model=ContinuityDrillRead)
def request_continuity_failback(
    drill_id: str,
    user: User = Depends(require_roles(UserRole.OWNER, UserRole.ADMIN)),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
):
    """Запросить the fenced return from standby to the original source site."""

    try:
        drill = request_failback(db, drill_id=drill_id, actor=user, settings=settings)
    except ContinuityError as exc:
        db.rollback()
        _conflict(exc)
    db.commit()
    db.refresh(drill)
    return drill


@router.post("/drills/{drill_id}/signoff", response_model=ContinuityDrillRead)
def signoff_continuity_drill(
    drill_id: str,
    payload: ContinuitySignoffRequest,
    user: User = Depends(require_roles(UserRole.OWNER, UserRole.ADMIN)),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
):
    """Независимо accept or reject cryptographically verified evidence."""

    try:
        drill = signoff_drill(
            db,
            drill_id=drill_id,
            actor=user,
            settings=settings,
            accepted=payload.accepted,
            note=payload.note,
        )
    except ContinuityError as exc:
        db.rollback()
        _conflict(exc)
    db.commit()
    db.refresh(drill)
    return drill


@router.post("/drills/{drill_id}/cancel", response_model=ContinuityDrillRead)
def cancel_continuity_drill(
    drill_id: str,
    user: User = Depends(require_roles(UserRole.OWNER, UserRole.ADMIN)),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
):
    """Отменить an unfinished exercise without leaving active on standby."""

    try:
        drill = cancel_drill(db, drill_id=drill_id, actor=user, settings=settings)
    except ContinuityError as exc:
        db.rollback()
        _conflict(exc)
    db.commit()
    db.refresh(drill)
    return drill


@router.get("/drills/{drill_id}/events", response_model=list[ContinuityDrillEventRead])
def list_continuity_events(
    drill_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Вернуть the ordered append-only history after tenant ownership check."""

    drill = db.scalar(
        select(ContinuityDrill.id).where(
            ContinuityDrill.id == drill_id,
            ContinuityDrill.organization_id == user.organization_id,
        )
    )
    if drill is None:
        raise HTTPException(status_code=404, detail="Continuity drill не найден")
    return list(
        db.scalars(
            select(ContinuityDrillEvent)
            .where(ContinuityDrillEvent.drill_id == drill_id)
            .order_by(ContinuityDrillEvent.sequence)
        ).all()
    )


@router.get("/drills/{drill_id}/verify")
def verify_continuity_history(
    drill_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Пересчитать the tenant drill event chain without changing evidence."""

    drill = db.scalar(
        select(ContinuityDrill.id).where(
            ContinuityDrill.id == drill_id,
            ContinuityDrill.organization_id == user.organization_id,
        )
    )
    if drill is None:
        raise HTTPException(status_code=404, detail="Continuity drill не найден")
    return verify_event_chain(db, drill_id=drill_id)
