from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.enums import JobStatus
from app.models import DeliveryJob, Destination, PublishingBlackout
from app.services.blackouts import blackout_active_until, evaluate_blackouts
from app.services.delivery import DeliveryService
from app.services.scheduler import SchedulerService
from app.services.telegram.errors import TelegramFloodWait, TelegramWriteForbidden
from tests.conftest import csrf_headers
from tests.test_api_workflow import (
    approve_campaign,
    create_campaign,
    create_connection,
    create_destination,
    create_template,
)


def test_destination_validation_history_batch_and_pilot_attention(
    auth_client: TestClient,
) -> None:
    """Проверить сценарий destination validation history batch and pilot attention. Тест
    завершается ошибкой при нарушении зафиксированного инварианта.
    """
    connection = create_connection(auth_client)
    destination = create_destination(auth_client, connection["id"])

    history = auth_client.get(f"/api/v1/destinations/{destination['id']}/validation-history")
    assert history.status_code == 200, history.text
    assert len(history.json()) == 1
    assert history.json()[0]["status"] == "passed"
    assert history.json()[0]["source"] == "create"

    with auth_client.app.state.session_factory() as db:
        stored = db.get(Destination, destination["id"])
        assert stored is not None
        stored.validation_expires_at = datetime.now(UTC) - timedelta(seconds=1)
        db.commit()

    overview = auth_client.get("/api/v1/pilot/overview")
    assert overview.status_code == 200, overview.text
    assert overview.json()["destinations_validation_due"] == 1
    assert any(item["destination_id"] == destination["id"] for item in overview.json()["attention"])

    batch = auth_client.post(
        "/api/v1/destinations/validate-batch",
        headers=csrf_headers(auth_client),
        json={"destination_ids": [destination["id"]]},
    )
    assert batch.status_code == 200, batch.text
    assert batch.json()["passed"] == 1
    assert batch.json()["failed"] == 0

    refreshed = auth_client.get(f"/api/v1/destinations/{destination['id']}")
    assert refreshed.status_code == 200, refreshed.text
    assert refreshed.json()["validated"] is True
    validation_expiry = datetime.fromisoformat(refreshed.json()["validation_expires_at"])
    if validation_expiry.tzinfo is None:
        validation_expiry = validation_expiry.replace(tzinfo=UTC)
    assert validation_expiry > datetime.now(UTC)

    history = auth_client.get(f"/api/v1/destinations/{destination['id']}/validation-history")
    assert history.status_code == 200
    assert [item["source"] for item in history.json()[:2]] == ["batch", "create"]


