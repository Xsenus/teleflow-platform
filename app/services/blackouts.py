from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.enums import BlackoutKind, BlackoutScope
from app.models import PublishingBlackout, utcnow
from app.security import aware_utc


@dataclass(frozen=True)
class ActiveBlackout:
    blackout_id: str
    title: str
    reason: str
    scope: BlackoutScope
    active_until: datetime


@dataclass(frozen=True)
class BlackoutDecision:
    active: bool
    defer_until: datetime | None
    matches: tuple[ActiveBlackout, ...]

    @property
    def reason(self) -> str | None:
        """Выполнить операцию reason класса BlackoutDecision. Аргументы интерпретируются в
        контексте модуля, результат возвращается вызывающему коду.
        """
        if not self.matches:
            return None
        titles = ", ".join(item.title for item in self.matches)
        return f"Действует запрет публикаций: {titles}"


def _zone(name: str | None) -> ZoneInfo:
    """Реализовать внутренний этап zone step. Вспомогательная функция сохраняет детерминированность
    и тестируемость процесса.
    """
    try:
        return ZoneInfo(name or "UTC")
    except ZoneInfoNotFoundError:
        return ZoneInfo("UTC")


def _weekly_active_until(blackout: PublishingBlackout, at: datetime) -> datetime | None:
    """Реализовать внутренний этап weekly active until step. Вспомогательная функция сохраняет
    детерминированность и тестируемость процесса.
    """
    if not blackout.weekdays or blackout.start_time is None or blackout.end_time is None:
        return None
    zone = _zone(blackout.timezone_name)
    local = at.astimezone(zone)
    local_time = local.timetz().replace(tzinfo=None)
    weekdays = set(int(day) for day in blackout.weekdays)
    start = blackout.start_time
    end = blackout.end_time

    if start < end:
        if local.weekday() not in weekdays or not (start <= local_time < end):
            return None
        end_local = datetime.combine(local.date(), end, tzinfo=zone)
        return end_local.astimezone(UTC)

    # Overnight windows are anchored to the weekday on which they start.
    if local.weekday() in weekdays and local_time >= start:
        end_local = datetime.combine(local.date() + timedelta(days=1), end, tzinfo=zone)
        return end_local.astimezone(UTC)
    previous_day = (local.weekday() - 1) % 7
    if previous_day in weekdays and local_time < end:
        end_local = datetime.combine(local.date(), end, tzinfo=zone)
        return end_local.astimezone(UTC)
    return None


def blackout_active_until(blackout: PublishingBlackout, at: datetime) -> datetime | None:
    """Выполнить операцию blackout active until. Аргументы интерпретируются в контексте модуля,
    результат возвращается вызывающему коду.
    """
    at = aware_utc(at) or at.replace(tzinfo=UTC)
    if not blackout.enabled:
        return None
    if blackout.kind == BlackoutKind.ONE_TIME:
        starts_at = aware_utc(blackout.starts_at)
        ends_at = aware_utc(blackout.ends_at)
        if starts_at and ends_at and starts_at <= at < ends_at:
            return ends_at
        return None
    return _weekly_active_until(blackout, at)


def _applies(
    blackout: PublishingBlackout,
    *,
    connection_id: str | None,
    destination_id: str | None,
) -> bool:
    """Реализовать внутренний этап applies step. Вспомогательная функция сохраняет
    детерминированность и тестируемость процесса.
    """
    if blackout.scope == BlackoutScope.ORGANIZATION:
        return True
    if blackout.scope == BlackoutScope.CONNECTION:
        return bool(connection_id and blackout.connection_id == connection_id)
    if blackout.scope == BlackoutScope.DESTINATION:
        return bool(destination_id and blackout.destination_id == destination_id)
    return False


def evaluate_blackouts(
    db: Session,
    *,
    organization_id: str,
    connection_id: str | None = None,
    destination_id: str | None = None,
    at: datetime | None = None,
) -> BlackoutDecision:
    """Выполнить операцию evaluate blackouts. Аргументы интерпретируются в контексте модуля,
    результат возвращается вызывающему коду.
    """
    at = aware_utc(at) or utcnow()
    candidates = list(
        db.scalars(
            select(PublishingBlackout).where(
                PublishingBlackout.organization_id == organization_id,
                PublishingBlackout.enabled.is_(True),
            )
        ).all()
    )
    matches: list[ActiveBlackout] = []
    for blackout in candidates:
        if not _applies(
            blackout,
            connection_id=connection_id,
            destination_id=destination_id,
        ):
            continue
        active_until = blackout_active_until(blackout, at)
        if active_until is None:
            continue
        matches.append(
            ActiveBlackout(
                blackout_id=blackout.id,
                title=blackout.title,
                reason=blackout.reason,
                scope=blackout.scope,
                active_until=active_until,
            )
        )
    matches.sort(key=lambda item: (item.active_until, item.blackout_id))
    return BlackoutDecision(
        active=bool(matches),
        defer_until=max((item.active_until for item in matches), default=None),
        matches=tuple(matches),
    )
