from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.enums import CandidateStatus, ConsentStatus, ConversationStatus
from app.models import AutomationFlow, CandidateProfile, Conversation, ConversationMessage
from app.services.inbound import InboundService
from tests.conftest import csrf_headers
from tests.test_business_automation import (
    _business_connection_update,
    _business_message,
    _configure_business,
    _post_update,
)


def _definition() -> dict:
    """Реализовать внутренний этап definition step. Вспомогательная функция сохраняет
    детерминированность и тестируемость процесса.
    """
    return {
        "version": 1,
        "start_node_id": "welcome",
        "nodes": [
            {
                "id": "welcome",
                "type": "message",
                "text": "Спасибо. Я задам несколько вопросов.",
                "next_node_id": "name",
            },
            {
                "id": "name",
                "type": "question",
                "text": "Как вас зовут?",
                "field": "full_name",
                "next_node_id": "schedule",
            },
            {
                "id": "schedule",
                "type": "choice",
                "text": "Какой график вам подходит?",
                "field": "schedule",
                "options": [
                    {
                        "label": "Полный день",
                        "aliases": ["полный", "полный график"],
                        "next_node_id": "age",
                    },
                    {
                        "label": "Сменный",
                        "aliases": ["смены"],
                        "next_node_id": "age",
                    },
                ],
            },
            {
                "id": "age",
                "type": "question",
                "text": "Сколько вам полных лет?",
                "field": "age",
                "validation": "age",
                "next_node_id": "finish",
            },
            {
                "id": "finish",
                "type": "end",
                "text": "Спасибо. Анкета готова, передаю её специалисту.",
                "completion_mode": "handoff",
            },
        ],
    }


