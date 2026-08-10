from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.enums import (
    BusinessConnectionStatus,
    CandidateStatus,
    ConsentStatus,
    ConversationStatus,
    MessageAuthor,
    MessageDirection,
)
from app.models import (
    AutomationPolicy,
    CandidateProfile,
    Conversation,
    ConversationMessage,
    Organization,
    TelegramBusinessConnection,
    User,
)
from tests.conftest import csrf_headers
from tests.test_api_workflow import create_connection


def seed_conversation(auth_client: TestClient) -> tuple[str, str, str, str]:
    """Создать изолированные conversation, message и candidate для API-проверок."""

    connection = create_connection(auth_client)
    with auth_client.app.state.session_factory() as db:
        organization = db.scalar(select(Organization))
        owner = db.scalar(select(User))
        assert organization and owner
        business = TelegramBusinessConnection(
            organization_id=organization.id,
            telegram_connection_id=connection["id"],
            business_connection_id="bc-api-coverage",
            status=BusinessConnectionStatus.ACTIVE,
            telegram_user_id=7001,
            user_chat_id=7002,
            username="alice",
            first_name="Alice",
            rights={"can_reply": True},
            is_enabled=True,
        )
        db.add(business)
        db.flush()
        policy = AutomationPolicy(
            organization_id=organization.id,
            name="Conversation API policy",
            telegram_connection_id=connection["id"],
            enabled=True,
            timezone_name="Asia/Novosibirsk",
            active_hours={},
            allowed_chat_types=["private"],
            consent_notice="Тестовое уведомление о согласии",
            fallback_message="Передаю оператору",
            max_auto_replies_per_day=20,
            require_consent_before_ai=True,
            handoff_keywords=["оператор"],
            stop_words=["стоп"],
            vacancy_detection_rules={},
            created_by_id=owner.id,
        )
        db.add(policy)
        db.flush()
        conversation = Conversation(
            organization_id=organization.id,
            business_connection_id=business.id,
            automation_policy_id=policy.id,
            telegram_chat_id=7100,
            telegram_user_id=7101,
            username="alice",
            first_name="Alice",
            status=ConversationStatus.NEW,
            consent_status=ConsentStatus.GRANTED,
            vacancy_key="python-dev",
            ai_enabled=True,
            metadata_json={},
            last_message_at=datetime.now(UTC),
        )
        db.add(conversation)
        db.flush()
        message = ConversationMessage(
            organization_id=organization.id,
            conversation_id=conversation.id,
            telegram_message_id=7200,
            direction=MessageDirection.INBOUND,
            author=MessageAuthor.CONTACT,
            body_preview="Здравствуйте",
            raw_metadata={},
        )
        db.add(message)
        db.flush()
        message.body_enc = auth_client.app.state.cipher.encrypt(
            "Здравствуйте, это полный текст",
            context=f"conversation-message:{message.id}:body",
        )
        candidate = CandidateProfile(
            organization_id=organization.id,
            conversation_id=conversation.id,
            full_name="Alice Candidate",
            city="Новосибирск",
            age=30,
            vacancy_key="python-dev",
            status=CandidateStatus.NEW,
            structured_data={},
            consent_to_storage=True,
        )
        db.add(candidate)
        db.commit()
        return conversation.id, message.id, candidate.id, owner.id


def test_conversation_and_candidate_forms_cover_full_api_lifecycle(
    auth_client: TestClient,
) -> None:
    """Проверить CRUD, фильтры, шифрование контактов, CSV и безопасное закрытие диалога."""

    conversation_id, message_id, candidate_id, owner_id = seed_conversation(auth_client)
    headers = csrf_headers(auth_client)

    listed = auth_client.get("/api/v1/conversations", params={"status": "new", "search": "alice"})
    assert listed.status_code == 200 and [item["id"] for item in listed.json()] == [conversation_id]
    assert auth_client.get(f"/api/v1/conversations/{conversation_id}").status_code == 200
    assert auth_client.get("/api/v1/conversations/missing").status_code == 404

    invalid_assignee = auth_client.patch(
        f"/api/v1/conversations/{conversation_id}",
        headers=headers,
        json={"assigned_user_id": "missing"},
    )
    assert invalid_assignee.status_code == 422
    patched = auth_client.patch(
        f"/api/v1/conversations/{conversation_id}",
        headers=headers,
        json={
            "assigned_user_id": owner_id,
            "status": "human_handoff",
            "consent_status": "revoked",
            "ai_enabled": True,
        },
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["assigned_user_id"] == owner_id
    assert patched.json()["ai_enabled"] is False

    messages = auth_client.get(
        f"/api/v1/conversations/{conversation_id}/messages",
        params={"before": (datetime.now(UTC) + timedelta(days=1)).isoformat()},
    )
    assert messages.status_code == 200 and messages.json()[0]["id"] == message_id
    body = auth_client.get(f"/api/v1/conversations/{conversation_id}/messages/{message_id}/body")
    assert body.status_code == 200
    assert body.json()["body"] == "Здравствуйте, это полный текст"
    assert (
        auth_client.get(
            f"/api/v1/conversations/{conversation_id}/messages/missing/body"
        ).status_code
        == 404
    )

    reply = auth_client.post(
        f"/api/v1/conversations/{conversation_id}/reply",
        headers=headers,
        json={"text": "Ответ оператора"},
    )
    assert reply.status_code == 200, reply.text
    closed = auth_client.post(f"/api/v1/conversations/{conversation_id}/close", headers=headers)
    assert closed.status_code == 200
    blocked_reply = auth_client.post(
        f"/api/v1/conversations/{conversation_id}/reply",
        headers=headers,
        json={"text": "Повтор"},
    )
    assert blocked_reply.status_code == 409

    candidates = auth_client.get(
        "/api/v1/candidates",
        params={"status": "new", "vacancy_key": "python-dev", "search": "Alice"},
    )
    assert candidates.status_code == 200 and candidates.json()[0]["id"] == candidate_id
    assert auth_client.get(f"/api/v1/candidates/{candidate_id}").status_code == 200
    assert auth_client.get("/api/v1/candidates/missing").status_code == 404

    candidate = auth_client.patch(
        f"/api/v1/candidates/{candidate_id}",
        headers=headers,
        json={
            "full_name": "Алиса Кандидат",
            "phone": "+79990001122",
            "email": "alice@example.com",
            "status": "ready_for_review",
            "summary": "Готова к интервью",
        },
    )
    assert candidate.status_code == 200, candidate.text
    assert candidate.json()["status"] == "ready_for_review"
    contact = auth_client.get(f"/api/v1/candidates/{candidate_id}/contact")
    assert contact.status_code == 200
    assert contact.json() == {
        "candidate_id": candidate_id,
        "phone": "+79990001122",
        "email": "alice@example.com",
    }

    without_contacts = auth_client.get("/api/v1/candidates-export.csv")
    assert without_contacts.status_code == 200
    assert "phone,email" not in without_contacts.text.splitlines()[0]
    with_contacts = auth_client.get(
        "/api/v1/candidates-export.csv", params={"include_contacts": True}
    )
    assert with_contacts.status_code == 200
    assert "+79990001122" in with_contacts.text
    assert "alice@example.com" in with_contacts.text
