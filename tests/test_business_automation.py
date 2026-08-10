from __future__ import annotations

import json

from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.enums import ConsentStatus, ConversationStatus, InboundUpdateStatus
from app.models import (
    CandidateProfile,
    Conversation,
    ConversationMessage,
    InboundTelegramUpdate,
    OutboxEvent,
    TelegramBusinessConnection,
    TelegramConnection,
)
from app.services.inbound import InboundService
from tests.conftest import csrf_headers
from tests.test_api_workflow import create_connection


def _configure_business(client: TestClient) -> tuple[dict, str, str]:
    """Реализовать внутренний этап configure business step. Вспомогательная функция сохраняет
    детерминированность и тестируемость процесса.
    """
    connection = create_connection(client)
    response = client.post(
        f"/api/v1/business/bots/{connection['id']}/webhook",
        headers=csrf_headers(client),
        json={"drop_pending_updates": False},
    )
    assert response.status_code == 200, response.text
    with client.app.state.session_factory() as db:
        item = db.get(TelegramConnection, connection["id"])
        assert item and item.credentials_enc
        credentials = client.app.state.cipher.decrypt_json(
            item.credentials_enc, context=f"telegram-connection:{item.id}"
        )
    return connection, credentials["webhook_path_token"], credentials["webhook_secret"]


def _post_update(
    client: TestClient,
    connection_id: str,
    path_token: str,
    header_secret: str,
    payload: dict,
) -> None:
    """Реализовать внутренний этап post update step. Вспомогательная функция сохраняет
    детерминированность и тестируемость процесса.
    """
    response = client.post(
        f"/hooks/telegram/{connection_id}/{path_token}",
        headers={"X-Telegram-Bot-Api-Secret-Token": header_secret},
        json=payload,
    )
    assert response.status_code == 200, response.text


def _business_connection_update(update_id: int = 1) -> dict:
    """Реализовать внутренний этап business connection update step. Вспомогательная функция
    сохраняет детерминированность и тестируемость процесса.
    """
    return {
        "update_id": update_id,
        "business_connection": {
            "id": "bc-test-001",
            "user": {
                "id": 111000,
                "first_name": "Business Owner",
                "username": "business_owner",
            },
            "user_chat_id": 111001,
            "can_reply": True,
            "is_enabled": True,
        },
    }


def _business_message(update_id: int, message_id: int, text: str) -> dict:
    """Реализовать внутренний этап business message step. Вспомогательная функция сохраняет
    детерминированность и тестируемость процесса.
    """
    return {
        "update_id": update_id,
        "business_message": {
            "business_connection_id": "bc-test-001",
            "message_id": message_id,
            "date": 1785960000,
            "from": {
                "id": 222000,
                "is_bot": False,
                "first_name": "Иван",
                "username": "candidate_ivan",
            },
            "chat": {"id": 333000, "type": "private", "first_name": "Иван"},
            "text": text,
        },
    }


def test_webhook_auth_idempotency_and_encrypted_payload(auth_client: TestClient) -> None:
    """Проверить сценарий webhook auth idempotency and encrypted payload. Тест завершается ошибкой
    при нарушении зафиксированного инварианта.
    """
    connection, path_token, header_secret = _configure_business(auth_client)
    payload = _business_connection_update()

    denied = auth_client.post(
        f"/hooks/telegram/{connection['id']}/{path_token}",
        headers={"X-Telegram-Bot-Api-Secret-Token": "wrong"},
        json=payload,
    )
    assert denied.status_code == 403

    _post_update(auth_client, connection["id"], path_token, header_secret, payload)
    _post_update(auth_client, connection["id"], path_token, header_secret, payload)

    with auth_client.app.state.session_factory() as db:
        items = list(db.scalars(select(InboundTelegramUpdate)).all())
        assert len(items) == 1
        item = items[0]
        assert item.status == InboundUpdateStatus.RECEIVED
        assert item.payload_enc
        assert "Business Owner" not in item.payload_enc
        decoded = auth_client.app.state.cipher.decrypt_json(
            item.payload_enc, context=f"inbound-update:{item.id}:payload"
        )
        assert decoded == payload

    service = InboundService(
        auth_client.app.state.session_factory,
        auth_client.app.state.settings,
        auth_client.app.state.cipher,
    )
    assert service.process_next() is True
    with auth_client.app.state.session_factory() as db:
        business = db.scalar(select(TelegramBusinessConnection))
        assert business is not None
        assert business.business_connection_id == "bc-test-001"
        assert business.is_enabled is True


