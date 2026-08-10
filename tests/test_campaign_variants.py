from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.models import DeliveryJob
from app.services.campaign_variants import variant_bucket
from app.services.scheduler import SchedulerService
from tests.conftest import csrf_headers
from tests.test_api_workflow import (
    approve_campaign,
    create_connection,
    create_destination,
    create_template,
)


def _create_secondary_template(client: TestClient) -> dict:
    """Реализовать внутренний этап create secondary template step. Вспомогательная функция
    сохраняет детерминированность и тестируемость процесса.
    """
    response = client.post(
        "/api/v1/templates",
        headers=csrf_headers(client),
        json={
            "name": "Vacancy B",
            "body": "Вариант B: тестовое объявление о вакансии",
            "parse_mode": "plain",
            "link_preview": False,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_variant_bucket_is_stable_and_covers_both_sides() -> None:
    """Проверить сценарий variant bucket is stable and covers both sides. Тест завершается ошибкой
    при нарушении зафиксированного инварианта.
    """
    first = variant_bucket("campaign", "destination")
    assert first == variant_bucket("campaign", "destination")
    assert 0 <= first <= 99
    buckets = {variant_bucket("campaign", f"destination-{index}") for index in range(300)}
    assert min(buckets) < 25
    assert max(buckets) >= 75


def test_campaign_rejects_invalid_ab_configuration(auth_client: TestClient) -> None:
    """Проверить сценарий campaign rejects invalid ab configuration. Тест завершается ошибкой при
    нарушении зафиксированного инварианта.
    """
    connection = create_connection(auth_client)
    destination = create_destination(auth_client, connection["id"])
    template = create_template(auth_client)
    base = {
        "name": "Invalid A/B",
        "connection_id": connection["id"],
        "template_id": template["id"],
        "destination_ids": [destination["id"]],
        "schedule_type": "once",
        "schedule_at": datetime.now(UTC).isoformat(),
        "timezone_name": "Europe/Amsterdam",
        "weekdays": [],
        "spacing_seconds": 1,
    }
    same = auth_client.post(
        "/api/v1/campaigns",
        headers=csrf_headers(auth_client),
        json={**base, "secondary_template_id": template["id"], "secondary_template_weight": 50},
    )
    assert same.status_code == 422
    missing = auth_client.post(
        "/api/v1/campaigns",
        headers=csrf_headers(auth_client),
        json={**base, "secondary_template_weight": 50},
    )
    assert missing.status_code == 422


def test_ab_preview_and_job_use_the_same_stable_template(auth_client: TestClient) -> None:
    """Проверить сценарий ab preview and job use the same stable template. Тест завершается ошибкой
    при нарушении зафиксированного инварианта.
    """
    connection = create_connection(auth_client)
    destination = create_destination(auth_client, connection["id"])
    primary = create_template(auth_client)
    secondary = _create_secondary_template(auth_client)
    response = auth_client.post(
        "/api/v1/campaigns",
        headers=csrf_headers(auth_client),
        json={
            "name": "A/B pilot",
            "connection_id": connection["id"],
            "template_id": primary["id"],
            "secondary_template_id": secondary["id"],
            "secondary_template_weight": 50,
            "destination_ids": [destination["id"]],
            "schedule_type": "once",
            "schedule_at": datetime.now(UTC).isoformat(),
            "timezone_name": "Europe/Amsterdam",
            "weekdays": [],
            "spacing_seconds": 1,
        },
    )
    assert response.status_code == 201, response.text
    campaign = response.json()
    assert campaign["secondary_template_id"] == secondary["id"]
    assert campaign["secondary_template_weight"] == 50

    preview = auth_client.get(f"/api/v1/campaigns/{campaign['id']}/preview")
    assert preview.status_code == 200, preview.text
    item = preview.json()["items"][0]
    assert item["template_variant"] in {"A", "B"}
    assert item["template_id"] in {primary["id"], secondary["id"]}

    approve_campaign(auth_client, campaign["id"])
    run_now = auth_client.post(
        f"/api/v1/campaigns/{campaign['id']}/run-now",
        headers=csrf_headers(auth_client),
    )
    assert run_now.status_code == 200, run_now.text
    scheduler = SchedulerService(
        auth_client.app.state.session_factory,
        auth_client.app.state.settings,
    )
    assert scheduler.tick(now=datetime.now(UTC) + timedelta(seconds=2)) == 1
    with auth_client.app.state.session_factory() as db:
        job = db.scalar(select(DeliveryJob).where(DeliveryJob.campaign_id == campaign["id"]))
        assert job is not None
        assert job.template_id == item["template_id"]
        expected_body = primary["body"] if item["template_variant"] == "A" else secondary["body"]
        assert job.body_snapshot == expected_body
