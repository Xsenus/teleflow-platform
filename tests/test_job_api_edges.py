from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.api import jobs as jobs_api
from app.enums import (
    CampaignStatus,
    ConnectionStatus,
    DeliveryReviewResolution,
    JobStatus,
)
from app.models import DeliveryJob
from tests.conftest import csrf_headers
from tests.test_delivery_safety import prepare_job


def test_job_listing_retry_and_cancel_lifecycle(auth_client: TestClient) -> None:
    """Проверить фильтры очереди и безопасный переход failed → retry → cancelled."""
    _connection, now = prepare_job(auth_client)
    with auth_client.app.state.session_factory() as db:
        job = db.query(DeliveryJob).one()
        job_id = job.id
        campaign_id = job.campaign_id

    pending_retry = auth_client.post(
        f"/api/v1/jobs/{job_id}/retry", headers=csrf_headers(auth_client)
    )
    assert pending_retry.status_code == 409

    with auth_client.app.state.session_factory() as db:
        job = db.get(DeliveryJob, job_id)
        assert job is not None
        job.status = JobStatus.FAILED
        job.finished_at = now
        job.error_code = "TEST_FAILURE"
        db.commit()

    retried = auth_client.post(f"/api/v1/jobs/{job_id}/retry", headers=csrf_headers(auth_client))
    assert retried.status_code == 200, retried.text
    assert retried.json()["status"] == JobStatus.RETRY.value
    assert retried.json()["error_code"] is None

    filtered = auth_client.get(
        "/api/v1/jobs",
        params={"campaign_id": campaign_id, "status_filter": "retry", "limit": 0},
    )
    absent = auth_client.get("/api/v1/jobs", params={"campaign_id": "missing-campaign"})
    assert filtered.status_code == absent.status_code == 200
    assert [item["id"] for item in filtered.json()] == [job_id]
    assert absent.json() == []

    cancelled = auth_client.post(f"/api/v1/jobs/{job_id}/cancel", headers=csrf_headers(auth_client))
    assert cancelled.status_code == 200, cancelled.text
    stored = auth_client.get(f"/api/v1/jobs/{job_id}")
    assert stored.status_code == 200, stored.text
    assert stored.json()["status"] == JobStatus.CANCELLED.value
    assert stored.json()["error_code"] == "MANUAL_CANCEL"
    assert (
        auth_client.post(
            f"/api/v1/jobs/{job_id}/cancel", headers=csrf_headers(auth_client)
        ).status_code
        == 409
    )

    assert auth_client.get("/api/v1/jobs/missing-job").status_code == 404
    assert (
        auth_client.post(
            "/api/v1/jobs/missing-job/cancel", headers=csrf_headers(auth_client)
        ).status_code
        == 404
    )


def test_job_retry_validates_related_state(auth_client: TestClient) -> None:
    """Проверить запрет retry после review и при неактивных связанных объектах."""
    _connection, now = prepare_job(auth_client)
    with auth_client.app.state.session_factory() as db:
        job = db.query(DeliveryJob).one()
        job_id = job.id
        job.status = JobStatus.FAILED
        job.finished_at = now
        job.review_resolution = DeliveryReviewResolution.SKIPPED
        db.commit()
    assert (
        auth_client.post(
            f"/api/v1/jobs/{job_id}/retry", headers=csrf_headers(auth_client)
        ).status_code
        == 409
    )

    with auth_client.app.state.session_factory() as db:
        job = db.get(DeliveryJob, job_id)
        assert job is not None
        job.review_resolution = None
        job.connection.status = ConnectionStatus.PAUSED
        db.commit()
    assert (
        auth_client.post(
            f"/api/v1/jobs/{job_id}/retry", headers=csrf_headers(auth_client)
        ).status_code
        == 409
    )

    with auth_client.app.state.session_factory() as db:
        job = db.get(DeliveryJob, job_id)
        assert job is not None
        job.connection.status = ConnectionStatus.ACTIVE
        job.destination.enabled = False
        db.commit()
    assert (
        auth_client.post(
            f"/api/v1/jobs/{job_id}/retry", headers=csrf_headers(auth_client)
        ).status_code
        == 409
    )

    with auth_client.app.state.session_factory() as db:
        job = db.get(DeliveryJob, job_id)
        assert job is not None
        job.destination.enabled = True
        job.campaign.status = CampaignStatus.PAUSED
        db.commit()
    assert (
        auth_client.post(
            f"/api/v1/jobs/{job_id}/retry", headers=csrf_headers(auth_client)
        ).status_code
        == 409
    )

    with auth_client.app.state.session_factory() as db:
        job = db.get(DeliveryJob, job_id)
        assert job is not None
        job.campaign.status = CampaignStatus.SCHEDULED
        job.campaign.notes = "Изменение после утверждения"
        db.commit()
    stale = auth_client.post(f"/api/v1/jobs/{job_id}/retry", headers=csrf_headers(auth_client))
    assert stale.status_code == 409
    assert "утверждение" in stale.text.lower()


