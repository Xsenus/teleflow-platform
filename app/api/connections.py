from __future__ import annotations

from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.orm import Session

from app.audit import write_audit
from app.config import Settings
from app.database import get_db
from app.dependencies import get_app_settings, get_cipher, get_current_user, require_roles
from app.enums import (
    ChallengeStatus,
    ConnectionKind,
    ConnectionStatus,
    JobStatus,
    SafetySeverity,
    UserRole,
)
from app.models import (
    DeliveryJob,
    TelegramAuthChallenge,
    TelegramConnection,
    User,
    new_id,
    utcnow,
)
from app.schemas import (
    BotConnectionCreate,
    ConnectionHealthResponse,
    ConnectionPatch,
    ConnectionRead,
    DiscoveredDestination,
    MessageResponse,
    UserConnectionComplete,
    UserConnectionStart,
    UserConnectionStartResponse,
)
from app.security import aware_utc
from app.services.crypto import SecretCipher
from app.services.telegram.errors import TelegramGatewayError
from app.services.telegram.factory import build_gateway
from app.services.telegram.fake import FakeTelegramGateway
from app.services.telegram.mtproto import MTProtoAuth

router = APIRouter(prefix="/connections", tags=["telegram-connections"])


def _get_connection(db: Session, connection_id: str, organization_id: str) -> TelegramConnection:
    """Реализовать внутренний этап get connection step. Вспомогательная функция сохраняет
    детерминированность и тестируемость процесса.
    """
    connection = db.scalar(
        select(TelegramConnection).where(
            TelegramConnection.id == connection_id,
            TelegramConnection.organization_id == organization_id,
        )
    )
    if not connection:
        raise HTTPException(status_code=404, detail="Подключение не найдено")
    return connection


class ResumeConnectionRequest(BaseModel):
    acknowledge_manual_review: bool = False


def _validate_safety(
    *,
    kind: ConnectionKind,
    min_interval: int,
    daily_cap: int,
    cooldown: int,
    settings: Settings,
) -> None:
    """Реализовать внутренний этап validate safety step. Вспомогательная функция сохраняет
    детерминированность и тестируемость процесса.
    """
    hard_min = (
        settings.user_hard_min_interval_seconds
        if kind == ConnectionKind.USER
        else settings.bot_hard_min_interval_seconds
    )
    if min_interval < hard_min:
        raise HTTPException(
            status_code=422,
            detail=f"Минимальный интервал для режима {kind.value}: {hard_min} секунд",
        )
    if daily_cap > settings.global_hard_daily_cap:
        raise HTTPException(
            status_code=422,
            detail=f"Дневной лимит не может превышать {settings.global_hard_daily_cap}",
        )
    if kind == ConnectionKind.USER and cooldown < 60:
        raise HTTPException(
            status_code=422,
            detail="Cooldown для пользовательского аккаунта не может быть меньше 60 минут",
        )


def _gateway_http_error(exc: TelegramGatewayError) -> HTTPException:
    """Реализовать внутренний этап gateway http error step. Вспомогательная функция сохраняет
    детерминированность и тестируемость процесса.
    """
    status_code = 503 if exc.transient else 400
    return HTTPException(
        status_code=status_code,
        detail={"code": exc.code, "message": str(exc), "retry_after": exc.retry_after},
    )


