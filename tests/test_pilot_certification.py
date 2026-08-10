from __future__ import annotations

import hashlib
import io
import json
import zipfile
from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.enums import (
    CampaignStatus,
    ConnectionStatus,
    JobStatus,
    NotificationStatus,
    PilotCanaryStatus,
    PilotStage,
    RunStatus,
    SafetySeverity,
)
from app.models import (
    Campaign,
    CampaignRun,
    DeliveryJob,
    Destination,
    Notification,
    Organization,
    PilotCanaryAttempt,
    TelegramConnection,
    WorkerHeartbeat,
)
from app.services.scheduler import SchedulerService
from tests.conftest import csrf_headers
from tests.test_api_workflow import (
    approve_campaign,
    create_connection,
    create_destination,
    create_template,
)
from tests.test_controlled_operations import (
    create_multi_destination_campaign,
    create_named_destination,
)

CANARY_CONFIRMATION = "ОТПРАВИТЬ СЛУЖЕБНОЕ СООБЩЕНИЕ"


def _set_real_pilot_prerequisites(
    client: TestClient,
    *,
    connection_id: str,
    destination_id: str,
) -> None:
    """Реализовать внутренний этап set real pilot prerequisites step. Вспомогательная функция
    сохраняет детерминированность и тестируемость процесса.
    """
    now = datetime.now(UTC)
    with client.app.state.session_factory() as db:
        connection = db.get(TelegramConnection, connection_id)
        destination = db.get(Destination, destination_id)
        assert connection is not None and destination is not None
        connection.last_checked_at = now
        destination.validated = True
        destination.validated_at = now
        destination.validation_expires_at = now + timedelta(days=7)
        db.add(
            WorkerHeartbeat(
                worker_id="pilot-certification-worker",
                hostname="test-host",
                pid=1234,
                version="2.5.0",
                last_seen_at=now,
                details={"test": True},
            )
        )
        db.add(
            PilotCanaryAttempt(
                organization_id=destination.organization_id,
                connection_id=connection.id,
                destination_id=destination.id,
                status=PilotCanaryStatus.SENT,
                marker="TF-CANARY-REALTEST",
                body_sha256="a" * 64,
                telegram_message_id="123456",
                is_fake=False,
                requested_by_id=connection.created_by_id,
                started_at=now,
                completed_at=now,
                created_at=now,
            )
        )
        db.commit()


def test_stage_assessment_blocks_fake_transport_and_fake_canary(
    auth_client: TestClient,
) -> None:
    """Проверить сценарий stage assessment blocks fake transport and fake canary. Тест завершается
    ошибкой при нарушении зафиксированного инварианта.
    """
    connection = create_connection(auth_client)
    destination = create_destination(auth_client, connection["id"])

    canary = auth_client.post(
        "/api/v1/pilot/canaries",
        headers=csrf_headers(auth_client),
        json={
            "destination_id": destination["id"],
            "confirmation": CANARY_CONFIRMATION,
        },
    )
    assert canary.status_code == 201, canary.text
    assert canary.json()["status"] == "sent"
    assert canary.json()["is_fake"] is True

    assessment = auth_client.post(
        "/api/v1/pilot/stage/assess",
        headers=csrf_headers(auth_client),
        json={"requested_stage": "service"},
    )
    assert assessment.status_code == 201, assessment.text
    body = assessment.json()
    assert body["status"] == "blocked"
    assert body["summary"]["blocked_checks"] == len(body["blockers"])
    assert body["summary"]["passed_checks"] + body["summary"]["warning_checks"] + body["summary"][
        "blocked_checks"
    ] == len(body["checks"])
    by_code = {item["code"]: item for item in body["checks"]}
    assert by_code["LIVE_TRANSPORT"]["status"] == "blocked"
    assert by_code["REAL_CANARY"]["status"] == "blocked"


