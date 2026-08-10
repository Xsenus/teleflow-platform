from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import Settings
from app.dependencies import get_app_settings, get_current_user, get_db, require_roles
from app.enums import DeliveryAttemptStatus, FailoverRequestStatus, JobStatus, UserRole
from app.models import (
    DeliveryAttempt,
    DeliveryJob,
    ExecutionSite,
    FailoverRequest,
    User,
)
from app.schemas import (
    DeliveryAttemptRead,
    ExecutionOverviewRead,
    ExecutionSiteRead,
    FailoverApprovalRequest,
    FailoverRequestCreate,
    FailoverRequestRead,
)
from app.services.execution import (
    ExecutionError,
    approve_failover,
    cancel_failover,
    get_or_create_execution_lease,
    request_failover,
    site_is_fresh,
    upsert_site_heartbeat,
)

router = APIRouter(prefix="/execution", tags=["execution"])


def _raise_conflict(exc: ExecutionError) -> None:
    """Реализовать внутренний этап raise conflict step. Вспомогательная функция сохраняет
    детерминированность и тестируемость процесса.
    """
    raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/overview", response_model=ExecutionOverviewRead)
def get_execution_overview(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
):
    """Прочитать execution overview. Значение возвращается без несвязанных изменений состояния."""
    now = datetime.now(UTC)
    upsert_site_heartbeat(
        db,
        organization_id=user.organization_id,
        settings=settings,
        worker_id=f"api:{settings.execution_site_key}",
        details={"api": True},
        now=now,
    )
    lease = get_or_create_execution_lease(
        db,
        organization_id=user.organization_id,
        settings=settings,
        now=now,
    )
    sites = list(
        db.scalars(
            select(ExecutionSite)
            .where(ExecutionSite.organization_id == user.organization_id)
            .order_by(ExecutionSite.site_key)
        ).all()
    )
    open_failover = db.scalar(
        select(FailoverRequest)
        .where(
            FailoverRequest.organization_id == user.organization_id,
            FailoverRequest.status == FailoverRequestStatus.REQUESTED,
        )
        .order_by(FailoverRequest.created_at.desc())
        .limit(1)
    )
    processing_jobs = int(
        db.scalar(
            select(func.count(DeliveryJob.id)).where(
                DeliveryJob.organization_id == user.organization_id,
                DeliveryJob.status == JobStatus.PROCESSING,
            )
        )
        or 0
    )
    uncertain_jobs = int(
        db.scalar(
            select(func.count(DeliveryJob.id)).where(
                DeliveryJob.organization_id == user.organization_id,
                DeliveryJob.status == JobStatus.WAITING_REVIEW,
                DeliveryJob.error_code.in_(
                    ["DELIVERY_RESULT_UNCERTAIN", "WORKER_CRASH_DURING_SEND"]
                ),
            )
        )
        or 0
    )
    active_attempts = int(
        db.scalar(
            select(func.count(DeliveryAttempt.id)).where(
                DeliveryAttempt.organization_id == user.organization_id,
                DeliveryAttempt.status == DeliveryAttemptStatus.NETWORK_STARTED,
            )
        )
        or 0
    )
    site_payloads = [
        ExecutionSiteRead.model_validate(site).model_copy(
            update={
                "online": site_is_fresh(site, settings, now=now),
                "is_active_site": site.site_key == lease.active_site_key,
            }
        )
        for site in sites
    ]
    db.commit()
    db.refresh(lease)
    return ExecutionOverviewRead(
        fencing_required=settings.execution_fencing_required,
        current_site_key=settings.execution_site_key,
        primary_site_key=settings.execution_primary_site_key,
        lease=lease,
        sites=site_payloads,
        open_failover=open_failover,
        processing_jobs=processing_jobs,
        uncertain_jobs=uncertain_jobs,
        active_attempts=active_attempts,
    )