@router.get("", response_model=list[ConnectionRead])
def list_connections(
    _user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> list[TelegramConnection]:
    """Прочитать connections. Значение возвращается без несвязанных изменений состояния."""
    return list(
        db.scalars(
            select(TelegramConnection)
            .where(TelegramConnection.organization_id == _user.organization_id)
            .order_by(TelegramConnection.created_at)
        ).all()
    )


@router.get("/{connection_id}", response_model=ConnectionRead)
def get_connection(
    connection_id: str,
    _user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> TelegramConnection:
    """Прочитать connection. Значение возвращается без несвязанных изменений состояния."""
    return _get_connection(db, connection_id, _user.organization_id)


@router.post("/bot", response_model=ConnectionRead, status_code=201)
def create_bot_connection(
    payload: BotConnectionCreate,
    request: Request,
    user: User = Depends(require_roles(UserRole.OWNER, UserRole.ADMIN)),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
    cipher: SecretCipher = Depends(get_cipher),
) -> TelegramConnection:
    """Создать bot connection. Перед сохранением или возвратом нового значения проверяются
    связанные инварианты.
    """
    min_interval = payload.min_interval_seconds or settings.bot_default_interval_seconds
    daily_cap = payload.daily_cap or 100
    cooldown = payload.destination_cooldown_minutes or settings.default_destination_cooldown_minutes
    _validate_safety(
        kind=ConnectionKind.BOT,
        min_interval=min_interval,
        daily_cap=daily_cap,
        cooldown=cooldown,
        settings=settings,
    )
    connection = TelegramConnection(
        organization_id=user.organization_id,
        name=payload.name,
        kind=ConnectionKind.BOT,
        status=ConnectionStatus.DRAFT,
        min_interval_seconds=min_interval,
        daily_cap=daily_cap,
        destination_cooldown_minutes=cooldown,
        require_manual_approval=payload.require_manual_approval,
        stop_on_flood=payload.stop_on_flood,
        created_by_id=user.id,
    )
    db.add(connection)
    db.flush()
    connection.credentials_enc = cipher.encrypt_json(
        {"bot_token": payload.bot_token},
        context=f"telegram-connection:{connection.id}",
    )
    try:
        identity = build_gateway(connection, settings, cipher).get_identity()
    except TelegramGatewayError as exc:
        db.rollback()
        raise _gateway_http_error(exc) from exc
    connection.telegram_account_id = identity.account_id
    connection.telegram_username = identity.username
    connection.telegram_display_name = identity.display_name
    connection.status = ConnectionStatus.ACTIVE
    connection.last_checked_at = utcnow()
    write_audit(
        db,
        action="telegram.connection_created",
        actor=user,
        entity_type="telegram_connection",
        entity_id=connection.id,
        details={"kind": connection.kind.value, "username": identity.username},
        request=request,
    )
    db.commit()
    db.refresh(connection)
    return connection


@router.post("/user/start", response_model=UserConnectionStartResponse, status_code=201)
def start_user_connection(
    payload: UserConnectionStart,
    request: Request,
    user: User = Depends(require_roles(UserRole.OWNER, UserRole.ADMIN)),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
    cipher: SecretCipher = Depends(get_cipher),
) -> UserConnectionStartResponse:
    """Выполнить операцию start user connection. Аргументы интерпретируются в контексте модуля,
    результат возвращается вызывающему коду.
    """
    min_interval = payload.min_interval_seconds or settings.user_default_interval_seconds
    daily_cap = payload.daily_cap or 100
    cooldown = payload.destination_cooldown_minutes or settings.default_destination_cooldown_minutes
    _validate_safety(
        kind=ConnectionKind.USER,
        min_interval=min_interval,
        daily_cap=daily_cap,
        cooldown=cooldown,
        settings=settings,
    )
    connection = TelegramConnection(
        organization_id=user.organization_id,
        name=payload.name,
        kind=ConnectionKind.USER,
        status=ConnectionStatus.AUTH_PENDING,
        min_interval_seconds=min_interval,
        daily_cap=daily_cap,
        destination_cooldown_minutes=cooldown,
        require_manual_approval=True,
        stop_on_flood=True,
        created_by_id=user.id,
    )
    db.add(connection)
    db.flush()

    try:
        if settings.telegram_fake_mode:
            auth_payload = {
                "api_id": payload.api_id,
                "api_hash": payload.api_hash,
                "phone": payload.phone,
                "session_string": "fake-pending-session",
                "phone_code_hash": "fake-code-hash",
            }
        else:
            started = MTProtoAuth.start(
                api_id=payload.api_id, api_hash=payload.api_hash, phone=payload.phone
            )
            auth_payload = {
                "api_id": payload.api_id,
                "api_hash": payload.api_hash,
                "phone": payload.phone,
                "session_string": started.session_string,
                "phone_code_hash": started.phone_code_hash,
            }
    except TelegramGatewayError as exc:
        db.rollback()
        raise _gateway_http_error(exc) from exc

    challenge_id = new_id()
    expires_at = utcnow() + timedelta(minutes=10)
    challenge = TelegramAuthChallenge(
        id=challenge_id,
        organization_id=user.organization_id,
        connection_id=connection.id,
        payload_enc=cipher.encrypt_json(
            auth_payload, context=f"telegram-auth-challenge:{challenge_id}"
        ),
        expires_at=expires_at,
        created_by_id=user.id,
    )
    db.add(challenge)
    write_audit(
        db,
        action="telegram.user_auth_started",
        actor=user,
        entity_type="telegram_connection",
        entity_id=connection.id,
        details={"phone_masked": payload.phone[-4:]},
        request=request,
    )
    db.commit()
    return UserConnectionStartResponse(
        connection_id=connection.id,
        challenge_id=challenge.id,
        expires_at=expires_at,
        code_hint="В тестовом режиме используйте 12345" if settings.telegram_fake_mode else None,
    )


@router.post("/user/complete", response_model=ConnectionRead)
def complete_user_connection(
    payload: UserConnectionComplete,
    request: Request,
    user: User = Depends(require_roles(UserRole.OWNER, UserRole.ADMIN)),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
    cipher: SecretCipher = Depends(get_cipher),
) -> TelegramConnection:
    """Выполнить операцию complete user connection. Аргументы интерпретируются в контексте модуля,
    результат возвращается вызывающему коду.
    """
    challenge = db.scalar(
        select(TelegramAuthChallenge).where(
            TelegramAuthChallenge.id == payload.challenge_id,
            TelegramAuthChallenge.organization_id == user.organization_id,
        )
    )
    now = utcnow()
    if not challenge or challenge.status != ChallengeStatus.PENDING:
        raise HTTPException(status_code=404, detail="Запрос авторизации не найден")
    connection = _get_connection(db, challenge.connection_id, user.organization_id)
    if (aware_utc(challenge.expires_at) or now) <= now:
        challenge.status = ChallengeStatus.EXPIRED
        challenge.payload_enc = cipher.encrypt_json(
            {"expired": True}, context=f"telegram-auth-challenge:{challenge.id}"
        )
        connection.status = ConnectionStatus.ERROR
        connection.last_error_code = "AUTH_CHALLENGE_EXPIRED"
        connection.last_error_message = "Запрос авторизации Telegram истёк"
        db.commit()
        raise HTTPException(status_code=410, detail="Запрос авторизации истёк")
    if challenge.created_by_id != user.id and user.role != UserRole.OWNER:
        raise HTTPException(status_code=403, detail="Этот запрос создан другим администратором")
    challenge.attempts += 1
    if challenge.attempts > 5:
        challenge.status = ChallengeStatus.CANCELLED
        challenge.payload_enc = cipher.encrypt_json(
            {"cancelled": True}, context=f"telegram-auth-challenge:{challenge.id}"
        )
        connection.status = ConnectionStatus.ERROR
        connection.last_error_code = "AUTH_ATTEMPTS_EXCEEDED"
        connection.last_error_message = "Превышено количество попыток авторизации Telegram"
        db.commit()
        raise HTTPException(status_code=429, detail="Превышено количество попыток")
    if not challenge.payload_enc:
        raise HTTPException(status_code=409, detail="Данные запроса авторизации отсутствуют")
    auth_payload = cipher.decrypt_json(
        challenge.payload_enc, context=f"telegram-auth-challenge:{challenge.id}"
    )
    try:
        if settings.telegram_fake_mode:
            if payload.code != "12345":
                db.commit()
                raise HTTPException(status_code=400, detail="Неверный тестовый код")
            identity = FakeTelegramGateway(ConnectionKind.USER, connection.id).get_identity()
            session_string = "fake-authorized-session"
        else:
            completed = MTProtoAuth.complete(
                api_id=int(auth_payload["api_id"]),
                api_hash=auth_payload["api_hash"],
                phone=auth_payload["phone"],
                session_string=auth_payload["session_string"],
                phone_code_hash=auth_payload["phone_code_hash"],
                code=payload.code,
                password=payload.password,
            )
            identity = completed.identity
            session_string = completed.session_string
    except TelegramGatewayError as exc:
        db.commit()
        raise _gateway_http_error(exc) from exc

    if identity.is_bot:
        db.rollback()
        raise HTTPException(status_code=400, detail="Ожидался пользовательский аккаунт, а не бот")
    connection.credentials_enc = cipher.encrypt_json(
        {
            "api_id": int(auth_payload["api_id"]),
            "api_hash": auth_payload["api_hash"],
            "phone": auth_payload["phone"],
            "session_string": session_string,
        },
        context=f"telegram-connection:{connection.id}",
    )
    connection.telegram_account_id = identity.account_id
    connection.telegram_username = identity.username
    connection.telegram_display_name = identity.display_name
    connection.status = ConnectionStatus.ACTIVE
    connection.last_checked_at = now
    challenge.status = ChallengeStatus.COMPLETED
    challenge.payload_enc = cipher.encrypt_json(
        {"completed": True}, context=f"telegram-auth-challenge:{challenge.id}"
    )
    write_audit(
        db,
        action="telegram.user_auth_completed",
        actor=user,
        entity_type="telegram_connection",
        entity_id=connection.id,
        details={"username": identity.username, "account_id": identity.account_id},
        request=request,
    )
    db.commit()
    db.refresh(connection)
    return connection


@router.patch("/{connection_id}", response_model=ConnectionRead)
def patch_connection(
    connection_id: str,
    payload: ConnectionPatch,
    request: Request,
    user: User = Depends(require_roles(UserRole.OWNER, UserRole.ADMIN)),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
) -> TelegramConnection:
    """Обновить connection. Переход применяется только после проверки его предусловий."""
    connection = _get_connection(db, connection_id, user.organization_id)
    data = payload.model_dump(exclude_unset=True)
    min_interval = data.get("min_interval_seconds", connection.min_interval_seconds)
    daily_cap = data.get("daily_cap", connection.daily_cap)
    cooldown = data.get("destination_cooldown_minutes", connection.destination_cooldown_minutes)
    _validate_safety(
        kind=connection.kind,
        min_interval=min_interval,
        daily_cap=daily_cap,
        cooldown=cooldown,
        settings=settings,
    )
    if connection.kind == ConnectionKind.USER:
        data["require_manual_approval"] = True
        data["stop_on_flood"] = True
    for key, value in data.items():
        if value is not None:
            setattr(connection, key, value)
    write_audit(
        db,
        action="telegram.connection_updated",
        actor=user,
        entity_type="telegram_connection",
        entity_id=connection.id,
        details=data,
        request=request,
    )
    db.commit()
    db.refresh(connection)
    return connection


@router.post("/{connection_id}/health", response_model=ConnectionHealthResponse)
def connection_health(
    connection_id: str,
    request: Request,
    user: User = Depends(require_roles(UserRole.OWNER, UserRole.ADMIN, UserRole.OPERATOR)),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
    cipher: SecretCipher = Depends(get_cipher),
) -> ConnectionHealthResponse:
    """Выполнить операцию connection health. Аргументы интерпретируются в контексте модуля,
    результат возвращается вызывающему коду.
    """
    connection = _get_connection(db, connection_id, user.organization_id)
    if connection.status == ConnectionStatus.REVOKED:
        raise HTTPException(status_code=409, detail="Подключение отозвано")
    try:
        identity = build_gateway(connection, settings, cipher).get_identity()
    except TelegramGatewayError as exc:
        connection.status = ConnectionStatus.ERROR if exc.requires_review else connection.status
        connection.last_error_code = exc.code
        connection.last_error_message = str(exc)
        connection.last_checked_at = utcnow()
        write_audit(
            db,
            action="telegram.connection_health_failed",
            actor=user,
            entity_type="telegram_connection",
            entity_id=connection.id,
            severity=SafetySeverity.WARNING,
            details={"code": exc.code},
            request=request,
        )
        db.commit()
        return ConnectionHealthResponse(
            ok=False, status=connection.status, error=str(exc), identity=None
        )
    connection.telegram_account_id = identity.account_id
    connection.telegram_username = identity.username
    connection.telegram_display_name = identity.display_name
    if connection.status == ConnectionStatus.ERROR:
        connection.status = ConnectionStatus.PAUSED
    connection.last_checked_at = utcnow()
    connection.last_error_code = None
    connection.last_error_message = None
    db.commit()
    return ConnectionHealthResponse(
        ok=True,
        status=connection.status,
        identity={
            "account_id": identity.account_id,
            "username": identity.username,
            "display_name": identity.display_name,
            "is_bot": identity.is_bot,
        },
    )


@router.get("/{connection_id}/discover", response_model=list[DiscoveredDestination])
def discover_destinations(
    connection_id: str,
    _user: User = Depends(require_roles(UserRole.OWNER, UserRole.ADMIN, UserRole.OPERATOR)),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
    cipher: SecretCipher = Depends(get_cipher),
) -> list[DiscoveredDestination]:
    """Выполнить операцию discover destinations. Аргументы интерпретируются в контексте модуля,
    результат возвращается вызывающему коду.
    """
    connection = _get_connection(db, connection_id, _user.organization_id)
    if connection.status != ConnectionStatus.ACTIVE:
        raise HTTPException(status_code=409, detail="Подключение не активно")
    try:
        items = build_gateway(connection, settings, cipher).list_destinations()
    except TelegramGatewayError as exc:
        raise _gateway_http_error(exc) from exc
    return [
        DiscoveredDestination(
            telegram_chat_id=item.chat_id,
            username=item.username,
            title=item.title,
            kind=item.kind,
        )
        for item in items
    ]


@router.post("/{connection_id}/pause", response_model=ConnectionRead)
def pause_connection(
    connection_id: str,
    request: Request,
    user: User = Depends(require_roles(UserRole.OWNER, UserRole.ADMIN, UserRole.OPERATOR)),
    db: Session = Depends(get_db),
) -> TelegramConnection:
    """Выполнить операцию pause connection. Аргументы интерпретируются в контексте модуля,
    результат возвращается вызывающему коду.
    """
    connection = _get_connection(db, connection_id, user.organization_id)
    if connection.status == ConnectionStatus.REVOKED:
        raise HTTPException(status_code=409, detail="Подключение отозвано")
    connection.status = ConnectionStatus.PAUSED
    write_audit(
        db,
        action="telegram.connection_paused",
        actor=user,
        entity_type="telegram_connection",
        entity_id=connection.id,
        request=request,
    )
    db.commit()
    db.refresh(connection)
    return connection


@router.post("/{connection_id}/resume", response_model=ConnectionRead)
def resume_connection(
    connection_id: str,
    payload: ResumeConnectionRequest,
    request: Request,
    user: User = Depends(require_roles(UserRole.OWNER, UserRole.ADMIN)),
    db: Session = Depends(get_db),
) -> TelegramConnection:
    """Выполнить операцию resume connection. Аргументы интерпретируются в контексте модуля,
    результат возвращается вызывающему коду.
    """
    connection = _get_connection(db, connection_id, user.organization_id)
    if connection.status == ConnectionStatus.REVOKED:
        raise HTTPException(status_code=409, detail="Подключение отозвано")
    now = utcnow()
    blocked_until = aware_utc(connection.flood_blocked_until)
    if blocked_until and blocked_until > now:
        raise HTTPException(
            status_code=409,
            detail=f"Telegram требует паузу до {blocked_until.isoformat()}",
        )
    if (
        connection.last_error_code
        in {
            "ANTI_SPAM_RESTRICTION",
            "TELEGRAM_AUTH_INVALID",
            "DELIVERY_RESULT_UNCERTAIN",
            "WORKER_CRASH_DURING_SEND",
        }
        and not payload.acknowledge_manual_review
    ):
        raise HTTPException(
            status_code=409,
            detail="Нужно подтвердить ручную проверку аккаунта перед возобновлением",
        )
    connection.status = ConnectionStatus.ACTIVE
    connection.flood_blocked_until = None
    connection.last_error_code = None
    connection.last_error_message = None
    db.execute(
        update(DeliveryJob)
        .where(
            DeliveryJob.connection_id == connection.id,
            DeliveryJob.organization_id == user.organization_id,
            DeliveryJob.status == JobStatus.WAITING_REVIEW,
            or_(
                DeliveryJob.error_code.is_(None),
                ~DeliveryJob.error_code.in_(
                    ["DELIVERY_RESULT_UNCERTAIN", "WORKER_CRASH_DURING_SEND"]
                ),
            ),
        )
        .values(status=JobStatus.RETRY, due_at=now, error_code=None, error_message=None)
    )
    write_audit(
        db,
        action="telegram.connection_resumed",
        actor=user,
        entity_type="telegram_connection",
        entity_id=connection.id,
        details={"manual_review_acknowledged": payload.acknowledge_manual_review},
        request=request,
    )
    db.commit()
    db.refresh(connection)
    return connection


@router.delete("/{connection_id}", response_model=MessageResponse)
def revoke_connection(
    connection_id: str,
    request: Request,
    user: User = Depends(require_roles(UserRole.OWNER)),
    db: Session = Depends(get_db),
    cipher: SecretCipher = Depends(get_cipher),
) -> MessageResponse:
    """Безопасно выполнить revoke connection. Зависимое состояние и видимые в аудите последствия
    обрабатываются согласованно.
    """
    connection = _get_connection(db, connection_id, user.organization_id)
    connection.status = ConnectionStatus.REVOKED
    connection.revoked_at = utcnow()
    connection.credentials_enc = cipher.encrypt_json(
        {"revoked": True}, context=f"telegram-connection:{connection.id}"
    )
    preserved_uncertain_jobs = int(
        db.scalar(
            select(func.count(DeliveryJob.id)).where(
                DeliveryJob.connection_id == connection.id,
                DeliveryJob.organization_id == user.organization_id,
                DeliveryJob.status == JobStatus.WAITING_REVIEW,
                DeliveryJob.error_code.in_(
                    ["DELIVERY_RESULT_UNCERTAIN", "WORKER_CRASH_DURING_SEND"]
                ),
            )
        )
        or 0
    )
    result = db.execute(
        update(DeliveryJob)
        .where(
            DeliveryJob.connection_id == connection.id,
            DeliveryJob.organization_id == user.organization_id,
            or_(
                DeliveryJob.status.in_([JobStatus.PENDING, JobStatus.RETRY]),
                and_(
                    DeliveryJob.status == JobStatus.WAITING_REVIEW,
                    or_(
                        DeliveryJob.error_code.is_(None),
                        ~DeliveryJob.error_code.in_(
                            ["DELIVERY_RESULT_UNCERTAIN", "WORKER_CRASH_DURING_SEND"]
                        ),
                    ),
                ),
            ),
        )
        .values(status=JobStatus.CANCELLED, finished_at=utcnow(), error_code="CONNECTION_REVOKED")
    )
    write_audit(
        db,
        action="telegram.connection_revoked",
        actor=user,
        entity_type="telegram_connection",
        entity_id=connection.id,
        severity=SafetySeverity.WARNING,
        details={
            "cancelled_jobs": int(getattr(result, "rowcount", 0) or 0),
            "preserved_uncertain_jobs": preserved_uncertain_jobs,
        },
        request=request,
    )
    db.commit()
    message = "Подключение отозвано, секреты уничтожены"
    if preserved_uncertain_jobs:
        message += (
            f"; неоднозначных доставок сохранено для ручной сверки: {preserved_uncertain_jobs}"
        )
    return MessageResponse(message=message)