def test_consent_ai_candidate_handoff_and_privacy_export(auth_client: TestClient) -> None:
    """Проверить сценарий consent ai candidate handoff and privacy export. Тест завершается ошибкой
    при нарушении зафиксированного инварианта.
    """
    connection, path_token, header_secret = _configure_business(auth_client)

    provider = auth_client.post(
        "/api/v1/automation/providers",
        headers=csrf_headers(auth_client),
        json={
            "name": "Deterministic AI",
            "kind": "rule_based",
            "enabled": True,
            "system_prompt": "Собирай сведения о кандидате по одному вопросу.",
        },
    )
    assert provider.status_code == 201, provider.text
    policy = auth_client.post(
        "/api/v1/automation/policies",
        headers=csrf_headers(auth_client),
        json={
            "name": "Recruitment assistant",
            "telegram_connection_id": connection["id"],
            "ai_provider_config_id": provider.json()["id"],
            "enabled": True,
            "timezone_name": "Europe/Helsinki",
            "active_hours": {},
            "allowed_chat_types": ["private"],
            "consent_notice": (
                "Согласны на автоматизированную обработку сообщений и хранение данных кандидата? "
                "Ответьте «да» или «нет»."
            ),
            "fallback_message": "Передаю диалог специалисту, он ответит вручную.",
            "max_auto_replies_per_day": 20,
            "require_consent_before_ai": True,
            "handoff_keywords": ["оператор", "человек"],
            "stop_words": ["стоп", "отписаться"],
            "vacancy_detection_rules": {},
        },
    )
    assert policy.status_code == 201, policy.text

    service = InboundService(
        auth_client.app.state.session_factory,
        auth_client.app.state.settings,
        auth_client.app.state.cipher,
    )
    _post_update(
        auth_client,
        connection["id"],
        path_token,
        header_secret,
        _business_connection_update(),
    )
    assert service.process_next()

    first_text = "Здравствуйте, хочу устроиться. Телефон +358 40 123 4567"
    _post_update(
        auth_client,
        connection["id"],
        path_token,
        header_secret,
        _business_message(2, 10, first_text),
    )
    assert service.process_next()
    with auth_client.app.state.session_factory() as db:
        conversation = db.scalar(select(Conversation))
        assert conversation is not None
        assert conversation.status == ConversationStatus.AWAITING_CONSENT
        assert conversation.consent_status == ConsentStatus.REQUESTED
        messages = list(db.scalars(select(ConversationMessage)).all())
        assert len(messages) == 2
        inbound = next(item for item in messages if item.direction.value == "inbound")
        assert inbound.body_enc and first_text not in inbound.body_enc
        assert "+358 40 123 4567" not in inbound.body_preview

    _post_update(
        auth_client,
        connection["id"],
        path_token,
        header_secret,
        _business_message(3, 11, "да"),
    )
    assert service.process_next()
    with auth_client.app.state.session_factory() as db:
        conversation = db.scalar(select(Conversation))
        assert conversation is not None
        assert conversation.consent_status == ConsentStatus.GRANTED
        assert conversation.status == ConversationStatus.AI_ACTIVE
        candidate = db.scalar(select(CandidateProfile))
        assert candidate is not None
        assert candidate.consent_to_storage is True
        assert db.scalar(select(func.count(OutboxEvent.id))) == 1

    _post_update(
        auth_client,
        connection["id"],
        path_token,
        header_secret,
        _business_message(4, 12, "Позовите оператора"),
    )
    assert service.process_next()
    with auth_client.app.state.session_factory() as db:
        conversation = db.scalar(select(Conversation))
        assert conversation is not None
        assert conversation.status == ConversationStatus.HUMAN_HANDOFF
        assert conversation.ai_enabled is False
        conversation_id = conversation.id

    export = auth_client.post(
        "/api/v1/privacy/requests",
        headers=csrf_headers(auth_client),
        json={"request_type": "export", "conversation_id": conversation_id},
    )
    assert export.status_code == 201, export.text
    processed = auth_client.post(
        f"/api/v1/privacy/requests/{export.json()['id']}/process",
        headers=csrf_headers(auth_client),
    )
    assert processed.status_code == 200, processed.text
    assert processed.json()["status"] == "completed"
    downloaded = auth_client.get(f"/api/v1/privacy/requests/{export.json()['id']}/download")
    assert downloaded.status_code == 200, downloaded.text
    data = json.loads(downloaded.content)
    assert data["conversations"][0]["id"] == conversation_id
    assert any(item["body"] == first_text for item in data["conversations"][0]["messages"])

    delete_request = auth_client.post(
        "/api/v1/privacy/requests",
        headers=csrf_headers(auth_client),
        json={"request_type": "delete", "conversation_id": conversation_id},
    )
    assert delete_request.status_code == 201, delete_request.text
    deleted = auth_client.post(
        f"/api/v1/privacy/requests/{delete_request.json()['id']}/process",
        headers=csrf_headers(auth_client),
    )
    assert deleted.status_code == 200, deleted.text
    with auth_client.app.state.session_factory() as db:
        assert db.get(Conversation, conversation_id) is None
        updates = list(db.scalars(select(InboundTelegramUpdate)).all())
        assert any(item.payload_enc is None for item in updates)
