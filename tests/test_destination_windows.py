from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.enums import JobStatus
from app.models import Campaign, DeliveryJob, Destination
from app.services.delivery import DeliveryService
from app.services.destination_windows import evaluate_destination_window
from app.services.scheduler import SchedulerService
from tests.conftest import csrf_headers
from tests.test_api_workflow import (
    approve_campaign,
    create_campaign,
    create_connection,
    create_destination,
    create_template,
)


def _destination(**values) -> Destination:
    """Реализовать внутренний этап destination step. Вспомогательная функция сохраняет
    детерминированность и тестируемость процесса.
    """
    defaults = {
        "organization_id": "org",
        "connection_id": "connection",
        "telegram_chat_id": -1001,
        "title": "Test",
        "allowed_weekdays": [],
    }
    defaults.update(values)
    return Destination(**defaults)


def test_daytime_window_allows_inside_and_defers_after_close() -> None:
    """Проверить сценарий daytime window allows inside and defers after close. Тест завершается
    ошибкой при нарушении зафиксированного инварианта.
    """
    destination = _destination(
        timezone_name="Europe/Amsterdam",
        allowed_weekdays=[0, 1, 2, 3, 4],
        allowed_start_time=datetime.strptime("09:00", "%H:%M").time(),
        allowed_end_time=datetime.strptime("18:00", "%H:%M").time(),
    )

    monday_inside = datetime(2026, 8, 3, 8, 0, tzinfo=UTC)  # 10:00 local
    assert evaluate_destination_window(
        destination, fallback_timezone="UTC", now=monday_inside
    ).allowed

    monday_after = datetime(2026, 8, 3, 17, 0, tzinfo=UTC)  # 19:00 local
    decision = evaluate_destination_window(destination, fallback_timezone="UTC", now=monday_after)
    assert decision.allowed is False
    assert decision.defer_until == datetime(2026, 8, 4, 7, 0, tzinfo=UTC)


def test_weekday_only_window_defers_weekend_to_monday() -> None:
    """Проверить сценарий weekday only window defers weekend to monday. Тест завершается ошибкой
    при нарушении зафиксированного инварианта.
    """
    destination = _destination(
        timezone_name="Europe/Amsterdam",
        allowed_weekdays=[0, 1, 2, 3, 4],
    )
    saturday = datetime(2026, 8, 8, 10, 0, tzinfo=UTC)
    decision = evaluate_destination_window(destination, fallback_timezone="UTC", now=saturday)

    assert decision.allowed is False
    assert decision.defer_until == datetime(2026, 8, 9, 22, 0, tzinfo=UTC)
    # Midnight Monday in Amsterdam is 22:00 UTC on Sunday during summer time.


def test_overnight_window_belongs_to_start_weekday() -> None:
    """Проверить сценарий overnight window belongs to start weekday. Тест завершается ошибкой при
    нарушении зафиксированного инварианта.
    """
    destination = _destination(
        timezone_name="Europe/Amsterdam",
        allowed_weekdays=[4],  # Friday
        allowed_start_time=datetime.strptime("22:00", "%H:%M").time(),
        allowed_end_time=datetime.strptime("06:00", "%H:%M").time(),
    )

    saturday_two_local = datetime(2026, 8, 8, 0, 0, tzinfo=UTC)
    assert evaluate_destination_window(
        destination, fallback_timezone="UTC", now=saturday_two_local
    ).allowed

    saturday_seven_local = datetime(2026, 8, 8, 5, 0, tzinfo=UTC)
    decision = evaluate_destination_window(
        destination, fallback_timezone="UTC", now=saturday_seven_local
    )
    assert decision.allowed is False
    assert decision.defer_until == datetime(2026, 8, 14, 20, 0, tzinfo=UTC)


