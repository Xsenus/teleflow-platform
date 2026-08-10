from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path

from app.enums import ConnectionKind, DestinationKind, ParseMode
from app.services.telegram.base import (
    TelegramDestinationInfo,
    TelegramGateway,
    TelegramIdentity,
    TelegramSendResult,
)
from app.services.telegram.errors import TelegramNotFound


class FakeTelegramGateway(TelegramGateway):
    """Детерминированный Telegram-транспорт для разработки и автотестов."""

    def __init__(self, kind: ConnectionKind, identity_seed: str = "teleflow"):
        """Инициализировать FakeTelegramGateway with its explicit dependencies. Сохраняется только
        состояние, необходимое последующим операциям.
        """
        self.kind = kind
        self.identity_seed = identity_seed

    def get_identity(self) -> TelegramIdentity:
        """Прочитать identity класса FakeTelegramGateway. Значение возвращается без несвязанных
        изменений состояния.
        """
        account_id = int(hashlib.sha256(self.identity_seed.encode()).hexdigest()[:12], 16)
        is_bot = self.kind == ConnectionKind.BOT
        return TelegramIdentity(
            account_id=account_id,
            username="teleflow_test_bot" if is_bot else "teleflow_test_user",
            display_name="TeleFlow Test Bot" if is_bot else "TeleFlow Test User",
            is_bot=is_bot,
        )

    def resolve_destination(
        self, *, chat_id: int | None, username: str | None, topic_id: int | None = None
    ) -> TelegramDestinationInfo:
        """Прочитать destination класса FakeTelegramGateway. Значение возвращается без несвязанных
        изменений состояния.
        """
        if chat_id is None and not username:
            raise TelegramNotFound("Не указан идентификатор назначения")
        # Проверяем именно ``None``: нулевой chat_id некорректен для Telegram,
        # но не должен приводить к вызову ``encode`` у отсутствующего username.
        if chat_id is not None:
            resolved_id = chat_id
        else:
            assert username is not None  # Проверено общей валидацией выше.
            resolved_id = -int(hashlib.sha256(username.encode()).hexdigest()[:10], 16)
        title = f"Тестовая группа {username or resolved_id}"
        kind = DestinationKind.FORUM_TOPIC if topic_id else DestinationKind.SUPERGROUP
        return TelegramDestinationInfo(
            chat_id=resolved_id,
            username=username.removeprefix("@") if username else None,
            title=title,
            kind=kind,
            capabilities={"can_send_messages": True, "fake": True},
        )

    def list_destinations(self) -> list[TelegramDestinationInfo]:
        """Прочитать destinations класса FakeTelegramGateway. Значение возвращается без несвязанных
        изменений состояния.
        """
        return [
            TelegramDestinationInfo(
                chat_id=-100000000001,
                username="test_jobs_one",
                title="Тестовая группа вакансий 1",
                kind=DestinationKind.SUPERGROUP,
                capabilities={"can_send_messages": True, "fake": True},
            ),
            TelegramDestinationInfo(
                chat_id=-100000000002,
                username="test_jobs_two",
                title="Тестовая группа вакансий 2",
                kind=DestinationKind.SUPERGROUP,
                capabilities={"can_send_messages": True, "fake": True},
            ),
        ]

    def send_message(
        self,
        *,
        chat_id: int,
        topic_id: int | None,
        body: str,
        parse_mode: ParseMode,
        link_preview: bool,
        media_path: Path | None = None,
        media_content_type: str | None = None,
    ) -> TelegramSendResult:
        """Выполнить операцию send message класса FakeTelegramGateway. Аргументы интерпретируются в
        контексте модуля, результат возвращается вызывающему коду.
        """
        digest = hashlib.sha256(f"{chat_id}:{topic_id}:{body}:{media_path}".encode()).hexdigest()[
            :16
        ]
        return TelegramSendResult(message_id=f"fake-{digest}", date=datetime.now(UTC).isoformat())
