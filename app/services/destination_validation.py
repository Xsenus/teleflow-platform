from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session

from app.config import Settings
from app.enums import DestinationValidationStatus
from app.models import Destination, DestinationValidationRecord, User, utcnow
from app.security import aware_utc
from app.services.telegram.base import TelegramDestinationInfo


def effective_validation_expiry(
    destination: Destination,
    settings: Settings,
) -> datetime | None:
    """Выполнить операцию effective validation expiry. Аргументы интерпретируются в контексте
    модуля, результат возвращается вызывающему коду.
    """
    explicit = aware_utc(destination.validation_expires_at)
    if explicit is not None:
        return explicit
    checked_at = aware_utc(destination.validated_at)
    if checked_at is None:
        return None
    return checked_at + timedelta(hours=settings.destination_validation_ttl_hours)


def validation_is_fresh(
    destination: Destination,
    settings: Settings,
    *,
    now: datetime | None = None,
) -> bool:
    """Выполнить операцию validation is fresh. Аргументы интерпретируются в контексте модуля,
    результат возвращается вызывающему коду.
    """
    if not destination.validated:
        return False
    expiry = effective_validation_expiry(destination, settings)
    return bool(expiry and expiry > (aware_utc(now) or utcnow()))


def record_validation_success(
    db: Session,
    *,
    destination: Destination,
    resolved: TelegramDestinationInfo,
    settings: Settings,
    checked_by: User | None,
    source: str,
    display_title: str | None = None,
    now: datetime | None = None,
) -> DestinationValidationRecord:
    """Выполнить операцию record validation success. Аргументы интерпретируются в контексте модуля,
    результат возвращается вызывающему коду.
    """
    now = aware_utc(now) or utcnow()
    can_send = resolved.capabilities.get("can_send_messages") is not False
    status = (
        DestinationValidationStatus.PASSED
        if can_send
        else DestinationValidationStatus.WRITE_FORBIDDEN
    )
    destination.telegram_chat_id = resolved.chat_id
    destination.username = resolved.username
    destination.title = display_title or resolved.title
    destination.kind = resolved.kind
    destination.validated = can_send
    destination.validated_at = now
    destination.validation_expires_at = now + timedelta(
        hours=settings.destination_validation_ttl_hours
    )
    destination.last_error_code = None if can_send else "WRITE_PERMISSION_MISSING"
    destination.last_error_message = (
        None if can_send else "У подключения нет права отправлять сообщения"
    )
    destination.consecutive_failures = 0 if can_send else destination.consecutive_failures + 1
    if not can_send:
        destination.enabled = False

    record = DestinationValidationRecord(
        organization_id=destination.organization_id,
        destination_id=destination.id,
        connection_id=destination.connection_id,
        status=status,
        capabilities=dict(resolved.capabilities or {}),
        error_code=destination.last_error_code,
        error_message=destination.last_error_message,
        source=source,
        checked_by_id=checked_by.id if checked_by else None,
        checked_at=now,
    )
    db.add(record)
    return record


def record_validation_failure(
    db: Session,
    *,
    destination: Destination,
    error_code: str,
    error_message: str,
    checked_by: User | None,
    source: str,
    capabilities: dict[str, Any] | None = None,
    now: datetime | None = None,
) -> DestinationValidationRecord:
    """Выполнить операцию record validation failure. Аргументы интерпретируются в контексте модуля,
    результат возвращается вызывающему коду.
    """
    now = aware_utc(now) or utcnow()
    write_forbidden = error_code in {"CHAT_WRITE_FORBIDDEN", "WRITE_PERMISSION_MISSING"}
    destination.validated = False
    destination.validated_at = now
    destination.validation_expires_at = now
    destination.last_error_code = error_code
    destination.last_error_message = error_message
    destination.consecutive_failures += 1
    if write_forbidden:
        destination.enabled = False
    record = DestinationValidationRecord(
        organization_id=destination.organization_id,
        destination_id=destination.id,
        connection_id=destination.connection_id,
        status=(
            DestinationValidationStatus.WRITE_FORBIDDEN
            if write_forbidden
            else DestinationValidationStatus.FAILED
        ),
        capabilities=dict(capabilities or {}),
        error_code=error_code,
        error_message=error_message,
        source=source,
        checked_by_id=checked_by.id if checked_by else None,
        checked_at=now,
    )
    db.add(record)
    return record
