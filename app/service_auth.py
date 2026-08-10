from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass

from fastapi import Depends, Header, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.enums import ApiKeyStatus, OrganizationStatus
from app.models import ApiKey, Organization, utcnow
from app.security import aware_utc


@dataclass(frozen=True, slots=True)
class ServicePrincipal:
    organization_id: str
    api_key_id: str
    name: str
    scopes: frozenset[str]


def hash_api_key(secret: str) -> str:
    """Вычислить hash api key. Канонический ввод обеспечивает детерминированное сравнение
    целостности между процессами.
    """
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


def get_service_principal(
    request: Request,
    authorization: str | None = Header(default=None),
    x_api_key: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> ServicePrincipal:
    """Прочитать service principal. Значение возвращается без несвязанных изменений состояния."""
    secret = x_api_key
    if not secret and authorization and authorization.lower().startswith("bearer "):
        secret = authorization.split(" ", 1)[1].strip()
    if not secret or not secret.startswith("tfp_"):
        raise HTTPException(status_code=401, detail="API key отсутствует или некорректен")
    prefix = secret[:16]
    candidates = list(
        db.scalars(
            select(ApiKey).where(
                ApiKey.key_prefix == prefix,
                ApiKey.status == ApiKeyStatus.ACTIVE,
            )
        ).all()
    )
    digest = hash_api_key(secret)
    item = next(
        (
            candidate
            for candidate in candidates
            if secrets.compare_digest(candidate.secret_hash, digest)
        ),
        None,
    )
    if not item:
        raise HTTPException(status_code=401, detail="API key недействителен")
    now = utcnow()
    expires_at = aware_utc(item.expires_at)
    if expires_at and expires_at <= now:
        item.status = ApiKeyStatus.EXPIRED
        db.commit()
        raise HTTPException(status_code=401, detail="API key истёк")
    organization = db.get(Organization, item.organization_id)
    if not organization or organization.status != OrganizationStatus.ACTIVE:
        raise HTTPException(status_code=403, detail="Организация недоступна")
    item.last_used_at = now
    db.commit()
    request.state.organization_id = item.organization_id
    return ServicePrincipal(
        organization_id=item.organization_id,
        api_key_id=item.id,
        name=item.name,
        scopes=frozenset(item.scopes),
    )


def require_scope(scope: str):
    """Выполнить операцию require scope. Аргументы интерпретируются в контексте модуля, результат
    возвращается вызывающему коду.
    """

    def dependency(
        principal: ServicePrincipal = Depends(get_service_principal),
    ) -> ServicePrincipal:
        """Выполнить операцию dependency. Аргументы интерпретируются в контексте модуля, результат
        возвращается вызывающему коду.
        """
        if "*" not in principal.scopes and scope not in principal.scopes:
            raise HTTPException(status_code=403, detail=f"Требуется scope: {scope}")
        return principal

    return dependency
