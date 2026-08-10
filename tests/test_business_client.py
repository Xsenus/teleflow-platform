from __future__ import annotations

from typing import Any

import pytest

from app.config import Settings
from app.enums import ConnectionKind
from app.models import TelegramConnection
from app.services.crypto import SecretCipher
from app.services.telegram import business


def connection(
    kind: ConnectionKind, cipher: SecretCipher, *, credentials: bool = True
) -> TelegramConnection:
    """Создать автономную TelegramConnection для тестирования Business client."""

    item = TelegramConnection(id="business-client-test", kind=kind, name="Business Bot")
    if credentials:
        item.credentials_enc = cipher.encrypt_json(
            {"bot_token": "1234567890:test-token-for-business-client"},
            context=f"telegram-connection:{item.id}",
        )
    return item


class FakeBotGateway:
    """Записывать делегированные Business-запросы к Bot API gateway."""

    instances: list[FakeBotGateway] = []

    def __init__(self, token: str, *, timeout_seconds: float) -> None:
        """Сохранить token, timeout и журнал вызовов."""

        self.token = token
        self.timeout_seconds = timeout_seconds
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.__class__.instances.append(self)

    def set_webhook(self, **kwargs: Any) -> bool:
        """Записать установку webhook."""

        self.calls.append(("set_webhook", kwargs))
        return True

    def delete_webhook(self, **kwargs: Any) -> bool:
        """Записать удаление webhook."""

        self.calls.append(("delete_webhook", kwargs))
        return True

    def get_webhook_info(self) -> dict[str, Any]:
        """Вернуть тестовую информацию webhook."""

        self.calls.append(("get_webhook_info", {}))
        return {"url": "https://example.test/hook"}

    def get_business_connection(self, connection_id: str) -> dict[str, Any]:
        """Вернуть тестовое Business-подключение."""

        self.calls.append(("get_business_connection", {"id": connection_id}))
        return {"id": connection_id}

    def send_business_message(self, **kwargs: Any) -> dict[str, Any]:
        """Вернуть результат тестовой Business-отправки."""

        self.calls.append(("send_business_message", kwargs))
        return {"message_id": 77, **kwargs}

    def read_business_message(self, **kwargs: Any) -> bool:
        """Подтвердить тестовую отметку сообщения прочитанным."""

        self.calls.append(("read_business_message", kwargs))
        return True


def test_business_client_fake_mode(settings: Settings) -> None:
    """Проверить детерминированные webhook, lookup, send и read без Telegram-сети."""

    cipher = SecretCipher(settings.master_key)
    client = business.BusinessBotClient(connection(ConnectionKind.BOT, cipher), settings, cipher)

    assert client.credentials["bot_token"].startswith("1234567890:")
    assert client.set_webhook(
        url="https://example.test/hook", secret_token="secret", drop_pending_updates=False
    )
    assert client.delete_webhook(drop_pending_updates=True)
    assert client.get_webhook_info()["url"] == "fake://telegram-webhook"
    profile = client.get_business_connection("bc-fake")
    assert profile["id"] == "bc-fake" and profile["can_reply"] is True
    sent = client.send_message(
        business_connection_id="bc-fake", chat_id=123, text="Ответ", reply_to_message_id=5
    )
    assert sent["business_connection_id"] == "bc-fake"
    assert sent["chat"]["id"] == 123
    assert client.read_message(business_connection_id="bc-fake", chat_id=123, message_id=5)


def test_business_client_real_mode_delegates_to_gateway(
    monkeypatch: pytest.MonkeyPatch, settings: Settings
) -> None:
    """Проверить точное делегирование production-веток в BotApiGateway."""

    FakeBotGateway.instances = []
    monkeypatch.setattr(business, "BotApiGateway", FakeBotGateway)
    real_settings = settings.model_copy(update={"telegram_fake_mode": False})
    cipher = SecretCipher(real_settings.master_key)
    client = business.BusinessBotClient(
        connection(ConnectionKind.BOT, cipher), real_settings, cipher
    )
    gateway = FakeBotGateway.instances[0]

    assert gateway.token.startswith("1234567890:")
    assert gateway.timeout_seconds == real_settings.webhook_timeout_seconds
    assert client.set_webhook(
        url="https://example.test/hook", secret_token="secret", drop_pending_updates=True
    )
    assert gateway.calls[-1][1]["allowed_updates"] == business.ALLOWED_BUSINESS_UPDATES
    assert client.delete_webhook()
    assert client.get_webhook_info()["url"].startswith("https://")
    assert client.get_business_connection("bc-real") == {"id": "bc-real"}
    assert (
        client.send_message(
            business_connection_id="bc-real", chat_id=9, text="Hello", reply_to_message_id=4
        )["message_id"]
        == 77
    )
    assert client.read_message(business_connection_id="bc-real", chat_id=9, message_id=4)
    assert [name for name, _payload in gateway.calls] == [
        "set_webhook",
        "delete_webhook",
        "get_webhook_info",
        "get_business_connection",
        "send_business_message",
        "read_business_message",
    ]


def test_business_client_validates_connection_and_credentials(settings: Settings) -> None:
    """Проверить отказ для user connection и Bot API-подключения без секрета."""

    cipher = SecretCipher(settings.master_key)
    with pytest.raises(ValueError, match="только Bot API"):
        business.BusinessBotClient(connection(ConnectionKind.USER, cipher), settings, cipher)

    client = business.BusinessBotClient(
        connection(ConnectionKind.BOT, cipher, credentials=False), settings, cipher
    )
    with pytest.raises(ValueError, match="отсутствуют"):
        _ = client.credentials
