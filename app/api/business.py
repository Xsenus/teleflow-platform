from __future__ import annotations

import json
import secrets

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit import write_audit
from app.database import get_db
from app.dependencies import get_current_user, require_roles
from app.enums import ConnectionKind, ConnectionStatus, UserRole
from app.models import TelegramBusinessConnection, TelegramConnection, User
from app.observability import WEBHOOK_UPDATES
from app.schemas import (
    BusinessConnectionPatch,
    BusinessConnectionRead,
    MessageResponse,
    WebhookSetupRequest,
    WebhookSetupResponse,
)
from app.services.inbound import InboundService
from app.services.telegram.business import ALLOWED_BUSINESS_UPDATES, BusinessBotClient

router = APIRouter(prefix="/business", tags=["telegram-business"])
hooks_router = APIRouter(prefix="/hooks/telegram", tags=["telegram-webhook"])


def _bot_connection(db: Session, connection_id: str, organization_id: str) -> TelegramConnection:
    """Реализовать внутренний этап bot connection step. Вспомогательная функция сохраняет
    детерминированность и тестируемость процесса.
    """
    connection = db.scalar(
        select(TelegramConnection).where(
            TelegramConnection.id == connection_id,
            TelegramConnection.organization_id == organization_id,
            TelegramConnection.kind == ConnectionKind.BOT,
        )
    )
    if not connection:
        raise HTTPException(status_code=404, detail="Bot API-подключение не найдено")
    return connection


