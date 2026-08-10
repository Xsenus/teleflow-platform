from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from app.enums import JobStatus
from app.models import DeliveryJob, WorkerHeartbeat
from app.services.scheduler import SchedulerService
from tests.conftest import csrf_headers
from tests.test_api_workflow import (
    approve_campaign,
    create_campaign,
    create_connection,
    create_destination,
    create_template,
)


def _fresh_heartbeat(client: TestClient, worker_id: str = "slo-test-worker") -> None:
    """Реализовать внутренний этап fresh heartbeat step. Вспомогательная функция сохраняет
    детерминированность и тестируемость процесса.
    """
    factory = client.app.state.session_factory
    with factory() as db:
        item = db.get(WorkerHeartbeat, worker_id)
        if item is None:
            item = WorkerHeartbeat(
                worker_id=worker_id,
                hostname="test-host",
                pid=1,
                version=client.app.state.settings.version,
                last_seen_at=datetime.now(UTC),
                details={"test": True},
            )
            db.add(item)
        else:
            item.last_seen_at = datetime.now(UTC)
        db.commit()


def _enable_gate(client: TestClient, **extra: object) -> dict:
    """Реализовать внутренний этап enable gate step. Вспомогательная функция сохраняет
    детерминированность и тестируемость процесса.
    """
    payload = {
        "enabled": True,
        "gate_publishing": True,
        "gate_changes": True,
        "minimum_delivery_sample_size": 1,
        **extra,
    }
    response = client.patch(
        "/api/v1/operations/slo-policy",
        headers=csrf_headers(client),
        json=payload,
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_missing_worker_creates_slo_incident_and_blocks_gate(auth_client: TestClient) -> None:
    """Проверить сценарий missing worker creates slo incident and blocks gate. Тест завершается
    ошибкой при нарушении зафиксированного инварианта.
    """
    _enable_gate(auth_client)

    assessed = auth_client.post(
        "/api/v1/operations/slo-assessments",
        headers=csrf_headers(auth_client),
    )
    assert assessed.status_code == 201, assessed.text
    body = assessed.json()
    assert body["status"] == "blocked"
    assert any(item["code"] == "worker_heartbeat" for item in body["checks"])
    assert len(body["fingerprint"]) == 64

    incidents = auth_client.get("/api/v1/operations/incidents")
    assert incidents.status_code == 200
    items = incidents.json()
    assert any(
        item["source"] == "slo" and item["status"] == "open" and item["severity"] == "critical"
        for item in items
    )

    overview = auth_client.get("/api/v1/operations/overview")
    assert overview.status_code == 200
    assert overview.json()["assessment_current"] is True
    assert overview.json()["publishing_gate_allowed"] is False
    assert overview.json()["open_critical_incidents"] >= 1


def test_recovered_slo_auto_resolves_its_incident(auth_client: TestClient) -> None:
    """Проверить сценарий recovered slo auto resolves its incident. Тест завершается ошибкой при
    нарушении зафиксированного инварианта.
    """
    _enable_gate(auth_client)
    first = auth_client.post(
        "/api/v1/operations/slo-assessments",
        headers=csrf_headers(auth_client),
    )
    assert first.status_code == 201
    assert first.json()["status"] == "blocked"

    _fresh_heartbeat(auth_client)
    second = auth_client.post(
        "/api/v1/operations/slo-assessments",
        headers=csrf_headers(auth_client),
    )
    assert second.status_code == 201, second.text
    assert second.json()["status"] == "warning"
    assert not second.json()["blockers"]

    incidents = auth_client.get("/api/v1/operations/incidents").json()
    worker_incidents = [item for item in incidents if item["dedup_key"] == "slo:worker_heartbeat"]
    assert len(worker_incidents) == 1
    assert worker_incidents[0]["status"] == "resolved"

    overview = auth_client.get("/api/v1/operations/overview").json()
    assert overview["publishing_gate_allowed"] is True
    assert overview["changes_gate_allowed"] is True


def test_error_budget_breach_is_detected(auth_client: TestClient) -> None:
    """Проверить сценарий error budget breach is detected. Тест завершается ошибкой при нарушении
    зафиксированного инварианта.
    """
    connection = create_connection(auth_client, name="SLO Bot")
    destination = create_destination(auth_client, connection["id"])
    template = create_template(auth_client)
    campaign = create_campaign(auth_client, connection["id"], template["id"], destination["id"])
    approve_campaign(auth_client, campaign["id"])
    run_now = auth_client.post(
        f"/api/v1/campaigns/{campaign['id']}/run-now",
        headers=csrf_headers(auth_client),
    )
    assert run_now.status_code == 200, run_now.text
    scheduler = SchedulerService(
        auth_client.app.state.session_factory, auth_client.app.state.settings
    )
    assert scheduler.tick(now=datetime.now(UTC) + timedelta(seconds=2)) == 1

    factory = auth_client.app.state.session_factory
    with factory() as db:
        job = db.query(DeliveryJob).one()
        job.status = JobStatus.FAILED
        job.finished_at = datetime.now(UTC)
        job.error_code = "TEST_FAILURE"
        db.commit()

    _fresh_heartbeat(auth_client)
    _enable_gate(
        auth_client,
        delivery_success_target_bps=9900,
        error_budget_warning_percent=50,
        error_budget_critical_percent=100,
    )
    assessed = auth_client.post(
        "/api/v1/operations/slo-assessments",
        headers=csrf_headers(auth_client),
    )
    assert assessed.status_code == 201, assessed.text
    body = assessed.json()
    assert body["status"] == "blocked"
    assert body["eligible_deliveries"] == 1
    assert body["failed_deliveries"] == 1
    assert body["delivery_success_rate_bps"] == 0
    assert body["error_budget_consumed_bps"] >= 10000
    assert any(item["code"] == "delivery_error_budget" for item in body["checks"])


def test_publishing_gate_blocks_until_current_assessment(auth_client: TestClient) -> None:
    """Проверить сценарий publishing gate blocks until current assessment. Тест завершается ошибкой
    при нарушении зафиксированного инварианта.
    """
    _enable_gate(auth_client)
    connection = create_connection(auth_client, name="Gate Bot")
    destination = create_destination(auth_client, connection["id"])
    template = create_template(auth_client)
    campaign = create_campaign(auth_client, connection["id"], template["id"], destination["id"])
    approve_campaign(auth_client, campaign["id"])

    blocked = auth_client.post(
        f"/api/v1/campaigns/{campaign['id']}/run-now",
        headers=csrf_headers(auth_client),
    )
    assert blocked.status_code == 409
    assert "SLO" in blocked.text

    _fresh_heartbeat(auth_client)
    assessed = auth_client.post(
        "/api/v1/operations/slo-assessments",
        headers=csrf_headers(auth_client),
    )
    assert assessed.status_code == 201
    assert assessed.json()["status"] == "warning"

    allowed = auth_client.post(
        f"/api/v1/campaigns/{campaign['id']}/run-now",
        headers=csrf_headers(auth_client),
    )
    assert allowed.status_code == 200, allowed.text


def test_manual_incident_lifecycle_and_event_history(auth_client: TestClient) -> None:
    """Проверить сценарий manual incident lifecycle and event history. Тест завершается ошибкой при
    нарушении зафиксированного инварианта.
    """
    created = auth_client.post(
        "/api/v1/operations/incidents",
        headers=csrf_headers(auth_client),
        json={
            "title": "Worker queue degradation",
            "summary": "Очередь растёт быстрее обработки",
            "severity": "critical",
            "impact": "Отложенная публикация разрешённых сообщений",
        },
    )
    assert created.status_code == 201, created.text
    assert created.json()["source"] == "manual"
    incident_id = created.json()["id"]

    for action, expected in [
        ("acknowledge", "acknowledged"),
        ("mitigate", "mitigating"),
        ("resolve", "resolved"),
        ("close", "closed"),
        ("reopen", "open"),
    ]:
        response = auth_client.post(
            f"/api/v1/operations/incidents/{incident_id}/{action}",
            headers=csrf_headers(auth_client),
            json={
                "note": f"Действие {action} выполнено оператором",
                "root_cause": "Тестовая причина" if action == "resolve" else None,
            },
        )
        assert response.status_code == 200, response.text
        assert response.json()["status"] == expected

    events = auth_client.get(f"/api/v1/operations/incidents/{incident_id}/events")
    assert events.status_code == 200
    event_types = [item["event_type"] for item in events.json()]
    assert event_types == [
        "created",
        "acknowledged",
        "mitigation_started",
        "resolved",
        "closed",
        "reopened",
    ]


def test_policy_rejects_inverted_error_budget_thresholds(auth_client: TestClient) -> None:
    """Проверить сценарий policy rejects inverted error budget thresholds. Тест завершается ошибкой
    при нарушении зафиксированного инварианта.
    """
    response = auth_client.patch(
        "/api/v1/operations/slo-policy",
        headers=csrf_headers(auth_client),
        json={
            "error_budget_warning_percent": 200,
            "error_budget_critical_percent": 100,
        },
    )
    assert response.status_code == 422


def test_viewer_cannot_modify_slo_or_incidents(auth_client: TestClient) -> None:
    """Проверить сценарий viewer cannot modify slo or incidents. Тест завершается ошибкой при
    нарушении зафиксированного инварианта.
    """
    create_user = auth_client.post(
        "/api/v1/users",
        headers=csrf_headers(auth_client),
        json={
            "email": "slo-viewer@example.com",
            "display_name": "SLO Viewer",
            "password": "ViewerPassword_123!",
            "role": "viewer",
        },
    )
    assert create_user.status_code == 201, create_user.text

    auth_client.post("/api/v1/auth/logout", headers=csrf_headers(auth_client))
    login = auth_client.post(
        "/api/v1/auth/login",
        json={"email": "slo-viewer@example.com", "password": "ViewerPassword_123!"},
    )
    assert login.status_code == 200, login.text

    assert auth_client.get("/api/v1/operations/overview").status_code == 200
    assert (
        auth_client.patch(
            "/api/v1/operations/slo-policy",
            headers=csrf_headers(auth_client),
            json={"enabled": False},
        ).status_code
        == 403
    )
    assert (
        auth_client.post(
            "/api/v1/operations/incidents",
            headers=csrf_headers(auth_client),
            json={"title": "Denied", "summary": "Viewer cannot create incidents"},
        ).status_code
        == 403
    )


def test_policy_change_invalidates_previous_assessment(auth_client: TestClient) -> None:
    """Проверить сценарий policy change invalidates previous assessment. Тест завершается ошибкой
    при нарушении зафиксированного инварианта.
    """
    _fresh_heartbeat(auth_client)
    _enable_gate(auth_client, delivery_success_target_bps=9900)
    assessed = auth_client.post(
        "/api/v1/operations/slo-assessments",
        headers=csrf_headers(auth_client),
    )
    assert assessed.status_code == 201, assessed.text
    assert auth_client.get("/api/v1/operations/overview").json()["publishing_gate_allowed"] is True

    changed = auth_client.patch(
        "/api/v1/operations/slo-policy",
        headers=csrf_headers(auth_client),
        json={"delivery_success_target_bps": 9950},
    )
    assert changed.status_code == 200, changed.text
    overview = auth_client.get("/api/v1/operations/overview")
    assert overview.status_code == 200
    assert overview.json()["assessment_current"] is False
    assert overview.json()["publishing_gate_allowed"] is False


def test_expired_assessment_is_not_accepted_by_gate(auth_client: TestClient) -> None:
    """Проверить сценарий expired assessment is not accepted by gate. Тест завершается ошибкой при
    нарушении зафиксированного инварианта.
    """
    from app.models import SLOAssessment

    _fresh_heartbeat(auth_client)
    _enable_gate(auth_client)
    assessed = auth_client.post(
        "/api/v1/operations/slo-assessments",
        headers=csrf_headers(auth_client),
    )
    assert assessed.status_code == 201
    with auth_client.app.state.session_factory() as db:
        item = db.get(SLOAssessment, assessed.json()["id"])
        assert item is not None
        item.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        db.commit()

    overview = auth_client.get("/api/v1/operations/overview")
    assert overview.status_code == 200
    assert overview.json()["assessment_current"] is False
    assert overview.json()["publishing_gate_allowed"] is False


def test_maintenance_suppresses_automatic_slo_incident(auth_client: TestClient) -> None:
    """Проверить сценарий maintenance suppresses automatic slo incident. Тест завершается ошибкой
    при нарушении зафиксированного инварианта.
    """
    _enable_gate(auth_client, suppress_incidents_during_maintenance=True)
    start = auth_client.post(
        "/api/v1/changes/maintenance/start",
        headers=csrf_headers(auth_client),
        json={"reason": "Проверка подавления автоматических SLO-инцидентов"},
    )
    assert start.status_code == 200, start.text

    assessed = auth_client.post(
        "/api/v1/operations/slo-assessments",
        headers=csrf_headers(auth_client),
    )
    assert assessed.status_code == 201
    assert assessed.json()["status"] == "blocked"
    assert auth_client.get("/api/v1/operations/incidents").json() == []


def test_manual_api_cannot_spoof_system_incident_source(auth_client: TestClient) -> None:
    """Проверить сценарий manual api cannot spoof system incident source. Тест завершается ошибкой
    при нарушении зафиксированного инварианта.
    """
    response = auth_client.post(
        "/api/v1/operations/incidents",
        headers=csrf_headers(auth_client),
        json={
            "title": "Проверка происхождения",
            "summary": "Клиент пытается выдать ручной инцидент за системный",
            "severity": "warning",
            "source": "security",
        },
    )
    assert response.status_code == 201, response.text
    assert response.json()["source"] == "manual"


def test_slo_and_incident_data_are_tenant_isolated(auth_client: TestClient) -> None:
    """Проверить сценарий slo and incident data are tenant isolated. Тест завершается ошибкой при
    нарушении зафиксированного инварианта.
    """
    from app.enums import OrganizationStatus, UserRole
    from app.models import Organization, User
    from app.security import hash_password

    first_incident = auth_client.post(
        "/api/v1/operations/incidents",
        headers=csrf_headers(auth_client),
        json={
            "title": "Tenant one incident",
            "summary": "Должен быть скрыт от другой организации",
            "severity": "critical",
        },
    )
    assert first_incident.status_code == 201, first_incident.text
    foreign_id = first_incident.json()["id"]

    with auth_client.app.state.session_factory() as db:
        organization = Organization(
            name="SLO Tenant Two",
            slug="slo-tenant-two",
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
            email="slo-second-owner@example.com",
            display_name="SLO Second Owner",
            password_hash=hash_password("SloSecondOwner_123!", auth_client.app.state.settings),
            role=UserRole.OWNER,
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
                "email": "slo-second-owner@example.com",
                "password": "SloSecondOwner_123!",
            },
        )
        assert login.status_code == 200, login.text
        assert second.get("/api/v1/operations/incidents").json() == []
        assert second.get(f"/api/v1/operations/incidents/{foreign_id}").status_code == 404
        assert second.get(f"/api/v1/operations/incidents/{foreign_id}/events").status_code == 404
        assert second.get("/api/v1/operations/overview").status_code == 200
        second_assessments = second.get("/api/v1/operations/slo-assessments")
        assert second_assessments.status_code == 200
        assert second_assessments.json() == []
    finally:
        second.close()


