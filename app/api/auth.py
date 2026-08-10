from __future__ import annotations

import base64
import io
from datetime import timedelta

import qrcode
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit import write_audit
from app.config import Settings
from app.database import get_db
from app.dependencies import get_app_settings, get_cipher, get_current_user
from app.enums import SafetySeverity
from app.models import Organization, RefreshToken, User, utcnow
from app.schemas import (
    LoginRequest,
    LoginResponse,
    MessageResponse,
    OrganizationRead,
    PasswordChange,
    RefreshSessionRead,
    TotpConfirmRequest,
    TotpDisableRequest,
    TotpStartResponse,
    UserRead,
)
from app.security import (
    aware_utc,
    clear_auth_cookies,
    extract_client_ip,
    hash_password,
    hash_refresh_token,
    issue_tokens,
    needs_rehash,
    set_auth_cookies,
    verify_password,
    verify_user_totp,
)
from app.services.crypto import SecretCipher
from app.services.totp import generate_secret, provisioning_uri, verify_code

router = APIRouter(prefix="/auth", tags=["auth"])


def _generic_login_error() -> HTTPException:
    """Реализовать внутренний этап generic login error step. Вспомогательная функция сохраняет
    детерминированность и тестируемость процесса.
    """
    return HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Неверные данные входа")


@router.post("/login", response_model=LoginResponse)
def login(
    payload: LoginRequest,
    response: Response,
    request: Request,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
    cipher: SecretCipher = Depends(get_cipher),
) -> LoginResponse:
    """Выполнить операцию login. Аргументы интерпретируются в контексте модуля, результат
    возвращается вызывающему коду.
    """
    user = db.scalar(select(User).where(User.email == payload.email.lower()))
    now = utcnow()
    if not user or not user.is_active:
        raise _generic_login_error()
    locked_until = aware_utc(user.locked_until)
    if locked_until and locked_until > now:
        raise HTTPException(
            status_code=status.HTTP_423_LOCKED,
            detail="Вход временно заблокирован после нескольких неудачных попыток",
        )
    if not verify_password(payload.password, user.password_hash, settings):
        user.failed_login_count += 1
        if user.failed_login_count >= settings.login_max_failures:
            user.locked_until = now + timedelta(minutes=settings.login_lock_minutes)
        write_audit(
            db,
            action="auth.login_failed",
            actor=user,
            entity_type="user",
            entity_id=user.id,
            severity=SafetySeverity.WARNING,
            details={"reason": "invalid_credentials"},
            request=request,
        )
        db.commit()
        raise _generic_login_error()
    if not verify_user_totp(user, payload.totp_code, cipher):
        user.failed_login_count += 1
        if user.failed_login_count >= settings.login_max_failures:
            user.locked_until = now + timedelta(minutes=settings.login_lock_minutes)
        write_audit(
            db,
            action="auth.login_failed",
            actor=user,
            entity_type="user",
            entity_id=user.id,
            severity=SafetySeverity.WARNING,
            details={"reason": "invalid_totp"},
            request=request,
        )
        db.commit()
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Требуется действующий шестизначный код 2FA",
        )

    if needs_rehash(user.password_hash, settings):
        user.password_hash = hash_password(payload.password, settings)
    user.failed_login_count = 0
    user.locked_until = None
    user.last_login_at = now
    tokens = issue_tokens(
        db,
        user,
        settings,
        ip_address=extract_client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )
    write_audit(
        db,
        action="auth.login_succeeded",
        actor=user,
        entity_type="user",
        entity_id=user.id,
        request=request,
    )
    db.commit()
    set_auth_cookies(response, tokens, settings)
    return LoginResponse(
        user=UserRead.model_validate(user),
        organization=OrganizationRead.model_validate(db.get(Organization, user.organization_id)),
        access_expires_at=tokens.access_expires_at,
    )


@router.post("/refresh", response_model=LoginResponse)
def refresh(
    response: Response,
    request: Request,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
) -> LoginResponse:
    """Выполнить операцию refresh. Аргументы интерпретируются в контексте модуля, результат
    возвращается вызывающему коду.
    """
    raw_token = request.cookies.get(settings.refresh_cookie_name)
    if not raw_token:
        raise HTTPException(status_code=401, detail="Refresh-сессия отсутствует")
    token_hash = hash_refresh_token(raw_token)
    stored = db.scalar(
        select(RefreshToken).where(RefreshToken.token_hash == token_hash).with_for_update()
    )
    now = utcnow()
    if (
        not stored
        or stored.revoked_at is not None
        or (aware_utc(stored.expires_at) or now) <= now
        or not stored.user.is_active
    ):
        clear_auth_cookies(response, settings)
        raise HTTPException(status_code=401, detail="Refresh-сессия недействительна")
    stored.revoked_at = now
    tokens = issue_tokens(
        db,
        stored.user,
        settings,
        ip_address=extract_client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )
    stored.replaced_by_hash = hash_refresh_token(tokens.refresh_token)
    write_audit(
        db,
        action="auth.token_refreshed",
        actor=stored.user,
        entity_type="refresh_token",
        entity_id=stored.id,
        request=request,
    )
    db.commit()
    set_auth_cookies(response, tokens, settings)
    return LoginResponse(
        user=UserRead.model_validate(stored.user),
        organization=OrganizationRead.model_validate(
            db.get(Organization, stored.user.organization_id)
        ),
        access_expires_at=tokens.access_expires_at,
    )