def test_job_uncertain_resolution_rejects_invalid_states(
    auth_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Проверить 404/409 ручной сверки и запрет отмены неоднозначной доставки."""
    _connection, _now = prepare_job(auth_client)
    with auth_client.app.state.session_factory() as db:
        job = db.query(DeliveryJob).one()
        job_id = job.id

    payload = {
        "resolution": "confirmed_not_sent",
        "note": "Результат вручную проверен оператором",
    }
    assert (
        auth_client.post(
            "/api/v1/jobs/missing-job/resolve",
            headers=csrf_headers(auth_client),
            json=payload,
        ).status_code
        == 404
    )
    assert (
        auth_client.post(
            f"/api/v1/jobs/{job_id}/resolve",
            headers=csrf_headers(auth_client),
            json=payload,
        ).status_code
        == 409
    )

    with auth_client.app.state.session_factory() as db:
        job = db.get(DeliveryJob, job_id)
        assert job is not None
        job.status = JobStatus.WAITING_REVIEW
        job.error_code = "SAFETY_POLICY_BLOCK"
        db.commit()
    wrong_blocker = auth_client.post(
        f"/api/v1/jobs/{job_id}/resolve",
        headers=csrf_headers(auth_client),
        json=payload,
    )
    assert wrong_blocker.status_code == 409
    assert "safety" in wrong_blocker.text.lower()

    with auth_client.app.state.session_factory() as db:
        job = db.get(DeliveryJob, job_id)
        assert job is not None
        job.error_code = "DELIVERY_RESULT_UNCERTAIN"
        db.commit()
    real_capacity_decision = jobs_api.capacity_ready_release_decision
    monkeypatch.setattr(
        jobs_api,
        "capacity_ready_release_decision",
        lambda *args, **kwargs: SimpleNamespace(allowed=False, message="Очередь заполнена"),
    )
    capacity_blocked = auth_client.post(
        f"/api/v1/jobs/{job_id}/resolve",
        headers=csrf_headers(auth_client),
        json=payload,
    )
    assert capacity_blocked.status_code == 409
    assert "очередь" in capacity_blocked.text.lower()
    monkeypatch.setattr(jobs_api, "capacity_ready_release_decision", real_capacity_decision)

    with auth_client.app.state.session_factory() as db:
        job = db.get(DeliveryJob, job_id)
        assert job is not None
        job.connection.status = ConnectionStatus.PAUSED
        db.commit()
    assert (
        auth_client.post(
            f"/api/v1/jobs/{job_id}/cancel", headers=csrf_headers(auth_client)
        ).status_code
        == 409
    )
    inactive_connection = auth_client.post(
        f"/api/v1/jobs/{job_id}/resolve",
        headers=csrf_headers(auth_client),
        json=payload,
    )
    assert inactive_connection.status_code == 409
    assert "подключение" in inactive_connection.text.lower()

    with auth_client.app.state.session_factory() as db:
        job = db.get(DeliveryJob, job_id)
        assert job is not None
        job.connection.status = ConnectionStatus.ACTIVE
        job.destination.enabled = False
        db.commit()
    disabled_destination = auth_client.post(
        f"/api/v1/jobs/{job_id}/resolve",
        headers=csrf_headers(auth_client),
        json=payload,
    )
    assert disabled_destination.status_code == 409
    assert "назначение" in disabled_destination.text.lower()

    with auth_client.app.state.session_factory() as db:
        job = db.get(DeliveryJob, job_id)
        assert job is not None
        job.destination.enabled = True
        job.campaign.status = CampaignStatus.PAUSED
        db.commit()
    inactive_campaign = auth_client.post(
        f"/api/v1/jobs/{job_id}/resolve",
        headers=csrf_headers(auth_client),
        json=payload,
    )
    assert inactive_campaign.status_code == 409
    assert "кампания" in inactive_campaign.text.lower()

    with auth_client.app.state.session_factory() as db:
        job = db.get(DeliveryJob, job_id)
        assert job is not None
        job.campaign.status = CampaignStatus.SCHEDULED
        job.campaign.notes = "Устаревший fingerprint после ручного изменения"
        db.commit()
    stale_approval = auth_client.post(
        f"/api/v1/jobs/{job_id}/resolve",
        headers=csrf_headers(auth_client),
        json=payload,
    )
    assert stale_approval.status_code == 409
    assert "утверждение" in stale_approval.text.lower()
