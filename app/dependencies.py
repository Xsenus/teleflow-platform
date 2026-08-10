from __future__ import annotations

from collections.abc import Callable

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.database import get_db
from app.enums import OrganizationStatus, UserRole
from app.models import Organization, User
from app.security import decode_access_token
from app.services.crypto import SecretCipher

_bearer = HTTPBearer(auto_error=False)


def get_app_settings(request: Request) -> Settings:
    """Прочитать app settings. Значение возвращается без несвязанных изменений состояния."""
    return request.app.state.settings


def get_cipher(request: Request) -> SecretCipher:
    """Прочитать cipher. Значение возвращается без несвязанных изменений состояния."""
    return request.app.state.cipher


def get_current_user(
    request: Request,
    db: Session = Depends(get_db),
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    settings: Settings = Depends(get_app_settings),
) -> User:
    """Прочитать current user. Значение возвращается без несвязанных изменений состояния."""
    token = (
        credentials.credentials if credentials else request.cookies.get(settings.access_cookie_name)
    )
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Требуется вход")
    payload = decode_access_token(token, settings)
    user = db.get(User, payload["sub"])
    if not user or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Пользователь отключён"
        )
    if payload.get("org") and payload["org"] != user.organization_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Организация сессии изменилась"
        )
    organization = db.get(Organization, user.organization_id)
    if not organization or organization.status != OrganizationStatus.ACTIVE:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Организация недоступна")
    request.state.organization_id = user.organization_id
    request.state.user_id = user.id
    if (
        settings.require_admin_totp
        and user.role in {UserRole.OWNER, UserRole.ADMIN}
        and not user.totp_enabled
        and not request.url.path.startswith(f"{settings.api_prefix}/auth/")
    ):
        raise HTTPException(
            status_code=status.HTTP_428_PRECONDITION_REQUIRED,
            detail="Для владельца и администратора требуется включить 2FA",
        )
    return user


def get_current_organization(
    user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> Organization:
    """Прочитать current organization. Значение возвращается без несвязанных изменений состояния."""
    organization = db.get(Organization, user.organization_id)
    if organization is None:
        raise HTTPException(status_code=404, detail="Организация не найдена")
    return organization


def require_roles(*roles: UserRole) -> Callable[..., User]:
    """Выполнить операцию require roles. Аргументы интерпретируются в контексте модуля, результат
    возвращается вызывающему коду.
    """
    allowed = set(roles)

    def dependency(user: User = Depends(get_current_user)) -> User:
        """Выполнить операцию dependency. Аргументы интерпретируются в контексте модуля, результат
        возвращается вызывающему коду.
        """
        if user.role not in allowed:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Недостаточно прав для операции",
            )
        return user

    return dependency


def tenant_get_or_404[ModelT](
    db: Session,
    model: type[ModelT],
    entity_id: str,
    organization_id: str,
    *,
    detail: str = "Объект не найден",
) -> ModelT:
    """Получить принадлежащую организации сущность или вернуть HTTP 404."""
    id_column = vars(model)["id"]
    organization_column = vars(model)["organization_id"]
    entity = db.scalar(
        select(model).where(id_column == entity_id, organization_column == organization_id)
    )
    if entity is None:
        raise HTTPException(status_code=404, detail=detail)
    return entity