@router.get("/sites", response_model=list[ExecutionSiteRead])
def list_execution_sites(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
):
    """Прочитать execution sites. Значение возвращается без несвязанных изменений состояния."""
    now = datetime.now(UTC)
    upsert_site_heartbeat(
        db,
        organization_id=user.organization_id,
        settings=settings,
        worker_id=f"api:{settings.execution_site_key}",
        details={"api": True},
        now=now,
    )
    lease = get_or_create_execution_lease(
        db,
        organization_id=user.organization_id,
        settings=settings,
        now=now,
    )
    sites = list(
        db.scalars(
            select(ExecutionSite)
            .where(ExecutionSite.organization_id == user.organization_id)
            .order_by(ExecutionSite.site_key)
        ).all()
    )
    db.commit()
    return [
        ExecutionSiteRead.model_validate(site).model_copy(
            update={
                "online": site_is_fresh(site, settings, now=now),
                "is_active_site": site.site_key == lease.active_site_key,
            }
        )
        for site in sites
    ]


@router.get("/failovers", response_model=list[FailoverRequestRead])
def list_failover_requests(
    limit: int = Query(default=100, ge=1, le=500),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Прочитать failover requests. Значение возвращается без несвязанных изменений состояния."""
    return list(
        db.scalars(
            select(FailoverRequest)
            .where(FailoverRequest.organization_id == user.organization_id)
            .order_by(FailoverRequest.created_at.desc())
            .limit(limit)
        ).all()
    )


@router.post("/failovers", response_model=FailoverRequestRead, status_code=201)
def create_failover_request(
    payload: FailoverRequestCreate,
    user: User = Depends(require_roles(UserRole.OWNER, UserRole.ADMIN)),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
):
    """Создать failover request. Перед сохранением или возвратом нового значения проверяются
    связанные инварианты.
    """
    try:
        item = request_failover(
            db,
            organization_id=user.organization_id,
            target_site_key=payload.target_site_key,
            reason=payload.reason,
            actor=user,
            settings=settings,
        )
    except ExecutionError as exc:
        _raise_conflict(exc)
    db.commit()
    db.refresh(item)
    return item


@router.post("/failovers/{request_id}/approve", response_model=FailoverRequestRead)
def execute_failover_request(
    request_id: str,
    payload: FailoverApprovalRequest,
    user: User = Depends(require_roles(UserRole.OWNER, UserRole.ADMIN)),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
):
    """Выполнить execute failover request. Операция координирует ограниченные побочные эффекты и
    возвращает детерминированный результат.
    """
    try:
        item = approve_failover(
            db,
            request_id=request_id,
            confirmation=payload.confirmation,
            actor=user,
            settings=settings,
        )
    except ExecutionError as exc:
        # approve_failover may persist a blocker snapshot for operator
        # diagnostics before refusing the switch. No other failure path mutates
        # state, so commit only when SQLAlchemy reports deliberate changes.
        if db.dirty:
            db.commit()
        else:
            db.rollback()
        _raise_conflict(exc)
    db.commit()
    db.refresh(item)
    return item


@router.post("/failovers/{request_id}/cancel", response_model=FailoverRequestRead)
def cancel_failover_request(
    request_id: str,
    user: User = Depends(require_roles(UserRole.OWNER, UserRole.ADMIN)),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
):
    """Безопасно выполнить cancel failover request. Зависимое состояние и видимые в аудите
    последствия обрабатываются согласованно.
    """
    try:
        item = cancel_failover(
            db,
            request_id=request_id,
            actor=user,
            settings=settings,
        )
    except ExecutionError as exc:
        db.rollback()
        _raise_conflict(exc)
    db.commit()
    db.refresh(item)
    return item


@router.get("/delivery-attempts", response_model=list[DeliveryAttemptRead])
def list_delivery_attempts(
    job_id: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Прочитать delivery attempts. Значение возвращается без несвязанных изменений состояния."""
    stmt = select(DeliveryAttempt).where(DeliveryAttempt.organization_id == user.organization_id)
    if job_id:
        stmt = stmt.where(DeliveryAttempt.job_id == job_id)
    return list(db.scalars(stmt.order_by(DeliveryAttempt.created_at.desc()).limit(limit)).all())
