from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.models import Destination
from app.security import aware_utc


@dataclass(frozen=True, slots=True)
class DestinationWindowDecision:
    allowed: bool
    timezone_name: str
    defer_until: datetime | None = None


def effective_destination_timezone(destination: Destination, fallback: str) -> ZoneInfo:
    """Выполнить операцию effective destination timezone. Аргументы интерпретируются в контексте
    модуля, результат возвращается вызывающему коду.
    """
    name = destination.timezone_name or fallback or "UTC"
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError:
        return ZoneInfo("UTC")


def _window_for_date(
    start_date: date,
    *,
    zone: ZoneInfo,
    start_time: time | None,
    end_time: time | None,
) -> tuple[datetime, datetime]:
    """Реализовать внутренний этап window for date step. Вспомогательная функция сохраняет
    детерминированность и тестируемость процесса.
    """
    if start_time is None or end_time is None:
        start = datetime.combine(start_date, time.min, tzinfo=zone)
        return start, start + timedelta(days=1)
    start = datetime.combine(start_date, start_time, tzinfo=zone)
    end_date = start_date + timedelta(days=1) if end_time <= start_time else start_date
    end = datetime.combine(end_date, end_time, tzinfo=zone)
    return start, end


def evaluate_destination_window(
    destination: Destination,
    *,
    fallback_timezone: str,
    now: datetime,
) -> DestinationWindowDecision:
    """Вернуть whether a delivery is inside the destination's explicit local window. An empty
    weekday list means every day. A window crossing midnight belongs to the weekday on which it
    starts, so Friday 22:00–06:00 also allows Saturday 02:00.
    """

    now_utc = aware_utc(now) or now.replace(tzinfo=UTC)
    zone = effective_destination_timezone(destination, fallback_timezone)
    zone_name = destination.timezone_name or fallback_timezone or "UTC"
    weekdays = set(destination.allowed_weekdays or range(7))
    start_time = destination.allowed_start_time
    end_time = destination.allowed_end_time

    if not destination.allowed_weekdays and start_time is None and end_time is None:
        return DestinationWindowDecision(True, zone_name)

    local_now = now_utc.astimezone(zone)

    # Check yesterday as well because an overnight window may still be open today.
    for offset in (-1, 0):
        start_date = local_now.date() + timedelta(days=offset)
        if start_date.weekday() not in weekdays:
            continue
        window_start, window_end = _window_for_date(
            start_date,
            zone=zone,
            start_time=start_time,
            end_time=end_time,
        )
        if window_start <= local_now < window_end:
            return DestinationWindowDecision(True, zone_name)

    # Seven weekdays plus one DST/overnight safety day is sufficient to find a window.
    for offset in range(0, 9):
        start_date = local_now.date() + timedelta(days=offset)
        if start_date.weekday() not in weekdays:
            continue
        window_start, _window_end = _window_for_date(
            start_date,
            zone=zone,
            start_time=start_time,
            end_time=end_time,
        )
        if window_start > local_now:
            return DestinationWindowDecision(
                False,
                zone_name,
                defer_until=window_start.astimezone(UTC),
            )

    # Schema validation guarantees at least one valid weekday; this is a defensive stop.
    return DestinationWindowDecision(
        False,
        zone_name,
        defer_until=now_utc + timedelta(days=7),
    )
