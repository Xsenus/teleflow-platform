from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from app.services.delivery import DeliveryService
from app.services.scheduler import SchedulerService
from tests.conftest import csrf_headers
from tests.test_api_workflow import (
    approve_campaign,
    create_campaign,
    create_connection,
    create_destination,
    create_template,
)


def _login(client: TestClient) -> None:
    """Реализовать внутренний этап login step. Вспомогательная функция сохраняет
    детерминированность и тестируемость процесса.
    """
    response = client.post(
        "/api/v1/auth/login",
        json={"email": "owner@example.com", "password": "OwnerPassword_123!"},
    )
    assert response.status_code == 200, response.text


def test_refresh_sessions_can_be_listed_and_revoked(client: TestClient) -> None:
    """Проверить сценарий refresh sessions can be listed and revoked. Тест завершается ошибкой при
    нарушении зафиксированного инварианта.
    """
    _login(client)
    _login(client)

    response = client.get("/api/v1/auth/sessions")
    assert response.status_code == 200, response.text
    sessions = response.json()
    assert len(sessions) == 2
    assert sum(1 for item in sessions if item["current"]) == 1
    assert all("token" not in str(item).lower() for item in sessions)

    revoke = client.post(
        "/api/v1/auth/sessions/revoke-others",
        headers=csrf_headers(client),
    )
    assert revoke.status_code == 200, revoke.text
    assert "1" in revoke.json()["message"]

    remaining = client.get("/api/v1/auth/sessions")
    assert remaining.status_code == 200
    payload = remaining.json()
    assert len(payload) == 1
    assert payload[0]["current"] is True


def test_current_refresh_session_can_be_revoked(auth_client: TestClient) -> None:
    """Проверить сценарий current refresh session can be revoked. Тест завершается ошибкой при
    нарушении зафиксированного инварианта.
    """
    sessions = auth_client.get("/api/v1/auth/sessions").json()
    current = next(item for item in sessions if item["current"])

    response = auth_client.delete(
        f"/api/v1/auth/sessions/{current['id']}",
        headers=csrf_headers(auth_client),
    )
    assert response.status_code == 200, response.text
    assert auth_client.get("/api/v1/auth/me").status_code == 401


def test_analytics_overview_timeseries_and_csv(auth_client: TestClient) -> None:
    """Проверить сценарий analytics overview timeseries and csv. Тест завершается ошибкой при
    нарушении зафиксированного инварианта.
    """
    connection = create_connection(auth_client)
    destination = create_destination(auth_client, connection["id"])
    template = create_template(auth_client)
    campaign = create_campaign(
        auth_client,
        connection["id"],
        template["id"],
        destination["id"],
    )
    approve_campaign(auth_client, campaign["id"])
    run_now = auth_client.post(
        f"/api/v1/campaigns/{campaign['id']}/run-now",
        headers=csrf_headers(auth_client),
    )
    assert run_now.status_code == 200, run_now.text

    app = auth_client.app
    now = datetime.now(UTC) + timedelta(seconds=2)
    scheduler = SchedulerService(app.state.session_factory, app.state.settings)
    assert scheduler.tick(now=now) == 1
    delivery = DeliveryService(
        app.state.session_factory,
        app.state.settings,
        app.state.cipher,
        worker_id="analytics-test-worker",
    )
    assert delivery.process_next(now=now + timedelta(seconds=1)) is True

    # Аналитика запрашивается в UTC, поэтому и границы теста должны вычисляться
    # по UTC, а не по локальному часовому поясу машины, где запущен pytest.
    today = now.date().isoformat()
    query = f"date_from={today}&date_to={today}&timezone_name=UTC"
    overview = auth_client.get(f"/api/v1/analytics/overview?{query}")
    assert overview.status_code == 200, overview.text
    body = overview.json()
    assert body["delivery_total"] == 1
    assert body["delivery_sent"] == 1
    assert body["delivery_success_rate"] == 100.0
    assert body["campaigns"][0]["campaign_name"] == "Pilot"
    assert body["template_variants"][0]["template_name"] == "Vacancy"
    assert body["template_variants"][0]["variant"] == "A"
    assert body["template_variants"][0]["sent"] == 1

    timeseries = auth_client.get(f"/api/v1/analytics/timeseries?{query}")
    assert timeseries.status_code == 200, timeseries.text
    assert len(timeseries.json()) == 1
    assert timeseries.json()[0]["delivery_sent"] == 1

    export = auth_client.get(f"/api/v1/analytics/export.csv?{query}")
    assert export.status_code == 200, export.text
    assert export.content.startswith("\ufeff".encode("utf-8"))
    assert "delivery_sent" in export.text
    assert ",1," in export.text
    assert "template_name" in export.text
    assert "Vacancy" in export.text
    assert "attachment;" in export.headers["content-disposition"]


def test_analytics_rejects_invalid_ranges(auth_client: TestClient) -> None:
    """Проверить сценарий analytics rejects invalid ranges. Тест завершается ошибкой при нарушении
    зафиксированного инварианта.
    """
    invalid = auth_client.get(
        "/api/v1/analytics/overview?date_from=2026-08-06&date_to=2026-08-01&timezone_name=UTC"
    )
    assert invalid.status_code == 422

    unknown_zone = auth_client.get(
        "/api/v1/analytics/overview?date_from=2026-08-01&date_to=2026-08-06&timezone_name=No/Such_Zone"
    )
    assert unknown_zone.status_code == 422
