from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.enums import DestinationKind, ParseMode


@dataclass(frozen=True)
class TelegramIdentity:
    account_id: int
    username: str | None
    display_name: str
    is_bot: bool


@dataclass(frozen=True)
class TelegramDestinationInfo:
    chat_id: int
    username: str | None
    title: str
    kind: DestinationKind
    capabilities: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class TelegramSendResult:
    message_id: str
    date: str | None = None


class TelegramGateway(ABC):
    @abstractmethod
    def get_identity(self) -> TelegramIdentity:
        """Прочитать identity класса TelegramGateway. Значение возвращается без несвязанных
        изменений состояния.
        """
        raise NotImplementedError

    @abstractmethod
    def resolve_destination(
        self, *, chat_id: int | None, username: str | None, topic_id: int | None = None
    ) -> TelegramDestinationInfo:
        """Прочитать destination класса TelegramGateway. Значение возвращается без несвязанных
        изменений состояния.
        """
        raise NotImplementedError

    @abstractmethod
    def list_destinations(self) -> list[TelegramDestinationInfo]:
        """Прочитать destinations класса TelegramGateway. Значение возвращается без несвязанных
        изменений состояния.
        """
        raise NotImplementedError

    @abstractmethod
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
        """Выполнить операцию send message класса TelegramGateway. Аргументы интерпретируются в
        контексте модуля, результат возвращается вызывающему коду.
        """
        raise NotImplementedError
