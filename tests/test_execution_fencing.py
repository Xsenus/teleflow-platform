from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from app.config import Settings
from app.enums import (
    DeliveryAttemptStatus,
    ExecutionLeaseStatus,
    FailoverRequestStatus,
    JobStatus,
)
from app.models import (
    DeliveryAttempt,
    DeliveryJob,
    ExecutionLease,
    FailoverRequest,
    User,
)
from app.services.delivery import DeliveryService
from app.services.execution import claim_delivery_lease, upsert_site_heartbeat
from app.services.scheduler import SchedulerService
from app.services.telegram.errors import TelegramSlowModeWait
from app.services.telegram.fake import FakeTelegramGateway
from tests.conftest import csrf_headers
from tests.test_api_workflow import (
    approve_campaign,
    create_campaign,
    create_connection,
    create_destination,
    create_template,
)
from tests.test_delivery_safety import prepare_job


def _organization_id(client: TestClient) -> str:
    """Реализовать внутренний этап organization id step. Вспомогательная функция сохраняет
    детерминированность и тестируемость процесса.
    """
    with client.app.state.session_factory() as db:
        return db.query(User).filter(User.email == "owner@example.com").one().organization_id


def _create_user_client(
    owner: TestClient,
    *,
    email: str,
    password: str,
    role: str,
) -> TestClient:
    """Реализовать внутренний этап create user client step. Вспомогательная функция сохраняет
    детерминированность и тестируемость процесса.
    """
    created = owner.post(
        "/api/v1/users",
        headers=csrf_headers(owner),
        json={
            "email": email,
            "display_name": email.split("@", 1)[0],
            "password": password,
            "role": role,
        },
    )
    assert created.status_code == 201, created.text
    client = TestClient(owner.app)
    login = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert login.status_code == 200, login.text
    return client


def _register_standby(client: TestClient, *, now: datetime | None = None) -> None:
    """Реализовать внутренний этап register standby step. Вспомогательная функция сохраняет
    детерминированность и тестируемость процесса.
    """
    current = now or datetime.now(UTC)
    with client.app.state.session_factory() as db:
        upsert_site_heartbeat(
            db,
            organization_id=_organization_id(client),
            settings=client.app.state.settings.model_copy(
                update={
                    "execution_site_key": "standby",
                    "execution_site_name": "Резервная площадка",
                }
            ),
            worker_id="standby-worker",
            details={"role": "standby"},
            now=current,
        )
        db.commit()


def test_successful_delivery_records_fenced_attempt(auth_client: TestClient) -> None:
    """Проверить сценарий successful delivery records fenced attempt. Тест завершается ошибкой при
    нарушении зафиксированного инварианта.
    """
    _, now = prepare_job(auth_client)
    delivery = DeliveryService(
        auth_client.app.state.session_factory,
        auth_client.app.state.settings,
        auth_client.app.state.cipher,
        worker_id="primary-worker",
    )

    assert delivery.process_next(now=now + timedelta(seconds=1)) is True

    with auth_client.app.state.session_factory() as db:
        job = db.query(DeliveryJob).one()
        attempt = db.query(DeliveryAttempt).one()
        lease = db.query(ExecutionLease).one()
        assert job.status == JobStatus.SENT
        assert job.execution_site_key == "primary"
        assert job.execution_epoch == lease.epoch
        assert attempt.status == DeliveryAttemptStatus.SENT
        assert attempt.site_key == "primary"
        assert attempt.fence_epoch == lease.epoch
        assert attempt.telegram_message_id == job.telegram_message_id
        assert attempt.network_started_at is not None


class SlowOnceGateway(FakeTelegramGateway):
    calls = 0

    def send_message(self, **kwargs):  # type: ignore[no-untyped-def]
        """Выполнить операцию send message класса SlowOnceGateway. Аргументы интерпретируются в
        контексте модуля, результат возвращается вызывающему коду.
        """
        type(self).calls += 1
        if type(self).calls == 1:
            raise TelegramSlowModeWait("slow mode", retry_after=1)
        return super().send_message(**kwargs)


