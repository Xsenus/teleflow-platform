from __future__ import annotations

from fastapi.testclient import TestClient

from app.enums import InboundUpdateStatus
from app.models import InboundTelegramUpdate, TelegramConnection, new_id
from app.services.inbound import InboundService
from tests.test_api_workflow import create_connection


def service(client: TestClient) -> InboundService:
    """Создать inbound worker для тестовой session factory."""

    return InboundService(
        client.app.state.session_factory,
        client.app.state.settings,
        client.app.state.cipher,
        worker_id="inbound-edge-test",
    )


def accept(client: TestClient, payload: dict) -> str:
    """Сохранить Telegram update через production accept_update и вернуть id."""

    connection = client.get("/api/v1/connections").json()[0]
    inbound = service(client)
    with client.app.state.session_factory() as db:
        stored_connection = db.get(TelegramConnection, connection["id"])
        assert stored_connection is not None
        item, created = inbound.accept_update(db, connection=stored_connection, payload=payload)
        assert created is True
        return item.id


def test_inbound_accept_validation_and_unsupported_update(auth_client: TestClient) -> None:
    """Проверить обязательный update_id и безопасное игнорирование неизвестного update type."""

    create_connection(auth_client)
    inbound = service(auth_client)
    with auth_client.app.state.session_factory() as db:
        connection = db.query(TelegramConnection).one()
        for payload in ({}, {"update_id": "not-int"}):
            try:
                inbound.accept_update(db, connection=connection, payload=payload)
            except ValueError as exc:
                assert "update_id" in str(exc)
            else:  # pragma: no cover - явная защита тестового контракта
                raise AssertionError("Некорректный update_id должен быть отклонён")

    item_id = accept(auth_client, {"update_id": 9001, "unknown": {"value": 1}})
    assert inbound.process_next() is True
    assert inbound.process_next() is False
    with auth_client.app.state.session_factory() as db:
        item = db.get(InboundTelegramUpdate, item_id)
        assert item is not None
        assert item.update_type == "unsupported"
        assert item.status == InboundUpdateStatus.IGNORED
        assert item.processed_at is not None


def test_inbound_marks_missing_ciphertext_and_invalid_business_failed(
    auth_client: TestClient,
) -> None:
    """Проверить durable FAILED evidence для повреждённого payload и Business update."""

    connection = create_connection(auth_client)
    with auth_client.app.state.session_factory() as db:
        broken = InboundTelegramUpdate(
            id=new_id(),
            organization_id=connection["organization_id"],
            telegram_connection_id=connection["id"],
            telegram_update_id=9002,
            update_type="business_message",
            status=InboundUpdateStatus.RECEIVED,
            payload_redacted={},
            payload_enc="",
        )
        db.add(broken)
        db.commit()
        broken_id = broken.id

    inbound = service(auth_client)
    assert inbound.process_next() is True
    with auth_client.app.state.session_factory() as db:
        failed = db.get(InboundTelegramUpdate, broken_id)
        assert failed is not None
        assert failed.status == InboundUpdateStatus.FAILED
        assert failed.error_code == "ValueError"
        assert "отсутствует" in (failed.error_message or "")

    invalid_id = accept(
        auth_client,
        {"update_id": 9003, "business_connection": {"user": {"id": 10}}},
    )
    assert inbound.process_next() is True
    with auth_client.app.state.session_factory() as db:
        failed = db.get(InboundTelegramUpdate, invalid_id)
        assert failed is not None
        assert failed.status == InboundUpdateStatus.FAILED
        assert "business_connection.id" in (failed.error_message or "")


def test_inbound_edited_and_deleted_unknown_conversations_are_ignored(
    auth_client: TestClient,
) -> None:
    """Проверить безопасную обработку edit/delete событий без локального диалога."""

    create_connection(auth_client)
    edited_id = accept(
        auth_client,
        {
            "update_id": 9004,
            "edited_business_message": {
                "business_connection_id": "bc-edge",
                "message_id": 77,
                "chat": {"id": 123, "type": "private"},
                "text": "Исправленный текст",
            },
        },
    )
    deleted_id = accept(
        auth_client,
        {
            "update_id": 9005,
            "deleted_business_messages": {
                "business_connection_id": "bc-edge",
                "chat_id": 123,
                "message_ids": [77, "invalid"],
            },
        },
    )

    inbound = service(auth_client)
    assert inbound.process_next() is True
    assert inbound.process_next() is True
    with auth_client.app.state.session_factory() as db:
        edited = db.get(InboundTelegramUpdate, edited_id)
        deleted = db.get(InboundTelegramUpdate, deleted_id)
        assert edited is not None and edited.status == InboundUpdateStatus.IGNORED
        assert deleted is not None and deleted.status == InboundUpdateStatus.IGNORED
