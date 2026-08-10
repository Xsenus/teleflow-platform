from __future__ import annotations

from fastapi.testclient import TestClient

from app.models import CandidateProfile, OutboxEvent
from tests.conftest import csrf_headers
from tests.test_conversation_api import seed_conversation


def create_service_key(client: TestClient) -> tuple[str, str]:
    """Создать service API key со всеми внешними scopes."""

    response = client.post(
        "/api/v1/api-keys",
        headers=csrf_headers(client),
        json={
            "name": "External API lifecycle",
            "scopes": [
                "candidates:read",
                "candidates:contacts",
                "conversations:read",
                "events:write",
            ],
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["secret"], response.json()["api_key"]["id"]


def test_external_api_candidate_conversation_contact_and_events(auth_client: TestClient) -> None:
    """Проверить все внешние read/write endpoints, scopes и encrypted contacts."""

    conversation_id, _message_id, candidate_id, _owner_id = seed_conversation(auth_client)
    with auth_client.app.state.session_factory() as db:
        candidate = db.get(CandidateProfile, candidate_id)
        assert candidate is not None
        candidate.phone_enc = auth_client.app.state.cipher.encrypt(
            "+79991234567", context=f"candidate:{candidate.id}:phone"
        )
        candidate.email_enc = auth_client.app.state.cipher.encrypt(
            "alice@example.test", context=f"candidate:{candidate.id}:email"
        )
        db.commit()

    secret, key_id = create_service_key(auth_client)
    headers = {"X-API-Key": secret}
    listed_keys = auth_client.get("/api/v1/api-keys")
    assert listed_keys.status_code == 200 and listed_keys.json()[0]["id"] == key_id

    candidates = auth_client.get(
        "/api/v1/external/candidates", headers=headers, params={"limit": 0}
    )
    assert candidates.status_code == 200
    assert candidates.json()[0]["id"] == candidate_id
    conversations = auth_client.get(
        "/api/v1/external/conversations", headers=headers, params={"limit": 999}
    )
    assert conversations.status_code == 200
    assert conversations.json()[0]["id"] == conversation_id
    contact = auth_client.get(
        f"/api/v1/external/candidates/{candidate_id}/contact", headers=headers
    )
    assert contact.status_code == 200
    assert contact.json() == {
        "candidate_id": candidate_id,
        "phone": "+79991234567",
        "email": "alice@example.test",
    }
    assert (
        auth_client.get("/api/v1/external/candidates/missing/contact", headers=headers).status_code
        == 404
    )

    default_event = auth_client.post("/api/v1/external/events", headers=headers, json=[])
    assert default_event.status_code == 422
    default_event = auth_client.post("/api/v1/external/events", headers=headers, json={})
    assert default_event.status_code == 200
    custom_event = auth_client.post(
        "/api/v1/external/events",
        headers=headers,
        json={
            "event_type": "crm.candidate_synced",
            "aggregate_type": "candidate",
            "aggregate_id": candidate_id,
            "data": {"source": "crm", "ok": True},
        },
    )
    assert custom_event.status_code == 200
    with auth_client.app.state.session_factory() as db:
        events = db.query(OutboxEvent).order_by(OutboxEvent.created_at).all()
        matching = [event for event in events if event.event_type.startswith(("external.", "crm."))]
        assert [event.event_type for event in matching] == [
            "external.event",
            "crm.candidate_synced",
        ]
        assert matching[1].payload["source"] == "crm"


def test_api_key_validation_and_missing_revoke(auth_client: TestClient) -> None:
    """Проверить неизвестный scope и 404 при отзыве отсутствующего API key."""

    invalid = auth_client.post(
        "/api/v1/api-keys",
        headers=csrf_headers(auth_client),
        json={"name": "Invalid key", "scopes": ["root:everything"]},
    )
    assert invalid.status_code == 422 and "Неизвестные scopes" in invalid.text
    missing = auth_client.post("/api/v1/api-keys/missing/revoke", headers=csrf_headers(auth_client))
    assert missing.status_code == 404
