from __future__ import annotations

import json
import logging
import sys

from app.config import Settings
from app.enums import ConnectionKind
from app.models import TelegramConnection
from app.services.crypto import SecretCipher
from app.services.logging import JsonFormatter, configure_logging
from app.services.telegram.bot_api import BotApiGateway
from app.services.telegram.factory import build_gateway
from app.services.telegram.fake import FakeTelegramGateway
from app.services.telegram.mtproto import MTProtoGateway


def test_json_formatter_includes_context_and_exception() -> None:
    """Проверить JSON log envelope, context fields, Unicode и traceback."""

    try:
        raise ValueError("ошибка формата")
    except ValueError:
        exc_info = sys.exc_info()
    record = logging.LogRecord(
        name="teleflow.test",
        level=logging.ERROR,
        pathname=__file__,
        lineno=20,
        msg="Событие %s",
        args=("записано",),
        exc_info=exc_info,
    )
    record.request_id = "request-1"
    record.organization_id = "organization-1"
    record.job_id = "job-1"

    payload = json.loads(JsonFormatter().format(record))

    assert payload["level"] == "ERROR"
    assert payload["logger"] == "teleflow.test"
    assert payload["message"] == "Событие записано"
    assert payload["request_id"] == "request-1"
    assert payload["organization_id"] == "organization-1"
    assert payload["job_id"] == "job-1"
    assert "ValueError: ошибка формата" in payload["exception"]
    assert payload["timestamp"].endswith("+00:00")


def test_configure_logging_json_text_and_level_fallback() -> None:
    """Проверить замену handlers, JSON/text formatter и безопасный уровень INFO."""

    root = logging.getLogger()
    original_handlers = list(root.handlers)
    original_level = root.level
    try:
        configure_logging("debug", "json")
        assert root.level == logging.DEBUG
        assert len(root.handlers) == 1
        assert isinstance(root.handlers[0].formatter, JsonFormatter)
        assert logging.getLogger("httpx").level == logging.WARNING
        assert logging.getLogger("httpcore").level == logging.WARNING

        configure_logging("not-a-level", "text")
        assert root.level == logging.INFO
        assert len(root.handlers) == 1
        assert isinstance(root.handlers[0].formatter, logging.Formatter)
        assert not isinstance(root.handlers[0].formatter, JsonFormatter)
    finally:
        root.handlers.clear()
        root.handlers.extend(original_handlers)
        root.setLevel(original_level)


def telegram_connection(kind: ConnectionKind, *, connection_id: str) -> TelegramConnection:
    """Создать автономное Telegram-подключение для factory-тестов."""

    return TelegramConnection(id=connection_id, kind=kind, name=f"Test {kind.value}")


def test_gateway_factory_fake_and_missing_credentials(settings: Settings) -> None:
    """Проверить fake routing и отказ production gateway без credentials."""

    cipher = SecretCipher(settings.master_key)
    connection = telegram_connection(ConnectionKind.USER, connection_id="fake-user")
    fake = build_gateway(connection, settings, cipher)
    assert isinstance(fake, FakeTelegramGateway)
    assert fake.get_identity().is_bot is False

    real_settings = settings.model_copy(update={"telegram_fake_mode": False})
    try:
        build_gateway(connection, real_settings, cipher)
    except ValueError as exc:
        assert "учётные данные" in str(exc)
    else:  # pragma: no cover - явная защита factory-контракта
        raise AssertionError("Production gateway без credentials должен быть отклонён")


def test_gateway_factory_builds_bot_and_mtproto(settings: Settings) -> None:
    """Проверить расшифрование и выбор Bot API/MTProto production adapters."""

    real_settings = settings.model_copy(update={"telegram_fake_mode": False})
    cipher = SecretCipher(real_settings.master_key)
    bot = telegram_connection(ConnectionKind.BOT, connection_id="bot-factory")
    bot.credentials_enc = cipher.encrypt_json(
        {"bot_token": "1234567890:factory-test-token"},
        context=f"telegram-connection:{bot.id}",
    )
    bot_gateway = build_gateway(bot, real_settings, cipher)
    assert isinstance(bot_gateway, BotApiGateway)
    assert bot_gateway._base_url.endswith("1234567890:factory-test-token")

    user = telegram_connection(ConnectionKind.USER, connection_id="user-factory")
    user.credentials_enc = cipher.encrypt_json(
        {
            "api_id": "123456",
            "api_hash": "factory-api-hash",
            "session_string": "factory-session",
        },
        context=f"telegram-connection:{user.id}",
    )
    mtproto_gateway = build_gateway(user, real_settings, cipher)
    assert isinstance(mtproto_gateway, MTProtoGateway)
    assert mtproto_gateway.api_id == 123456
    assert mtproto_gateway.api_hash == "factory-api-hash"
    assert mtproto_gateway.session_string == "factory-session"
