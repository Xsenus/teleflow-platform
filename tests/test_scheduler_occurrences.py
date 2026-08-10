from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import cast

from app.enums import RolloutMode, ScheduleType
from app.models import Campaign
from app.services.scheduler import campaign_capacity_projection, next_occurrence


def campaign(
    schedule_type: ScheduleType,
    *,
    timezone_name: str = "UTC",
    weekdays: list[int] | None = None,
    end_at: datetime | None = None,
) -> Campaign:
    """Создать минимальную Campaign для расчёта следующего запуска."""

    return Campaign(
        schedule_type=schedule_type,
        timezone_name=timezone_name,
        weekdays=weekdays or [],
        end_at=end_at,
    )


def test_next_occurrence_once_daily_and_invalid_timezone() -> None:
    """Проверить one-shot, daily и fallback UTC для неизвестной timezone."""

    current = datetime(2026, 8, 10, 10, 30, tzinfo=UTC)
    assert next_occurrence(campaign(ScheduleType.ONCE), current) is None
    daily = next_occurrence(campaign(ScheduleType.DAILY, timezone_name="Invalid/Timezone"), current)
    assert daily == datetime(2026, 8, 11, 10, 30, tzinfo=UTC)


def test_next_occurrence_weekly_and_end_boundary() -> None:
    """Проверить поиск ближайшего weekday, пустое расписание и ограничение end_at."""

    monday = datetime(2026, 8, 10, 8, 15, tzinfo=UTC)
    weekly = campaign(ScheduleType.WEEKLY, weekdays=[4, 2, 2])
    assert next_occurrence(weekly, monday) == datetime(2026, 8, 12, 8, 15, tzinfo=UTC)
    assert next_occurrence(campaign(ScheduleType.WEEKLY), monday) is None
    ended = campaign(
        ScheduleType.DAILY,
        end_at=datetime(2026, 8, 10, 23, 59, tzinfo=UTC),
    )
    assert next_occurrence(ended, monday) is None


def test_next_occurrence_preserves_local_wall_clock() -> None:
    """Проверить сохранение локального времени при переходе к следующему дню."""

    current = datetime(2026, 8, 10, 3, 0, tzinfo=UTC)
    result = next_occurrence(
        campaign(ScheduleType.DAILY, timezone_name="Asia/Novosibirsk"), current
    )
    assert result == datetime(2026, 8, 11, 3, 0, tzinfo=UTC)


def test_campaign_capacity_projection_full_and_staged() -> None:
    """Проверить ready budget, отключённые назначения и due-time span."""

    links = [
        SimpleNamespace(enabled=True, destination=SimpleNamespace(enabled=True)),
        SimpleNamespace(enabled=True, destination=SimpleNamespace(enabled=True)),
        SimpleNamespace(enabled=False, destination=SimpleNamespace(enabled=True)),
        SimpleNamespace(enabled=True, destination=SimpleNamespace(enabled=False)),
    ]
    full = cast(
        Campaign,
        SimpleNamespace(
            destinations=links,
            rollout_mode=RolloutMode.STANDARD,
            rollout_batch_size=1,
            spacing_seconds=30,
        ),
    )
    assert campaign_capacity_projection(full) == (2, 2, 30)

    staged = cast(
        Campaign,
        SimpleNamespace(
            destinations=links,
            rollout_mode=RolloutMode.STAGED,
            rollout_batch_size=1,
            spacing_seconds=-10,
        ),
    )
    assert campaign_capacity_projection(staged) == (2, 1, 0)