def test_stage_can_advance_only_with_current_passed_assessment(
    auth_client: TestClient,
) -> None:
    """Проверить сценарий stage can advance only with current passed assessment. Тест завершается
    ошибкой при нарушении зафиксированного инварианта.
    """
    connection = create_connection(auth_client)
    destination = create_destination(auth_client, connection["id"])
    _set_real_pilot_prerequisites(
        auth_client,
        connection_id=connection["id"],
        destination_id=destination["id"],
    )
    auth_client.app.state.settings.telegram_fake_mode = False

    assessment = auth_client.post(
        "/api/v1/pilot/stage/assess",
        headers=csrf_headers(auth_client),
        json={"requested_stage": "service"},
    )
    assert assessment.status_code == 201, assessment.text
    assert assessment.json()["status"] == "passed"

    wrong = auth_client.post(
        "/api/v1/pilot/stage/advance",
        headers=csrf_headers(auth_client),
        json={
            "assessment_id": assessment.json()["id"],
            "confirmation": "ПЕРЕЙТИ",
            "note": "Проверили служебную группу",
        },
    )
    assert wrong.status_code == 422

    advanced = auth_client.post(
        "/api/v1/pilot/stage/advance",
        headers=csrf_headers(auth_client),
        json={
            "assessment_id": assessment.json()["id"],
            "confirmation": "ПЕРЕЙТИ НА ЭТАП 1",
            "note": "Служебная группа и canary проверены вручную",
        },
    )
    assert advanced.status_code == 200, advanced.text
    assert advanced.json()["current_stage"] == "service"
    assert advanced.json()["current_limit"] == 1

    replay = auth_client.post(
        "/api/v1/pilot/stage/advance",
        headers=csrf_headers(auth_client),
        json={
            "assessment_id": assessment.json()["id"],
            "confirmation": "ПЕРЕЙТИ НА ЭТАП 1",
            "note": "Повторное решение не должно сработать",
        },
    )
    assert replay.status_code == 409


def test_stage_assessment_fingerprint_invalidates_on_new_critical_event_or_stale_worker(
    auth_client: TestClient,
) -> None:
    """Проверить сценарий stage assessment fingerprint invalidates on new critical event or stale
    worker. Тест завершается ошибкой при нарушении зафиксированного инварианта.
    """
    connection = create_connection(auth_client)
    destination = create_destination(auth_client, connection["id"])
    _set_real_pilot_prerequisites(
        auth_client,
        connection_id=connection["id"],
        destination_id=destination["id"],
    )
    auth_client.app.state.settings.telegram_fake_mode = False

    first = auth_client.post(
        "/api/v1/pilot/stage/assess",
        headers=csrf_headers(auth_client),
        json={"requested_stage": "service"},
    )
    assert first.status_code == 201, first.text
    assert first.json()["status"] == "passed"

    with auth_client.app.state.session_factory() as db:
        organization = db.scalar(select(Organization))
        assert organization is not None
        notification = Notification(
            organization_id=organization.id,
            event_type="pilot.test_new_critical_event",
            severity=SafetySeverity.CRITICAL,
            status=NotificationStatus.UNREAD,
            title="Новая критическая проверка",
            message="Оценка этапа должна устареть",
            details={},
            last_occurred_at=datetime.now(UTC),
            created_at=datetime.now(UTC),
        )
        db.add(notification)
        db.commit()

    rejected_for_notification = auth_client.post(
        "/api/v1/pilot/stage/advance",
        headers=csrf_headers(auth_client),
        json={
            "assessment_id": first.json()["id"],
            "confirmation": "ПЕРЕЙТИ НА ЭТАП 1",
            "note": "Состояние изменилось после оценки",
        },
    )
    assert rejected_for_notification.status_code == 409

    with auth_client.app.state.session_factory() as db:
        notification = db.scalar(select(Notification))
        assert notification is not None
        notification.status = NotificationStatus.ACKNOWLEDGED
        notification.acknowledged_at = datetime.now(UTC)
        db.commit()

    second = auth_client.post(
        "/api/v1/pilot/stage/assess",
        headers=csrf_headers(auth_client),
        json={"requested_stage": "service"},
    )
    assert second.status_code == 201, second.text
    assert second.json()["status"] == "passed"

    with auth_client.app.state.session_factory() as db:
        heartbeat = db.scalar(select(WorkerHeartbeat))
        assert heartbeat is not None
        heartbeat.last_seen_at = datetime.now(UTC) - timedelta(
            seconds=auth_client.app.state.settings.worker_readiness_max_age_seconds + 10
        )
        db.commit()

    rejected_for_worker = auth_client.post(
        "/api/v1/pilot/stage/advance",
        headers=csrf_headers(auth_client),
        json={
            "assessment_id": second.json()["id"],
            "confirmation": "ПЕРЕЙТИ НА ЭТАП 1",
            "note": "Worker перестал отвечать после оценки",
        },
    )
    assert rejected_for_worker.status_code == 409