def test_worker_evaluates_due_policy_only_once_per_interval(
    auth_client: TestClient,
) -> None:
    """Проверить сценарий worker evaluates due policy only once per interval. Тест завершается
    ошибкой при нарушении зафиксированного инварианта.
    """
    from app.models import SLOAssessment
    from app.services.operations import evaluate_due_slo_policies

    _fresh_heartbeat(auth_client)
    assert auth_client.get("/api/v1/operations/slo-policy").status_code == 200
    with auth_client.app.state.session_factory() as db:
        assert evaluate_due_slo_policies(db, settings=auth_client.app.state.settings) == 1
        db.commit()
    with auth_client.app.state.session_factory() as db:
        assert evaluate_due_slo_policies(db, settings=auth_client.app.state.settings) == 0
        assert db.query(SLOAssessment).count() == 1


def test_maintenance_suppresses_new_incidents_but_allows_recovery_resolution(
    auth_client: TestClient,
) -> None:
    """Проверить сценарий maintenance suppresses new incidents but allows recovery resolution. Тест
    завершается ошибкой при нарушении зафиксированного инварианта.
    """
    _enable_gate(auth_client, suppress_incidents_during_maintenance=True)
    initial = auth_client.post(
        "/api/v1/operations/slo-assessments",
        headers=csrf_headers(auth_client),
    )
    assert initial.status_code == 201
    assert initial.json()["status"] == "blocked"
    assert any(
        item["dedup_key"] == "slo:worker_heartbeat" and item["status"] == "open"
        for item in auth_client.get("/api/v1/operations/incidents").json()
    )

    assert (
        auth_client.post(
            "/api/v1/changes/maintenance/start",
            headers=csrf_headers(auth_client),
            json={"reason": "Восстановление worker во время обслуживания"},
        ).status_code
        == 200
    )
    _fresh_heartbeat(auth_client)
    recovered = auth_client.post(
        "/api/v1/operations/slo-assessments",
        headers=csrf_headers(auth_client),
    )
    assert recovered.status_code == 201
    assert recovered.json()["status"] == "warning"
    worker_incidents = [
        item
        for item in auth_client.get("/api/v1/operations/incidents").json()
        if item["dedup_key"] == "slo:worker_heartbeat"
    ]
    assert len(worker_incidents) == 1
    assert worker_incidents[0]["status"] == "resolved"


