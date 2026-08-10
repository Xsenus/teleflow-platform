from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.enums import UserRole
from app.models import Organization, User


def resolve_organization(db: Session, reference: str | None) -> Organization:
    """Прочитать organization. Значение возвращается без несвязанных изменений состояния."""
    stmt = select(Organization)
    if reference:
        stmt = stmt.where((Organization.id == reference) | (Organization.slug == reference))
    else:
        stmt = stmt.order_by(Organization.created_at).limit(1)
    organization = db.scalar(stmt)
    if organization is None:
        raise SystemExit("Организация не найдена")
    return organization


def resolve_actor(db: Session, organization: Organization, email: str | None) -> User:
    """Прочитать actor. Значение возвращается без несвязанных изменений состояния."""
    stmt = select(User).where(
        User.organization_id == organization.id,
        User.is_active.is_(True),
    )
    if email:
        stmt = stmt.where(User.email == email.strip().lower())
    else:
        stmt = stmt.where(User.role.in_([UserRole.OWNER, UserRole.ADMIN])).order_by(
            User.role, User.created_at
        )
    actor = db.scalar(stmt.limit(1))
    if actor is None:
        raise SystemExit("Активный Owner/Admin для recovery операции не найден")
    return actor