def test_stage_limit_is_visible_in_preview_and_blocks_manual_run(
    auth_client: TestClient,
) -> None:
    """Проверить сценарий stage limit is visible in preview and blocks manual run. Тест завершается
    ошибкой при нарушении зафиксированного инварианта.
    """
    connection = create_connection(auth_client)
    destinations = [
        create_named_destination(auth_client, connection["id"], suffix)
        for suffix in ("stage-one", "stage-two")
    ]
    template = create_template(auth_client)
    campaign = create_multi_destination_campaign(
        auth_client,
        connection["id"],
        template["id"],
        [item["id"] for item in destinations],
        rollout_mode="standard",
        checkpoint=False,
    )
    with auth_client.app.state.session_factory() as db:
        organization = db.scalar(select(Organization))
        assert organization is not None
        organization.pilot_stage = PilotStage.SERVICE
        db.commit()
    auth_client.app.state.settings.pilot_stage_enforcement_required = True

    preview = auth_client.get(f"/api/v1/campaigns/{campaign['id']}/preview")
    assert preview.status_code == 200, preview.text
    assert preview.json()["valid"] is False
    assert any("не более 1" in item for item in preview.json()["blockers"])

    approval = auth_client.post(
        f"/api/v1/campaigns/{campaign['id']}/approve",
        headers=csrf_headers(auth_client),
    )
    assert approval.status_code == 422


def test_lowering_stage_pauses_oversized_campaign_and_pending_jobs(
    auth_client: TestClient,
) -> None:
    """Проверить сценарий lowering stage pauses oversized campaign and pending jobs. Тест
    завершается ошибкой при нарушении зафиксированного инварианта.
    """
    connection = create_connection(auth_client)
    destinations = [
        create_named_destination(auth_client, connection["id"], suffix)
        for suffix in ("lower-one", "lower-two")
    ]
    template = create_template(auth_client)
    campaign = create_multi_destination_campaign(
        auth_client,
        connection["id"],
        template["id"],
        [item["id"] for item in destinations],
        rollout_mode="standard",
        checkpoint=False,
    )
    auth_client.app.state.settings.pilot_stage_enforcement_required = True
    with auth_client.app.state.session_factory() as db:
        organization = db.scalar(select(Organization))
        assert organization is not None
        organization.pilot_stage = PilotStage.HUNDRED
        db.commit()

    approve_campaign(auth_client, campaign["id"])
    started = auth_client.post(
        f"/api/v1/campaigns/{campaign['id']}/run-now",
        headers=csrf_headers(auth_client),
    )
    assert started.status_code == 200, started.text
    scheduler = SchedulerService(
        auth_client.app.state.session_factory,
        auth_client.app.state.settings,
    )
    assert scheduler.tick(now=datetime.now(UTC) + timedelta(seconds=2)) == 1

    lowered = auth_client.post(
        "/api/v1/pilot/stage/lower",
        headers=csrf_headers(auth_client),
        json={
            "target_stage": "service",
            "confirmation": "СНИЗИТЬ ЭТАП ДО 1",
            "reason": "Возвращаемся к одной служебной группе после проверки",
        },
    )
    assert lowered.status_code == 200, lowered.text
    assert lowered.json()["current_stage"] == "service"

    with auth_client.app.state.session_factory() as db:
        stored = db.get(Campaign, campaign["id"])
        assert stored is not None
        assert stored.status == CampaignStatus.PAUSED
        jobs = list(
            db.scalars(select(DeliveryJob).where(DeliveryJob.campaign_id == campaign["id"])).all()
        )
        assert len(jobs) == 2
        assert {job.status for job in jobs} == {JobStatus.WAITING_REVIEW}
        assert {job.error_code for job in jobs} == {"PILOT_STAGE_LIMIT_REDUCED"}