@router.get("/connections", response_model=list[BusinessConnectionRead])
def list_business_connections(
    telegram_connection_id: str | None = None,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[TelegramBusinessConnection]:
    """Прочитать business connections. Значение возвращается без несвязанных изменений состояния."""
    stmt = (
        select(TelegramBusinessConnection)
        .where(TelegramBusinessConnection.organization_id == user.organization_id)
        .order_by(TelegramBusinessConnection.last_update_at.desc())
    )
    if telegram_connection_id:
        stmt = stmt.where(
            TelegramBusinessConnection.telegram_connection_id == telegram_connection_id
        )
    return list(db.scalars(stmt).all())


@router.patch("/connections/{business_id}", response_model=BusinessConnectionRead)
def patch_business_connection(
    business_id: str,
    payload: BusinessConnectionPatch,
    request: Request,
    user: User = Depends(require_roles(UserRole.OWNER, UserRole.ADMIN)),
    db: Session = Depends(get_db),
) -> TelegramBusinessConnection:
    """Обновить business connection. Переход применяется только после проверки его предусловий."""
    item = db.scalar(
        select(TelegramBusinessConnection).where(
            TelegramBusinessConnection.id == business_id,
            TelegramBusinessConnection.organization_id == user.organization_id,
        )
    )
    if not item:
        raise HTTPException(status_code=404, detail="Business connection не найден")
    for key, value in payload.model_dump(exclude_unset=True).items():
        setattr(item, key, value)
    write_audit(
        db,
        actor=user,
        action="telegram.business_connection_patched",
        entity_type="telegram_business_connection",
        entity_id=item.id,
        request=request,
    )
    db.commit()
    db.refresh(item)
    return item


@router.post(
    "/bots/{connection_id}/webhook",
    response_model=WebhookSetupResponse,
)
def setup_webhook(
    connection_id: str,
    payload: WebhookSetupRequest,
    request: Request,
    user: User = Depends(require_roles(UserRole.OWNER, UserRole.ADMIN)),
    db: Session = Depends(get_db),
) -> WebhookSetupResponse:
    """Выполнить операцию setup webhook. Аргументы интерпретируются в контексте модуля, результат
    возвращается вызывающему коду.
    """
    settings = request.app.state.settings
    cipher = request.app.state.cipher
    connection = _bot_connection(db, connection_id, user.organization_id)
    if connection.status != ConnectionStatus.ACTIVE:
        raise HTTPException(status_code=409, detail="Сначала активируйте Telegram-бота")
    credentials = cipher.decrypt_json(
        connection.credentials_enc,
        context=f"telegram-connection:{connection.id}",
    )
    header_secret = secrets.token_urlsafe(settings.webhook_secret_length)
    path_token = secrets.token_urlsafe(32)
    webhook_url = (
        f"{settings.public_base_url.rstrip('/')}/hooks/telegram/{connection.id}/{path_token}"
    )
    try:
        BusinessBotClient(connection, settings, cipher).set_webhook(
            url=webhook_url,
            secret_token=header_secret,
            drop_pending_updates=payload.drop_pending_updates,
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Telegram не принял webhook: {exc}") from exc
    credentials.update(
        {
            "webhook_secret": header_secret,
            "webhook_path_token": path_token,
            "webhook_url": webhook_url,
            "webhook_allowed_updates": ALLOWED_BUSINESS_UPDATES,
        }
    )
    connection.credentials_enc = cipher.encrypt_json(
        credentials, context=f"telegram-connection:{connection.id}"
    )
    write_audit(
        db,
        actor=user,
        action="telegram.webhook_configured",
        entity_type="telegram_connection",
        entity_id=connection.id,
        details={"url": webhook_url, "allowed_updates": ALLOWED_BUSINESS_UPDATES},
        request=request,
    )
    db.commit()
    return WebhookSetupResponse(
        webhook_url=webhook_url,
        secret_hint=header_secret[:6] + "…" + header_secret[-4:],
        allowed_updates=ALLOWED_BUSINESS_UPDATES,
    )


@router.get("/bots/{connection_id}/webhook")
def get_webhook_info(
    connection_id: str,
    request: Request,
    user: User = Depends(require_roles(UserRole.OWNER, UserRole.ADMIN)),
    db: Session = Depends(get_db),
) -> dict:
    """Прочитать webhook info. Значение возвращается без несвязанных изменений состояния."""
    connection = _bot_connection(db, connection_id, user.organization_id)
    try:
        return BusinessBotClient(
            connection, request.app.state.settings, request.app.state.cipher
        ).get_webhook_info()
    except Exception as exc:
        raise HTTPException(
            status_code=502, detail=f"Не удалось получить webhook info: {exc}"
        ) from exc


@router.delete("/bots/{connection_id}/webhook", response_model=MessageResponse)
def delete_webhook(
    connection_id: str,
    request: Request,
    user: User = Depends(require_roles(UserRole.OWNER, UserRole.ADMIN)),
    db: Session = Depends(get_db),
) -> MessageResponse:
    """Безопасно выполнить delete webhook. Зависимое состояние и видимые в аудите последствия
    обрабатываются согласованно.
    """
    connection = _bot_connection(db, connection_id, user.organization_id)
    settings = request.app.state.settings
    cipher = request.app.state.cipher
    client = BusinessBotClient(connection, settings, cipher)
    try:
        client.delete_webhook()
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Telegram не удалил webhook: {exc}") from exc
    credentials = client.credentials
    for key in ("webhook_secret", "webhook_path_token", "webhook_url", "webhook_allowed_updates"):
        credentials.pop(key, None)
    connection.credentials_enc = cipher.encrypt_json(
        credentials, context=f"telegram-connection:{connection.id}"
    )
    write_audit(
        db,
        actor=user,
        action="telegram.webhook_deleted",
        entity_type="telegram_connection",
        entity_id=connection.id,
        request=request,
    )
    db.commit()
    return MessageResponse(message="Webhook удалён")


@hooks_router.post("/{connection_id}/{path_token}", include_in_schema=False)
async def telegram_webhook(
    connection_id: str,
    path_token: str,
    request: Request,
) -> Response:
    """Выполнить операцию telegram webhook. Аргументы интерпретируются в контексте модуля,
    результат возвращается вызывающему коду.
    """
    settings = request.app.state.settings
    if not settings.inbound_enabled:
        WEBHOOK_UPDATES.labels("disabled", "unknown").inc()
        return Response(status_code=404)

    # Authenticate the unguessable path and Telegram secret header before reading or
    # parsing the potentially large request body. Revoked/non-active bot connections
    # no longer accept inbound updates.
    with request.app.state.session_factory() as db:
        connection = db.scalar(
            select(TelegramConnection).where(
                TelegramConnection.id == connection_id,
                TelegramConnection.kind == ConnectionKind.BOT,
                TelegramConnection.status == ConnectionStatus.ACTIVE,
            )
        )
        if not connection or not connection.credentials_enc:
            WEBHOOK_UPDATES.labels("unknown_connection", "unknown").inc()
            return Response(status_code=404)
        try:
            credentials = request.app.state.cipher.decrypt_json(
                connection.credentials_enc,
                context=f"telegram-connection:{connection.id}",
            )
        except Exception:
            WEBHOOK_UPDATES.labels("invalid_credentials", "unknown").inc()
            return Response(status_code=404)

        expected_path = str(credentials.get("webhook_path_token") or "")
        expected_header = str(credentials.get("webhook_secret") or "")
        supplied_header = request.headers.get("x-telegram-bot-api-secret-token", "")
        if not expected_path or not expected_header:
            WEBHOOK_UPDATES.labels("not_configured", "unknown").inc()
            return Response(status_code=404)
        if not secrets.compare_digest(expected_path, path_token) or not secrets.compare_digest(
            expected_header, supplied_header
        ):
            WEBHOOK_UPDATES.labels("unauthorized", "unknown").inc()
            return Response(status_code=403)

        raw = await request.body()
        if len(raw) > settings.inbound_update_max_bytes:
            WEBHOOK_UPDATES.labels("too_large", "unknown").inc()
            return Response(status_code=413)
        try:
            payload = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError):
            WEBHOOK_UPDATES.labels("invalid_json", "unknown").inc()
            return Response(status_code=400)
        if not isinstance(payload, dict):
            WEBHOOK_UPDATES.labels("invalid_payload", "unknown").inc()
            return Response(status_code=400)

        try:
            service = InboundService(
                request.app.state.session_factory,
                settings,
                request.app.state.cipher,
            )
            item, created = service.accept_update(db, connection=connection, payload=payload)
            WEBHOOK_UPDATES.labels("accepted" if created else "duplicate", item.update_type).inc()
        except ValueError:
            WEBHOOK_UPDATES.labels("invalid_update", "unknown").inc()
            return Response(status_code=400)
    return Response(status_code=200)
