from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError

from app.config import Settings
from app.enums import (
    DeliveryAttemptStatus,
    JobStatus,
    OrganizationStatus,
    UserRole,
)
from app.models import (
    CapacityAssessment,
    CapacityPolicy,
    DeliveryAttempt,
    DeliveryJob,
    Organization,
    User,
)
from app.security import hash_password
from app.services.capacity import (
    CapacityError,
    capacity_admission_decision,
    capacity_dispatch_decision,
    evaluate_due_capacity_policies,
)
from app.services.delivery import DeliveryService
from app.services.rollouts import release_next_batch
from app.services.runtime_evidence import canonical_sha256, critical_config_payload
from app.services.scheduler import SchedulerService
from tests.conftest import csrf_headers
from tests.test_api_workflow import (
    approve_campaign,
    create_connection,
    create_template,
)


def _create_destination(
    client: TestClient,
    connection_id: str,
    index: int,
) -> dict:
    """Создать one independently resolved destination for multi-route capacity tests."""

    response = client.post(
        "/api/v1/destinations",
        headers=csrf_headers(client),
        json={
            "connection_id": connection_id,
            "username": f"capacity_allowed_group_{index}",
            "kind": "supergroup",
            "permission_confirmed": True,
            "permission_note": "Публикации разрешены администратором тестовой группы",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def _create_campaign(
    client: TestClient,
    *,
    destination_count: int = 1,
    staged: bool = False,
    batch_size: int = 1,
) -> tuple[dict, dict, list[dict]]:
    """Создать a fully approved campaign whose route size is controlled by the test."""

    connection = create_connection(client, name=f"Capacity Bot {destination_count}")
    destinations = [
        _create_destination(client, connection["id"], index) for index in range(destination_count)
    ]
    template = create_template(client)
    response = client.post(
        "/api/v1/campaigns",
        headers=csrf_headers(client),
        json={
            "name": f"Capacity route {destination_count}",
            "connection_id": connection["id"],
            "template_id": template["id"],
            "destination_ids": [item["id"] for item in destinations],
            "schedule_type": "once",
            "schedule_at": datetime.now(UTC).isoformat(),
            "timezone_name": "Europe/Amsterdam",
            "weekdays": [],
            "spacing_seconds": 1,
            "rollout_mode": "staged" if staged else "standard",
            "rollout_batch_size": batch_size,
            "rollout_pause_seconds": 0,
            "rollout_require_checkpoint": True,
        },
    )
    assert response.status_code == 201, response.text
    campaign = response.json()
    approve_campaign(client, campaign["id"])
    return campaign, connection, destinations


def _patch_policy(client: TestClient, **values: object) -> dict:
    """Включить strict capacity gates while preserving unspecified safe defaults."""

    if "max_active_jobs" in values and "max_jobs_per_run" not in values:
        values["max_jobs_per_run"] = min(int(values["max_active_jobs"]), 100)
    response = client.patch(
        "/api/v1/capacity/policy",
        headers=csrf_headers(client),
        json={
            "enabled": True,
            "gate_admission": True,
            "gate_dispatch": True,
            **values,
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def _schedule_single_job(client: TestClient) -> tuple[dict, datetime]:
    """Создать, approve and schedule exactly one delivery job without sending it."""

    campaign, connection, _destinations = _create_campaign(client)
    response = client.post(
        f"/api/v1/campaigns/{campaign['id']}/run-now",
        headers=csrf_headers(client),
    )
    assert response.status_code == 200, response.text
    now = datetime.now(UTC) + timedelta(seconds=2)
    scheduler = SchedulerService(
        client.app.state.session_factory,
        client.app.state.settings,
    )
    assert scheduler.tick(now=now) == 1
    return connection, now


def test_capacity_policy_overview_and_manual_assessment(
    auth_client: TestClient,
) -> None:
    """Проверить safe defaults, current metrics and immutable manual evidence."""

    overview = auth_client.get("/api/v1/capacity/overview")
    assert overview.status_code == 200, overview.text
    body = overview.json()
    assert body["policy"]["max_active_jobs"] >= body["policy"]["max_ready_jobs"]
    assert body["policy"]["max_ready_jobs"] >= body["policy"]["max_processing_jobs"]
    assert body["metrics"]["active_jobs"] == 0
    assert body["admission_allowed"] is True

    created = auth_client.post(
        "/api/v1/capacity/assessments",
        headers=csrf_headers(auth_client),
    )
    assert created.status_code == 201, created.text
    evidence = created.json()
    assert evidence["source"] == "manual"
    assert len(evidence["policy_sha256"]) == 64
    assert len(evidence["fingerprint"]) == 64
    assert auth_client.get("/api/v1/capacity/overview").json()["assessment_current"] is True


def test_capacity_policy_rejects_contradictory_limits(
    auth_client: TestClient,
) -> None:
    """Проверить API and service validation reject internally impossible policies."""

    response = auth_client.patch(
        "/api/v1/capacity/policy",
        headers=csrf_headers(auth_client),
        json={
            "max_active_jobs": 5,
            "max_ready_jobs": 4,
            "max_processing_jobs": 6,
        },
    )
    assert response.status_code in {409, 422}

    response = auth_client.patch(
        "/api/v1/capacity/policy",
        headers=csrf_headers(auth_client),
        json={
            "max_network_starts_per_minute": 100,
            "max_network_starts_per_hour": 50,
        },
    )
    assert response.status_code in {409, 422}


def test_admission_utilization_uses_the_most_saturated_queue_dimension(
    auth_client: TestClient,
) -> None:
    """Проверить a nearly full ready queue is not hidden by a much larger active-job limit."""

    _patch_policy(
        auth_client,
        max_active_jobs=100,
        max_ready_jobs=10,
        max_processing_jobs=2,
        max_active_runs=20,
        max_jobs_per_run=100,
        warning_utilization_percent=70,
        admission_block_utilization_percent=90,
    )
    with auth_client.app.state.session_factory() as db:
        owner = db.query(User).filter(User.email == "owner@example.com").one()
        decision = capacity_admission_decision(
            db,
            organization_id=owner.organization_id,
            settings=auth_client.app.state.settings,
            incoming_jobs=9,
            incoming_ready_jobs=9,
            incoming_runs=1,
            incoming_connection_id=None,
            incoming_due_span_seconds=0,
            persist_assessment=True,
        )
        assert decision.allowed is False
        assert decision.assessment is not None
        assert decision.assessment.queue_utilization_percent == 90
        assert any(
            item["code"] == "queue_utilization" and item["status"] == "blocked"
            for item in decision.assessment.checks
        )


def test_run_now_is_blocked_before_jobs_are_created(
    auth_client: TestClient,
) -> None:
    """Проверить an oversized route is rejected before scheduler admission or queue mutation."""

    campaign, _connection, _destinations = _create_campaign(
        auth_client,
        destination_count=2,
    )
    _patch_policy(
        auth_client,
        max_active_jobs=10,
        max_ready_jobs=10,
        max_processing_jobs=2,
        max_jobs_per_run=1,
    )
    response = auth_client.post(
        f"/api/v1/campaigns/{campaign['id']}/run-now",
        headers=csrf_headers(auth_client),
    )
    assert response.status_code == 409
    assert "capacity" in response.text.lower() or "запуск" in response.text.lower()
    with auth_client.app.state.session_factory() as db:
        assert db.query(DeliveryJob).count() == 0


def test_monitor_only_policy_reports_blocker_but_allows_run_request(
    auth_client: TestClient,
) -> None:
    """Проверить operators can observe projected overload before enabling enforcement."""

    campaign, _connection, _destinations = _create_campaign(
        auth_client,
        destination_count=2,
    )
    response = auth_client.patch(
        "/api/v1/capacity/policy",
        headers=csrf_headers(auth_client),
        json={
            "enabled": True,
            "gate_admission": False,
            "gate_dispatch": False,
            "max_active_jobs": 10,
            "max_ready_jobs": 10,
            "max_processing_jobs": 2,
            "max_jobs_per_run": 1,
        },
    )
    assert response.status_code == 200, response.text
    run = auth_client.post(
        f"/api/v1/campaigns/{campaign['id']}/run-now",
        headers=csrf_headers(auth_client),
    )
    assert run.status_code == 200, run.text
    assessments = auth_client.get("/api/v1/capacity/assessments").json()
    assert assessments[0]["status"] == "blocked"
    assert assessments[0]["projected_jobs"] == 2


def test_scheduler_rechecks_capacity_after_run_now(
    auth_client: TestClient,
) -> None:
    """Проверить a policy tightened after run-now still blocks transactional job creation."""

    campaign, _connection, _destinations = _create_campaign(
        auth_client,
        destination_count=2,
    )
    assert (
        auth_client.post(
            f"/api/v1/campaigns/{campaign['id']}/run-now",
            headers=csrf_headers(auth_client),
        ).status_code
        == 200
    )
    _patch_policy(
        auth_client,
        max_active_jobs=10,
        max_ready_jobs=10,
        max_processing_jobs=2,
        max_jobs_per_run=1,
    )
    scheduler = SchedulerService(
        auth_client.app.state.session_factory,
        auth_client.app.state.settings,
    )
    assert scheduler.tick(now=datetime.now(UTC) + timedelta(seconds=3)) == 0
    with auth_client.app.state.session_factory() as db:
        assert db.query(DeliveryJob).count() == 0
    current = auth_client.get(f"/api/v1/campaigns/{campaign['id']}").json()
    assert current["status"] == "paused"


def test_transient_rate_exhaustion_defers_dispatch_without_rejecting_bounded_queue(
    auth_client: TestClient,
) -> None:
    """Проверить admission includes window delay but leaves immediate dispatch to Safety Engine."""

    _patch_policy(
        auth_client,
        max_active_jobs=10,
        max_ready_jobs=10,
        max_processing_jobs=2,
        max_network_starts_per_minute=1,
        max_network_starts_per_hour=10,
        max_estimated_drain_seconds=10_000,
    )
    connection, now = _schedule_single_job(auth_client)
    with auth_client.app.state.session_factory() as db:
        job = db.query(DeliveryJob).one()
        db.add(
            DeliveryAttempt(
                organization_id=job.organization_id,
                job_id=job.id,
                attempt_number=99,
                worker_id="recent-network-start",
                site_key="primary",
                fence_epoch=0,
                status=DeliveryAttemptStatus.SENT,
                prepared_at=now,
                network_started_at=now,
                finished_at=now,
                telegram_message_id="recent-message",
                details={},
            )
        )
        db.commit()
        decision = capacity_admission_decision(
            db,
            organization_id=job.organization_id,
            settings=auth_client.app.state.settings,
            incoming_jobs=0,
            incoming_ready_jobs=0,
            incoming_runs=0,
            incoming_connection_id=connection["id"],
            incoming_due_span_seconds=0,
            now=now + timedelta(seconds=1),
            persist_assessment=True,
        )
        assert decision.allowed is True
        assert decision.code == "CAPACITY_ADMISSION_ALLOWED_DISPATCH_DEFERRED"
        assert decision.assessment is not None
        assert decision.assessment.status.value == "blocked"
        assert decision.assessment.estimated_drain_seconds >= 59
        assert any(
            item["code"] == "network_starts_minute" and item["status"] == "blocked"
            for item in decision.assessment.checks
        )


def test_exact_minute_limit_blocks_before_gateway(
    auth_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Проверить the exact tenant rate limit defers work before gateway construction."""

    _patch_policy(
        auth_client,
        max_network_starts_per_minute=1,
        max_network_starts_per_hour=10,
    )
    _connection, now = _schedule_single_job(auth_client)
    with auth_client.app.state.session_factory() as db:
        job = db.query(DeliveryJob).one()
        db.add(
            DeliveryAttempt(
                organization_id=job.organization_id,
                job_id=job.id,
                attempt_number=99,
                worker_id="capacity-history",
                site_key="primary",
                fence_epoch=0,
                status=DeliveryAttemptStatus.SENT,
                prepared_at=now,
                network_started_at=now,
                finished_at=now,
                telegram_message_id="historical-message",
                details={},
            )
        )
        db.commit()

    def fail_gateway(*_args: object, **_kwargs: object) -> None:
        """Завершить с ошибкой, если immediately if capacity regression constructs a Telegram
        gateway.
        """

        raise AssertionError("Telegram gateway must not be constructed at the rate limit")

    monkeypatch.setattr("app.services.delivery.build_gateway", fail_gateway)
    delivery = DeliveryService(
        auth_client.app.state.session_factory,
        auth_client.app.state.settings,
        auth_client.app.state.cipher,
        worker_id="capacity-rate-worker",
    )
    assert delivery.process_next(now=now + timedelta(seconds=1)) is True
    with auth_client.app.state.session_factory() as db:
        job = db.query(DeliveryJob).one()
        assert job.status == JobStatus.PENDING
        assert job.attempt_count == 0
        assert job.telegram_message_id is None
        assert job.safety_decision["code"] == "CAPACITY_MINUTE_RATE_LIMIT"


def test_durable_reservations_prevent_concurrent_overbooking(
    auth_client: TestClient,
) -> None:
    """Проверить PREPARED attempt rows reserve tenant rate and processing capacity atomically."""

    _patch_policy(
        auth_client,
        max_active_jobs=10,
        max_ready_jobs=10,
        max_processing_jobs=1,
        max_network_starts_per_minute=1,
        max_network_starts_per_hour=10,
    )
    _connection, now = _schedule_single_job(auth_client)
    settings = auth_client.app.state.settings
    with auth_client.app.state.session_factory() as db:
        job = db.query(DeliveryJob).one()
        job.status = JobStatus.PROCESSING
        attempt = DeliveryAttempt(
            organization_id=job.organization_id,
            job_id=job.id,
            attempt_number=1,
            worker_id="reservation-one",
            site_key="primary",
            fence_epoch=1,
            status=DeliveryAttemptStatus.PREPARED,
            prepared_at=now,
            details={},
        )
        db.add(attempt)
        db.commit()
        allowed = capacity_dispatch_decision(
            db,
            organization_id=job.organization_id,
            settings=settings,
            now=now,
            reservation_attempt_id=attempt.id,
        )
        assert allowed.allowed is True

        second = DeliveryAttempt(
            organization_id=job.organization_id,
            job_id=job.id,
            attempt_number=2,
            worker_id="reservation-two",
            site_key="primary",
            fence_epoch=1,
            status=DeliveryAttemptStatus.PREPARED,
            prepared_at=now,
            details={},
        )
        db.add(second)
        db.commit()
        blocked = capacity_dispatch_decision(
            db,
            organization_id=job.organization_id,
            settings=settings,
            now=now,
            reservation_attempt_id=second.id,
        )
        assert blocked.allowed is False
        assert blocked.code == "CAPACITY_MINUTE_RATE_LIMIT"


def test_delivery_final_reservation_blocks_race_before_gateway(
    auth_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Проверить a reservation arriving after safety precheck still prevents a Telegram call."""

    _patch_policy(
        auth_client,
        max_active_jobs=10,
        max_ready_jobs=10,
        max_processing_jobs=5,
        max_network_starts_per_minute=1,
        max_network_starts_per_hour=10,
    )
    _connection, now = _schedule_single_job(auth_client)
    with auth_client.app.state.session_factory() as db:
        job = db.query(DeliveryJob).one()
        db.add(
            DeliveryAttempt(
                organization_id=job.organization_id,
                job_id=job.id,
                attempt_number=99,
                worker_id="concurrent-reservation",
                site_key="primary",
                fence_epoch=0,
                status=DeliveryAttemptStatus.PREPARED,
                prepared_at=now,
                details={"test": "competing durable reservation"},
            )
        )
        db.commit()

    def fail_gateway(*_args: object, **_kwargs: object) -> None:
        """Завершить с ошибкой, если if the final capacity reservation does not stop gateway
        construction.
        """

        raise AssertionError("Telegram gateway must not be constructed after reservation race")

    monkeypatch.setattr("app.services.delivery.build_gateway", fail_gateway)
    delivery = DeliveryService(
        auth_client.app.state.session_factory,
        auth_client.app.state.settings,
        auth_client.app.state.cipher,
        worker_id="capacity-race-worker",
    )
    assert delivery.process_next(now=now + timedelta(seconds=1)) is True
    with auth_client.app.state.session_factory() as db:
        job = db.query(DeliveryJob).one()
        attempts = list(
            db.query(DeliveryAttempt)
            .filter(DeliveryAttempt.job_id == job.id)
            .order_by(DeliveryAttempt.attempt_number)
        )
        current = next(item for item in attempts if item.attempt_number == 1)
        assert job.status == JobStatus.PENDING
        assert job.telegram_message_id is None
        assert current.status == DeliveryAttemptStatus.ABANDONED
        assert current.network_started_at is None
        assert current.error_code == "CAPACITY_MINUTE_RATE_LIMIT"
        assert current.details["telegram_call_started"] is False


def test_partial_policy_update_revalidates_effective_limits(
    auth_client: TestClient,
) -> None:
    """Проверить partial API updates cannot bypass invariants using values already in the row."""

    _patch_policy(
        auth_client,
        max_active_jobs=10,
        max_ready_jobs=5,
        max_processing_jobs=2,
    )
    response = auth_client.patch(
        "/api/v1/capacity/policy",
        headers=csrf_headers(auth_client),
        json={"max_processing_jobs": 6},
    )
    assert response.status_code == 409
    stored = auth_client.get("/api/v1/capacity/policy").json()
    assert stored["max_processing_jobs"] == 2
    assert stored["max_ready_jobs"] == 5


def test_ready_queue_blocks_staged_batch_release(
    auth_client: TestClient,
) -> None:
    """Проверить HELD jobs remain held when releasing them would overbook the ready queue."""

    campaign, _connection, _destinations = _create_campaign(
        auth_client,
        destination_count=2,
        staged=True,
        batch_size=1,
    )
    assert (
        auth_client.post(
            f"/api/v1/campaigns/{campaign['id']}/run-now",
            headers=csrf_headers(auth_client),
        ).status_code
        == 200
    )
    scheduler = SchedulerService(
        auth_client.app.state.session_factory,
        auth_client.app.state.settings,
    )
    now = datetime.now(UTC) + timedelta(seconds=2)
    assert scheduler.tick(now=now) == 1
    _patch_policy(
        auth_client,
        max_active_jobs=10,
        max_ready_jobs=1,
        max_processing_jobs=1,
    )
    with auth_client.app.state.session_factory() as db:
        run = db.query(DeliveryJob).first().run
        with pytest.raises(CapacityError):
            release_next_batch(
                db,
                run=run,
                now=now,
                settings=auth_client.app.state.settings,
            )
        statuses = [item.status for item in db.query(DeliveryJob).order_by(DeliveryJob.id)]
        assert statuses.count(JobStatus.HELD) == 1
        assert statuses.count(JobStatus.PENDING) == 1


def test_manual_retry_is_atomic_under_ready_queue_backpressure(
    auth_client: TestClient,
) -> None:
    """Проверить rejected manual retry leaves the terminal job unchanged."""

    _connection, now = _schedule_single_job(auth_client)
    _patch_policy(
        auth_client,
        max_active_jobs=10,
        max_ready_jobs=1,
        max_processing_jobs=1,
    )
    with auth_client.app.state.session_factory() as db:
        original = db.query(DeliveryJob).one()
        original.status = JobStatus.FAILED
        original.finished_at = now
        original.error_code = "TEST_FAILURE"
        clone = DeliveryJob(
            organization_id=original.organization_id,
            run_id=original.run_id,
            campaign_id=original.campaign_id,
            connection_id=original.connection_id,
            destination_id=original.destination_id,
            template_id=original.template_id,
            body_snapshot=original.body_snapshot,
            parse_mode=original.parse_mode,
            link_preview=original.link_preview,
            due_at=now,
            status=JobStatus.PENDING,
            idempotency_key="f" * 64,
            batch_number=1,
        )
        db.add(clone)
        db.commit()
        job_id = original.id

    response = auth_client.post(
        f"/api/v1/jobs/{job_id}/retry",
        headers=csrf_headers(auth_client),
    )
    assert response.status_code == 409
    with auth_client.app.state.session_factory() as db:
        stored = db.get(DeliveryJob, job_id)
        assert stored is not None
        assert stored.status == JobStatus.FAILED
        assert stored.error_code == "TEST_FAILURE"


def test_worker_creates_one_capacity_assessment_per_interval(
    auth_client: TestClient,
) -> None:
    """Проверить periodic assessment is idempotent within the configured worker interval."""

    assert auth_client.get("/api/v1/capacity/policy").status_code == 200
    with auth_client.app.state.session_factory() as db:
        assert (
            evaluate_due_capacity_policies(
                db,
                settings=auth_client.app.state.settings,
            )
            == 1
        )
        db.commit()
    with auth_client.app.state.session_factory() as db:
        assert (
            evaluate_due_capacity_policies(
                db,
                settings=auth_client.app.state.settings,
            )
            == 0
        )
        assert db.query(CapacityAssessment).count() == 1


def test_policy_change_invalidates_previous_capacity_assessment(
    auth_client: TestClient,
) -> None:
    """Проверить policy fingerprints make older evidence stale without rewriting it."""

    created = auth_client.post(
        "/api/v1/capacity/assessments",
        headers=csrf_headers(auth_client),
    )
    assert created.status_code == 201
    before = created.json()["policy_sha256"]
    _patch_policy(auth_client, max_active_jobs=600)
    overview = auth_client.get("/api/v1/capacity/overview")
    assert overview.status_code == 200
    assert overview.json()["assessment_current"] is False
    with auth_client.app.state.session_factory() as db:
        stored = db.get(CapacityAssessment, created.json()["id"])
        assert stored is not None
        assert stored.policy_sha256 == before


def test_capacity_metrics_are_exported(auth_client: TestClient) -> None:
    """Проверить Prometheus exposes queue pressure, staleness and backpressure state."""

    assert (
        auth_client.post(
            "/api/v1/capacity/assessments",
            headers=csrf_headers(auth_client),
        ).status_code
        == 201
    )
    metrics = auth_client.get("/metrics")
    assert metrics.status_code == 200
    assert "teleflow_capacity_latest_assessments" in metrics.text
    assert "teleflow_capacity_queue_utilization_max_percent" in metrics.text
    assert "teleflow_capacity_estimated_drain_seconds_max" in metrics.text
    assert "teleflow_capacity_stale_assessments" in metrics.text
    assert "teleflow_capacity_backpressure_organizations" in metrics.text


def test_capacity_monitoring_assets_cover_all_backpressure_signals() -> None:
    """Проверить Prometheus alerts and Grafana panels expose every operational limit."""

    alerts = Path("deploy/prometheus/alerts.yml").read_text(encoding="utf-8")
    dashboard = Path("deploy/grafana/dashboards/teleflow-overview.json").read_text(encoding="utf-8")
    for alert_name in (
        "TeleFlowCapacityBackpressureActive",
        "TeleFlowCapacityAssessmentStale",
        "TeleFlowCapacityDrainTimeHigh",
    ):
        assert alert_name in alerts
    for metric_name in (
        "teleflow_capacity_backpressure_organizations",
        "teleflow_capacity_stale_assessments",
        "teleflow_capacity_queue_utilization_max_percent",
        "teleflow_capacity_estimated_drain_seconds_max",
    ):
        assert metric_name in dashboard


def test_capacity_data_is_tenant_isolated_and_viewer_cannot_modify(
    auth_client: TestClient,
) -> None:
    """Проверить capacity policies and evidence never cross tenant or read-only role boundaries."""

    assert (
        auth_client.post(
            "/api/v1/capacity/assessments",
            headers=csrf_headers(auth_client),
        ).status_code
        == 201
    )
    with auth_client.app.state.session_factory() as db:
        organization = Organization(
            name="Capacity Tenant Two",
            slug="capacity-tenant-two",
            status=OrganizationStatus.ACTIVE,
            timezone_name="Europe/Amsterdam",
            retention_days=30,
            ai_enabled=False,
            settings={},
        )
        db.add(organization)
        db.flush()
        user = User(
            organization_id=organization.id,
            email="capacity-viewer@example.com",
            display_name="Capacity Viewer",
            password_hash=hash_password(
                "CapacityViewer_123!",
                auth_client.app.state.settings,
            ),
            role=UserRole.VIEWER,
            is_active=True,
            must_change_password=False,
        )
        db.add(user)
        db.commit()

    second = TestClient(auth_client.app)
    try:
        login = second.post(
            "/api/v1/auth/login",
            json={
                "email": "capacity-viewer@example.com",
                "password": "CapacityViewer_123!",
            },
        )
        assert login.status_code == 200, login.text
        assert second.get("/api/v1/capacity/assessments").json() == []
        assert second.get("/api/v1/capacity/overview").status_code == 200
        denied = second.patch(
            "/api/v1/capacity/policy",
            headers=csrf_headers(second),
            json={"enabled": False},
        )
        assert denied.status_code == 403
    finally:
        second.close()


def test_capacity_controls_are_part_of_runtime_fencing_fingerprint(
    auth_client: TestClient,
) -> None:
    """Проверить active and standby sites cannot disagree about capacity enforcement."""

    settings = auth_client.app.state.settings
    payload = critical_config_payload(settings)
    assert payload["capacity_assurance_required"] == settings.capacity_assurance_required
    assert payload["capacity_default_max_ready_jobs"] == settings.capacity_default_max_ready_jobs
    original = canonical_sha256(payload)
    changed = settings.model_copy(
        update={"capacity_default_max_ready_jobs": settings.capacity_default_max_ready_jobs + 1}
    )
    assert original != canonical_sha256(critical_config_payload(changed))


def test_production_requires_capacity_assurance() -> None:
    """Проверить production configuration cannot disable queue and dispatch backpressure gates."""

    settings = Settings(
        _env_file=None,
        environment="production",
        debug=False,
        database_url="postgresql+psycopg://user:pass@db/teleflow",
        master_key="base64:MDEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODlhYmNkZWY=",
        jwt_secret="j" * 64,
        bootstrap_admin_password="A-strong-bootstrap-password-123!",
        public_base_url="https://teleflow.example.com",
        cors_origins="https://teleflow.example.com",
        allowed_hosts="teleflow.example.com",
        secure_cookies=True,
        telegram_fake_mode=False,
        auto_create_schema=False,
        require_admin_totp=True,
        pilot_readiness_required=True,
        pilot_stage_enforcement_required=True,
        slo_gate_required=True,
        execution_fencing_required=True,
        execution_site_key="primary",
        execution_primary_site_key="primary",
        continuity_assurance_required=True,
        continuity_require_distinct_signoff=True,
        continuity_default_require_live_drill=True,
        artifact_signature_policy="require_trusted",
        require_trusted_release_attestation=True,
        require_release_transparency=True,
        require_dependency_assessment=True,
        dependency_require_vulnerability_scan=True,
        dependency_require_trusted_report=True,
        recovery_require_encrypted_backup=True,
        recovery_require_trusted_signature=True,
        recovery_age_recipient="age1qql3f0exampleonlyrecipientxxxxxxxxxxxxxxxxxxxxxxxxx",
        capacity_assurance_required=False,
    )
    with pytest.raises(RuntimeError, match="CAPACITY_ASSURANCE_REQUIRED"):
        settings.validate_runtime_security()


def test_database_constraints_reject_invalid_capacity_policy(
    auth_client: TestClient,
) -> None:
    """Проверить direct database writes cannot bypass ordered capacity limits."""

    with auth_client.app.state.session_factory() as db:
        owner = db.query(User).filter(User.email == "owner@example.com").one()
        policy = CapacityPolicy(
            organization_id=owner.organization_id,
            enabled=True,
            max_active_jobs=5,
            max_ready_jobs=6,
            max_processing_jobs=1,
            max_active_runs=1,
            max_jobs_per_run=1,
            max_network_starts_per_minute=1,
            max_network_starts_per_hour=1,
            max_estimated_drain_seconds=60,
            warning_utilization_percent=70,
            admission_block_utilization_percent=90,
            assessment_ttl_minutes=15,
            gate_admission=True,
            gate_dispatch=True,
        )
        db.add(policy)
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()
