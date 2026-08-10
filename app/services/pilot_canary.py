from __future__ import annotations

import hashlib
from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import Settings
from app.enums import (
    ConnectionKind,
    ConnectionStatus,
    JobStatus,
    OrganizationStatus,
    ParseMode,
    PermissionStatus,
    PilotCanaryStatus,
    SafetySeverity,
)
from app.models import (
    DeliveryJob,
    Destination,
    Organization,
    PilotCanaryAttempt,
    TelegramConnection,
    User,
    new_id,
    utcnow,
)
from app.security import aware_utc
from app.services.blackouts import evaluate_blackouts
from app.services.crypto import SecretCipher
from app.services.destination_validation import (
    record_validation_failure,
    validation_is_fresh,
)
from app.services.destination_windows import evaluate_destination_window
from app.services.notifications import create_notification
from app.services.telegram.errors import (
    TelegramAntiSpamRestriction,
    TelegramAuthError,
    TelegramDeliveryUncertain,
    TelegramFloodWait,
    TelegramGatewayError,
    TelegramWriteForbidden,
)
from app.services.telegram.factory import build_gateway

CANARY_CONFIRMATION = "ОТПРАВИТЬ СЛУЖЕБНОЕ СООБЩЕНИЕ"


def canary_body(marker: str) -> str:
    """Выполнить операцию canary body. Аргументы интерпретируются в контексте модуля, результат
    возвращается вызывающему коду.
    """
    return (
        "TeleFlow — служебная проверка публикации\n"
        f"Маркер: {marker}\n"
        "Это единичное тестовое сообщение в заранее разрешённой группе. "
        "Автоматический повтор отключён. Сообщение можно удалить после проверки."
    )


def _blocked_attempt(
    attempt: PilotCanaryAttempt,
    *,
    code: str,
    message: str,
    now: datetime,
) -> PilotCanaryAttempt:
    """Реализовать внутренний этап blocked attempt step. Вспомогательная функция сохраняет
    детерминированность и тестируемость процесса.
    """
    attempt.status = PilotCanaryStatus.BLOCKED
    attempt.error_code = code
    attempt.error_message = message
    attempt.completed_at = now
    return attempt


def _sent_today(
    db: Session,
    *,
    organization_id: str,
    connection_id: str | None,
    now: datetime,
) -> int:
    """Реализовать внутренний этап sent today step. Вспомогательная функция сохраняет
    детерминированность и тестируемость процесса.
    """
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    jobs_stmt = select(func.count(DeliveryJob.id)).where(
        DeliveryJob.organization_id == organization_id,
        DeliveryJob.status == JobStatus.SENT,
        DeliveryJob.finished_at >= day_start,
    )
    canary_stmt = select(func.count(PilotCanaryAttempt.id)).where(
        PilotCanaryAttempt.organization_id == organization_id,
        PilotCanaryAttempt.status == PilotCanaryStatus.SENT,
        PilotCanaryAttempt.completed_at >= day_start,
    )
    if connection_id:
        jobs_stmt = jobs_stmt.where(DeliveryJob.connection_id == connection_id)
        canary_stmt = canary_stmt.where(PilotCanaryAttempt.connection_id == connection_id)
    return int(db.scalar(jobs_stmt) or 0) + int(db.scalar(canary_stmt) or 0)


