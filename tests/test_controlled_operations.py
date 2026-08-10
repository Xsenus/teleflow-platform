from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.enums import (
    DeliveryReviewResolution,
    JobStatus,
    RunStatus,
)
from app.models import Campaign, CampaignRun, DeliveryJob, Destination
from app.services.delivery import DeliveryService
from app.services.scheduler import SchedulerService
from app.services.telegram.errors import (
    TelegramDeliveryUncertain,
    TelegramWriteForbidden,
)
from app.services.telegram.fake import FakeTelegramGateway
from tests.conftest import csrf_headers
from tests.test_api_workflow import (
    approve_campaign,
    create_campaign,
    create_connection,
    create_destination,
    create_template,
)


class UncertainGateway(FakeTelegramGateway):
    def send_message(self, **kwargs):  # type: ignore[no-untyped-def]
        """Выполнить операцию send message класса UncertainGateway. Аргументы интерпретируются в
        контексте модуля, результат возвращается вызывающему коду.
        """
        raise TelegramDeliveryUncertain("Проверьте целевой чат вручную")


class ForbiddenGateway(FakeTelegramGateway):
    def send_message(self, **kwargs):  # type: ignore[no-untyped-def]
        """Выполнить операцию send message класса ForbiddenGateway. Аргументы интерпретируются в
        контексте модуля, результат возвращается вызывающему коду.
        """
        raise TelegramWriteForbidden("Права отправки отозваны")


