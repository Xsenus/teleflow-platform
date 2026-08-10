from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.enums import ConnectionStatus, JobStatus
from app.models import DeliveryJob, TelegramConnection
from app.services.delivery import DeliveryService
from app.services.scheduler import SchedulerService
from app.services.telegram.errors import (
    TelegramAntiSpamRestriction,
    TelegramDeliveryUncertain,
    TelegramFloodWait,
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


class FloodGateway(FakeTelegramGateway):
    def send_message(self, **kwargs):  # type: ignore[no-untyped-def]
        """Выполнить операцию send message класса FloodGateway. Аргументы интерпретируются в
        контексте модуля, результат возвращается вызывающему коду.
        """
        raise TelegramFloodWait("pause", retry_after=120)


class AntiSpamGateway(FakeTelegramGateway):
    def send_message(self, **kwargs):  # type: ignore[no-untyped-def]
        """Выполнить операцию send message класса AntiSpamGateway. Аргументы интерпретируются в
        контексте модуля, результат возвращается вызывающему коду.
        """
        raise TelegramAntiSpamRestriction("manual review")


class UncertainGateway(FakeTelegramGateway):
    def send_message(self, **kwargs):  # type: ignore[no-untyped-def]
        """Выполнить операцию send message класса UncertainGateway. Аргументы интерпретируются в
        контексте модуля, результат возвращается вызывающему коду.
        """
        raise TelegramDeliveryUncertain("check destination manually")


def prepare_job(client):  # type: ignore[no-untyped-def]
    """Выполнить операцию prepare job. Аргументы интерпретируются в контексте модуля, результат
    возвращается вызывающему коду.
    """
    connection = create_connection(client)
    destination = create_destination(client, connection["id"])
    template = create_template(client)
    campaign = create_campaign(client, connection["id"], template["id"], destination["id"])
    approve_campaign(client, campaign["id"])
    response = client.post(
        f"/api/v1/campaigns/{campaign['id']}/run-now", headers=csrf_headers(client)
    )
    assert response.status_code == 200
    now = datetime.now(UTC) + timedelta(seconds=2)
    scheduler = SchedulerService(client.app.state.session_factory, client.app.state.settings)
    assert scheduler.tick(now=now) == 1
    return connection, now


def test_flood_wait_pauses_connection(auth_client, monkeypatch):  # type: ignore[no-untyped-def]
    """Проверить сценарий flood wait pauses connection. Тест завершается ошибкой при нарушении
    зафиксированного инварианта.
    """
    connection, now = prepare_job(auth_client)
    monkeypatch.setattr(
        "app.services.delivery.build_gateway",
        lambda conn, settings, cipher: FloodGateway(conn.kind, conn.id),
    )
    delivery = DeliveryService(
        auth_client.app.state.session_factory,
        auth_client.app.state.settings,
        auth_client.app.state.cipher,
        worker_id="flood-worker",
    )
    assert delivery.process_next(now=now + timedelta(seconds=1))
    with auth_client.app.state.session_factory() as db:
        stored = db.get(TelegramConnection, connection["id"])
        job = db.query(DeliveryJob).one()
        assert stored.status == ConnectionStatus.PAUSED
        assert stored.flood_blocked_until is not None
        assert job.status == JobStatus.WAITING_REVIEW
        assert job.error_code == "FLOOD_WAIT"


def test_anti_spam_requires_manual_review(auth_client, monkeypatch):  # type: ignore[no-untyped-def]
    """Проверить сценарий anti spam requires manual review. Тест завершается ошибкой при нарушении
    зафиксированного инварианта.
    """
    connection, now = prepare_job(auth_client)
    monkeypatch.setattr(
        "app.services.delivery.build_gateway",
        lambda conn, settings, cipher: AntiSpamGateway(conn.kind, conn.id),
    )
    delivery = DeliveryService(
        auth_client.app.state.session_factory,
        auth_client.app.state.settings,
        auth_client.app.state.cipher,
        worker_id="spam-worker",
    )
    assert delivery.process_next(now=now + timedelta(seconds=1))
    with auth_client.app.state.session_factory() as db:
        stored = db.get(TelegramConnection, connection["id"])
        job = db.query(DeliveryJob).one()
        assert stored.status == ConnectionStatus.PAUSED
        assert stored.last_error_code == "ANTI_SPAM_RESTRICTION"
        assert job.status == JobStatus.WAITING_REVIEW


def test_uncertain_delivery_blocks_automatic_retry(auth_client, monkeypatch):  # type: ignore[no-untyped-def]
    """Проверить сценарий uncertain delivery blocks automatic retry. Тест завершается ошибкой при
    нарушении зафиксированного инварианта.
    """
    connection, now = prepare_job(auth_client)
    monkeypatch.setattr(
        "app.services.delivery.build_gateway",
        lambda conn, settings, cipher: UncertainGateway(conn.kind, conn.id),
    )
    delivery = DeliveryService(
        auth_client.app.state.session_factory,
        auth_client.app.state.settings,
        auth_client.app.state.cipher,
        worker_id="uncertain-worker",
    )
    assert delivery.process_next(now=now + timedelta(seconds=1))
    with auth_client.app.state.session_factory() as db:
        stored = db.get(TelegramConnection, connection["id"])
        job = db.query(DeliveryJob).one()
        assert stored.status == ConnectionStatus.PAUSED
        assert stored.last_error_code == "DELIVERY_RESULT_UNCERTAIN"
        assert job.status == JobStatus.WAITING_REVIEW