def test_destination_api_stores_and_validates_individual_window(auth_client) -> None:  # type: ignore[no-untyped-def]
    """Проверить сценарий destination api stores and validates individual window. Тест завершается
    ошибкой при нарушении зафиксированного инварианта.
    """
    connection = create_connection(auth_client)
    response = auth_client.post(
        "/api/v1/destinations",
        headers=csrf_headers(auth_client),
        json={
            "connection_id": connection["id"],
            "username": "scheduled_jobs_group",
            "permission_confirmed": True,
            "permission_note": "Публикации разрешены по будням",
            "timezone_name": "Europe/Amsterdam",
            "allowed_weekdays": [4, 0, 1, 2, 3, 4],
            "allowed_start_time": "09:00",
            "allowed_end_time": "18:00",
            "cooldown_minutes_override": 720,
        },
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["allowed_weekdays"] == [0, 1, 2, 3, 4]
    assert body["allowed_start_time"] == "09:00:00"
    assert body["allowed_end_time"] == "18:00:00"
    assert body["cooldown_minutes_override"] == 720

    invalid_patch = auth_client.patch(
        f"/api/v1/destinations/{body['id']}",
        headers=csrf_headers(auth_client),
        json={"allowed_start_time": None},
    )
    assert invalid_patch.status_code == 422
    assert "задаются вместе" in invalid_patch.text

    clear_window = auth_client.patch(
        f"/api/v1/destinations/{body['id']}",
        headers=csrf_headers(auth_client),
        json={
            "allowed_weekdays": [],
            "allowed_start_time": None,
            "allowed_end_time": None,
            "timezone_name": None,
            "cooldown_minutes_override": None,
        },
    )
    assert clear_window.status_code == 200, clear_window.text
    cleared = clear_window.json()
    assert cleared["allowed_weekdays"] == []
    assert cleared["allowed_start_time"] is None
    assert cleared["cooldown_minutes_override"] is None


def test_destination_api_rejects_unknown_timezone_and_partial_window(auth_client) -> None:  # type: ignore[no-untyped-def]
    """Проверить сценарий destination api rejects unknown timezone and partial window. Тест
    завершается ошибкой при нарушении зафиксированного инварианта.
    """
    connection = create_connection(auth_client)
    invalid_zone = auth_client.post(
        "/api/v1/destinations",
        headers=csrf_headers(auth_client),
        json={
            "connection_id": connection["id"],
            "username": "invalid_timezone_group",
            "timezone_name": "Mars/Olympus",
        },
    )
    assert invalid_zone.status_code == 422

    partial = auth_client.post(
        "/api/v1/destinations",
        headers=csrf_headers(auth_client),
        json={
            "connection_id": connection["id"],
            "username": "partial_window_group",
            "allowed_start_time": "09:00",
        },
    )
    assert partial.status_code == 422
    assert "задаются вместе" in partial.text


def test_delivery_uses_destination_cooldown_override(auth_client) -> None:  # type: ignore[no-untyped-def]
    """Проверить сценарий delivery uses destination cooldown override. Тест завершается ошибкой при
    нарушении зафиксированного инварианта.
    """
    connection = create_connection(auth_client)
    destination = create_destination(auth_client, connection["id"])
    updated = auth_client.patch(
        f"/api/v1/destinations/{destination['id']}",
        headers=csrf_headers(auth_client),
        json={"cooldown_minutes_override": 5},
    )
    assert updated.status_code == 200
    template = create_template(auth_client)
    campaign = create_campaign(auth_client, connection["id"], template["id"], destination["id"])
    approve_campaign(auth_client, campaign["id"])
    run = auth_client.post(
        f"/api/v1/campaigns/{campaign['id']}/run-now",
        headers=csrf_headers(auth_client),
    )
    assert run.status_code == 200

    now = datetime.now(UTC) + timedelta(seconds=2)
    scheduler = SchedulerService(
        auth_client.app.state.session_factory, auth_client.app.state.settings
    )
    assert scheduler.tick(now=now) == 1
    delivery = DeliveryService(
        auth_client.app.state.session_factory,
        auth_client.app.state.settings,
        auth_client.app.state.cipher,
        worker_id="cooldown-worker",
    )
    assert delivery.process_next(now=now + timedelta(seconds=1))

    with auth_client.app.state.session_factory() as db:
        job = db.query(DeliveryJob).one()
        stored = db.get(Destination, destination["id"])
        assert job.finished_at is not None
        assert stored is not None and stored.next_allowed_at is not None
        delta = stored.next_allowed_at.replace(tzinfo=UTC) - job.finished_at.replace(tzinfo=UTC)
        assert delta == timedelta(minutes=5)


def test_worker_defers_job_until_destination_window(auth_client) -> None:  # type: ignore[no-untyped-def]
    """Проверить сценарий worker defers job until destination window. Тест завершается ошибкой при
    нарушении зафиксированного инварианта.
    """
    connection = create_connection(auth_client)
    destination = create_destination(auth_client, connection["id"])
    with auth_client.app.state.session_factory() as db:
        stored = db.get(Destination, destination["id"])
        assert stored is not None
        stored.timezone_name = "Europe/Amsterdam"
        stored.allowed_weekdays = [0, 1, 2, 3, 4]
        stored.allowed_start_time = datetime.strptime("09:00", "%H:%M").time()
        stored.allowed_end_time = datetime.strptime("18:00", "%H:%M").time()
        db.commit()

    template = create_template(auth_client)
    campaign = create_campaign(auth_client, connection["id"], template["id"], destination["id"])
    approve_campaign(auth_client, campaign["id"])
    run = auth_client.post(
        f"/api/v1/campaigns/{campaign['id']}/run-now",
        headers=csrf_headers(auth_client),
    )
    assert run.status_code == 200

    now = datetime(2026, 8, 3, 17, 0, tzinfo=UTC)  # Monday 19:00 Amsterdam
    with auth_client.app.state.session_factory() as db:
        stored_campaign = db.get(Campaign, campaign["id"])
        assert stored_campaign is not None
        stored_campaign.next_run_at = now
        db.commit()
    scheduler = SchedulerService(
        auth_client.app.state.session_factory, auth_client.app.state.settings
    )
    assert scheduler.tick(now=now) == 1

    delivery = DeliveryService(
        auth_client.app.state.session_factory,
        auth_client.app.state.settings,
        auth_client.app.state.cipher,
        worker_id="window-worker",
    )
    assert delivery.process_next(now=now + timedelta(seconds=1))
    with auth_client.app.state.session_factory() as db:
        job = db.query(DeliveryJob).one()
        assert job.status == JobStatus.PENDING
        assert job.safety_decision["code"] == "DESTINATION_TIME_WINDOW"
        due_at = job.due_at.replace(tzinfo=UTC)
        assert due_at == datetime(2026, 8, 4, 7, 0, tzinfo=UTC)
