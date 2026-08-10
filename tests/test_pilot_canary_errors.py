from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.enums import ConnectionStatus
from app.models import Destination, TelegramConnection
from app.services.pilot_canary import CANARY_CONFIRMATION
from app.services.telegram.errors import (
    TelegramAntiSpamRestriction,
    TelegramAuthError,
    TelegramDeliveryUncertain,
    TelegramFloodWait,
    TelegramInvalidRequest,
    TelegramWriteForbidden,
)
from tests.conftest import csrf_headers
from tests.test_api_workflow import create_connection, create_destination


class RaisingGateway:
    """Имитировать Telegram gateway с заданной ошибкой отправки canary."""

    def __init__(self, error: Exception) -> None:
        """Сохранить исключение для следующего send_message."""

        self.error = error

    def send_message(self, **_kwargs: Any) -> Any:
        """Выбросить подготовленную ошибку вместо сетевой отправки."""

        raise self.error


@pytest.mark.parametrize(
    ("error", "expected_status", "expected_code", "connection_status"),
    [
        (TelegramFloodWait("flood", retry_after=45), "blocked", "FLOOD_WAIT", "paused"),
        (
            TelegramWriteForbidden("forbidden"),
            "blocked",
            "CHAT_WRITE_FORBIDDEN",
            "active",
        ),
        (
            TelegramAntiSpamRestriction("spam"),
            "blocked",
            "ANTI_SPAM_RESTRICTION",
            "paused",
        ),
        (TelegramAuthError("revoked"), "blocked", "TELEGRAM_AUTH_INVALID", "error"),
        (
            TelegramDeliveryUncertain("uncertain"),
            "blocked",
            "DELIVERY_RESULT_UNCERTAIN",
            "paused",
        ),
        (
            TelegramInvalidRequest("invalid request"),
            "failed",
            "TELEGRAM_BAD_REQUEST",
            "active",
        ),
        (RuntimeError("unexpected"), "failed", "CANARY_UNEXPECTED_ERROR", "active"),
    ],
)
def test_canary_gateway_failures_are_classified_without_retry(
    auth_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    error: Exception,
    expected_status: str,
    expected_code: str,
    connection_status: str,
) -> None:
    """Проверить устойчивую классификацию всех критичных ошибок canary gateway."""

    connection = create_connection(auth_client, name=f"Canary {expected_code}")
    destination = create_destination(auth_client, connection["id"])
    monkeypatch.setattr(
        "app.services.pilot_canary.build_gateway",
        lambda *_args, **_kwargs: RaisingGateway(error),
    )

    response = auth_client.post(
        "/api/v1/pilot/canaries",
        headers=csrf_headers(auth_client),
        json={
            "destination_id": destination["id"],
            "confirmation": CANARY_CONFIRMATION,
        },
    )

    assert response.status_code == 201, response.text
    assert response.json()["status"] == expected_status
    assert response.json()["error_code"] == expected_code
    with auth_client.app.state.session_factory() as db:
        stored_connection = db.get(TelegramConnection, connection["id"])
        stored_destination = db.get(Destination, destination["id"])
        assert stored_connection is not None and stored_destination is not None
        assert stored_connection.status == ConnectionStatus(connection_status)
        if isinstance(error, TelegramFloodWait):
            assert stored_connection.flood_blocked_until is not None
        if isinstance(error, TelegramWriteForbidden):
            assert stored_destination.last_error_code == "CHAT_WRITE_FORBIDDEN"
            assert stored_destination.consecutive_failures == 1