def test_operational_metrics_are_exported(auth_client: TestClient) -> None:
    """Проверить сценарий operational metrics are exported. Тест завершается ошибкой при нарушении
    зафиксированного инварианта.
    """
    _fresh_heartbeat(auth_client)
    _enable_gate(auth_client)
    assert (
        auth_client.post(
            "/api/v1/operations/slo-assessments",
            headers=csrf_headers(auth_client),
        ).status_code
        == 201
    )
    assert (
        auth_client.post(
            "/api/v1/operations/incidents",
            headers=csrf_headers(auth_client),
            json={
                "title": "Metric incident",
                "summary": "Проверка экспорта операционных метрик",
                "severity": "critical",
            },
        ).status_code
        == 201
    )

    metrics = auth_client.get("/metrics")
    assert metrics.status_code == 200
    assert "teleflow_slo_latest_assessments" in metrics.text
    assert "teleflow_slo_error_budget_max_percent" in metrics.text
    assert "teleflow_slo_stale_assessments" in metrics.text
    assert "teleflow_open_incidents" in metrics.text


def test_safety_engine_rechecks_slo_before_telegram_gateway(
    auth_client: TestClient,
) -> None:
    """Проверить сценарий safety engine rechecks slo before telegram gateway. Тест завершается
    ошибкой при нарушении зафиксированного инварианта.
    """
    from app.models import SLOAssessment
    from app.services.delivery import DeliveryService

    _fresh_heartbeat(auth_client)
    _enable_gate(auth_client)
    assessed = auth_client.post(
        "/api/v1/operations/slo-assessments",
        headers=csrf_headers(auth_client),
    )
    assert assessed.status_code == 201

    connection = create_connection(auth_client, name="Pre-network SLO Bot")
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
    now = datetime.now(UTC) + timedelta(seconds=2)
    scheduler = SchedulerService(
        auth_client.app.state.session_factory, auth_client.app.state.settings
    )
    assert scheduler.tick(now=now) == 1

    with auth_client.app.state.session_factory() as db:
        assessment = db.get(SLOAssessment, assessed.json()["id"])
        assert assessment is not None
        assessment.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        db.commit()

    delivery = DeliveryService(
        auth_client.app.state.session_factory,
        auth_client.app.state.settings,
        auth_client.app.state.cipher,
        worker_id="slo-pre-network-test",
    )
    assert delivery.process_next(now=now + timedelta(seconds=1)) is True
    with auth_client.app.state.session_factory() as db:
        job = db.query(DeliveryJob).one()
        assert job.status == JobStatus.WAITING_REVIEW
        assert job.error_code == "SLO_GATE_BLOCKED"
        assert job.attempt_count == 0
        assert job.telegram_message_id is None


