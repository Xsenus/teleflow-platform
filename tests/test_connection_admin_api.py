from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from app.enums import ChallengeStatus, ConnectionStatus
from app.models import TelegramAuthChallenge, TelegramConnection
from tests.conftest import csrf_headers
from tests.test_api_workflow import create_connection


def test_bot_connection_admin_lifecycle(auth_client: TestClient) -> None:
    """Проверить list/get/patch/health/pause/resume/revoke формы Bot API-подключения."""

    connection = create_connection(auth_client, name="Lifecycle Bot")
    connection_id = connection["id"]

    listed = auth_client.get("/api/v1/connections")
    assert listed.status_code == 200
    assert [item["id"] for item in listed.json()] == [connection_id]
    assert auth_client.get(f"/api/v1/connections/{connection_id}").status_code == 200
    assert auth_client.get("/api/v1/connections/missing").status_code == 404

    updated = auth_client.patch(
        f"/api/v1/connections/{connection_id}",
        headers=csrf_headers(auth_client),
        json={"name": "Lifecycle Bot Updated", "daily_cap": 45, "min_interval_seconds": 2},
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["name"] == "Lifecycle Bot Updated"
    assert updated.json()["daily_cap"] == 45

    health = auth_client.post(
        f"/api/v1/connections/{connection_id}/health", headers=csrf_headers(auth_client)
    )
    assert health.status_code == 200
    assert health.json()["ok"] is True
    assert health.json()["identity"]["is_bot"] is True

    paused = auth_client.post(
        f"/api/v1/connections/{connection_id}/pause", headers=csrf_headers(auth_client)
    )
    assert paused.status_code == 200 and paused.json()["status"] == "paused"
    assert auth_client.get(f"/api/v1/connections/{connection_id}/discover").status_code == 409

    with auth_client.app.state.session_factory() as db:
        item = db.get(TelegramConnection, connection_id)
        assert item is not None
        item.flood_blocked_until = datetime.now(UTC) + timedelta(minutes=5)
        db.commit()
    blocked = auth_client.post(
        f"/api/v1/connections/{connection_id}/resume",
        headers=csrf_headers(auth_client),
        json={"acknowledge_manual_review": True},
    )
    assert blocked.status_code == 409 and "паузу" in blocked.text

    with auth_client.app.state.session_factory() as db:
        item = db.get(TelegramConnection, connection_id)
        assert item is not None
        item.flood_blocked_until = datetime.now(UTC) - timedelta(seconds=1)
        item.last_error_code = "ANTI_SPAM_RESTRICTION"
        db.commit()
    review_required = auth_client.post(
        f"/api/v1/connections/{connection_id}/resume",
        headers=csrf_headers(auth_client),
        json={"acknowledge_manual_review": False},
    )
    assert review_required.status_code == 409 and "ручную проверку" in review_required.text
    resumed = auth_client.post(
        f"/api/v1/connections/{connection_id}/resume",
        headers=csrf_headers(auth_client),
        json={"acknowledge_manual_review": True},
    )
    assert resumed.status_code == 200 and resumed.json()["status"] == "active"

    revoked = auth_client.delete(
        f"/api/v1/connections/{connection_id}", headers=csrf_headers(auth_client)
    )
    assert revoked.status_code == 200 and "секреты уничтожены" in revoked.json()["message"]
    with auth_client.app.state.session_factory() as db:
        item = db.get(TelegramConnection, connection_id)
        assert item is not None and item.credentials_enc
        payload = auth_client.app.state.cipher.decrypt_json(
            item.credentials_enc, context=f"telegram-connection:{item.id}"
        )
        assert payload == {"revoked": True}
    assert (
        auth_client.post(
            f"/api/v1/connections/{connection_id}/health", headers=csrf_headers(auth_client)
        ).status_code
        == 409
    )
    assert (
        auth_client.post(
            f"/api/v1/connections/{connection_id}/pause", headers=csrf_headers(auth_client)
        ).status_code
        == 409
    )
    assert (
        auth_client.post(
            f"/api/v1/connections/{connection_id}/resume",
            headers=csrf_headers(auth_client),
            json={"acknowledge_manual_review": True},
        ).status_code
        == 409
    )


def start_user_connection(client: TestClient, *, name: str = "User Account") -> dict:
    """Создать тестовый MTProto auth challenge через пользовательскую форму."""

    response = client.post(
        "/api/v1/connections/user/start",
        headers=csrf_headers(client),
        json={
            "name": name,
            "api_id": 123456,
            "api_hash": "0123456789abcdef0123456789abcdef",
            "phone": "+79990000000",
            "min_interval_seconds": 60,
            "destination_cooldown_minutes": 60,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_user_connection_auth_and_forced_safety(auth_client: TestClient) -> None:
    """Проверить safety validation, неверный код, успешную авторизацию и forced flags."""

    unsafe_interval = auth_client.post(
        "/api/v1/connections/user/start",
        headers=csrf_headers(auth_client),
        json={
            "name": "Unsafe User",
            "api_id": 1,
            "api_hash": "0123456789abcdef0123456789abcdef",
            "phone": "+70000000000",
            "min_interval_seconds": 1,
            "destination_cooldown_minutes": 60,
        },
    )
    assert unsafe_interval.status_code == 422 and "Минимальный интервал" in unsafe_interval.text

    unsafe_cooldown = auth_client.post(
        "/api/v1/connections/user/start",
        headers=csrf_headers(auth_client),
        json={
            "name": "Unsafe User",
            "api_id": 1,
            "api_hash": "0123456789abcdef0123456789abcdef",
            "phone": "+70000000000",
            "min_interval_seconds": 60,
            "destination_cooldown_minutes": 1,
        },
    )
    assert unsafe_cooldown.status_code == 422 and "Cooldown" in unsafe_cooldown.text

    started = start_user_connection(auth_client)
    assert started["code_hint"] == "В тестовом режиме используйте 12345"
    assert (
        auth_client.post(
            "/api/v1/connections/user/complete",
            headers=csrf_headers(auth_client),
            json={"challenge_id": "missing", "code": "12345"},
        ).status_code
        == 404
    )
    wrong = auth_client.post(
        "/api/v1/connections/user/complete",
        headers=csrf_headers(auth_client),
        json={"challenge_id": started["challenge_id"], "code": "99999"},
    )
    assert wrong.status_code == 400
    completed = auth_client.post(
        "/api/v1/connections/user/complete",
        headers=csrf_headers(auth_client),
        json={"challenge_id": started["challenge_id"], "code": "12345"},
    )
    assert completed.status_code == 200, completed.text
    assert completed.json()["kind"] == "user"
    assert completed.json()["status"] == "active"

    patched = auth_client.patch(
        f"/api/v1/connections/{started['connection_id']}",
        headers=csrf_headers(auth_client),
        json={"require_manual_approval": False, "stop_on_flood": False},
    )
    assert patched.status_code == 200
    assert patched.json()["require_manual_approval"] is True
    assert patched.json()["stop_on_flood"] is True


def test_user_auth_attempt_limit_scrubs_challenge(auth_client: TestClient) -> None:
    """Проверить отмену и очистку auth challenge после шестой попытки."""

    started = start_user_connection(auth_client, name="Attempt Limited User")
    with auth_client.app.state.session_factory() as db:
        challenge = db.get(TelegramAuthChallenge, started["challenge_id"])
        assert challenge is not None
        challenge.attempts = 5
        db.commit()

    limited = auth_client.post(
        "/api/v1/connections/user/complete",
        headers=csrf_headers(auth_client),
        json={"challenge_id": started["challenge_id"], "code": "99999"},
    )
    assert limited.status_code == 429
    with auth_client.app.state.session_factory() as db:
        challenge = db.get(TelegramAuthChallenge, started["challenge_id"])
        connection = db.get(TelegramConnection, started["connection_id"])
        assert challenge is not None and connection is not None
        assert challenge.status == ChallengeStatus.CANCELLED
        assert connection.status == ConnectionStatus.ERROR
        payload = auth_client.app.state.cipher.decrypt_json(
            challenge.payload_enc, context=f"telegram-auth-challenge:{challenge.id}"
        )
        assert payload == {"cancelled": True}
