from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit import write_audit
from app.config import Settings
from app.database import get_db
from app.dependencies import get_app_settings, get_current_user, require_roles
from app.enums import ReadinessStatus, SafetySeverity, UserRole
from app.models import CommissioningCheckRun, User
from app.schemas import CommissioningCheckRead
from app.services.commissioning import run_commissioning_checks

router = APIRouter(prefix="/commissioning", tags=["commissioning"])


@router.get("", response_model=list[CommissioningCheckRead])
def list_commissioning_checks(
    limit: int = 50,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[CommissioningCheckRun]:
    """Прочитать commissioning checks. Значение возвращается без несвязанных изменений состояния."""
    return list(
        db.scalars(
            select(CommissioningCheckRun)
            .where(CommissioningCheckRun.organization_id == user.organization_id)
            .order_by(CommissioningCheckRun.created_at.desc())
            .limit(min(max(limit, 1), 200))
        ).all()
    )


@router.get("/latest", response_model=CommissioningCheckRead | None)
def latest_commissioning_check(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> CommissioningCheckRun | None:
    """Выполнить операцию latest commissioning check. Аргументы интерпретируются в контексте
    модуля, результат возвращается вызывающему коду.
    """
    return db.scalar(
        select(CommissioningCheckRun)
        .where(CommissioningCheckRun.organization_id == user.organization_id)
        .order_by(CommissioningCheckRun.created_at.desc())
        .limit(1)
    )


@router.post("/run", response_model=CommissioningCheckRead, status_code=201)
def run_commissioning_check(
    request: Request,
    user: User = Depends(require_roles(UserRole.OWNER, UserRole.ADMIN)),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
) -> CommissioningCheckRun:
    """Выполнить run commissioning check. Операция координирует ограниченные побочные эффекты и
    возвращает детерминированный результат.
    """
    report = run_commissioning_checks(
        db,
        organization_id=user.organization_id,
        created_by=user,
        settings=settings,
        storage=request.app.state.storage,
        lock_manager=request.app.state.lock_manager,
    )
    severity = (
        SafetySeverity.CRITICAL
        if report.status == ReadinessStatus.BLOCKED
        else SafetySeverity.WARNING
        if report.status == ReadinessStatus.WARNING
        else SafetySeverity.INFO
    )
    write_audit(
        db,
        action="commissioning.completed",
        actor=user,
        entity_type="commissioning_check_run",
        entity_id=report.id,
        severity=severity,
        details={
            "status": report.status.value,
            "fingerprint": report.fingerprint,
            "blocker_count": len(report.blockers),
            "warning_count": len(report.warnings),
            "expires_at": report.expires_at.isoformat(),
        },
        request=request,
    )
    db.commit()
    db.refresh(report)
    return report