def test_scheduler_pauses_campaign_when_slo_becomes_stale(
    auth_client: TestClient,
) -> None:
    """Проверить сценарий scheduler pauses campaign when slo becomes stale. Тест завершается
    ошибкой при нарушении зафиксированного инварианта.
    """
    from app.models import SLOAssessment

    _fresh_heartbeat(auth_client)
    _enable_gate(auth_client)
    assessed = auth_client.post(
        "/api/v1/operations/slo-assessments",
        headers=csrf_headers(auth_client),
    )
    assert assessed.status_code == 201

    connection = create_connection(auth_client, name="Scheduler SLO Bot")
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

    with auth_client.app.state.session_factory() as db:
        assessment = db.get(SLOAssessment, assessed.json()["id"])
        assert assessment is not None
        assessment.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        db.commit()

    scheduler = SchedulerService(
        auth_client.app.state.session_factory, auth_client.app.state.settings
    )
    assert scheduler.tick(now=datetime.now(UTC) + timedelta(seconds=3)) == 0
    current = auth_client.get(f"/api/v1/campaigns/{campaign['id']}")
    assert current.status_code == 200
    assert current.json()["status"] == "paused"
    with auth_client.app.state.session_factory() as db:
        assert db.query(DeliveryJob).count() == 0


def test_worker_bootstraps_slo_policy_for_each_active_tenant(
    auth_client: TestClient,
) -> None:
    """Проверить сценарий worker bootstraps slo policy for each active tenant. Тест завершается
    ошибкой при нарушении зафиксированного инварианта.
    """
    from app.enums import OrganizationStatus
    from app.models import Organization, SLOAssessment, SLOPolicy
    from app.services.operations import evaluate_due_slo_policies

    _fresh_heartbeat(auth_client)
    with auth_client.app.state.session_factory() as db:
        db.add(
            Organization(
                name="Automatic SLO Tenant",
                slug="automatic-slo-tenant",
                status=OrganizationStatus.ACTIVE,
                timezone_name="Europe/Amsterdam",
                retention_days=30,
                ai_enabled=False,
                settings={},
            )
        )
        db.commit()

    with auth_client.app.state.session_factory() as db:
        assert evaluate_due_slo_policies(db, settings=auth_client.app.state.settings) == 2
        db.commit()
    with auth_client.app.state.session_factory() as db:
        assert db.query(SLOPolicy).count() == 2
        assert db.query(SLOAssessment).count() == 2