def test_slow_mode_keeps_attempt_numbers_monotonic(auth_client: TestClient, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Проверить сценарий slow mode keeps attempt numbers monotonic. Тест завершается ошибкой при
    нарушении зафиксированного инварианта.
    """
    SlowOnceGateway.calls = 0
    _, now = prepare_job(auth_client)
    monkeypatch.setattr(
        "app.services.delivery.build_gateway",
        lambda conn, settings, cipher: SlowOnceGateway(conn.kind, conn.id),
    )
    delivery = DeliveryService(
        auth_client.app.state.session_factory,
        auth_client.app.state.settings,
        auth_client.app.state.cipher,
        worker_id="slow-worker",
    )
    assert delivery.process_next(now=now + timedelta(seconds=1)) is True
    assert delivery.process_next(now=now + timedelta(seconds=3)) is True

    with auth_client.app.state.session_factory() as db:
        job = db.query(DeliveryJob).one()
        attempts = db.query(DeliveryAttempt).order_by(DeliveryAttempt.attempt_number).all()
        assert job.status == JobStatus.SENT
        assert job.attempt_count == 2
        assert [item.attempt_number for item in attempts] == [1, 2]
        assert attempts[0].status == DeliveryAttemptStatus.FAILED
        assert attempts[1].status == DeliveryAttemptStatus.SENT


def test_live_lease_fences_second_worker_and_expiry_advances_epoch(
    auth_client: TestClient,
) -> None:
    """Проверить сценарий live lease fences second worker and expiry advances epoch. Тест
    завершается ошибкой при нарушении зафиксированного инварианта.
    """
    organization_id = _organization_id(auth_client)
    settings = auth_client.app.state.settings
    now = datetime.now(UTC)

    with auth_client.app.state.session_factory() as db:
        first = claim_delivery_lease(
            db,
            organization_id=organization_id,
            settings=settings,
            worker_id="worker-one",
            now=now,
        )
        assert first.allowed is True
        first_epoch = first.epoch
        db.commit()

    with auth_client.app.state.session_factory() as db:
        blocked = claim_delivery_lease(
            db,
            organization_id=organization_id,
            settings=settings,
            worker_id="worker-two",
            now=now + timedelta(seconds=1),
        )
        assert blocked.allowed is False
        assert blocked.code == "EXECUTION_LEASE_HELD"
        db.rollback()

    with auth_client.app.state.session_factory() as db:
        takeover = claim_delivery_lease(
            db,
            organization_id=organization_id,
            settings=settings,
            worker_id="worker-two",
            now=now + timedelta(seconds=settings.execution_lease_ttl_seconds + 1),
        )
        assert takeover.allowed is True
        assert takeover.epoch == int(first_epoch or 0) + 1
        db.commit()


def test_stale_network_started_attempt_requires_manual_reconciliation(
    auth_client: TestClient,
) -> None:
    """Проверить сценарий stale network started attempt requires manual reconciliation. Тест
    завершается ошибкой при нарушении зафиксированного инварианта.
    """
    _, now = prepare_job(auth_client)
    stale_at = now - timedelta(minutes=30)
    with auth_client.app.state.session_factory() as db:
        job = db.query(DeliveryJob).one()
        job.status = JobStatus.PROCESSING
        job.locked_at = stale_at
        job.locked_by = "crashed-worker"
        job.attempt_count = 1
        db.add(
            DeliveryAttempt(
                organization_id=job.organization_id,
                job_id=job.id,
                attempt_number=1,
                worker_id="crashed-worker",
                site_key="primary",
                fence_epoch=7,
                status=DeliveryAttemptStatus.NETWORK_STARTED,
                prepared_at=stale_at,
                network_started_at=stale_at + timedelta(seconds=1),
                details={},
            )
        )
        db.commit()

    service = DeliveryService(
        auth_client.app.state.session_factory,
        auth_client.app.state.settings,
        auth_client.app.state.cipher,
        worker_id="recovery-worker",
    )
    assert service.recover_stale_jobs(now=now) == 1

    with auth_client.app.state.session_factory() as db:
        job = db.query(DeliveryJob).one()
        attempt = db.query(DeliveryAttempt).one()
        assert job.status == JobStatus.WAITING_REVIEW
        assert job.error_code == "WORKER_CRASH_DURING_SEND"
        assert attempt.status == DeliveryAttemptStatus.UNCERTAIN
        assert attempt.error_code == "WORKER_CRASH_DURING_SEND"


def test_worker_crash_attempt_can_be_reconciled_without_unsafe_retry(
    auth_client: TestClient,
) -> None:
    """Проверить сценарий worker crash attempt can be reconciled without unsafe retry. Тест
    завершается ошибкой при нарушении зафиксированного инварианта.
    """
    _, now = prepare_job(auth_client)
    stale_at = now - timedelta(minutes=30)
    with auth_client.app.state.session_factory() as db:
        job = db.query(DeliveryJob).one()
        job.status = JobStatus.PROCESSING
        job.locked_at = stale_at
        job.locked_by = "crashed-worker"
        job.attempt_count = 1
        db.add(
            DeliveryAttempt(
                organization_id=job.organization_id,
                job_id=job.id,
                attempt_number=1,
                worker_id="crashed-worker",
                site_key="primary",
                fence_epoch=11,
                status=DeliveryAttemptStatus.NETWORK_STARTED,
                prepared_at=stale_at,
                network_started_at=stale_at + timedelta(seconds=1),
                details={},
            )
        )
        db.commit()
        job_id = job.id

    service = DeliveryService(
        auth_client.app.state.session_factory,
        auth_client.app.state.settings,
        auth_client.app.state.cipher,
        worker_id="recovery-worker",
    )
    assert service.recover_stale_jobs(now=now) == 1
    resolved = auth_client.post(
        f"/api/v1/jobs/{job_id}/resolve",
        headers=csrf_headers(auth_client),
        json={
            "resolution": "confirmed_not_sent",
            "note": "Проверено вручную в целевой группе, сообщения нет",
        },
    )
    assert resolved.status_code == 200, resolved.text
    assert resolved.json()["status"] == JobStatus.RETRY.value

    with auth_client.app.state.session_factory() as db:
        attempt = db.query(DeliveryAttempt).one()
        assert attempt.status == DeliveryAttemptStatus.RECONCILED_NOT_SENT
        assert attempt.error_code == "MANUAL_CONFIRMED_NOT_SENT"


def test_stale_prepared_attempt_is_safe_to_retry(auth_client: TestClient) -> None:
    """Проверить сценарий stale prepared attempt is safe to retry. Тест завершается ошибкой при
    нарушении зафиксированного инварианта.
    """
    _, now = prepare_job(auth_client)
    stale_at = now - timedelta(minutes=30)
    with auth_client.app.state.session_factory() as db:
        job = db.query(DeliveryJob).one()
        job.status = JobStatus.PROCESSING
        job.locked_at = stale_at
        job.locked_by = "crashed-worker"
        job.attempt_count = 1
        db.add(
            DeliveryAttempt(
                organization_id=job.organization_id,
                job_id=job.id,
                attempt_number=1,
                worker_id="crashed-worker",
                site_key="primary",
                fence_epoch=3,
                status=DeliveryAttemptStatus.PREPARED,
                prepared_at=stale_at,
                details={},
            )
        )
        db.commit()

    service = DeliveryService(
        auth_client.app.state.session_factory,
        auth_client.app.state.settings,
        auth_client.app.state.cipher,
        worker_id="recovery-worker",
    )
    assert service.recover_stale_jobs(now=now) == 1

    with auth_client.app.state.session_factory() as db:
        job = db.query(DeliveryJob).one()
        attempt = db.query(DeliveryAttempt).one()
        assert job.status == JobStatus.RETRY
        assert job.error_code == "STALE_LEASE_RECOVERED"
        assert attempt.status == DeliveryAttemptStatus.ABANDONED
        assert attempt.error_code == "STALE_LEASE_BEFORE_NETWORK"


def test_scheduler_does_not_create_runs_on_standby_site(auth_client: TestClient) -> None:
    """Проверить сценарий scheduler does not create runs on standby site. Тест завершается ошибкой
    при нарушении зафиксированного инварианта.
    """
    connection = create_connection(auth_client)
    destination = create_destination(auth_client, connection["id"])
    template = create_template(auth_client)
    campaign = create_campaign(auth_client, connection["id"], template["id"], destination["id"])
    approve_campaign(auth_client, campaign["id"])
    run_now = auth_client.post(
        f"/api/v1/campaigns/{campaign['id']}/run-now",
        headers=csrf_headers(auth_client),
    )
    assert run_now.status_code == 200, run_now.text

    overview = auth_client.get("/api/v1/execution/overview")
    assert overview.status_code == 200, overview.text
    with auth_client.app.state.session_factory() as db:
        lease = db.query(ExecutionLease).one()
        lease.active_site_key = "standby"
        lease.status = ExecutionLeaseStatus.ACTIVE
        db.commit()

    scheduler = SchedulerService(
        auth_client.app.state.session_factory, auth_client.app.state.settings
    )
    assert scheduler.tick(now=datetime.now(UTC) + timedelta(seconds=2)) == 0
    with auth_client.app.state.session_factory() as db:
        assert db.query(DeliveryJob).count() == 0


def test_failover_requires_independent_approval_and_advances_epoch(
    auth_client: TestClient,
) -> None:
    """Проверить сценарий failover requires independent approval and advances epoch. Тест
    завершается ошибкой при нарушении зафиксированного инварианта.
    """
    _register_standby(auth_client)
    overview = auth_client.get("/api/v1/execution/overview")
    assert overview.status_code == 200
    source_epoch = overview.json()["lease"]["epoch"]

    created = auth_client.post(
        "/api/v1/execution/failovers",
        headers=csrf_headers(auth_client),
        json={
            "target_site_key": "standby",
            "reason": "Плановое переключение для проверки резервной площадки",
        },
    )
    assert created.status_code == 201, created.text
    request_id = created.json()["id"]

    own_approval = auth_client.post(
        f"/api/v1/execution/failovers/{request_id}/approve",
        headers=csrf_headers(auth_client),
        json={"confirmation": "ПЕРЕКЛЮЧИТЬ НА standby"},
    )
    assert own_approval.status_code == 409

    admin = _create_user_client(
        auth_client,
        email="failover-admin@example.com",
        password="FailoverAdmin_123!",
        role="admin",
    )
    try:
        approved = admin.post(
            f"/api/v1/execution/failovers/{request_id}/approve",
            headers=csrf_headers(admin),
            json={"confirmation": "ПЕРЕКЛЮЧИТЬ НА standby"},
        )
        assert approved.status_code == 200, approved.text
        assert approved.json()["status"] == FailoverRequestStatus.COMPLETED.value
        assert approved.json()["target_epoch"] == source_epoch + 1

        state = admin.get("/api/v1/execution/overview")
        assert state.status_code == 200
        assert state.json()["lease"]["active_site_key"] == "standby"
        assert state.json()["lease"]["epoch"] == source_epoch + 1
    finally:
        admin.close()


def test_failover_blockers_are_persisted(auth_client: TestClient) -> None:
    """Проверить сценарий failover blockers are persisted. Тест завершается ошибкой при нарушении
    зафиксированного инварианта.
    """
    _register_standby(auth_client)
    _, now = prepare_job(auth_client)
    with auth_client.app.state.session_factory() as db:
        job = db.query(DeliveryJob).one()
        job.status = JobStatus.PROCESSING
        job.locked_at = now
        job.locked_by = "primary-worker"
        db.commit()

    created = auth_client.post(
        "/api/v1/execution/failovers",
        headers=csrf_headers(auth_client),
        json={
            "target_site_key": "standby",
            "reason": "Проверка запрета переключения при активной доставке",
        },
    )
    assert created.status_code == 201, created.text
    request_id = created.json()["id"]
    admin = _create_user_client(
        auth_client,
        email="blocked-admin@example.com",
        password="BlockedAdmin_123!",
        role="admin",
    )
    try:
        blocked = admin.post(
            f"/api/v1/execution/failovers/{request_id}/approve",
            headers=csrf_headers(admin),
            json={"confirmation": "ПЕРЕКЛЮЧИТЬ НА standby"},
        )
        assert blocked.status_code == 409
        with auth_client.app.state.session_factory() as db:
            item = db.get(FailoverRequest, request_id)
            assert item is not None
            assert item.status == FailoverRequestStatus.REQUESTED
            assert any("активные Telegram-вызовы" in value for value in item.blockers)
    finally:
        admin.close()


def test_viewer_can_read_but_cannot_manage_failover(auth_client: TestClient) -> None:
    """Проверить сценарий viewer can read but cannot manage failover. Тест завершается ошибкой при
    нарушении зафиксированного инварианта.
    """
    _register_standby(auth_client)
    viewer = _create_user_client(
        auth_client,
        email="execution-viewer@example.com",
        password="ExecutionViewer_123!",
        role="viewer",
    )
    try:
        assert viewer.get("/api/v1/execution/overview").status_code == 200
        denied = viewer.post(
            "/api/v1/execution/failovers",
            headers=csrf_headers(viewer),
            json={
                "target_site_key": "standby",
                "reason": "Viewer не должен управлять failover операциями",
            },
        )
        assert denied.status_code == 403
    finally:
        viewer.close()


def test_production_requires_execution_fencing() -> None:
    """Проверить сценарий production requires execution fencing. Тест завершается ошибкой при
    нарушении зафиксированного инварианта.
    """
    base = {
        "_env_file": None,
        "environment": "production",
        "debug": False,
        "public_base_url": "https://teleflow.example",
        "cors_origins": "https://teleflow.example",
        "allowed_hosts": "teleflow.example",
        "master_key": "m" * 64,
        "jwt_secret": "j" * 64,
        "bootstrap_admin_password": "ProductionOwnerPassword_123!",
        "telegram_fake_mode": False,
        "database_url": "postgresql+psycopg://teleflow:secret@db/teleflow",
        "require_admin_totp": True,
        "cookies_secure": True,
        "enable_api_docs": False,
        "pilot_readiness_required": True,
        "pilot_stage_enforcement_required": True,
        "require_trusted_release_attestation": True,
        "artifact_signature_policy": "require_trusted",
        "recovery_require_encrypted_backup": True,
        "recovery_require_trusted_signature": True,
        "recovery_age_recipient": "age1qql3f0exampleonlyrecipientxxxxxxxxxxxxxxxxxxxxxxxxx",
        "require_release_transparency": True,
        "require_dependency_assessment": True,
        "dependency_require_vulnerability_scan": True,
        "dependency_require_trusted_report": True,
        "slo_gate_required": True,
        "capacity_assurance_required": True,
        "execution_fencing_required": False,
    }
    settings = Settings(**base)
    try:
        settings.validate_runtime_security()
    except RuntimeError as exc:
        assert "EXECUTION_FENCING_REQUIRED" in str(exc)
    else:  # pragma: no cover - explicit security assertion
        raise AssertionError("production must reject disabled execution fencing")
