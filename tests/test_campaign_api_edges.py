from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from app.enums import CampaignStatus, JobStatus
from app.models import Campaign, DeliveryJob
from app.services.scheduler import SchedulerService
from tests.conftest import csrf_headers
from tests.test_api_workflow import (
    approve_campaign,
    create_campaign,
    create_connection,
    create_destination,
    create_template,
)


def _new_campaign(client: TestClient) -> dict:
    """Создать минимальную кампанию с активным подключением и разрешённым назначением."""
    connection = create_connection(client)
    destination = create_destination(client, connection["id"])
    template = create_template(client)
    return create_campaign(client, connection["id"], template["id"], destination["id"])


def test_campaign_list_get_cancel_and_missing_paths(auth_client: TestClient) -> None:
    """Проверить чтение, фильтрацию, отмену draft и ответы для отсутствующей кампании."""
    campaign = _new_campaign(auth_client)
    campaign_id = campaign["id"]

    listed = auth_client.get("/api/v1/campaigns?status_filter=draft")
    absent_filter = auth_client.get("/api/v1/campaigns?status_filter=completed")
    fetched = auth_client.get(f"/api/v1/campaigns/{campaign_id}")
    assert listed.status_code == absent_filter.status_code == fetched.status_code == 200
    assert [item["id"] for item in listed.json()] == [campaign_id]
    assert absent_filter.json() == []
    assert fetched.json()["id"] == campaign_id

    assert (
        auth_client.post(
            f"/api/v1/campaigns/{campaign_id}/pause", headers=csrf_headers(auth_client)
        ).status_code
        == 409
    )
    assert (
        auth_client.post(
            f"/api/v1/campaigns/{campaign_id}/resume", headers=csrf_headers(auth_client)
        ).status_code
        == 409
    )

    cancelled = auth_client.post(
        f"/api/v1/campaigns/{campaign_id}/cancel", headers=csrf_headers(auth_client)
    )
    assert cancelled.status_code == 200, cancelled.text
    assert auth_client.get(f"/api/v1/campaigns/{campaign_id}").json()["status"] == "cancelled"
    assert (
        auth_client.post(
            f"/api/v1/campaigns/{campaign_id}/cancel", headers=csrf_headers(auth_client)
        ).status_code
        == 409
    )

    assert auth_client.get("/api/v1/campaigns/missing-campaign").status_code == 404
    assert auth_client.get("/api/v1/campaigns/missing-campaign/runs").status_code == 404
    assert (
        auth_client.post(
            "/api/v1/campaigns/missing-campaign/cancel", headers=csrf_headers(auth_client)
        ).status_code
        == 404
    )


def test_campaign_resume_with_and_without_pending_jobs(auth_client: TestClient) -> None:
    """Проверить возобновление в scheduled и running в зависимости от существующей очереди."""
    campaign = _new_campaign(auth_client)
    campaign_id = campaign["id"]
    approve_campaign(auth_client, campaign_id)

    paused = auth_client.post(
        f"/api/v1/campaigns/{campaign_id}/pause", headers=csrf_headers(auth_client)
    )
    assert paused.status_code == 200, paused.text
    assert paused.json()["status"] == CampaignStatus.PAUSED.value
    resumed_without_jobs = auth_client.post(
        f"/api/v1/campaigns/{campaign_id}/resume", headers=csrf_headers(auth_client)
    )
    assert resumed_without_jobs.status_code == 200, resumed_without_jobs.text
    assert resumed_without_jobs.json()["status"] == CampaignStatus.SCHEDULED.value

    run_now = auth_client.post(
        f"/api/v1/campaigns/{campaign_id}/run-now", headers=csrf_headers(auth_client)
    )
    assert run_now.status_code == 200, run_now.text
    scheduler = SchedulerService(
        auth_client.app.state.session_factory, auth_client.app.state.settings
    )
    assert scheduler.tick(now=datetime.now(UTC) + timedelta(seconds=2)) == 1
    with auth_client.app.state.session_factory() as db:
        stored = db.get(Campaign, campaign_id)
        assert stored is not None and stored.status == CampaignStatus.RUNNING
        assert db.query(DeliveryJob).filter_by(campaign_id=campaign_id).count() == 1

    paused_with_jobs = auth_client.post(
        f"/api/v1/campaigns/{campaign_id}/pause", headers=csrf_headers(auth_client)
    )
    assert paused_with_jobs.status_code == 200, paused_with_jobs.text
    resumed_with_jobs = auth_client.post(
        f"/api/v1/campaigns/{campaign_id}/resume", headers=csrf_headers(auth_client)
    )
    assert resumed_with_jobs.status_code == 200, resumed_with_jobs.text
    assert resumed_with_jobs.json()["status"] == CampaignStatus.RUNNING.value

    cancelled = auth_client.post(
        f"/api/v1/campaigns/{campaign_id}/cancel", headers=csrf_headers(auth_client)
    )
    assert cancelled.status_code == 200, cancelled.text
    with auth_client.app.state.session_factory() as db:
        jobs = db.query(DeliveryJob).filter_by(campaign_id=campaign_id).all()
        assert jobs and all(job.status == JobStatus.CANCELLED for job in jobs)
