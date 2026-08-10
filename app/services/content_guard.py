from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.enums import JobStatus, ParseMode
from app.models import DeliveryJob
from app.security import aware_utc

_WHITESPACE_RE = re.compile(r"\s+")


def normalize_message_body(body: str) -> str:
    """Нормализовать only insignificant whitespace for duplicate detection. The guard intentionally
    does not perform fuzzy matching or mutate content. It prevents accidental re-send of the
    same approved snapshot; it is not an anti-spam evasion mechanism.
    """

    return _WHITESPACE_RE.sub(" ", body.strip()).casefold()


def content_fingerprint(
    *,
    body: str,
    parse_mode: ParseMode,
    media_sha256: str | None,
    link_preview: bool,
) -> str:
    """Вычислить content fingerprint. Канонический ввод обеспечивает детерминированное сравнение
    целостности между процессами.
    """
    payload = {
        "body": normalize_message_body(body),
        "parse_mode": parse_mode.value,
        "media_sha256": media_sha256,
        "link_preview": bool(link_preview),
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def find_recent_duplicate(
    db: Session,
    *,
    organization_id: str,
    destination_id: str,
    fingerprint: str | None,
    lookback_minutes: int,
    now: datetime,
    exclude_job_id: str | None = None,
) -> DeliveryJob | None:
    """Прочитать recent duplicate. Значение возвращается без несвязанных изменений состояния."""
    if not fingerprint or lookback_minutes <= 0:
        return None
    now_utc = aware_utc(now) or now
    cutoff = now_utc - timedelta(minutes=lookback_minutes)
    stmt = (
        select(DeliveryJob)
        .where(
            DeliveryJob.organization_id == organization_id,
            DeliveryJob.destination_id == destination_id,
            DeliveryJob.content_fingerprint == fingerprint,
            DeliveryJob.status == JobStatus.SENT,
            DeliveryJob.finished_at.is_not(None),
            DeliveryJob.finished_at >= cutoff,
        )
        .order_by(DeliveryJob.finished_at.desc())
        .limit(1)
    )
    if exclude_job_id:
        stmt = stmt.where(DeliveryJob.id != exclude_job_id)
    return db.scalar(stmt)