@router.post("/logout", response_model=MessageResponse)
def logout(
    response: Response,
    request: Request,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
) -> MessageResponse:
    """Выполнить операцию logout. Аргументы интерпретируются в контексте модуля, результат
    возвращается вызывающему коду.
    """
    raw_token = request.cookies.get(settings.refresh_cookie_name)
    if raw_token:
        stored = db.scalar(
            select(RefreshToken).where(RefreshToken.token_hash == hash_refresh_token(raw_token))
        )
        if stored and stored.revoked_at is None:
            stored.revoked_at = utcnow()
            write_audit(
                db,
                action="auth.logout",
                actor=stored.user,
                entity_type="user",
                entity_id=stored.user_id,
                request=request,
            )
            db.commit()
    clear_auth_cookies(response, settings)
    return MessageResponse(message="Выход выполнен")


@router.get("/me", response_model=UserRead)
def me(user: User = Depends(get_current_user)) -> User:
    """Выполнить операцию me. Аргументы интерпретируются в контексте модуля, результат возвращается
    вызывающему коду.
    """
    return user


@router.get("/sessions", response_model=list[RefreshSessionRead])
def list_sessions(
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
) -> list[RefreshSessionRead]:
    """Прочитать sessions. Значение возвращается без несвязанных изменений состояния."""
    now = utcnow()
    current_raw = request.cookies.get(settings.refresh_cookie_name)
    current_hash = hash_refresh_token(current_raw) if current_raw else None
    sessions = list(
        db.scalars(
            select(RefreshToken)
            .where(
                RefreshToken.user_id == user.id,
                RefreshToken.revoked_at.is_(None),
                RefreshToken.expires_at > now,
            )
            .order_by(RefreshToken.created_at.desc())
        ).all()
    )
    return [
        RefreshSessionRead(
            id=item.id,
            created_at=item.created_at,
            expires_at=item.expires_at,
            created_ip=item.created_ip,
            user_agent=item.user_agent,
            current=item.token_hash == current_hash,
        )
        for item in sessions
    ]


@router.delete("/sessions/{session_id}", response_model=MessageResponse)
def revoke_session(
    session_id: str,
    response: Response,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
) -> MessageResponse:
    """Безопасно выполнить revoke session. Зависимое состояние и видимые в аудите последствия
    обрабатываются согласованно.
    """
    session = db.scalar(
        select(RefreshToken).where(
            RefreshToken.id == session_id,
            RefreshToken.user_id == user.id,
        )
    )
    if not session:
        raise HTTPException(status_code=404, detail="Сессия не найдена")
    if session.revoked_at is None:
        session.revoked_at = utcnow()
    current_raw = request.cookies.get(settings.refresh_cookie_name)
    current = bool(current_raw and session.token_hash == hash_refresh_token(current_raw))
    write_audit(
        db,
        action="auth.session_revoked",
        actor=user,
        entity_type="refresh_token",
        entity_id=session.id,
        details={"current": current},
        request=request,
    )
    db.commit()
    if current:
        clear_auth_cookies(response, settings)
    return MessageResponse(
        message="Текущая сессия завершена" if current else "Сессия устройства завершена"
    )


@router.post("/sessions/revoke-others", response_model=MessageResponse)
def revoke_other_sessions(
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
) -> MessageResponse:
    """Безопасно выполнить revoke other sessions. Зависимое состояние и видимые в аудите
    последствия обрабатываются согласованно.
    """
    current_raw = request.cookies.get(settings.refresh_cookie_name)
    current_hash = hash_refresh_token(current_raw) if current_raw else None
    now = utcnow()
    sessions = list(
        db.scalars(
            select(RefreshToken).where(
                RefreshToken.user_id == user.id,
                RefreshToken.revoked_at.is_(None),
            )
        ).all()
    )
    revoked = 0
    for session in sessions:
        if current_hash and session.token_hash == current_hash:
            continue
        session.revoked_at = now
        revoked += 1
    write_audit(
        db,
        action="auth.other_sessions_revoked",
        actor=user,
        entity_type="user",
        entity_id=user.id,
        details={"revoked_count": revoked},
        request=request,
    )
    db.commit()
    return MessageResponse(message=f"Завершено сессий: {revoked}")


