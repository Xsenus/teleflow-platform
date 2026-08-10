from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit import verify_audit_chain
from app.database import get_db
from app.dependencies import require_roles
from app.enums import SafetySeverity, UserRole
from app.models import AuditLog, User
from app.schemas import AuditLogRead, AuditVerificationRead

router = APIRouter(prefix="/audit", tags=["audit"])


@router.get("/verify", response_model=AuditVerificationRead)
def verify_chain(
    admin: User = Depends(require_roles(UserRole.OWNER, UserRole.ADMIN)),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    """Проверить chain. Некорректные данные или состояние отклоняются до побочного эффекта."""
    return verify_audit_chain(db, organization_id=admin.organization_id).to_dict()


@router.get("", response_model=list[AuditLogRead])
def list_audit_logs(
    severity: SafetySeverity | None = None,
    action: str | None = None,
    entity_id: str | None = None,
    limit: int = 200,
    admin: User = Depends(require_roles(UserRole.OWNER, UserRole.ADMIN)),
    db: Session = Depends(get_db),
) -> list[AuditLog]:
    """Прочитать audit logs. Значение возвращается без несвязанных изменений состояния."""
    limit = min(max(limit, 1), 1000)
    stmt = (
        select(AuditLog)
        .where(AuditLog.organization_id == admin.organization_id)
        .order_by(AuditLog.created_at.desc())
        .limit(limit)
    )
    if severity:
        stmt = stmt.where(AuditLog.severity == severity)
    if action:
        stmt = stmt.where(AuditLog.action == action)
    if entity_id:
        stmt = stmt.where(AuditLog.entity_id == entity_id)
    return list(db.scalars(stmt).all())