def test_canary_has_fixed_body_and_cooldown_without_second_gateway_call(
    auth_client: TestClient,
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    """Проверить сценарий canary has fixed body and cooldown without second gateway call. Тест
    завершается ошибкой при нарушении зафиксированного инварианта.
    """
    connection = create_connection(auth_client)
    destination = create_destination(auth_client, connection["id"])
    seen_bodies: list[str] = []

    class CountingGateway:
        def send_message(self, **kwargs):  # type: ignore[no-untyped-def]
            """Выполнить операцию send message класса CountingGateway. Аргументы интерпретируются в
            контексте модуля, результат возвращается вызывающему коду.
            """
            seen_bodies.append(kwargs["body"])
            return type("Result", (), {"message_id": "canary-1"})()

    monkeypatch.setattr(
        "app.services.pilot_canary.build_gateway",
        lambda *_args, **_kwargs: CountingGateway(),
    )
    first = auth_client.post(
        "/api/v1/pilot/canaries",
        headers=csrf_headers(auth_client),
        json={
            "destination_id": destination["id"],
            "confirmation": CANARY_CONFIRMATION,
        },
    )
    assert first.status_code == 201, first.text
    assert first.json()["status"] == "sent"
    assert len(seen_bodies) == 1
    assert "служебная проверка публикации" in seen_bodies[0]
    assert first.json()["marker"] in seen_bodies[0]
    assert first.json()["body_sha256"] == hashlib.sha256(seen_bodies[0].encode()).hexdigest()

    second = auth_client.post(
        "/api/v1/pilot/canaries",
        headers=csrf_headers(auth_client),
        json={
            "destination_id": destination["id"],
            "confirmation": CANARY_CONFIRMATION,
        },
    )
    assert second.status_code == 201, second.text
    assert second.json()["status"] == "blocked"
    assert second.json()["error_code"] == "CANARY_COOLDOWN"
    assert len(seen_bodies) == 1


def test_canary_respects_organization_emergency_stop_before_gateway(
    auth_client: TestClient,
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    """Проверить сценарий canary respects organization emergency stop before gateway. Тест
    завершается ошибкой при нарушении зафиксированного инварианта.
    """
    connection = create_connection(auth_client)
    destination = create_destination(auth_client, connection["id"])
    with auth_client.app.state.session_factory() as db:
        organization = db.scalar(select(Organization))
        assert organization is not None
        organization.publishing_paused = True
        organization.publishing_pause_reason = "Аварийная остановка для проверки"
        db.commit()

    monkeypatch.setattr(
        "app.services.pilot_canary.build_gateway",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("Gateway must not be created while publishing is paused")
        ),
    )
    response = auth_client.post(
        "/api/v1/pilot/canaries",
        headers=csrf_headers(auth_client),
        json={
            "destination_id": destination["id"],
            "confirmation": CANARY_CONFIRMATION,
        },
    )
    assert response.status_code == 201, response.text
    assert response.json()["status"] == "blocked"
    assert response.json()["error_code"] == "ORGANIZATION_PUBLISHING_PAUSED"


def test_canary_respects_destination_window_before_gateway(
    auth_client: TestClient,
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    """Проверить сценарий canary respects destination window before gateway. Тест завершается
    ошибкой при нарушении зафиксированного инварианта.
    """
    connection = create_connection(auth_client)
    destination = create_destination(auth_client, connection["id"])
    with auth_client.app.state.session_factory() as db:
        stored = db.get(Destination, destination["id"])
        assert stored is not None
        now = datetime.now(UTC)
        stored.timezone_name = "UTC"
        stored.allowed_weekdays = [now.weekday()]
        # A one-minute window in the past guarantees a future defer time.
        past = now - timedelta(minutes=2)
        stored.allowed_start_time = past.time().replace(second=0, microsecond=0)
        stored.allowed_end_time = (
            (past + timedelta(minutes=1)).time().replace(second=0, microsecond=0)
        )
        db.commit()

    monkeypatch.setattr(
        "app.services.pilot_canary.build_gateway",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("Gateway must not be created outside destination window")
        ),
    )
    response = auth_client.post(
        "/api/v1/pilot/canaries",
        headers=csrf_headers(auth_client),
        json={
            "destination_id": destination["id"],
            "confirmation": CANARY_CONFIRMATION,
        },
    )
    assert response.status_code == 201, response.text
    assert response.json()["status"] == "blocked"
    assert response.json()["error_code"] == "DESTINATION_TIME_WINDOW"


def test_stage_ready_destinations_require_their_own_healthy_connection(
    auth_client: TestClient,
) -> None:
    """Проверить сценарий stage ready destinations require their own healthy connection. Тест
    завершается ошибкой при нарушении зафиксированного инварианта.
    """
    healthy_connection = create_connection(auth_client)
    healthy_destination = create_destination(auth_client, healthy_connection["id"])
    paused_connection = create_connection(auth_client, name="Paused Pilot Bot")
    paused_destinations = [
        create_named_destination(auth_client, paused_connection["id"], f"paused-{index}")
        for index in range(4)
    ]
    _set_real_pilot_prerequisites(
        auth_client,
        connection_id=healthy_connection["id"],
        destination_id=healthy_destination["id"],
    )

    template = create_template(auth_client)
    campaign = create_multi_destination_campaign(
        auth_client,
        healthy_connection["id"],
        template["id"],
        [healthy_destination["id"]],
        rollout_mode="standard",
        checkpoint=False,
    )
    now = datetime.now(UTC)
    with auth_client.app.state.session_factory() as db:
        organization = db.scalar(select(Organization))
        paused = db.get(TelegramConnection, paused_connection["id"])
        assert organization is not None and paused is not None
        organization.pilot_stage = PilotStage.SERVICE
        paused.status = ConnectionStatus.PAUSED
        paused.last_checked_at = now
        for item in paused_destinations:
            destination = db.get(Destination, item["id"])
            assert destination is not None
            destination.validated = True
            destination.validated_at = now
            destination.validation_expires_at = now + timedelta(days=7)

        run = CampaignRun(
            organization_id=organization.id,
            campaign_id=campaign["id"],
            scheduled_for=now,
            status=RunStatus.COMPLETED,
            total_jobs=1,
            sent_jobs=1,
            failed_jobs=0,
            finished_at=now,
        )
        db.add(run)
        db.flush()
        db.add(
            DeliveryJob(
                organization_id=organization.id,
                run_id=run.id,
                campaign_id=campaign["id"],
                connection_id=healthy_connection["id"],
                destination_id=healthy_destination["id"],
                template_id=template["id"],
                body_snapshot="real pilot evidence",
                parse_mode="plain",
                link_preview=False,
                due_at=now,
                status=JobStatus.SENT,
                idempotency_key="real-health-evidence",
                telegram_message_id="real-health-message",
                finished_at=now,
            )
        )
        db.commit()

    auth_client.app.state.settings.telegram_fake_mode = False
    assessment = auth_client.post(
        "/api/v1/pilot/stage/assess",
        headers=csrf_headers(auth_client),
        json={"requested_stage": "five"},
    )
    assert assessment.status_code == 201, assessment.text
    ready_check = next(
        item for item in assessment.json()["checks"] if item["code"] == "READY_DESTINATIONS"
    )
    assert ready_check["status"] == "blocked"
    assert ready_check["details"] == {"ready": 1, "required": 5}


def test_fake_delivery_does_not_count_as_real_stage_evidence(
    auth_client: TestClient,
) -> None:
    """Проверить сценарий fake delivery does not count as real stage evidence. Тест завершается
    ошибкой при нарушении зафиксированного инварианта.
    """
    connection = create_connection(auth_client)
    destinations = [
        create_named_destination(auth_client, connection["id"], f"evidence-{index}")
        for index in range(5)
    ]
    _set_real_pilot_prerequisites(
        auth_client,
        connection_id=connection["id"],
        destination_id=destinations[0]["id"],
    )
    now = datetime.now(UTC)
    template = create_template(auth_client)
    campaign = create_multi_destination_campaign(
        auth_client,
        connection["id"],
        template["id"],
        [item["id"] for item in destinations],
        rollout_mode="standard",
        checkpoint=False,
    )
    with auth_client.app.state.session_factory() as db:
        organization = db.scalar(select(Organization))
        assert organization is not None
        organization.pilot_stage = PilotStage.SERVICE
        run = CampaignRun(
            organization_id=organization.id,
            campaign_id=campaign["id"],
            scheduled_for=now,
            status=RunStatus.COMPLETED,
            total_jobs=5,
            sent_jobs=5,
            failed_jobs=0,
            finished_at=now,
        )
        db.add(run)
        db.flush()
        for destination in destinations:
            db.add(
                DeliveryJob(
                    organization_id=organization.id,
                    run_id=run.id,
                    campaign_id=campaign["id"],
                    connection_id=connection["id"],
                    destination_id=destination["id"],
                    template_id=template["id"],
                    body_snapshot="fake evidence",
                    parse_mode="plain",
                    link_preview=False,
                    due_at=now,
                    status=JobStatus.SENT,
                    idempotency_key=f"fake-evidence-{destination['id']}",
                    telegram_message_id=f"fake-{destination['id']}",
                    finished_at=now,
                )
            )
        db.commit()
    auth_client.app.state.settings.telegram_fake_mode = False

    assessment = auth_client.post(
        "/api/v1/pilot/stage/assess",
        headers=csrf_headers(auth_client),
        json={"requested_stage": "five"},
    )
    assert assessment.status_code == 201, assessment.text
    body = assessment.json()
    assert body["status"] == "blocked"
    evidence = next(item for item in body["checks"] if item["code"] == "RUN_EVIDENCE")
    assert evidence["status"] == "blocked"
    assert "реального" in evidence["message"]


def test_support_bundle_is_redacted_manifested_and_deletable(
    auth_client: TestClient,
) -> None:
    """Проверить сценарий support bundle is redacted manifested and deletable. Тест завершается
    ошибкой при нарушении зафиксированного инварианта.
    """
    secret_token = "1234567890:very-sensitive-token-value"
    response = auth_client.post(
        "/api/v1/connections/bot",
        headers=csrf_headers(auth_client),
        json={"name": "Sensitive Connection Name", "bot_token": secret_token},
    )
    assert response.status_code == 201, response.text
    template_body = "PRIVATE-MESSAGE-BODY phone +31612345678 email private@example.com"
    created_template = auth_client.post(
        "/api/v1/templates",
        headers=csrf_headers(auth_client),
        json={"name": "Sensitive Template Name", "body": template_body},
    )
    assert created_template.status_code == 201, created_template.text

    generated = auth_client.post(
        "/api/v1/pilot/support-bundles",
        headers=csrf_headers(auth_client),
        json={"reason": "Передаём обезличенную диагностику специалисту поддержки"},
    )
    assert generated.status_code == 201, generated.text
    bundle = generated.json()
    assert bundle["status"] == "ready"
    assert bundle["size_bytes"] > 0

    downloaded = auth_client.get(f"/api/v1/pilot/support-bundles/{bundle['id']}/download")
    assert downloaded.status_code == 200, downloaded.text
    assert downloaded.headers["cache-control"] == "no-store"
    assert hashlib.sha256(downloaded.content).hexdigest() == bundle["sha256"]

    with zipfile.ZipFile(io.BytesIO(downloaded.content)) as archive:
        names = set(archive.namelist())
        assert "MANIFEST.sha256" in names
        manifest = archive.read("MANIFEST.sha256").decode("ascii")
        for line in manifest.strip().splitlines():
            digest, name = line.split("  ", 1)
            assert hashlib.sha256(archive.read(name)).hexdigest() == digest
        combined = b"\n".join(
            archive.read(name) for name in names if name.endswith((".json", ".txt"))
        )
        text = combined.decode("utf-8")
        assert secret_token not in text
        assert template_body not in text
        assert "+31612345678" not in text
        assert "private@example.com" not in text
        assert "Sensitive Connection Name" not in text
        runtime = json.loads(archive.read("runtime.json"))
        assert runtime["version"] == "2.5.0"

    deleted = auth_client.delete(
        f"/api/v1/pilot/support-bundles/{bundle['id']}",
        headers=csrf_headers(auth_client),
    )
    assert deleted.status_code == 200, deleted.text
    gone = auth_client.get(f"/api/v1/pilot/support-bundles/{bundle['id']}/download")
    assert gone.status_code == 410


def test_pilot_mutations_and_support_bundle_are_admin_only(
    auth_client: TestClient,
) -> None:
    """Проверить сценарий pilot mutations and support bundle are admin only. Тест завершается
    ошибкой при нарушении зафиксированного инварианта.
    """
    created = auth_client.post(
        "/api/v1/users",
        headers=csrf_headers(auth_client),
        json={
            "email": "pilot-operator@example.com",
            "display_name": "Pilot Operator",
            "password": "PilotOperatorPassword_123!",
            "role": "operator",
        },
    )
    assert created.status_code == 201, created.text

    operator = TestClient(auth_client.app)
    try:
        login = operator.post(
            "/api/v1/auth/login",
            json={
                "email": "pilot-operator@example.com",
                "password": "PilotOperatorPassword_123!",
            },
        )
        assert login.status_code == 200, login.text
        assert operator.get("/api/v1/pilot/stage").status_code == 200
        assert operator.get("/api/v1/pilot/canaries").status_code == 200
        assert operator.get("/api/v1/pilot/stage/assessments").status_code == 200

        denied_assessment = operator.post(
            "/api/v1/pilot/stage/assess",
            headers=csrf_headers(operator),
            json={"requested_stage": "service"},
        )
        assert denied_assessment.status_code == 403
        denied_canary = operator.post(
            "/api/v1/pilot/canaries",
            headers=csrf_headers(operator),
            json={
                "destination_id": "00000000-0000-0000-0000-000000000000",
                "confirmation": CANARY_CONFIRMATION,
            },
        )
        assert denied_canary.status_code == 403
        assert operator.get("/api/v1/pilot/support-bundles").status_code == 403
        denied_bundle = operator.post(
            "/api/v1/pilot/support-bundles",
            headers=csrf_headers(operator),
            json={"reason": "Недостаточно прав для диагностического архива"},
        )
        assert denied_bundle.status_code == 403
    finally:
        operator.close()
