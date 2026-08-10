from __future__ import annotations

from fastapi.testclient import TestClient

from app.models import TelegramConnection
from app.services.inbound import InboundService
from tests.conftest import csrf_headers
from tests.test_business_automation import (
    _business_connection_update,
    _configure_business,
    _post_update,
)


def test_business_connection_and_webhook_admin_lifecycle(auth_client: TestClient) -> None:
    """Проверить list/filter/patch/info/delete для Business и webhook форм."""

    connection, path_token, header_secret = _configure_business(auth_client)
    _post_update(
        auth_client,
        connection["id"],
        path_token,
        header_secret,
        _business_connection_update(801),
    )
    service = InboundService(
        auth_client.app.state.session_factory,
        auth_client.app.state.settings,
        auth_client.app.state.cipher,
    )
    assert service.process_next() is True

    listed = auth_client.get("/api/v1/business/connections")
    assert listed.status_code == 200 and len(listed.json()) == 1
    business = listed.json()[0]
    filtered = auth_client.get(
        "/api/v1/business/connections",
        params={"telegram_connection_id": connection["id"]},
    )
    assert filtered.status_code == 200 and filtered.json()[0]["id"] == business["id"]
    assert (
        auth_client.get(
            "/api/v1/business/connections", params={"telegram_connection_id": "missing"}
        ).json()
        == []
    )

    patched = auth_client.patch(
        f"/api/v1/business/connections/{business['id']}",
        headers=csrf_headers(auth_client),
        json={"is_enabled": False},
    )
    assert patched.status_code == 200
    assert patched.json()["is_enabled"] is False
    assert (
        auth_client.patch(
            "/api/v1/business/connections/missing",
            headers=csrf_headers(auth_client),
            json={"is_enabled": False},
        ).status_code
        == 404
    )

    info = auth_client.get(f"/api/v1/business/bots/{connection['id']}/webhook")
    assert info.status_code == 200
    assert info.json() == {"url": "fake://telegram-webhook", "pending_update_count": 0}
    deleted = auth_client.delete(
        f"/api/v1/business/bots/{connection['id']}/webhook",
        headers=csrf_headers(auth_client),
    )
    assert deleted.status_code == 200 and deleted.json()["message"] == "Webhook удалён"
    with auth_client.app.state.session_factory() as db:
        stored = db.get(TelegramConnection, connection["id"])
        assert stored is not None and stored.credentials_enc
        credentials = auth_client.app.state.cipher.decrypt_json(
            stored.credentials_enc, context=f"telegram-connection:{stored.id}"
        )
        assert "bot_token" in credentials
        assert not any(key.startswith("webhook_") for key in credentials)

    after_delete = auth_client.post(
        f"/hooks/telegram/{connection['id']}/{path_token}",
        headers={"X-Telegram-Bot-Api-Secret-Token": header_secret},
        json=_business_connection_update(802),
    )
    assert after_delete.status_code == 404


def test_business_webhook_rejects_invalid_payloads(auth_client: TestClient) -> None:
    """Проверить malformed JSON и JSON-массив после успешной webhook-аутентификации."""

    connection, path_token, header_secret = _configure_business(auth_client)
    url = f"/hooks/telegram/{connection['id']}/{path_token}"
    headers = {
        "X-Telegram-Bot-Api-Secret-Token": header_secret,
        "Content-Type": "application/json",
    }
    malformed = auth_client.post(url, headers=headers, content=b"{")
    assert malformed.status_code == 400
    array = auth_client.post(url, headers=headers, content=b"[]")
    assert array.status_code == 400


def test_webhook_setup_requires_active_bot(auth_client: TestClient) -> None:
    """Проверить 404 неизвестного bot id и 409 неактивного подключения."""

    missing = auth_client.post(
        "/api/v1/business/bots/missing/webhook",
        headers=csrf_headers(auth_client),
        json={"drop_pending_updates": False},
    )
    assert missing.status_code == 404

    connection, _path, _secret = _configure_business(auth_client)
    auth_client.post(
        f"/api/v1/connections/{connection['id']}/pause", headers=csrf_headers(auth_client)
    )
    inactive = auth_client.post(
        f"/api/v1/business/bots/{connection['id']}/webhook",
        headers=csrf_headers(auth_client),
        json={"drop_pending_updates": False},
    )
    assert inactive.status_code == 409
