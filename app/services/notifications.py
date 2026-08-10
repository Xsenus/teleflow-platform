from __future__ import annotations

from datetime import timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit import redact
from app.config import Settings
from app.enums import NotificationStatus, SafetySeverity
from app.models import Notification, utcnow
from app.services.outbox import enqueue_event


def create_notification(
    db: Session,
    *,
    settings: Settings,
    organization_id: str,
    event_type: str,
    title: str,
    message: str,
    severity: SafetySeverity = SafetySeverity.INFO,
    entity_type: str | None = None,
    entity_id: str | None = None,
    dedup_key: str | None = None,
    details: dict[str, Any] | None = None,
    emit_outbox: bool = True,
) -> Notification:
    """Создать or coalesce an in-app operational notification. Deduplication prevents a noisy
    Telegram failure from creating hundreds of identical rows while preserving the occurrence
    counter and last timestamp.
    """

    now = utcnow()
    safe_details = redact(details or {})
    existing: Notification | None = None
    if dedup_key:
        cutoff = now - timedelta(minutes=settings.notification_dedup_minutes)
        existing = db.scalar(
            select(Notification)
            .where(
                Notification.organization_id == organization_id,
                Notification.dedup_key == dedup_key,
                Notification.last_occurred_at >= cutoff,
                Notification.status != NotificationStatus.ACKNOWLEDGED,
            )
            .order_by(Notification.last_occurred_at.desc())
            .limit(1)
            .with_for_update()
        )
    if existing:
        existing.occurrence_count += 1
        existing.last_occurred_at = now
        existing.severity = severity
        existing.title = title[:220]
        existing.message = message[:4000]
        existing.details = safe_details
        existing.status = NotificationStatus.UNREAD
        existing.read_at = None
        existing.read_by_id = None
        item = existing
    else:
        item = Notification(
            organization_id=organization_id,
            event_type=event_type[:120],
            severity=severity,
            status=NotificationStatus.UNREAD,
            title=title[:220],
            message=message[:4000],
            entity_type=entity_type,
            entity_id=entity_id,
            dedup_key=dedup_key[:240] if dedup_key else None,
            details=safe_details,
            occurrence_count=1,
            last_occurred_at=now,
            created_at=now,
        )
        db.add(item)
        db.flush()

    if emit_outbox:
        enqueue_event(
            db,
            organization_id=organization_id,
            event_type="notification.created",
            aggregate_type="notification",
            aggregate_id=item.id,
            payload={
                "notification_id": item.id,
                "event_type": event_type,
                "severity": severity.value,
                "title": item.title,
                "message": item.message,
                "entity_type": entity_type,
                "entity_id": entity_id,
                "occurrence_count": item.occurrence_count,
                "created_at": item.created_at.isoformat(),
                "last_occurred_at": item.last_occurred_at.isoformat(),
            },
        )
    return item