def test_batch_validation_classifies_write_forbidden_and_disables_destination(
    auth_client: TestClient,
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    """Проверить сценарий batch validation classifies write forbidden and disables destination.
    Тест завершается ошибкой при нарушении зафиксированного инварианта.
    """
    connection = create_connection(auth_client)
    destination = create_destination(auth_client, connection["id"])

    class ForbiddenGateway:
        def resolve_destination(self, **_kwargs):  # type: ignore[no-untyped-def]
            """Прочитать destination класса ForbiddenGateway. Значение возвращается без несвязанных
            изменений состояния.
            """
            raise TelegramWriteForbidden("Права отправки отозваны")

    monkeypatch.setattr(
        "app.api.destinations.build_gateway",
        lambda *_args, **_kwargs: ForbiddenGateway(),
    )
    result = auth_client.post(
        "/api/v1/destinations/validate-batch",
        headers=csrf_headers(auth_client),
        json={"destination_ids": [destination["id"]]},
    )
    assert result.status_code == 200, result.text
    assert result.json()["write_forbidden"] == 1
    assert result.json()["failed"] == 0
    assert result.json()["items"][0]["status"] == "write_forbidden"

    refreshed = auth_client.get(f"/api/v1/destinations/{destination['id']}")
    assert refreshed.status_code == 200, refreshed.text
    assert refreshed.json()["enabled"] is False
    assert refreshed.json()["validated"] is False
    assert refreshed.json()["last_error_code"] == "CHAT_WRITE_FORBIDDEN"

    history = auth_client.get(f"/api/v1/destinations/{destination['id']}/validation-history")
    assert history.status_code == 200, history.text
    assert history.json()[0]["status"] == "write_forbidden"


def test_one_time_blackout_defers_delivery_before_telegram_gateway(
    auth_client: TestClient,
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    """Проверить сценарий one time blackout defers delivery before telegram gateway. Тест
    завершается ошибкой при нарушении зафиксированного инварианта.
    """
    connection = create_connection(auth_client)
    destination = create_destination(auth_client, connection["id"])
    template = create_template(auth_client)
    campaign = create_campaign(auth_client, connection["id"], template["id"], destination["id"])
    approve_campaign(auth_client, campaign["id"])
    assert (
        auth_client.post(
            f"/api/v1/campaigns/{campaign['id']}/run-now",
            headers=csrf_headers(auth_client),
        ).status_code
        == 200
    )

    scheduler_now = datetime.now(UTC) + timedelta(seconds=2)
    scheduler = SchedulerService(
        auth_client.app.state.session_factory, auth_client.app.state.settings
    )
    assert scheduler.tick(now=scheduler_now) == 1

    starts_at = scheduler_now - timedelta(minutes=1)
    ends_at = scheduler_now + timedelta(minutes=20)
    created = auth_client.post(
        "/api/v1/blackouts",
        headers=csrf_headers(auth_client),
        json={
            "title": "Окно ручной проверки",
            "reason": "В этот период публикации запрещены оператором",
            "scope": "destination",
            "kind": "one_time",
            "destination_id": destination["id"],
            "starts_at": starts_at.isoformat(),
            "ends_at": ends_at.isoformat(),
        },
    )
    assert created.status_code == 201, created.text

    evaluation = auth_client.get(
        "/api/v1/blackouts/evaluate/current",
        params={"destination_id": destination["id"], "at": scheduler_now.isoformat()},
    )
    assert evaluation.status_code == 200, evaluation.text
    assert evaluation.json()["active"] is True
    assert evaluation.json()["matches"][0]["title"] == "Окно ручной проверки"

    monkeypatch.setattr(
        "app.services.delivery.build_gateway",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("Telegram gateway must not be created during blackout")
        ),
    )
    delivery = DeliveryService(
        auth_client.app.state.session_factory,
        auth_client.app.state.settings,
        auth_client.app.state.cipher,
        worker_id="blackout-test-worker",
    )
    assert delivery.process_next(now=scheduler_now + timedelta(seconds=1)) is True

    with auth_client.app.state.session_factory() as db:
        job = db.scalar(select(DeliveryJob).where(DeliveryJob.campaign_id == campaign["id"]))
        assert job is not None
        assert job.status == JobStatus.PENDING
        assert job.safety_decision["code"] == "PUBLISHING_BLACKOUT"
        due_at = job.due_at if job.due_at.tzinfo else job.due_at.replace(tzinfo=UTC)
        assert due_at >= ends_at.replace(microsecond=0)
        assert job.attempt_count == 0


def test_weekly_blackout_handles_overnight_window(auth_client: TestClient) -> None:
    """Проверить сценарий weekly blackout handles overnight window. Тест завершается ошибкой при
    нарушении зафиксированного инварианта.
    """
    now = datetime(2026, 8, 6, 23, 30, tzinfo=UTC)  # Thursday
    response = auth_client.post(
        "/api/v1/blackouts",
        headers=csrf_headers(auth_client),
        json={
            "title": "Ночная тишина",
            "reason": "Публикации запрещены ночью",
            "scope": "organization",
            "kind": "weekly",
            "timezone_name": "UTC",
            "weekdays": [3],
            "start_time": "22:00:00",
            "end_time": "07:00:00",
        },
    )
    assert response.status_code == 201, response.text

    with auth_client.app.state.session_factory() as db:
        blackout = db.get(PublishingBlackout, response.json()["id"])
        assert blackout is not None
        active_until = blackout_active_until(blackout, now)
        assert active_until == datetime(2026, 8, 7, 7, 0, tzinfo=UTC)
        assert blackout_active_until(blackout, datetime(2026, 8, 7, 6, 59, tzinfo=UTC)) == datetime(
            2026, 8, 7, 7, 0, tzinfo=UTC
        )
        assert blackout_active_until(blackout, datetime(2026, 8, 7, 7, 0, tzinfo=UTC)) is None


def test_readiness_report_is_required_when_policy_enabled(auth_client: TestClient) -> None:
    """Проверить сценарий readiness report is required when policy enabled. Тест завершается
    ошибкой при нарушении зафиксированного инварианта.
    """
    connection = create_connection(auth_client)
    destination = create_destination(auth_client, connection["id"])
    template = create_template(auth_client)
    campaign = create_campaign(auth_client, connection["id"], template["id"], destination["id"])
    approve_campaign(auth_client, campaign["id"])
    auth_client.app.state.settings.pilot_readiness_required = True

    blocked = auth_client.post(
        f"/api/v1/campaigns/{campaign['id']}/run-now",
        headers=csrf_headers(auth_client),
    )
    assert blocked.status_code == 409
    assert "отчёт готовности" in blocked.text.lower()

    readiness = auth_client.post(
        f"/api/v1/pilot/readiness/{campaign['id']}",
        headers=csrf_headers(auth_client),
    )
    assert readiness.status_code == 201, readiness.text
    body = readiness.json()
    assert body["status"] == "warning"
    assert body["blockers"] == []
    assert any(item["code"] == "PREFLIGHT" for item in body["checks"])
    assert any(item["code"] == "TELEGRAM_FAKE_MODE" for item in body["checks"])

    allowed = auth_client.post(
        f"/api/v1/campaigns/{campaign['id']}/run-now",
        headers=csrf_headers(auth_client),
    )
    assert allowed.status_code == 200, allowed.text


def test_readiness_fingerprint_invalidates_after_campaign_edit(auth_client: TestClient) -> None:
    """Проверить сценарий readiness fingerprint invalidates after campaign edit. Тест завершается
    ошибкой при нарушении зафиксированного инварианта.
    """
    connection = create_connection(auth_client)
    destination = create_destination(auth_client, connection["id"])
    template = create_template(auth_client)
    campaign = create_campaign(auth_client, connection["id"], template["id"], destination["id"])
    approve_campaign(auth_client, campaign["id"])
    auth_client.app.state.settings.pilot_readiness_required = True

    report = auth_client.post(
        f"/api/v1/pilot/readiness/{campaign['id']}",
        headers=csrf_headers(auth_client),
    )
    assert report.status_code == 201, report.text

    paused = auth_client.post(
        f"/api/v1/campaigns/{campaign['id']}/pause",
        headers=csrf_headers(auth_client),
    )
    assert paused.status_code == 200, paused.text
    changed = auth_client.patch(
        f"/api/v1/campaigns/{campaign['id']}",
        headers=csrf_headers(auth_client),
        json={"notes": "Изменение после готовности требует нового review"},
    )
    assert changed.status_code == 200, changed.text

    # Editing already invalidates the approval. Re-approve and ensure the old
    # readiness fingerprint still cannot authorize a different configuration.
    approve_campaign(auth_client, campaign["id"])
    blocked = auth_client.post(
        f"/api/v1/campaigns/{campaign['id']}/run-now",
        headers=csrf_headers(auth_client),
    )
    assert blocked.status_code == 409
    assert "отчёт готовности" in blocked.text.lower()


def test_blackout_patch_validates_scope_references(auth_client: TestClient) -> None:
    """Проверить сценарий blackout patch validates scope references. Тест завершается ошибкой при
    нарушении зафиксированного инварианта.
    """
    connection = create_connection(auth_client)
    destination = create_destination(auth_client, connection["id"])
    now = datetime.now(UTC)
    blackout = auth_client.post(
        "/api/v1/blackouts",
        headers=csrf_headers(auth_client),
        json={
            "title": "Организационное окно",
            "reason": "Плановое обслуживание",
            "scope": "organization",
            "kind": "one_time",
            "starts_at": (now + timedelta(minutes=10)).isoformat(),
            "ends_at": (now + timedelta(minutes=20)).isoformat(),
        },
    )
    assert blackout.status_code == 201, blackout.text

    changed = auth_client.patch(
        f"/api/v1/blackouts/{blackout.json()['id']}",
        headers=csrf_headers(auth_client),
        json={
            "scope": "destination",
            "destination_id": destination["id"],
            "connection_id": connection["id"],
        },
    )
    assert changed.status_code == 200, changed.text
    assert changed.json()["scope"] == "destination"
    assert changed.json()["destination_id"] == destination["id"]


def test_blackout_patch_can_switch_window_kind_without_stale_fields(
    auth_client: TestClient,
) -> None:
    """Проверить сценарий blackout patch can switch window kind without stale fields. Тест
    завершается ошибкой при нарушении зафиксированного инварианта.
    """
    now = datetime.now(UTC)
    created = auth_client.post(
        "/api/v1/blackouts",
        headers=csrf_headers(auth_client),
        json={
            "title": "Разовое обслуживание",
            "reason": "Проверка переключения типа окна",
            "scope": "organization",
            "kind": "one_time",
            "starts_at": (now + timedelta(hours=1)).isoformat(),
            "ends_at": (now + timedelta(hours=2)).isoformat(),
        },
    )
    assert created.status_code == 201, created.text

    weekly = auth_client.patch(
        f"/api/v1/blackouts/{created.json()['id']}",
        headers=csrf_headers(auth_client),
        json={
            "kind": "weekly",
            "timezone_name": "Europe/Amsterdam",
            "weekdays": [0, 1, 2, 3, 4],
            "start_time": "23:00:00",
            "end_time": "06:00:00",
        },
    )
    assert weekly.status_code == 200, weekly.text
    assert weekly.json()["kind"] == "weekly"
    assert weekly.json()["starts_at"] is None
    assert weekly.json()["ends_at"] is None
    assert weekly.json()["weekdays"] == [0, 1, 2, 3, 4]

    one_time = auth_client.patch(
        f"/api/v1/blackouts/{created.json()['id']}",
        headers=csrf_headers(auth_client),
        json={
            "kind": "one_time",
            "starts_at": (now + timedelta(days=1)).isoformat(),
            "ends_at": (now + timedelta(days=1, hours=2)).isoformat(),
        },
    )
    assert one_time.status_code == 200, one_time.text
    assert one_time.json()["kind"] == "one_time"
    assert one_time.json()["timezone_name"] is None
    assert one_time.json()["weekdays"] == []
    assert one_time.json()["start_time"] is None
    assert one_time.json()["end_time"] is None


def test_blackout_filters_scope_normalization_and_delete(auth_client: TestClient) -> None:
    """Проверить фильтры списка, очистку scope-полей и удаление blackout-окна."""
    connection = create_connection(auth_client)
    destination = create_destination(auth_client, connection["id"])
    now = datetime.now(UTC)
    created = auth_client.post(
        "/api/v1/blackouts",
        headers=csrf_headers(auth_client),
        json={
            "title": "Окно назначения",
            "reason": "Проверка нормализации области действия",
            "scope": "destination",
            "kind": "one_time",
            "connection_id": connection["id"],
            "destination_id": destination["id"],
            "starts_at": (now + timedelta(hours=1)).isoformat(),
            "ends_at": (now + timedelta(hours=2)).isoformat(),
        },
    )
    assert created.status_code == 201, created.text
    blackout_id = created.json()["id"]

    as_connection = auth_client.patch(
        f"/api/v1/blackouts/{blackout_id}",
        headers=csrf_headers(auth_client),
        json={"scope": "connection", "connection_id": connection["id"]},
    )
    assert as_connection.status_code == 200, as_connection.text
    assert as_connection.json()["connection_id"] == connection["id"]
    assert as_connection.json()["destination_id"] is None

    as_organization = auth_client.patch(
        f"/api/v1/blackouts/{blackout_id}",
        headers=csrf_headers(auth_client),
        json={"scope": "organization", "enabled": False},
    )
    assert as_organization.status_code == 200, as_organization.text
    assert as_organization.json()["connection_id"] is None
    assert as_organization.json()["destination_id"] is None

    disabled = auth_client.get("/api/v1/blackouts?enabled=false&scope=organization")
    enabled = auth_client.get("/api/v1/blackouts?enabled=true")
    assert disabled.status_code == enabled.status_code == 200
    assert [item["id"] for item in disabled.json()] == [blackout_id]
    assert enabled.json() == []

    as_destination_without_connection = auth_client.patch(
        f"/api/v1/blackouts/{blackout_id}",
        headers=csrf_headers(auth_client),
        json={"scope": "destination", "destination_id": destination["id"]},
    )
    assert as_destination_without_connection.status_code == 200
    assert as_destination_without_connection.json()["connection_id"] is None
    assert as_destination_without_connection.json()["destination_id"] == destination["id"]

    deleted = auth_client.delete(
        f"/api/v1/blackouts/{blackout_id}", headers=csrf_headers(auth_client)
    )
    assert deleted.status_code == 200, deleted.text
    assert auth_client.get("/api/v1/blackouts").json() == []
    assert (
        auth_client.delete(
            f"/api/v1/blackouts/{blackout_id}", headers=csrf_headers(auth_client)
        ).status_code
        == 404
    )


def test_blackout_rejects_unknown_and_mismatched_references(auth_client: TestClient) -> None:
    """Проверить tenant-ссылки blackout и отказ evaluate/PATCH для неверных данных."""
    first_connection = create_connection(auth_client)
    second_connection = create_connection(auth_client, name="Second blackout bot")
    destination = create_destination(auth_client, first_connection["id"])
    now = datetime.now(UTC)
    window = {
        "title": "Проверка ссылок",
        "reason": "Неверная ссылка должна отклоняться",
        "kind": "one_time",
        "starts_at": (now + timedelta(hours=1)).isoformat(),
        "ends_at": (now + timedelta(hours=2)).isoformat(),
    }

    unknown_connection = auth_client.post(
        "/api/v1/blackouts",
        headers=csrf_headers(auth_client),
        json={**window, "scope": "connection", "connection_id": "missing-connection"},
    )
    unknown_destination = auth_client.post(
        "/api/v1/blackouts",
        headers=csrf_headers(auth_client),
        json={**window, "scope": "destination", "destination_id": "missing-destination"},
    )
    mismatch = auth_client.post(
        "/api/v1/blackouts",
        headers=csrf_headers(auth_client),
        json={
            **window,
            "scope": "destination",
            "connection_id": second_connection["id"],
            "destination_id": destination["id"],
        },
    )
    assert unknown_connection.status_code == unknown_destination.status_code == 404
    assert mismatch.status_code == 422

    created = auth_client.post(
        "/api/v1/blackouts",
        headers=csrf_headers(auth_client),
        json={**window, "scope": "organization"},
    )
    assert created.status_code == 201, created.text
    invalid_window = auth_client.patch(
        f"/api/v1/blackouts/{created.json()['id']}",
        headers=csrf_headers(auth_client),
        json={"ends_at": (now - timedelta(hours=1)).isoformat()},
    )
    assert invalid_window.status_code == 422
    assert (
        auth_client.patch(
            "/api/v1/blackouts/missing-blackout",
            headers=csrf_headers(auth_client),
            json={"enabled": False},
        ).status_code
        == 404
    )
    assert (
        auth_client.get(
            "/api/v1/blackouts/evaluate/current",
            params={"connection_id": "missing-connection"},
        ).status_code
        == 404
    )
    assert (
        auth_client.get(
            "/api/v1/blackouts/evaluate/current",
            params={"destination_id": "missing-destination"},
        ).status_code
        == 404
    )


def test_scheduler_pauses_due_campaign_without_current_readiness(
    auth_client: TestClient,
) -> None:
    """Проверить сценарий scheduler pauses due campaign without current readiness. Тест завершается
    ошибкой при нарушении зафиксированного инварианта.
    """
    connection = create_connection(auth_client)
    destination = create_destination(auth_client, connection["id"])
    template = create_template(auth_client)
    campaign = create_campaign(auth_client, connection["id"], template["id"], destination["id"])
    approved = approve_campaign(auth_client, campaign["id"])
    auth_client.app.state.settings.pilot_readiness_required = True

    scheduler = SchedulerService(
        auth_client.app.state.session_factory, auth_client.app.state.settings
    )
    scheduled_at = datetime.fromisoformat(approved["next_run_at"])
    if scheduled_at.tzinfo is None:
        scheduled_at = scheduled_at.replace(tzinfo=UTC)
    assert scheduler.tick(now=scheduled_at + timedelta(seconds=1)) == 0

    refreshed = auth_client.get(f"/api/v1/campaigns/{campaign['id']}")
    assert refreshed.status_code == 200, refreshed.text
    assert refreshed.json()["status"] == "paused"
    assert refreshed.json()["next_run_at"] is None

    notifications = auth_client.get("/api/v1/notifications")
    assert notifications.status_code == 200, notifications.text
    assert any(
        item["event_type"] == "campaign.readiness_stale_blocked" for item in notifications.json()
    )


def test_batch_validation_stops_after_retry_after(
    auth_client: TestClient,
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    """Проверить сценарий batch validation stops after retry after. Тест завершается ошибкой при
    нарушении зафиксированного инварианта.
    """
    connection = create_connection(auth_client)
    destination_ids: list[str] = []
    for index in range(3):
        response = auth_client.post(
            "/api/v1/destinations",
            headers=csrf_headers(auth_client),
            json={
                "connection_id": connection["id"],
                "username": f"retry_after_group_{index}",
                "kind": "supergroup",
                "permission_confirmed": True,
                "permission_note": "Публикация согласована для тестового назначения",
            },
        )
        assert response.status_code == 201, response.text
        destination_ids.append(response.json()["id"])

    class FloodGateway:
        calls = 0

        def resolve_destination(self, **_kwargs):  # type: ignore[no-untyped-def]
            """Прочитать destination класса FloodGateway. Значение возвращается без несвязанных
            изменений состояния.
            """
            self.calls += 1
            raise TelegramFloodWait("Telegram потребовал ожидание", retry_after=90)

    gateway = FloodGateway()
    monkeypatch.setattr(
        "app.api.destinations.build_gateway",
        lambda *_args, **_kwargs: gateway,
    )
    result = auth_client.post(
        "/api/v1/destinations/validate-batch",
        headers=csrf_headers(auth_client),
        json={"destination_ids": destination_ids},
    )
    assert result.status_code == 200, result.text
    body = result.json()
    assert gateway.calls == 1
    assert body["failed"] == 1
    assert body["deferred"] == 2
    assert [item["status"] for item in body["items"]] == [
        "failed",
        "deferred",
        "deferred",
    ]
    assert body["items"][0]["error_code"] == "FLOOD_WAIT"
    assert all(item["error_code"] == "BATCH_DEFERRED" for item in body["items"][1:])


def test_overlapping_blackouts_defer_until_latest_end(
    auth_client: TestClient,
) -> None:
    """Проверить сценарий overlapping blackouts defer until latest end. Тест завершается ошибкой
    при нарушении зафиксированного инварианта.
    """
    connection = create_connection(auth_client)
    destination = create_destination(auth_client, connection["id"])
    now = datetime.now(UTC).replace(microsecond=0)
    first_end = now + timedelta(minutes=10)
    second_end = now + timedelta(minutes=25)

    for payload in (
        {
            "title": "Общее обслуживание",
            "reason": "Остановлены все публикации",
            "scope": "organization",
            "kind": "one_time",
            "starts_at": (now - timedelta(minutes=1)).isoformat(),
            "ends_at": first_end.isoformat(),
        },
        {
            "title": "Проверка назначения",
            "reason": "Отдельная группа временно закрыта",
            "scope": "destination",
            "kind": "one_time",
            "destination_id": destination["id"],
            "starts_at": (now - timedelta(minutes=1)).isoformat(),
            "ends_at": second_end.isoformat(),
        },
    ):
        response = auth_client.post(
            "/api/v1/blackouts",
            headers=csrf_headers(auth_client),
            json=payload,
        )
        assert response.status_code == 201, response.text

    with auth_client.app.state.session_factory() as db:
        stored_destination = db.get(Destination, destination["id"])
        assert stored_destination is not None
        decision = evaluate_blackouts(
            db,
            organization_id=stored_destination.organization_id,
            connection_id=connection["id"],
            destination_id=destination["id"],
            at=now,
        )
        assert decision.active is True
        assert len(decision.matches) == 2
        assert decision.defer_until == second_end
        assert {item.title for item in decision.matches} == {
            "Общее обслуживание",
            "Проверка назначения",
        }


def test_expired_readiness_report_cannot_authorize_launch(
    auth_client: TestClient,
) -> None:
    """Проверить сценарий expired readiness report cannot authorize launch. Тест завершается
    ошибкой при нарушении зафиксированного инварианта.
    """
    connection = create_connection(auth_client)
    destination = create_destination(auth_client, connection["id"])
    template = create_template(auth_client)
    campaign = create_campaign(auth_client, connection["id"], template["id"], destination["id"])
    approve_campaign(auth_client, campaign["id"])
    auth_client.app.state.settings.pilot_readiness_required = True

    report = auth_client.post(
        f"/api/v1/pilot/readiness/{campaign['id']}",
        headers=csrf_headers(auth_client),
    )
    assert report.status_code == 201, report.text

    from app.models import PilotReadinessReport

    with auth_client.app.state.session_factory() as db:
        stored = db.get(PilotReadinessReport, report.json()["id"])
        assert stored is not None
        stored.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        db.commit()

    blocked = auth_client.post(
        f"/api/v1/campaigns/{campaign['id']}/run-now",
        headers=csrf_headers(auth_client),
    )
    assert blocked.status_code == 409
    assert "отчёт готовности" in blocked.text.lower()
