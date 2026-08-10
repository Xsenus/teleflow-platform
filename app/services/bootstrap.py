from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.audit import SYSTEM_CHAIN_KEY, ZERO_HASH
from app.config import Settings
from app.enums import OrganizationStatus, PilotStage, UserRole
from app.models import AuditChainState, Organization, User
from app.security import hash_password

logger = logging.getLogger(__name__)


def bootstrap_admin(session_factory: sessionmaker[Session], settings: Settings) -> None:
    """Выполнить операцию bootstrap admin. Аргументы интерпретируются в контексте модуля, результат
    возвращается вызывающему коду.
    """
    with session_factory() as db:
        organization = db.scalar(
            select(Organization).where(Organization.slug == settings.bootstrap_organization_slug)
        )
        if organization is None:
            organization = Organization(
                name=settings.bootstrap_organization_name,
                slug=settings.bootstrap_organization_slug,
                status=OrganizationStatus.ACTIVE,
                timezone_name="Europe/Helsinki",
                retention_days=settings.default_retention_days,
                ai_enabled=False,
                pilot_stage=PilotStage.LOCAL,
                require_distinct_campaign_approver=(
                    settings.default_require_distinct_campaign_approver
                ),
                high_risk_destination_threshold=(settings.default_high_risk_destination_threshold),
                high_risk_required_approvals=(settings.default_high_risk_required_approvals),
                approval_request_ttl_hours=(settings.default_approval_request_ttl_hours),
            )
            db.add(organization)
            db.flush()

        if db.get(AuditChainState, organization.id) is None:
            db.add(
                AuditChainState(
                    chain_key=organization.id,
                    organization_id=organization.id,
                    last_sequence=0,
                    last_hash=ZERO_HASH,
                )
            )
            db.flush()

        if db.get(AuditChainState, SYSTEM_CHAIN_KEY) is None:
            db.add(
                AuditChainState(
                    chain_key=SYSTEM_CHAIN_KEY,
                    organization_id=None,
                    last_sequence=0,
                    last_hash=ZERO_HASH,
                )
            )
            db.flush()

        exists = db.scalar(select(User.id).limit(1))
        if exists:
            db.commit()
            return
        user = User(
            organization_id=organization.id,
            email=settings.bootstrap_admin_email.lower(),
            display_name=settings.bootstrap_admin_name,
            password_hash=hash_password(settings.bootstrap_admin_password, settings),
            role=UserRole.OWNER,
            is_active=True,
            must_change_password=True,
        )
        db.add(user)
        db.commit()
        logger.warning(
            "Создан bootstrap-владелец %s в организации %s. Сразу смените пароль и включите 2FA.",
            settings.bootstrap_admin_email,
            organization.slug,
        )