def create_named_destination(
    client: TestClient,
    connection_id: str,
    suffix: str,
) -> dict:
    """Создать named destination. Перед сохранением или возвратом нового значения проверяются
    связанные инварианты.
    """
    response = client.post(
        "/api/v1/destinations",
        headers=csrf_headers(client),
        json={
            "connection_id": connection_id,
            "username": f"allowed_jobs_{suffix}",
            "title": f"Разрешённая группа {suffix}",
            "kind": "supergroup",
            "permission_confirmed": True,
            "permission_note": f"Разрешение администратора группы {suffix}",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def create_multi_destination_campaign(
    client: TestClient,
    connection_id: str,
    template_id: str,
    destination_ids: list[str],
    *,
    rollout_mode: str = "staged",
    batch_size: int = 1,
    checkpoint: bool = True,
    duplicate_guard_minutes: int = 0,
) -> dict:
    """Создать multi destination campaign. Перед сохранением или возвратом нового значения
    проверяются связанные инварианты.
    """
    response = client.post(
        "/api/v1/campaigns",
        headers=csrf_headers(client),
        json={
            "name": "Контролируемый пилот",
            "connection_id": connection_id,
            "template_id": template_id,
            "destination_ids": destination_ids,
            "schedule_type": "once",
            "schedule_at": datetime.now(UTC).isoformat(),
            "timezone_name": "Europe/Amsterdam",
            "weekdays": [],
            "spacing_seconds": 1,
            "rollout_mode": rollout_mode,
            "rollout_batch_size": batch_size,
            "rollout_pause_seconds": 0,
            "rollout_require_checkpoint": checkpoint,
            "rollout_failure_threshold_percent": 20,
            "duplicate_guard_minutes": duplicate_guard_minutes,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def prepare_uncertain_job(
    client: TestClient,
    monkeypatch,
) -> tuple[dict, dict, datetime]:  # type: ignore[no-untyped-def]
    """Выполнить операцию prepare uncertain job. Аргументы интерпретируются в контексте модуля,
    результат возвращается вызывающему коду.
    """
    connection = create_connection(client)
    destination = create_destination(client, connection["id"])
    template = create_template(client)
    campaign = create_campaign(client, connection["id"], template["id"], destination["id"])
    approve_campaign(client, campaign["id"])
    run = client.post(
        f"/api/v1/campaigns/{campaign['id']}/run-now",
        headers=csrf_headers(client),
    )
    assert run.status_code == 200, run.text
    now = datetime.now(UTC) + timedelta(seconds=2)
    scheduler = SchedulerService(client.app.state.session_factory, client.app.state.settings)
    assert scheduler.tick(now=now) == 1
    monkeypatch.setattr(
        "app.services.delivery.build_gateway",
        lambda conn, settings, cipher: UncertainGateway(conn.kind, conn.id),
    )
    delivery = DeliveryService(
        client.app.state.session_factory,
        client.app.state.settings,
        client.app.state.cipher,
        worker_id="uncertain-test-worker",
    )
    assert delivery.process_next(now=now + timedelta(seconds=1))
    return connection, campaign, now


def test_preflight_persists_blocked_expired_permission(auth_client: TestClient) -> None:
    """Проверить сценарий preflight persists blocked expired permission. Тест завершается ошибкой
    при нарушении зафиксированного инварианта.
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

    with auth_client.app.state.session_factory() as db:
        stored = db.get(Destination, destination["id"])
        assert stored is not None
        stored.permission_expires_at = datetime.now(UTC) - timedelta(minutes=1)
        db.commit()

    blocked = auth_client.post(
        f"/api/v1/campaigns/{campaign['id']}/preflight",
        headers=csrf_headers(auth_client),
    )
    assert blocked.status_code == 422, blocked.text
    assert any("истёк" in item for item in blocked.json()["detail"]["blockers"])

    latest = auth_client.get(f"/api/v1/campaigns/{campaign['id']}/preflight/latest")
    assert latest.status_code == 200, latest.text
    body = latest.json()
    assert body["status"] == "blocked"
    assert body["summary"]["blocked_destinations"] == 1


def test_staged_rollout_holds_batches_requires_checkpoint_and_can_abort(
    auth_client: TestClient,
) -> None:
    """Проверить сценарий staged rollout holds batches requires checkpoint and can abort. Тест
    завершается ошибкой при нарушении зафиксированного инварианта.
    """
    connection = create_connection(auth_client)
    destinations = [
        create_named_destination(auth_client, connection["id"], suffix)
        for suffix in ("one", "two", "three")
    ]
    template = create_template(auth_client)
    campaign = create_multi_destination_campaign(
        auth_client,
        connection["id"],
        template["id"],
        [item["id"] for item in destinations],
    )
    approved = approve_campaign(auth_client, campaign["id"])
    assert approved["rollout_mode"] == "staged"
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
        run = db.scalar(select(CampaignRun).where(CampaignRun.campaign_id == campaign["id"]))
        assert run is not None
        assert run.total_batches == 3
        jobs = list(
            db.scalars(
                select(DeliveryJob)
                .where(DeliveryJob.run_id == run.id)
                .order_by(DeliveryJob.batch_number)
            ).all()
        )
        assert [job.status for job in jobs] == [
            JobStatus.PENDING,
            JobStatus.HELD,
            JobStatus.HELD,
        ]
        run_id = run.id

    delivery = DeliveryService(
        auth_client.app.state.session_factory,
        auth_client.app.state.settings,
        auth_client.app.state.cipher,
        worker_id="staged-worker",
    )
    assert delivery.process_next(now=now + timedelta(seconds=1))

    with auth_client.app.state.session_factory() as db:
        run = db.get(CampaignRun, run_id)
        assert run is not None
        assert run.status == RunStatus.AWAITING_CHECKPOINT
        assert run.active_batch == 1

    continued = auth_client.post(
        f"/api/v1/campaigns/{campaign['id']}/runs/{run_id}/continue",
        headers=csrf_headers(auth_client),
        json={"note": "Первый пакет проверен, жалоб и ошибок нет"},
    )
    assert continued.status_code == 200, continued.text
    assert continued.json()["active_batch"] == 2
    assert continued.json()["status"] == "running"

    with auth_client.app.state.session_factory() as db:
        jobs = list(
            db.scalars(
                select(DeliveryJob)
                .where(DeliveryJob.run_id == run_id)
                .order_by(DeliveryJob.batch_number)
            ).all()
        )
        assert [job.status for job in jobs] == [
            JobStatus.SENT,
            JobStatus.PENDING,
            JobStatus.HELD,
        ]

    aborted = auth_client.post(
        f"/api/v1/campaigns/{campaign['id']}/runs/{run_id}/abort",
        headers=csrf_headers(auth_client),
        json={"reason": "Останавливаем пилот после ручной проверки второго этапа"},
    )
    assert aborted.status_code == 200, aborted.text
    assert aborted.json()["status"] == "cancelled"
    with auth_client.app.state.session_factory() as db:
        stored_campaign = db.get(Campaign, campaign["id"])
        assert stored_campaign is not None
        assert stored_campaign.status.value == "paused"
        remaining = list(
            db.scalars(
                select(DeliveryJob).where(
                    DeliveryJob.run_id == run_id,
                    DeliveryJob.status == JobStatus.CANCELLED,
                )
            ).all()
        )
        assert len(remaining) == 2


def test_delivery_time_duplicate_guard_stops_second_approved_job(
    auth_client: TestClient,
) -> None:
    """Проверить сценарий delivery time duplicate guard stops second approved job. Тест завершается
    ошибкой при нарушении зафиксированного инварианта.
    """
    connection = create_connection(auth_client)
    destination = create_destination(auth_client, connection["id"])
    template = create_template(auth_client)
    first = create_campaign(auth_client, connection["id"], template["id"], destination["id"])
    second = create_campaign(auth_client, connection["id"], template["id"], destination["id"])
    approve_campaign(auth_client, first["id"])
    approve_campaign(auth_client, second["id"])
    for campaign in (first, second):
        response = auth_client.post(
            f"/api/v1/campaigns/{campaign['id']}/run-now",
            headers=csrf_headers(auth_client),
        )
        assert response.status_code == 200, response.text

    now = datetime.now(UTC) + timedelta(seconds=2)
    scheduler = SchedulerService(
        auth_client.app.state.session_factory, auth_client.app.state.settings
    )
    assert scheduler.tick(now=now) == 2
    delivery = DeliveryService(
        auth_client.app.state.session_factory,
        auth_client.app.state.settings,
        auth_client.app.state.cipher,
        worker_id="duplicate-worker",
    )
    assert delivery.process_next(now=now + timedelta(seconds=1))
    assert delivery.process_next(now=now + timedelta(seconds=2))

    with auth_client.app.state.session_factory() as db:
        jobs = list(db.scalars(select(DeliveryJob)).all())
        assert sorted(job.status.value for job in jobs) == ["sent", "skipped"]
        duplicate = next(job for job in jobs if job.status == JobStatus.SKIPPED)
        assert duplicate.error_code == "DUPLICATE_CONTENT"
        assert duplicate.content_fingerprint


def test_uncertain_delivery_can_be_confirmed_sent_and_cannot_be_retried(
    auth_client: TestClient,
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    """Проверить сценарий uncertain delivery can be confirmed sent and cannot be retried. Тест
    завершается ошибкой при нарушении зафиксированного инварианта.
    """
    _connection, _campaign, _now = prepare_uncertain_job(auth_client, monkeypatch)
    job = auth_client.get("/api/v1/jobs").json()[0]
    resolved = auth_client.post(
        f"/api/v1/jobs/{job['id']}/resolve",
        headers=csrf_headers(auth_client),
        json={
            "resolution": "confirmed_sent",
            "telegram_message_id": "manual-telegram-message-42",
            "note": "Сообщение найдено в целевой группе и визуально проверено",
        },
    )
    assert resolved.status_code == 200, resolved.text
    body = resolved.json()
    assert body["status"] == "sent"
    assert body["review_resolution"] == DeliveryReviewResolution.CONFIRMED_SENT.value
    assert body["telegram_message_id"] == "manual-telegram-message-42"

    retry = auth_client.post(
        f"/api/v1/jobs/{job['id']}/retry",
        headers=csrf_headers(auth_client),
    )
    assert retry.status_code == 409


def test_uncertain_delivery_stays_held_on_connection_resume_then_can_retry(
    auth_client: TestClient,
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    """Проверить сценарий uncertain delivery stays held on connection resume then can retry. Тест
    завершается ошибкой при нарушении зафиксированного инварианта.
    """
    connection, _campaign, now = prepare_uncertain_job(auth_client, monkeypatch)
    job = auth_client.get("/api/v1/jobs").json()[0]

    resumed = auth_client.post(
        f"/api/v1/connections/{connection['id']}/resume",
        headers=csrf_headers(auth_client),
        json={"acknowledge_manual_review": True},
    )
    assert resumed.status_code == 200, resumed.text
    after_resume = auth_client.get(f"/api/v1/jobs/{job['id']}")
    assert after_resume.status_code == 200
    assert after_resume.json()["status"] == "waiting_review"

    resolved = auth_client.post(
        f"/api/v1/jobs/{job['id']}/resolve",
        headers=csrf_headers(auth_client),
        json={
            "resolution": "confirmed_not_sent",
            "note": "Целевой чат проверен, сообщения с этим текстом и временем нет",
        },
    )
    assert resolved.status_code == 200, resolved.text
    assert resolved.json()["status"] == "retry"
    assert resolved.json()["review_resolution"] == "confirmed_not_sent"

    # Restore the normal fake gateway and execute the explicitly authorised retry.
    monkeypatch.setattr(
        "app.services.delivery.build_gateway",
        lambda conn, settings, cipher: FakeTelegramGateway(conn.kind, conn.id),
    )
    delivery = DeliveryService(
        auth_client.app.state.session_factory,
        auth_client.app.state.settings,
        auth_client.app.state.cipher,
        worker_id="reconciled-retry-worker",
    )
    assert delivery.process_next(now=now + timedelta(seconds=5))
    final_job = auth_client.get(f"/api/v1/jobs/{job['id']}").json()
    assert final_job["status"] == "sent"
    assert final_job["telegram_message_id"].startswith("fake-")


def test_destination_permission_expiry_lifecycle_is_auditable(
    auth_client: TestClient,
) -> None:
    """Проверить сценарий destination permission expiry lifecycle is auditable. Тест завершается
    ошибкой при нарушении зафиксированного инварианта.
    """
    connection = create_connection(auth_client)
    expires_at = datetime.now(UTC) + timedelta(days=30)
    created = auth_client.post(
        "/api/v1/destinations",
        headers=csrf_headers(auth_client),
        json={
            "connection_id": connection["id"],
            "username": "permission_expiry_group",
            "title": "Группа со сроком разрешения",
            "kind": "supergroup",
            "permission_confirmed": True,
            "permission_note": "Разрешение администратора действует один месяц",
            "permission_expires_at": expires_at.isoformat(),
        },
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["permission_status"] == "confirmed"
    assert body["permission_reviewed_at"] is not None
    assert body["permission_expires_at"] is not None

    revoked = auth_client.patch(
        f"/api/v1/destinations/{body['id']}",
        headers=csrf_headers(auth_client),
        json={"permission_status": "unverified"},
    )
    assert revoked.status_code == 200, revoked.text
    assert revoked.json()["permission_expires_at"] is None
    assert revoked.json()["permission_reviewed_at"] is None


def test_scheduler_blocks_when_permission_expires_after_approval(
    auth_client: TestClient,
) -> None:
    """Проверить сценарий scheduler blocks when permission expires after approval. Тест завершается
    ошибкой при нарушении зафиксированного инварианта.
    """
    connection = create_connection(auth_client)
    expires_at = datetime.now(UTC) + timedelta(minutes=5)
    destination_response = auth_client.post(
        "/api/v1/destinations",
        headers=csrf_headers(auth_client),
        json={
            "connection_id": connection["id"],
            "username": "expires_after_approval",
            "title": "Разрешение скоро истечёт",
            "kind": "supergroup",
            "permission_confirmed": True,
            "permission_note": "Временное разрешение администратора",
            "permission_expires_at": expires_at.isoformat(),
        },
    )
    assert destination_response.status_code == 201, destination_response.text
    template = create_template(auth_client)
    campaign = create_campaign(
        auth_client,
        connection["id"],
        template["id"],
        destination_response.json()["id"],
    )
    approve_campaign(auth_client, campaign["id"])
    queued = auth_client.post(
        f"/api/v1/campaigns/{campaign['id']}/run-now",
        headers=csrf_headers(auth_client),
    )
    assert queued.status_code == 200, queued.text

    scheduler = SchedulerService(
        auth_client.app.state.session_factory, auth_client.app.state.settings
    )
    assert scheduler.tick(now=expires_at + timedelta(minutes=1)) == 0
    with auth_client.app.state.session_factory() as db:
        stored = db.get(Campaign, campaign["id"])
        assert stored is not None
        assert stored.status.value == "paused"
        assert (
            db.scalar(select(CampaignRun).where(CampaignRun.campaign_id == campaign["id"])) is None
        )

    latest = auth_client.get(f"/api/v1/campaigns/{campaign['id']}/preflight/latest")
    assert latest.status_code == 200, latest.text
    assert latest.json()["status"] == "blocked"
    assert any("истёк" in item for item in latest.json()["blockers"])


def test_staged_rollout_can_release_next_batch_automatically(
    auth_client: TestClient,
) -> None:
    """Проверить сценарий staged rollout can release next batch automatically. Тест завершается
    ошибкой при нарушении зафиксированного инварианта.
    """
    connection = create_connection(auth_client)
    destinations = [
        create_named_destination(auth_client, connection["id"], suffix)
        for suffix in ("auto_one", "auto_two")
    ]
    template = create_template(auth_client)
    campaign = create_multi_destination_campaign(
        auth_client,
        connection["id"],
        template["id"],
        [item["id"] for item in destinations],
        checkpoint=False,
    )
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
    delivery = DeliveryService(
        auth_client.app.state.session_factory,
        auth_client.app.state.settings,
        auth_client.app.state.cipher,
        worker_id="auto-rollout-worker",
    )
    assert delivery.process_next(now=now + timedelta(seconds=1))

    with auth_client.app.state.session_factory() as db:
        run = db.scalar(select(CampaignRun).where(CampaignRun.campaign_id == campaign["id"]))
        assert run is not None
        assert run.status == RunStatus.RUNNING
        assert run.active_batch == 2
        jobs = list(
            db.scalars(
                select(DeliveryJob)
                .where(DeliveryJob.run_id == run.id)
                .order_by(DeliveryJob.batch_number)
            ).all()
        )
        assert [job.status for job in jobs] == [JobStatus.SENT, JobStatus.PENDING]

    assert delivery.process_next(now=now + timedelta(seconds=3))
    with auth_client.app.state.session_factory() as db:
        run = db.scalar(select(CampaignRun).where(CampaignRun.campaign_id == campaign["id"]))
        assert run is not None
        assert run.status == RunStatus.COMPLETED
        assert run.sent_jobs == 2


def test_uncertain_delivery_blocks_campaign_cancel_and_survives_connection_revoke(
    auth_client: TestClient,
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    """Проверить сценарий uncertain delivery blocks campaign cancel and survives connection revoke.
    Тест завершается ошибкой при нарушении зафиксированного инварианта.
    """
    connection, campaign, _now = prepare_uncertain_job(auth_client, monkeypatch)
    job = auth_client.get("/api/v1/jobs").json()[0]

    cancel = auth_client.post(
        f"/api/v1/campaigns/{campaign['id']}/cancel",
        headers=csrf_headers(auth_client),
    )
    assert cancel.status_code == 409
    assert "неоднозначные" in cancel.json()["detail"]

    revoked = auth_client.delete(
        f"/api/v1/connections/{connection['id']}",
        headers=csrf_headers(auth_client),
    )
    assert revoked.status_code == 200, revoked.text
    preserved = auth_client.get(f"/api/v1/jobs/{job['id']}").json()
    assert preserved["status"] == "waiting_review"
    assert preserved["error_code"] == "DELIVERY_RESULT_UNCERTAIN"

    resolved = auth_client.post(
        f"/api/v1/jobs/{job['id']}/resolve",
        headers=csrf_headers(auth_client),
        json={
            "resolution": "skipped",
            "note": "После отзыва подключения результат нельзя доказать, повтор запрещён",
        },
    )
    assert resolved.status_code == 200, resolved.text
    assert resolved.json()["status"] == "skipped"

    cancelled = auth_client.post(
        f"/api/v1/campaigns/{campaign['id']}/cancel",
        headers=csrf_headers(auth_client),
    )
    assert cancelled.status_code == 200, cancelled.text


def test_staged_abort_requires_reconciliation_of_uncertain_active_batch(
    auth_client: TestClient,
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    """Проверить сценарий staged abort requires reconciliation of uncertain active batch. Тест
    завершается ошибкой при нарушении зафиксированного инварианта.
    """
    connection = create_connection(auth_client)
    destinations = [
        create_named_destination(auth_client, connection["id"], suffix)
        for suffix in ("ambiguous_one", "ambiguous_two")
    ]
    template = create_template(auth_client)
    campaign = create_multi_destination_campaign(
        auth_client,
        connection["id"],
        template["id"],
        [item["id"] for item in destinations],
        checkpoint=True,
    )
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
    monkeypatch.setattr(
        "app.services.delivery.build_gateway",
        lambda conn, settings, cipher: UncertainGateway(conn.kind, conn.id),
    )
    delivery = DeliveryService(
        auth_client.app.state.session_factory,
        auth_client.app.state.settings,
        auth_client.app.state.cipher,
        worker_id="ambiguous-staged-worker",
    )
    assert delivery.process_next(now=now + timedelta(seconds=1))

    with auth_client.app.state.session_factory() as db:
        run = db.scalar(select(CampaignRun).where(CampaignRun.campaign_id == campaign["id"]))
        assert run is not None
        run_id = run.id
        uncertain = db.scalar(
            select(DeliveryJob).where(
                DeliveryJob.run_id == run.id,
                DeliveryJob.status == JobStatus.WAITING_REVIEW,
            )
        )
        assert uncertain is not None
        uncertain_id = uncertain.id

    blocked = auth_client.post(
        f"/api/v1/campaigns/{campaign['id']}/runs/{run_id}/abort",
        headers=csrf_headers(auth_client),
        json={"reason": "Остановка после неоднозначного результата первого пакета"},
    )
    assert blocked.status_code == 409

    resolved = auth_client.post(
        f"/api/v1/jobs/{uncertain_id}/resolve",
        headers=csrf_headers(auth_client),
        json={
            "resolution": "skipped",
            "note": "Результат не доказан, поэтому повтор и следующий пакет запрещены",
        },
    )
    assert resolved.status_code == 200, resolved.text

    aborted = auth_client.post(
        f"/api/v1/campaigns/{campaign['id']}/runs/{run_id}/abort",
        headers=csrf_headers(auth_client),
        json={"reason": "После сверки безопасно отменяем оставшийся пакет"},
    )
    assert aborted.status_code == 200, aborted.text
    assert aborted.json()["status"] == "cancelled"


def test_automatic_rollout_stops_when_failure_threshold_is_exceeded(
    auth_client: TestClient,
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    """Проверить сценарий automatic rollout stops when failure threshold is exceeded. Тест
    завершается ошибкой при нарушении зафиксированного инварианта.
    """
    connection = create_connection(auth_client)
    destinations = [
        create_named_destination(auth_client, connection["id"], suffix)
        for suffix in ("threshold_one", "threshold_two")
    ]
    template = create_template(auth_client)
    campaign = create_multi_destination_campaign(
        auth_client,
        connection["id"],
        template["id"],
        [item["id"] for item in destinations],
        checkpoint=False,
    )
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
    monkeypatch.setattr(
        "app.services.delivery.build_gateway",
        lambda conn, settings, cipher: ForbiddenGateway(conn.kind, conn.id),
    )
    delivery = DeliveryService(
        auth_client.app.state.session_factory,
        auth_client.app.state.settings,
        auth_client.app.state.cipher,
        worker_id="threshold-worker",
    )
    assert delivery.process_next(now=now + timedelta(seconds=1))

    with auth_client.app.state.session_factory() as db:
        run = db.scalar(select(CampaignRun).where(CampaignRun.campaign_id == campaign["id"]))
        assert run is not None
        assert run.status == RunStatus.AWAITING_CHECKPOINT
        assert "превысила порог" in (run.checkpoint_reason or "")
        assert run.active_batch == 1
        jobs = list(
            db.scalars(
                select(DeliveryJob)
                .where(DeliveryJob.run_id == run.id)
                .order_by(DeliveryJob.batch_number)
            ).all()
        )
        assert [job.status for job in jobs] == [JobStatus.FAILED, JobStatus.HELD]
