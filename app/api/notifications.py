from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.audit import write_audit
from app.database import get_db
from app.dependencies import get_current_user, require_roles
from app.enums import NotificationStatus, SafetySeverity, UserRole
from app.models import Notification, User, utcnow
from app.schemas import MessageResponse, NotificationCountRead, NotificationRead

router = APIRouter(prefix="/notifications", tags=["notifications"])


@router.get("", response_model=list[NotificationRead])
def list_notifications(
    status_filter: NotificationStatus | None = None,
    severity: SafetySeverity | None = None,
    limit: int = 200,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[Notification]:
    """Прочитать notifications. Значение возвращается без несвязанных изменений состояния."""
    limit = min(max(limit, 1), 1000)
    stmt = (
        select(Notification)
        .where(Notification.organization_id == user.organization_id)
        .order_by(Notification.last_occurred_at.desc(), Notification.created_at.desc())
        .limit(limit)
    )
    if status_filter:
        stmt = stmt.where(Notification.status == status_filter)
    if severity:
        stmt = stmt.where(Notification.severity == severity)
    return list(db.scalars(stmt).all())


@router.get("/counts", response_model=NotificationCountRead)
def notification_counts(
    user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> NotificationCountRead:
    """Выполнить операцию notification counts. Аргументы интерпретируются в контексте модуля,
    результат возвращается вызывающему коду.
    """
    unread = (
        db.scalar(
            select(func.count(Notification.id)).where(
                Notification.organization_id == user.organization_id,
                Notification.status == NotificationStatus.UNREAD,
            )
        )
        or 0
    )
    critical = (
        db.scalar(
            select(func.count(Notification.id)).where(
                Notification.organization_id == user.organization_id,
                Notification.severity == SafetySeverity.CRITICAL,
                Notification.status != NotificationStatus.ACKNOWLEDGED,
            )
        )
        or 0
    )
    return NotificationCountRead(unread=int(unread), critical_unacknowledged=int(critical))


def _get_notification(db: Session, notification_id: str, organization_id: str) -> Notification:
    """Реализовать внутренний этап get notification step. Вспомогательная функция сохраняет
    детерминированность и тестируемость процесса.
    """
    item = db.scalar(
        select(Notification).where(
            Notification.id == notification_id,
            Notification.organization_id == organization_id,
        )
    )
    if not item:
        raise HTTPException(status_code=404, detail="Уведомление не найдено")
    return item


@router.post("/{notification_id}/read", response_model=NotificationRead)
def mark_read(
    notification_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Notification:
    """Обновить read. Переход применяется только после проверки его предусловий."""
    item = _get_notification(db, notification_id, user.organization_id)
    if item.status == NotificationStatus.UNREAD:
        item.status = NotificationStatus.READ
        item.read_at = utcnow()
        item.read_by_id = user.id
        db.commit()
        db.refresh(item)
    return item


@router.post("/read-all", response_model=MessageResponse)
def mark_all_read(
    user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> MessageResponse:
    """Обновить all read. Переход применяется только после проверки его предусловий."""
    now = utcnow()
    result = db.execute(
        update(Notification)
        .where(
            Notification.organization_id == user.organization_id,
            Notification.status == NotificationStatus.UNREAD,
        )
        .values(status=NotificationStatus.READ, read_at=now, read_by_id=user.id)
    )
    db.commit()
    updated = int(getattr(result, "rowcount", 0) or 0)
    return MessageResponse(message=f"Прочитано уведомлений: {updated}")


@router.post("/{notification_id}/acknowledge", response_model=NotificationRead)
def acknowledge_notification(
    notification_id: str,
    request: Request,
    user: User = Depends(require_roles(UserRole.OWNER, UserRole.ADMIN)),
    db: Session = Depends(get_db),
) -> Notification:
    """Выполнить операцию acknowledge notification. Аргументы интерпретируются в контексте модуля,
    результат возвращается вызывающему коду.
    """
    item = _get_notification(db, notification_id, user.organization_id)
    if item.status != NotificationStatus.ACKNOWLEDGED:
        now = utcnow()
        item.status = NotificationStatus.ACKNOWLEDGED
        item.read_at = item.read_at or now
        item.read_by_id = item.read_by_id or user.id
        item.acknowledged_at = now
        item.acknowledged_by_id = user.id
        write_audit(
            db,
            actor=user,
            action="notification.acknowledged",
            entity_type="notification",
            entity_id=item.id,
            details={"event_type": item.event_type, "severity": item.severity.value},
            request=request,
        )
        db.commit()
        db.refresh(item)
    return item
