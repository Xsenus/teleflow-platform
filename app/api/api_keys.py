from __future__ import annotations

import secrets

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit import write_audit
from app.database import get_db
from app.dependencies import require_roles
from app.enums import ApiKeyStatus, UserRole
from app.models import ApiKey, User, utcnow
from app.schemas import ApiKeyCreate, ApiKeyCreatedResponse, ApiKeyRead, MessageResponse
from app.service_auth import hash_api_key

router = APIRouter(prefix="/api-keys", tags=["api-keys"])

ALLOWED_SCOPES = {
    "candidates:read",
    "candidates:contacts",
    "conversations:read",
    "events:write",
    "*",
}


@router.get("", response_model=list[ApiKeyRead])
def list_api_keys(
    user: User = Depends(require_roles(UserRole.OWNER, UserRole.ADMIN)),
    db: Session = Depends(get_db),
) -> list[ApiKey]:
    """Прочитать api keys. Значение возвращается без несвязанных изменений состояния."""
    return list(
        db.scalars(
            select(ApiKey)
            .where(ApiKey.organization_id == user.organization_id)
            .order_by(ApiKey.created_at.desc())
        ).all()
    )


@router.post("", response_model=ApiKeyCreatedResponse, status_code=201)
def create_api_key(
    payload: ApiKeyCreate,
    request: Request,
    user: User = Depends(require_roles(UserRole.OWNER)),
    db: Session = Depends(get_db),
) -> ApiKeyCreatedResponse:
    """Создать api key. Перед сохранением или возвратом нового значения проверяются связанные
    инварианты.
    """
    invalid = set(payload.scopes) - ALLOWED_SCOPES
    if invalid:
        raise HTTPException(
            status_code=422, detail=f"Неизвестные scopes: {', '.join(sorted(invalid))}"
        )
    secret = "tfp_" + secrets.token_urlsafe(36)
    item = ApiKey(
        organization_id=user.organization_id,
        name=payload.name,
        key_prefix=secret[:16],
        secret_hash=hash_api_key(secret),
        scopes=sorted(set(payload.scopes)),
        expires_at=payload.expires_at,
        created_by_id=user.id,
    )
    db.add(item)
    write_audit(
        db,
        actor=user,
        action="api_key.created",
        entity_type="api_key",
        entity_id=item.id,
        details={"name": item.name, "scopes": item.scopes},
        request=request,
    )
    db.commit()
    db.refresh(item)
    return ApiKeyCreatedResponse(api_key=ApiKeyRead.model_validate(item), secret=secret)


@router.post("/{key_id}/revoke", response_model=MessageResponse)
def revoke_api_key(
    key_id: str,
    request: Request,
    user: User = Depends(require_roles(UserRole.OWNER)),
    db: Session = Depends(get_db),
) -> MessageResponse:
    """Безопасно выполнить revoke api key. Зависимое состояние и видимые в аудите последствия
    обрабатываются согласованно.
    """
    item = db.scalar(
        select(ApiKey).where(
            ApiKey.id == key_id,
            ApiKey.organization_id == user.organization_id,
        )
    )
    if not item:
        raise HTTPException(status_code=404, detail="API key не найден")
    item.status = ApiKeyStatus.REVOKED
    item.revoked_at = utcnow()
    write_audit(
        db,
        actor=user,
        action="api_key.revoked",
        entity_type="api_key",
        entity_id=item.id,
        request=request,
    )
    db.commit()
    return MessageResponse(message="API key отозван")
