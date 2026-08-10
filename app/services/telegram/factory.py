from __future__ import annotations

from app.config import Settings
from app.enums import ConnectionKind
from app.models import TelegramConnection
from app.services.crypto import SecretCipher
from app.services.telegram.base import TelegramGateway
from app.services.telegram.bot_api import BotApiGateway
from app.services.telegram.fake import FakeTelegramGateway
from app.services.telegram.mtproto import MTProtoGateway


def build_gateway(
    connection: TelegramConnection, settings: Settings, cipher: SecretCipher
) -> TelegramGateway:
    """Создать gateway. Перед сохранением или возвратом нового значения проверяются связанные
    инварианты.
    """
    if settings.telegram_fake_mode:
        return FakeTelegramGateway(connection.kind, identity_seed=connection.id)
    if not connection.credentials_enc:
        raise ValueError("У подключения отсутствуют учётные данные")
    credentials = cipher.decrypt_json(
        connection.credentials_enc, context=f"telegram-connection:{connection.id}"
    )
    if connection.kind == ConnectionKind.BOT:
        return BotApiGateway(credentials["bot_token"])
    return MTProtoGateway(
        api_id=int(credentials["api_id"]),
        api_hash=credentials["api_hash"],
        session_string=credentials["session_string"],
    )
