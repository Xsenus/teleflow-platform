from __future__ import annotations

from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.enums import IntegrationKind, OutboxStatus
from app.models import IntegrationEndpoint, Organization, OutboxEvent, User, utcnow
from app.services.outbox import OutboxService, enqueue_event


def outbox(client: TestClient) -> OutboxService:
    """Создать OutboxService с production session factory и локальным storage."""

    return OutboxService(
        client.app.state.session_factory,
        client.app.state.settings,
        client.app.state.cipher,
        worker_id="outbox-edge-test",
        storage=client.app.state.storage,
    )


def test_outbox_empty_auto_targets_and_missing_endpoint(auth_client: TestClient) -> None:
    """Проверить пустую очередь, auto-targets и skip удалённого endpoint."""

    service = outbox(auth_client)
    assert service.process_next() is False

    with auth_client.app.state.session_factory() as db:
        organization = db.scalar(select(Organization))
        assert organization is not None
        auto = enqueue_event(
            db,
            organization_id=organization.id,
            event_type="candidate.empty_targets",
            aggregate_type="candidate",
            aggregate_id="candidate-empty",
            payload={"ok": True},
        )
        missing = enqueue_event(
            db,
            organization_id=organization.id,
            event_type="candidate.missing_target",
            aggregate_type="candidate",
            aggregate_id="candidate-missing",
            payload={},
            target_endpoint_ids=["missing-endpoint"],
        )
        db.commit()
        auto_id, missing_id = auto.id, missing.id

    assert service.process_next() is True
    assert service.process_next() is True
    with auth_client.app.state.session_factory() as db:
        auto = db.get(OutboxEvent, auto_id)
        missing = db.get(OutboxEvent, missing_id)
        assert auto is not None and auto.status == OutboxStatus.DELIVERED
        assert auto.target_endpoint_ids == []
        assert missing is not None and missing.status == OutboxStatus.DELIVERED
        assert missing.delivery_state["missing-endpoint"]["status"] == "skipped"


def test_outbox_recovers_stale_and_marks_exhausted_delivery_dead(
    auth_client: TestClient,
) -> None:
    """Проверить stale lease recovery и DEAD после исчерпания max_attempts."""

    with auth_client.app.state.session_factory() as db:
        organization = db.scalar(select(Organization))
        user = db.scalar(select(User))
        assert organization is not None and user is not None
        endpoint = IntegrationEndpoint(
            organization_id=organization.id,
            name="Failing CSV",
            kind=IntegrationKind.CSV_EXPORT,
            event_types=[],
            is_active=True,
            created_by_id=user.id,
        )
        db.add(endpoint)
        db.flush()
        stale = OutboxEvent(
            organization_id=organization.id,
            event_type="stale.event",
            aggregate_type="test",
            aggregate_id="stale",
            payload={},
            target_endpoint_ids=[],
            delivery_state={},
            status=OutboxStatus.PROCESSING,
            attempt_count=1,
            max_attempts=8,
            due_at=utcnow(),
            locked_at=utcnow() - timedelta(hours=1),
            locked_by="dead-worker",
        )
        dead = OutboxEvent(
            organization_id=organization.id,
            event_type="dead.event",
            aggregate_type="test",
            aggregate_id="dead",
            payload={},
            target_endpoint_ids=[endpoint.id],
            delivery_state={},
            status=OutboxStatus.PENDING,
            attempt_count=0,
            max_attempts=1,
            due_at=utcnow(),
        )
        db.add_all([stale, dead])
        db.commit()
        stale_id, dead_id = stale.id, dead.id

    service = outbox(auth_client)
    assert service.recover_stale() == 1

    def fail(_endpoint: IntegrationEndpoint, _event: OutboxEvent) -> None:
        """Имитировать постоянный отказ внешней интеграции."""

        raise RuntimeError("permanent failure")

    service._deliver = fail  # type: ignore[method-assign]
    with auth_client.app.state.session_factory() as db:
        stale = db.get(OutboxEvent, stale_id)
        assert stale is not None
        assert stale.status == OutboxStatus.RETRY
        assert stale.locked_at is None and stale.locked_by is None
        stale.due_at = utcnow() + timedelta(hours=1)
        db.commit()

    assert service.process_next() is True
    with auth_client.app.state.session_factory() as db:
        dead = db.get(OutboxEvent, dead_id)
        assert dead is not None and dead.status == OutboxStatus.DEAD
        assert dead.attempt_count == 1
        assert dead.last_error_message == "permanent failure"
        assert dead.delivery_state[next(iter(dead.delivery_state))]["status"] == "retry"


def test_outbox_dispatch_validation_and_config(auth_client: TestClient) -> None:
    """Проверить выбор adapter, пустой config и validation Google Sheets/OAuth."""

    service = outbox(auth_client)
    event = OutboxEvent(
        id="dispatch-event",
        organization_id="org",
        event_type="candidate.updated",
        aggregate_type="candidate",
        aggregate_id="candidate-1",
        payload={"name": "Иван"},
        target_endpoint_ids=[],
        delivery_state={},
        status=OutboxStatus.PENDING,
        due_at=utcnow(),
        created_at=utcnow(),
    )
    endpoint = IntegrationEndpoint(
        id="dispatch-endpoint",
        organization_id="org",
        name="Dispatch",
        kind=IntegrationKind.CSV_EXPORT,
        config_enc=None,
        event_types=[],
        is_active=True,
        created_by_id="user",
    )
    assert service._config(endpoint) == {}

    calls: list[str] = []
    service._csv = lambda _config, _event: calls.append("csv")  # type: ignore[method-assign]
    service._webhook = lambda _config, _event: calls.append("webhook")  # type: ignore[method-assign]
    service._google_sheets = lambda _config, _event: calls.append("sheets")  # type: ignore[method-assign]
    for kind in (
        IntegrationKind.CSV_EXPORT,
        IntegrationKind.WEBHOOK,
        IntegrationKind.GOOGLE_SHEETS,
    ):
        endpoint.kind = kind
        service._deliver(endpoint, event)
    assert calls == ["csv", "webhook", "sheets"]

    with pytest.raises(ValueError, match="incomplete"):
        OutboxService._google_sheets(service, {}, event)
    with pytest.raises(ValueError, match="token_uri"):
        service._google_access_token(
            {
                "client_email": "service@example.test",
                "private_key": "not-used",
                "token_uri": "https://evil.example/token",
            }
        )
