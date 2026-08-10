from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from typing import Any

from app.config import Settings
from app.enums import ConnectionKind
from app.models import TelegramConnection
from app.services.crypto import SecretCipher
from app.services.telegram.bot_api import BotApiGateway

ALLOWED_BUSINESS_UPDATES = [
    "business_connection",
    "business_message",
    "edited_business_message",
    "deleted_business_messages",
]


class BusinessBotClient:
    def __init__(self, connection: TelegramConnection, settings: Settings, cipher: SecretCipher):
        """Инициализировать BusinessBotClient with its explicit dependencies. Сохраняется только
        состояние, необходимое последующим операциям.
        """
        if connection.kind != ConnectionKind.BOT:
            raise ValueError("Telegram Business поддерживается только Bot API-подключением")
        self.connection = connection
        self.settings = settings
        self.cipher = cipher
        self.fake = settings.telegram_fake_mode
        self._gateway: BotApiGateway | None = None
        if not self.fake:
            credentials = self.credentials
            self._gateway = BotApiGateway(
                credentials["bot_token"], timeout_seconds=settings.webhook_timeout_seconds
            )

    @property
    def credentials(self) -> dict[str, Any]:
        """Выполнить операцию credentials класса BusinessBotClient. Аргументы интерпретируются в
        контексте модуля, результат возвращается вызывающему коду.
        """
        if not self.connection.credentials_enc:
            raise ValueError("У бота отсутствуют учётные данные")
        return self.cipher.decrypt_json(
            self.connection.credentials_enc,
            context=f"telegram-connection:{self.connection.id}",
        )

    def set_webhook(
        self,
        *,
        url: str,
        secret_token: str,
        drop_pending_updates: bool,
    ) -> bool:
        """Обновить webhook класса BusinessBotClient. Переход применяется только после проверки его
        предусловий.
        """
        if self.fake:
            return True
        assert self._gateway is not None
        return self._gateway.set_webhook(
            url=url,
            secret_token=secret_token,
            allowed_updates=ALLOWED_BUSINESS_UPDATES,
            drop_pending_updates=drop_pending_updates,
        )

    def delete_webhook(self, *, drop_pending_updates: bool = False) -> bool:
        """Безопасно выполнить delete webhook класса BusinessBotClient. Зависимое состояние и
        видимые в аудите последствия обрабатываются согласованно.
        """
        if self.fake:
            return True
        assert self._gateway is not None
        return self._gateway.delete_webhook(drop_pending_updates=drop_pending_updates)

    def get_webhook_info(self) -> dict[str, Any]:
        """Прочитать webhook info класса BusinessBotClient. Значение возвращается без несвязанных
        изменений состояния.
        """
        if self.fake:
            return {"url": "fake://telegram-webhook", "pending_update_count": 0}
        assert self._gateway is not None
        return self._gateway.get_webhook_info()

    def get_business_connection(self, business_connection_id: str) -> dict[str, Any]:
        """Прочитать business connection класса BusinessBotClient. Значение возвращается без
        несвязанных изменений состояния.
        """
        if self.fake:
            seed = int(hashlib.sha256(business_connection_id.encode()).hexdigest()[:12], 16)
            return {
                "id": business_connection_id,
                "user": {
                    "id": seed,
                    "first_name": "Test Business User",
                    "username": "test_business_user",
                },
                "user_chat_id": seed + 1,
                "date": int(datetime.now(UTC).timestamp()),
                "can_reply": True,
                "is_enabled": True,
                "rights": {"can_reply": True},
            }
        assert self._gateway is not None
        return self._gateway.get_business_connection(business_connection_id)

    def send_message(
        self,
        *,
        business_connection_id: str,
        chat_id: int,
        text: str,
        reply_to_message_id: int | None = None,
    ) -> dict[str, Any]:
        """Выполнить операцию send message класса BusinessBotClient. Аргументы интерпретируются в
        контексте модуля, результат возвращается вызывающему коду.
        """
        if self.fake:
            digest = hashlib.sha256(
                f"{business_connection_id}:{chat_id}:{text}".encode()
            ).hexdigest()[:12]
            return {
                "message_id": int(digest, 16) % 2_000_000_000,
                "date": int(datetime.now(UTC).timestamp()),
                "chat": {"id": chat_id, "type": "private"},
                "text": text,
                "business_connection_id": business_connection_id,
            }
        assert self._gateway is not None
        return self._gateway.send_business_message(
            business_connection_id=business_connection_id,
            chat_id=chat_id,
            text=text,
            reply_to_message_id=reply_to_message_id,
        )

    def read_message(
        self,
        *,
        business_connection_id: str,
        chat_id: int,
        message_id: int,
    ) -> bool:
        """Прочитать message класса BusinessBotClient. Значение возвращается без несвязанных
        изменений состояния.
        """
        if self.fake:
            return True
        assert self._gateway is not None
        return self._gateway.read_business_message(
            business_connection_id=business_connection_id,
            chat_id=chat_id,
            message_id=message_id,
        )
