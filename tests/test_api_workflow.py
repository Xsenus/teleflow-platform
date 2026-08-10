from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from app.services.delivery import DeliveryService
from app.services.scheduler import SchedulerService
from tests.conftest import csrf_headers


def create_connection(client: TestClient, *, name: str = "Test Bot") -> dict:
    """Создать connection. Перед сохранением или возвратом нового значения проверяются связанные
    инварианты.
    """
    response = client.post(
        "/api/v1/connections/bot",
        headers=csrf_headers(client),
        json={
            "name": name,
            "bot_token": "1234567890:abcdefghijklmnopqrstuvwxyz",
            "min_interval_seconds": 1,
            "daily_cap": 20,
            "destination_cooldown_minutes": 60,
            "require_manual_approval": True,
            "stop_on_flood": True,
        },
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert "credentials_enc" not in body
    return body


def create_destination(client: TestClient, connection_id: str, *, confirmed: bool = True) -> dict:
    """Создать destination. Перед сохранением или возвратом нового значения проверяются связанные
    инварианты.
    """
    payload = {
        "connection_id": connection_id,
        "username": "allowed_jobs_group",
        "kind": "supergroup",
        "permission_confirmed": confirmed,
    }
    if confirmed:
        payload["permission_note"] = "Публикации вакансий разрешены администратором группы"
    response = client.post("/api/v1/destinations", headers=csrf_headers(client), json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def create_template(client: TestClient) -> dict:
    """Создать template. Перед сохранением или возвратом нового значения проверяются связанные
    инварианты.
    """
    response = client.post(
        "/api/v1/templates",
        headers=csrf_headers(client),
        json={
            "name": "Vacancy",
            "body": "Тестовое объявление о вакансии",
            "parse_mode": "plain",
            "link_preview": False,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def create_campaign(
    client: TestClient, connection_id: str, template_id: str, destination_id: str
) -> dict:
    """Создать campaign. Перед сохранением или возвратом нового значения проверяются связанные
    инварианты.
    """
    response = client.post(
        "/api/v1/campaigns",
        headers=csrf_headers(client),
        json={
            "name": "Pilot",
            "connection_id": connection_id,
            "template_id": template_id,
            "destination_ids": [destination_id],
            "schedule_type": "once",
            "schedule_at": datetime.now(UTC).isoformat(),
            "timezone_name": "Europe/Amsterdam",
            "weekdays": [],
            "spacing_seconds": 1,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def approve_campaign(client: TestClient, campaign_id: str) -> dict:
    """Выполнить операцию approve campaign. Аргументы интерпретируются в контексте модуля,
    результат возвращается вызывающему коду.
    """
    response = client.post(
        f"/api/v1/campaigns/{campaign_id}/approve",
        headers=csrf_headers(client),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "scheduled"
    assert body["approved_at"] is not None
    return body


def test_full_fake_delivery(auth_client: TestClient) -> None:
    """Проверить сценарий full fake delivery. Тест завершается ошибкой при нарушении
    зафиксированного инварианта.
    """
    connection = create_connection(auth_client)
    destination = create_destination(auth_client, connection["id"])
    template = create_template(auth_client)
    campaign = create_campaign(auth_client, connection["id"], template["id"], destination["id"])

    preview = auth_client.get(f"/api/v1/campaigns/{campaign['id']}/preview")
    assert preview.status_code == 200
    assert preview.json()["valid"] is True

    approve_campaign(auth_client, campaign["id"])
    run_now = auth_client.post(
        f"/api/v1/campaigns/{campaign['id']}/run-now", headers=csrf_headers(auth_client)
    )
    assert run_now.status_code == 200, run_now.text

    app = auth_client.app
    scheduler = SchedulerService(app.state.session_factory, app.state.settings)
    now = datetime.now(UTC) + timedelta(seconds=2)
    assert scheduler.tick(now=now) == 1
    delivery = DeliveryService(
        app.state.session_factory,
        app.state.settings,
        app.state.cipher,
        worker_id="test-worker",
    )
    delivery.heartbeat()
    assert delivery.process_next(now=now + timedelta(seconds=1)) is True

    jobs = auth_client.get("/api/v1/jobs")
    assert jobs.status_code == 200
    assert len(jobs.json()) == 1
    assert jobs.json()[0]["status"] == "sent"
    assert jobs.json()[0]["telegram_message_id"].startswith("fake-")

    dashboard = auth_client.get("/api/v1/dashboard/summary")
    assert dashboard.status_code == 200
    assert dashboard.json()["jobs_sent_today"] == 1
    assert dashboard.json()["worker_online"] is True


def test_unconfirmed_destination_blocks_approval(auth_client: TestClient) -> None:
    """Проверить сценарий unconfirmed destination blocks approval. Тест завершается ошибкой при
    нарушении зафиксированного инварианта.
    """
    connection = create_connection(auth_client)
    destination = create_destination(auth_client, connection["id"], confirmed=False)
    template = create_template(auth_client)
    campaign = create_campaign(auth_client, connection["id"], template["id"], destination["id"])

    preview = auth_client.get(f"/api/v1/campaigns/{campaign['id']}/preview")
    assert preview.status_code == 200
    assert preview.json()["valid"] is False
    assert any("не подтверждено" in item for item in preview.json()["blockers"])

    approve = auth_client.post(
        f"/api/v1/campaigns/{campaign['id']}/approve", headers=csrf_headers(auth_client)
    )
    assert approve.status_code == 422


def test_duplicate_destination_is_rejected(auth_client: TestClient) -> None:
    """Проверить сценарий duplicate destination is rejected. Тест завершается ошибкой при нарушении
    зафиксированного инварианта.
    """
    connection = create_connection(auth_client)
    create_destination(auth_client, connection["id"])
    duplicate = auth_client.post(
        "/api/v1/destinations",
        headers=csrf_headers(auth_client),
        json={
            "connection_id": connection["id"],
            "username": "allowed_jobs_group",
            "kind": "supergroup",
            "permission_confirmed": True,
            "permission_note": "Разрешено",
        },
    )
    assert duplicate.status_code == 409


def test_audit_does_not_expose_bot_token(auth_client: TestClient) -> None:
    """Проверить сценарий audit does not expose bot token. Тест завершается ошибкой при нарушении
    зафиксированного инварианта.
    """
    secret = "1234567890:secret-token-not-for-logs"
    response = auth_client.post(
        "/api/v1/connections/bot",
        headers=csrf_headers(auth_client),
        json={"name": "Audit Bot", "bot_token": secret},
    )
    assert response.status_code == 201
    audit = auth_client.get("/api/v1/audit?limit=100")
    assert audit.status_code == 200
    assert secret not in audit.text


def test_campaign_read_supports_safe_editing_and_destination_order(auth_client: TestClient) -> None:
    """Проверить сценарий campaign read supports safe editing and destination order. Тест
    завершается ошибкой при нарушении зафиксированного инварианта.
    """
    connection = create_connection(auth_client)
    destination = create_destination(auth_client, connection["id"])
    template = create_template(auth_client)
    campaign = create_campaign(auth_client, connection["id"], template["id"], destination["id"])

    assert campaign["destination_ids"] == [destination["id"]]

    invalid = auth_client.patch(
        f"/api/v1/campaigns/{campaign['id']}",
        headers=csrf_headers(auth_client),
        json={"schedule_type": "weekly", "weekdays": []},
    )
    assert invalid.status_code == 422

    updated = auth_client.patch(
        f"/api/v1/campaigns/{campaign['id']}",
        headers=csrf_headers(auth_client),
        json={
            "name": "Pilot updated",
            "schedule_type": "weekly",
            "weekdays": [0, 2],
            "spacing_seconds": 90,
        },
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["status"] == "draft"
    assert updated.json()["schedule_type"] == "weekly"
    assert updated.json()["weekdays"] == [0, 2]
    assert updated.json()["destination_ids"] == [destination["id"]]

    replaced = auth_client.put(
        f"/api/v1/campaigns/{campaign['id']}/destinations",
        headers=csrf_headers(auth_client),
        json={"destination_ids": [destination["id"]]},
    )
    assert replaced.status_code == 200, replaced.text
    assert replaced.json()["destination_ids"] == [destination["id"]]


def test_template_form_crud_and_revision_lifecycle(auth_client: TestClient) -> None:
    """Проверить чтение, фильтр, revision, media-validation и удаление шаблона."""

    template = create_template(auth_client)
    template_id = template["id"]
    assert auth_client.get(f"/api/v1/templates/{template_id}").status_code == 200
    active = auth_client.get("/api/v1/templates", params={"active_only": True})
    assert active.status_code == 200 and active.json()[0]["id"] == template_id
    missing_media = auth_client.patch(
        f"/api/v1/templates/{template_id}",
        headers=csrf_headers(auth_client),
        json={"media_asset_id": "missing"},
    )
    assert missing_media.status_code == 404
    renamed = auth_client.patch(
        f"/api/v1/templates/{template_id}",
        headers=csrf_headers(auth_client),
        json={"name": "Renamed only"},
    )
    assert renamed.status_code == 200 and renamed.json()["revision"] == 1
    revised = auth_client.patch(
        f"/api/v1/templates/{template_id}",
        headers=csrf_headers(auth_client),
        json={"body": "Новая версия сообщения", "parse_mode": "plain", "link_preview": False},
    )
    assert revised.status_code == 200 and revised.json()["revision"] == 2
    deleted = auth_client.delete(
        f"/api/v1/templates/{template_id}", headers=csrf_headers(auth_client)
    )
    assert deleted.status_code == 200
    assert auth_client.get(f"/api/v1/templates/{template_id}").status_code == 404
