from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from typing import Any

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError
from fastapi import HTTPException, Request, Response, status
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import RefreshToken, User, utcnow
from app.services.crypto import SecretCipher
from app.services.totp import verify_code


@lru_cache(maxsize=16)
def _password_hasher(time_cost: int, memory_cost: int, parallelism: int) -> PasswordHasher:
    """Вычислить password hasher. Канонический ввод обеспечивает детерминированное сравнение
    целостности между процессами.
    """
    return PasswordHasher(
        time_cost=time_cost,
        memory_cost=memory_cost,
        parallelism=parallelism,
    )


def _hasher(settings: Settings | None = None) -> PasswordHasher:
    """Вычислить hasher. Канонический ввод обеспечивает детерминированное сравнение целостности
    между процессами.
    """
    return _password_hasher(
        settings.password_hash_time_cost if settings else 3,
        settings.password_hash_memory_cost_kib if settings else 65536,
        settings.password_hash_parallelism if settings else 2,
    )


@dataclass(frozen=True)
class IssuedTokens:
    access_token: str
    refresh_token: str
    csrf_token: str
    access_expires_at: datetime
    refresh_expires_at: datetime


def hash_password(password: str, settings: Settings | None = None) -> str:
    """Вычислить hash password. Канонический ввод обеспечивает детерминированное сравнение
    целостности между процессами.
    """
    if len(password) < 12:
        raise ValueError("Пароль должен содержать не менее 12 символов")
    return _hasher(settings).hash(password)


def verify_password(password: str, password_hash: str, settings: Settings | None = None) -> bool:
    """Проверить password. Некорректные данные или состояние отклоняются до побочного эффекта."""
    try:
        return _hasher(settings).verify(password_hash, password)
    except (VerifyMismatchError, InvalidHashError):
        return False


def needs_rehash(password_hash: str, settings: Settings | None = None) -> bool:
    """Вычислить needs rehash. Канонический ввод обеспечивает детерминированное сравнение
    целостности между процессами.
    """
    try:
        return _hasher(settings).check_needs_rehash(password_hash)
    except InvalidHashError:
        return True


def hash_refresh_token(token: str) -> str:
    """Вычислить hash refresh token. Канонический ввод обеспечивает детерминированное сравнение
    целостности между процессами.
    """
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def create_access_token(user: User, settings: Settings) -> tuple[str, datetime]:
    """Создать access token. Перед сохранением или возвратом нового значения проверяются связанные
    инварианты.
    """
    now = utcnow()
    expires_at = now + timedelta(minutes=settings.access_token_minutes)
    payload: dict[str, Any] = {
        "sub": user.id,
        "email": user.email,
        "role": user.role.value,
        "org": user.organization_id,
        "iss": settings.jwt_issuer,
        "iat": int(now.timestamp()),
        "exp": int(expires_at.timestamp()),
        "jti": secrets.token_urlsafe(16),
        "type": "access",
    }
    token = jwt.encode(payload, settings.jwt_secret, algorithm="HS256")
    return token, expires_at


def decode_access_token(token: str, settings: Settings) -> dict[str, Any]:
    """Преобразовать data for decode access token using the project's canonical representation."""
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret,
            algorithms=["HS256"],
            issuer=settings.jwt_issuer,
            options={"require": ["exp", "iat", "sub", "type"]},
        )
    except jwt.PyJWTError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Сессия недействительна"
        ) from exc
    if payload.get("type") != "access":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Неверный тип токена")
    return payload


def issue_tokens(
    db: Session,
    user: User,
    settings: Settings,
    *,
    ip_address: str | None,
    user_agent: str | None,
) -> IssuedTokens:
    """Выполнить операцию issue tokens. Аргументы интерпретируются в контексте модуля, результат
    возвращается вызывающему коду.
    """
    access_token, access_expires_at = create_access_token(user, settings)
    refresh_token = secrets.token_urlsafe(48)
    refresh_expires_at = utcnow() + timedelta(days=settings.refresh_token_days)
    db.add(
        RefreshToken(
            user_id=user.id,
            token_hash=hash_refresh_token(refresh_token),
            expires_at=refresh_expires_at,
            created_ip=ip_address,
            user_agent=(user_agent or "")[:500] or None,
        )
    )
    csrf_token = secrets.token_urlsafe(32)
    return IssuedTokens(
        access_token=access_token,
        refresh_token=refresh_token,
        csrf_token=csrf_token,
        access_expires_at=access_expires_at,
        refresh_expires_at=refresh_expires_at,
    )


def set_auth_cookies(response: Response, tokens: IssuedTokens, settings: Settings) -> None:
    """Обновить auth cookies. Переход применяется только после проверки его предусловий."""
    secure = settings.effective_cookie_secure
    response.set_cookie(
        settings.access_cookie_name,
        tokens.access_token,
        httponly=True,
        max_age=max(1, int((tokens.access_expires_at - utcnow()).total_seconds())),
        secure=secure,
        samesite=settings.cookie_samesite,
        path="/",
    )
    response.set_cookie(
        settings.refresh_cookie_name,
        tokens.refresh_token,
        httponly=True,
        max_age=max(1, int((tokens.refresh_expires_at - utcnow()).total_seconds())),
        secure=secure,
        samesite=settings.cookie_samesite,
        path="/",
    )
    response.set_cookie(
        settings.csrf_cookie_name,
        tokens.csrf_token,
        httponly=False,
        max_age=max(1, int((tokens.refresh_expires_at - utcnow()).total_seconds())),
        secure=secure,
        samesite=settings.cookie_samesite,
        path="/",
    )


def clear_auth_cookies(response: Response, settings: Settings) -> None:
    """Выполнить операцию clear auth cookies. Аргументы интерпретируются в контексте модуля,
    результат возвращается вызывающему коду.
    """
    for name in (
        settings.access_cookie_name,
        settings.refresh_cookie_name,
        settings.csrf_cookie_name,
    ):
        response.delete_cookie(name, path="/")


def extract_client_ip(request: Request) -> str | None:
    """Выполнить операцию extract client ip. Аргументы интерпретируются в контексте модуля,
    результат возвращается вызывающему коду.
    """
    state_ip = getattr(request.state, "client_ip", None)
    if state_ip:
        return str(state_ip)
    return request.client.host if request.client else None


def verify_user_totp(user: User, code: str | None, cipher: SecretCipher) -> bool:
    """Проверить user totp. Некорректные данные или состояние отклоняются до побочного эффекта."""
    if not user.totp_enabled:
        return True
    if not code or not user.totp_secret_enc:
        return False
    secret = cipher.decrypt(user.totp_secret_enc, context=f"user-totp:{user.id}")
    return verify_code(secret, code)


def aware_utc(value: datetime | None) -> datetime | None:
    """Выполнить операцию aware utc. Аргументы интерпретируются в контексте модуля, результат
    возвращается вызывающему коду.
    """
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