def _create_flow(client: TestClient, *, active: bool = True) -> dict:
    """Реализовать внутренний этап create flow step. Вспомогательная функция сохраняет
    детерминированность и тестируемость процесса.
    """
    response = client.post(
        "/api/v1/automation/flows",
        headers=csrf_headers(client),
        json={
            "name": "Первичная анкета",
            "description": "Безопасный линейный сценарий сбора данных",
            "is_active": active,
            "definition": _definition(),
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_flow_graph_validation_and_active_immutability(auth_client: TestClient) -> None:
    """Проверить сценарий flow graph validation and active immutability. Тест завершается ошибкой
    при нарушении зафиксированного инварианта.
    """
    invalid = _definition()
    invalid["nodes"][0]["next_node_id"] = "welcome"
    response = auth_client.post(
        "/api/v1/automation/flows",
        headers=csrf_headers(auth_client),
        json={"name": "Цикл", "definition": invalid},
    )
    assert response.status_code == 422
    assert "цикл" in response.text.lower()

    flow = _create_flow(auth_client, active=True)
    patch = auth_client.patch(
        f"/api/v1/automation/flows/{flow['id']}",
        headers=csrf_headers(auth_client),
        json={"definition": _definition()},
    )
    assert patch.status_code == 409

    listed = auth_client.get("/api/v1/automation/flows")
    assert listed.status_code == 200
    assert listed.json()[0]["revision"] == 1
    assert listed.json()[0]["definition"]["start_node_id"] == "welcome"


def test_inactive_flow_cannot_be_attached_to_policy(auth_client: TestClient) -> None:
    """Проверить сценарий inactive flow cannot be attached to policy. Тест завершается ошибкой при
    нарушении зафиксированного инварианта.
    """
    connection, _path, _secret = _configure_business(auth_client)
    flow = _create_flow(auth_client, active=False)
    policy = auth_client.post(
        "/api/v1/automation/policies",
        headers=csrf_headers(auth_client),
        json={
            "name": "Inactive flow policy",
            "telegram_connection_id": connection["id"],
            "automation_flow_id": flow["id"],
            "enabled": False,
            "timezone_name": "UTC",
            "active_hours": {},
            "allowed_chat_types": ["private"],
            "consent_notice": "Согласны на автоматизированную обработку данных? Ответьте да или нет.",
            "fallback_message": "Передаю диалог оператору.",
            "max_auto_replies_per_day": 20,
            "require_consent_before_ai": True,
            "handoff_keywords": ["оператор"],
            "stop_words": ["стоп"],
            "vacancy_detection_rules": {},
        },
    )
    assert policy.status_code == 409


def test_business_conversation_runs_flow_and_encrypts_contacts(auth_client: TestClient) -> None:
    """Проверить сценарий business conversation runs flow and encrypts contacts. Тест завершается
    ошибкой при нарушении зафиксированного инварианта.
    """
    connection, path_token, header_secret = _configure_business(auth_client)
    flow = _create_flow(auth_client)
    policy = auth_client.post(
        "/api/v1/automation/policies",
        headers=csrf_headers(auth_client),
        json={
            "name": "Flow recruitment assistant",
            "telegram_connection_id": connection["id"],
            "automation_flow_id": flow["id"],
            "enabled": True,
            "timezone_name": "UTC",
            "active_hours": {},
            "allowed_chat_types": ["private"],
            "consent_notice": (
                "Согласны на автоматизированную обработку сообщений и хранение данных кандидата? "
                "Ответьте «да» или «нет»."
            ),
            "fallback_message": "Передаю диалог специалисту.",
            "max_auto_replies_per_day": 20,
            "require_consent_before_ai": True,
            "handoff_keywords": ["оператор"],
            "stop_words": ["стоп"],
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

    inputs = [
        (2, 10, "Здравствуйте"),
        (3, 11, "да"),
        (4, 12, "Иван Иванов"),
        (5, 13, "полный"),
        (6, 14, "13"),
        (7, 15, "30"),
    ]
    for update_id, message_id, text in inputs:
        _post_update(
            auth_client,
            connection["id"],
            path_token,
            header_secret,
            _business_message(update_id, message_id, text),
        )
        assert service.process_next()

    with auth_client.app.state.session_factory() as db:
        conversation = db.scalar(select(Conversation))
        candidate = db.scalar(select(CandidateProfile))
        assert conversation is not None and candidate is not None
        assert conversation.consent_status == ConsentStatus.GRANTED
        assert conversation.status == ConversationStatus.HUMAN_HANDOFF
        assert conversation.flow_completed_at is not None
        assert conversation.flow_state_json["completion_mode"] == "handoff"
        assert candidate.full_name == "Иван Иванов"
        assert candidate.schedule == "Полный день"
        assert candidate.age == 30
        assert candidate.status == CandidateStatus.READY_FOR_REVIEW
        assert candidate.consent_to_storage is True

        outbound = list(
            db.scalars(
                select(ConversationMessage).where(ConversationMessage.direction == "OUTBOUND")
            ).all()
        )
        previews = " ".join(item.body_preview for item in outbound)
        assert "Как вас зовут" in previews
        assert "Возраст должен быть" in previews
        assert "Анкета готова" in previews

        stored_flow = db.get(AutomationFlow, flow["id"])
        assert stored_flow is not None and stored_flow.is_active


def test_flow_phone_is_encrypted_and_not_duplicated_in_state(auth_client: TestClient) -> None:
    # Direct engine coverage keeps this check independent from Telegram update shape.
    """Проверить сценарий flow phone is encrypted and not duplicated in state. Тест завершается
    ошибкой при нарушении зафиксированного инварианта.
    """
    from app.enums import BusinessConnectionStatus
    from app.models import TelegramBusinessConnection
    from app.services.flows import AutomationFlowEngine

    connection, _path, _secret = _configure_business(auth_client)
    phone_definition = {
        "version": 1,
        "start_node_id": "phone",
        "nodes": [
            {
                "id": "phone",
                "type": "question",
                "text": "Ваш телефон?",
                "field": "phone",
                "validation": "phone",
                "next_node_id": "done",
            },
            {
                "id": "done",
                "type": "end",
                "text": "Принято",
                "completion_mode": "ai",
            },
        ],
    }
    response = auth_client.post(
        "/api/v1/automation/flows",
        headers=csrf_headers(auth_client),
        json={"name": "Телефон", "is_active": True, "definition": phone_definition},
    )
    assert response.status_code == 201, response.text

    with auth_client.app.state.session_factory() as db:
        business = TelegramBusinessConnection(
            organization_id=connection["organization_id"],
            telegram_connection_id=connection["id"],
            business_connection_id="direct-flow",
            telegram_user_id=1,
            user_chat_id=2,
            status=BusinessConnectionStatus.ACTIVE,
            is_enabled=True,
            rights={},
        )
        db.add(business)
        db.flush()
        conversation = Conversation(
            organization_id=connection["organization_id"],
            business_connection_id=business.id,
            telegram_chat_id=123,
            consent_status=ConsentStatus.GRANTED,
        )
        db.add(conversation)
        db.flush()
        stored_flow = db.get(AutomationFlow, response.json()["id"])
        engine = AutomationFlowEngine(auth_client.app.state.cipher)

        first = engine.process(
            db,
            conversation=conversation,
            flow=stored_flow,
            message_text="hello",
        )
        assert first.handled and "телефон" in first.reply_text.lower()
        phone = "+31 6 1234 5678"
        second = engine.process(
            db,
            conversation=conversation,
            flow=stored_flow,
            message_text=phone,
        )
        assert second.completed and not second.handoff
        assert conversation.candidate is not None
        assert conversation.candidate.phone_enc
        assert phone not in conversation.candidate.phone_enc
        assert phone not in str(conversation.flow_state_json)
        decrypted = auth_client.app.state.cipher.decrypt(
            conversation.candidate.phone_enc,
            context=f"candidate:{conversation.candidate.id}:phone",
        )
        assert decrypted == phone