def create_canary_attempt(
    db: Session,
    *,
    destination: Destination,
    connection: TelegramConnection,
    requested_by: User,
    settings: Settings,
    cipher: SecretCipher,
    confirmation: str,
    now: datetime | None = None,
) -> PilotCanaryAttempt:
    """Создать canary attempt. Перед сохранением или возвратом нового значения проверяются
    связанные инварианты.
    """
    now = aware_utc(now) or utcnow()
    if confirmation.strip() != CANARY_CONFIRMATION:
        raise ValueError(f"Для отправки введите: {CANARY_CONFIRMATION}")

    marker = f"TF-CANARY-{new_id().replace('-', '')[:12].upper()}"
    body = canary_body(marker)
    attempt = PilotCanaryAttempt(
        organization_id=destination.organization_id,
        connection_id=connection.id,
        destination_id=destination.id,
        status=PilotCanaryStatus.PENDING,
        marker=marker,
        body_sha256=hashlib.sha256(body.encode("utf-8")).hexdigest(),
        is_fake=settings.telegram_fake_mode,
        requested_by_id=requested_by.id,
        started_at=now,
        created_at=now,
    )
    db.add(attempt)
    db.flush()

    if connection.status != ConnectionStatus.ACTIVE:
        return _blocked_attempt(
            attempt,
            code="CONNECTION_NOT_ACTIVE",
            message="Telegram-подключение не активно",
            now=now,
        )
    if not destination.enabled:
        return _blocked_attempt(
            attempt,
            code="DESTINATION_DISABLED",
            message="Назначение отключено",
            now=now,
        )
    if destination.permission_status != PermissionStatus.CONFIRMED:
        return _blocked_attempt(
            attempt,
            code="PERMISSION_NOT_CONFIRMED",
            message="Разрешение на публикацию не подтверждено",
            now=now,
        )
    if not destination.permission_note and not destination.rules_url:
        return _blocked_attempt(
            attempt,
            code="PERMISSION_EVIDENCE_MISSING",
            message="Не зафиксировано основание разрешения на публикацию",
            now=now,
        )
    permission_expiry = aware_utc(destination.permission_expires_at)
    if permission_expiry and permission_expiry <= now:
        return _blocked_attempt(
            attempt,
            code="PERMISSION_EXPIRED",
            message="Срок разрешения на публикацию истёк",
            now=now,
        )
    if not validation_is_fresh(destination, settings, now=now):
        return _blocked_attempt(
            attempt,
            code="DESTINATION_VALIDATION_STALE",
            message="Проверка доступа Telegram отсутствует или устарела",
            now=now,
        )
    if destination.telegram_chat_id is None:
        return _blocked_attempt(
            attempt,
            code="DESTINATION_CHAT_ID_MISSING",
            message="После Telegram-проверки не сохранён chat ID",
            now=now,
        )

    organization = db.get(Organization, destination.organization_id)
    if organization is None or organization.status != OrganizationStatus.ACTIVE:
        return _blocked_attempt(
            attempt,
            code="ORGANIZATION_NOT_ACTIVE",
            message="Организация не активна",
            now=now,
        )
    if organization.publishing_paused:
        return _blocked_attempt(
            attempt,
            code="ORGANIZATION_PUBLISHING_PAUSED",
            message=organization.publishing_pause_reason or "Публикации организации остановлены",
            now=now,
        )

    blackout = evaluate_blackouts(
        db,
        organization_id=destination.organization_id,
        connection_id=connection.id,
        destination_id=destination.id,
        at=now,
    )
    if blackout.active:
        blackout_message = blackout.reason
        if not blackout_message:
            blackout_message = (
                f"Действует запрет публикации до {blackout.defer_until.isoformat()}"
                if blackout.defer_until
                else "Действует запрет публикации"
            )
        return _blocked_attempt(
            attempt,
            code="PUBLISHING_BLACKOUT",
            message=blackout_message,
            now=now,
        )

    window = evaluate_destination_window(
        destination,
        fallback_timezone=organization.timezone_name if organization else "UTC",
        now=now,
    )
    if not window.allowed:
        return _blocked_attempt(
            attempt,
            code="DESTINATION_TIME_WINDOW",
            message=(
                "Служебная проверка вне разрешённого локального окна"
                + (
                    f"; ближайшее время {window.defer_until.isoformat()}"
                    if window.defer_until
                    else ""
                )
            ),
            now=now,
        )

    latest = db.scalar(
        select(PilotCanaryAttempt)
        .where(
            PilotCanaryAttempt.organization_id == destination.organization_id,
            PilotCanaryAttempt.destination_id == destination.id,
            PilotCanaryAttempt.status == PilotCanaryStatus.SENT,
            PilotCanaryAttempt.id != attempt.id,
        )
        .order_by(PilotCanaryAttempt.completed_at.desc())
        .limit(1)
    )
    latest_completed_at = aware_utc(latest.completed_at) if latest else None
    if latest_completed_at is not None:
        next_canary = latest_completed_at + timedelta(hours=settings.pilot_canary_cooldown_hours)
        if next_canary > now:
            return _blocked_attempt(
                attempt,
                code="CANARY_COOLDOWN",
                message=f"Следующая служебная проверка допустима после {next_canary.isoformat()}",
                now=now,
            )

    blocked_until = aware_utc(connection.flood_blocked_until)
    if blocked_until and blocked_until > now:
        return _blocked_attempt(
            attempt,
            code="CONNECTION_FLOOD_BLOCKED",
            message=f"Telegram потребовал паузу до {blocked_until.isoformat()}",
            now=now,
        )
    next_allowed = aware_utc(destination.next_allowed_at)
    if next_allowed and next_allowed > now:
        return _blocked_attempt(
            attempt,
            code="DESTINATION_COOLDOWN",
            message=f"Для группы действует cooldown до {next_allowed.isoformat()}",
            now=now,
        )
    last_delivery = aware_utc(connection.last_delivery_at)
    hard_minimum = (
        settings.user_hard_min_interval_seconds
        if connection.kind == ConnectionKind.USER
        else settings.bot_hard_min_interval_seconds
    )
    effective_interval = max(connection.min_interval_seconds, hard_minimum)
    if last_delivery:
        next_connection_send = last_delivery + timedelta(seconds=effective_interval)
        if next_connection_send > now:
            return _blocked_attempt(
                attempt,
                code="CONNECTION_INTERVAL",
                message=f"Интервал подключения действует до {next_connection_send.isoformat()}",
                now=now,
            )

    connection_sent = _sent_today(
        db,
        organization_id=destination.organization_id,
        connection_id=connection.id,
        now=now,
    )
    if connection_sent >= min(connection.daily_cap, settings.global_hard_daily_cap):
        return _blocked_attempt(
            attempt,
            code="DAILY_CAP_REACHED",
            message="Достигнут дневной лимит Telegram-подключения",
            now=now,
        )
    organization_sent = _sent_today(
        db,
        organization_id=destination.organization_id,
        connection_id=None,
        now=now,
    )
    if organization_sent >= settings.global_hard_daily_cap:
        return _blocked_attempt(
            attempt,
            code="GLOBAL_DAILY_CAP_REACHED",
            message="Достигнут системный дневной предел отправок",
            now=now,
        )

    try:
        gateway = build_gateway(connection, settings, cipher)
        result = gateway.send_message(
            chat_id=destination.telegram_chat_id,
            topic_id=destination.topic_id,
            body=body,
            parse_mode=ParseMode.PLAIN,
            link_preview=False,
        )
    except TelegramFloodWait as exc:
        delay = max(int(exc.retry_after or 60), 1)
        connection.status = ConnectionStatus.PAUSED
        connection.flood_blocked_until = now + timedelta(seconds=delay)
        connection.last_error_code = exc.code
        connection.last_error_message = str(exc)
        _blocked_attempt(attempt, code=exc.code, message=str(exc), now=now)
        create_notification(
            db,
            settings=settings,
            organization_id=destination.organization_id,
            event_type="pilot.canary_flood_wait",
            title="Canary остановлена ограничением Telegram",
            message=f"Подключение приостановлено на {delay} секунд",
            severity=SafetySeverity.CRITICAL,
            entity_type="pilot_canary_attempt",
            entity_id=attempt.id,
            dedup_key=f"pilot-canary-flood:{connection.id}:{connection.flood_blocked_until.isoformat()}",
            details={"connection_id": connection.id, "retry_after": delay},
        )
    except TelegramWriteForbidden as exc:
        record_validation_failure(
            db,
            destination=destination,
            error_code=exc.code,
            error_message=str(exc),
            checked_by=requested_by,
            source="pilot_canary",
            now=now,
        )
        _blocked_attempt(attempt, code=exc.code, message=str(exc), now=now)
    except (TelegramAntiSpamRestriction, TelegramAuthError, TelegramDeliveryUncertain) as exc:
        connection.status = (
            ConnectionStatus.ERROR
            if isinstance(exc, TelegramAuthError)
            else ConnectionStatus.PAUSED
        )
        connection.last_error_code = exc.code
        connection.last_error_message = str(exc)
        _blocked_attempt(attempt, code=exc.code, message=str(exc), now=now)
        create_notification(
            db,
            settings=settings,
            organization_id=destination.organization_id,
            event_type="pilot.canary_requires_review",
            title="Canary требует ручной проверки",
            message=str(exc),
            severity=SafetySeverity.CRITICAL,
            entity_type="pilot_canary_attempt",
            entity_id=attempt.id,
            dedup_key=f"pilot-canary-review:{attempt.id}",
            details={"connection_id": connection.id, "error_code": exc.code},
        )
    except TelegramGatewayError as exc:
        attempt.status = PilotCanaryStatus.FAILED
        attempt.error_code = exc.code
        attempt.error_message = str(exc)
        attempt.completed_at = now
    except Exception as exc:  # defensive: canary must never auto-retry on unknown failure
        attempt.status = PilotCanaryStatus.FAILED
        attempt.error_code = "CANARY_UNEXPECTED_ERROR"
        attempt.error_message = f"{type(exc).__name__}: unexpected gateway failure"
        attempt.completed_at = now
    else:
        attempt.status = PilotCanaryStatus.SENT
        attempt.telegram_message_id = result.message_id
        attempt.completed_at = utcnow()
        connection.last_delivery_at = attempt.completed_at
        connection.last_error_code = None
        connection.last_error_message = None
        destination.last_sent_at = attempt.completed_at
        cooldown = (
            destination.cooldown_minutes_override
            if destination.cooldown_minutes_override is not None
            else connection.destination_cooldown_minutes
        )
        destination.next_allowed_at = attempt.completed_at + timedelta(minutes=max(cooldown, 1))
        destination.last_error_code = None
        destination.last_error_message = None
        destination.consecutive_failures = 0
    return attempt