@router.post("/change-password", response_model=MessageResponse)
def change_password(
    payload: PasswordChange,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
) -> MessageResponse:
    """Выполнить операцию change password. Аргументы интерпретируются в контексте модуля, результат
    возвращается вызывающему коду.
    """
    if not verify_password(payload.current_password, user.password_hash, settings):
        raise HTTPException(status_code=400, detail="Текущий пароль указан неверно")
    if payload.current_password == payload.new_password:
        raise HTTPException(status_code=400, detail="Новый пароль должен отличаться")
    user.password_hash = hash_password(payload.new_password, settings)
    user.must_change_password = False
    now = utcnow()
    for token in user.refresh_tokens:
        if token.revoked_at is None:
            token.revoked_at = now
    write_audit(
        db,
        action="auth.password_changed",
        actor=user,
        entity_type="user",
        entity_id=user.id,
        request=request,
    )
    db.commit()
    return MessageResponse(message="Пароль изменён. Выполните повторный вход на других устройствах")


@router.post("/totp/start", response_model=TotpStartResponse)
def start_totp(
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    cipher: SecretCipher = Depends(get_cipher),
    settings: Settings = Depends(get_app_settings),
) -> TotpStartResponse:
    """Выполнить операцию start totp. Аргументы интерпретируются в контексте модуля, результат
    возвращается вызывающему коду.
    """
    secret = generate_secret()
    user.pending_totp_secret_enc = cipher.encrypt(secret, context=f"user-totp-pending:{user.id}")
    uri = provisioning_uri(secret, user.email, settings.app_name)
    image = qrcode.make(uri)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    qr_data = "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")
    write_audit(
        db,
        action="auth.totp_enrollment_started",
        actor=user,
        entity_type="user",
        entity_id=user.id,
        request=request,
    )
    db.commit()
    return TotpStartResponse(
        provisioning_uri=uri,
        qr_data_uri=qr_data,
        secret_hint=f"{secret[:4]}…{secret[-4:]}",
    )


@router.post("/totp/confirm", response_model=MessageResponse)
def confirm_totp(
    payload: TotpConfirmRequest,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    cipher: SecretCipher = Depends(get_cipher),
) -> MessageResponse:
    """Выполнить операцию confirm totp. Аргументы интерпретируются в контексте модуля, результат
    возвращается вызывающему коду.
    """
    if not user.pending_totp_secret_enc:
        raise HTTPException(status_code=400, detail="Сначала начните подключение 2FA")
    secret = cipher.decrypt(user.pending_totp_secret_enc, context=f"user-totp-pending:{user.id}")
    if not verify_code(secret, payload.code):
        raise HTTPException(status_code=400, detail="Код 2FA недействителен")
    user.totp_secret_enc = cipher.encrypt(secret, context=f"user-totp:{user.id}")
    user.pending_totp_secret_enc = None
    user.totp_enabled = True
    write_audit(
        db,
        action="auth.totp_enabled",
        actor=user,
        entity_type="user",
        entity_id=user.id,
        request=request,
    )
    db.commit()
    return MessageResponse(message="Двухфакторная аутентификация включена")


@router.post("/totp/disable", response_model=MessageResponse)
def disable_totp(
    payload: TotpDisableRequest,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    cipher: SecretCipher = Depends(get_cipher),
    settings: Settings = Depends(get_app_settings),
) -> MessageResponse:
    """Безопасно выполнить disable totp. Зависимое состояние и видимые в аудите последствия
    обрабатываются согласованно.
    """
    if not user.totp_enabled or not user.totp_secret_enc:
        raise HTTPException(status_code=400, detail="2FA уже отключена")
    if not verify_password(payload.password, user.password_hash, settings):
        raise HTTPException(status_code=400, detail="Пароль указан неверно")
    secret = cipher.decrypt(user.totp_secret_enc, context=f"user-totp:{user.id}")
    if not verify_code(secret, payload.code):
        raise HTTPException(status_code=400, detail="Код 2FA недействителен")
    user.totp_secret_enc = None
    user.pending_totp_secret_enc = None
    user.totp_enabled = False
    write_audit(
        db,
        action="auth.totp_disabled",
        actor=user,
        entity_type="user",
        entity_id=user.id,
        severity=SafetySeverity.WARNING,
        request=request,
    )
    db.commit()
    return MessageResponse(message="Двухфакторная аутентификация отключена")
