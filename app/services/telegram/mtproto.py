from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.enums import DestinationKind, ParseMode
from app.services.telegram.base import (
    TelegramDestinationInfo,
    TelegramGateway,
    TelegramIdentity,
    TelegramSendResult,
)
from app.services.telegram.errors import (
    TelegramAntiSpamRestriction,
    TelegramAuthError,
    TelegramDeliveryUncertain,
    TelegramDependencyMissing,
    TelegramFloodWait,
    TelegramInvalidRequest,
    TelegramNotFound,
    TelegramSlowModeWait,
    TelegramWriteForbidden,
)

_CLIENT_METADATA = {
    "device_model": "TeleFlow Platform",
    "system_version": "Server",
    "app_version": "2.5.0",
}


def _run[T](coro: Coroutine[Any, Any, T]) -> T:
    """Реализовать внутренний этап run step. Вспомогательная функция сохраняет детерминированность
    и тестируемость процесса.
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    raise RuntimeError("MTProto gateway должен вызываться из синхронного worker-потока")


def _imports() -> tuple[Any, Any, Any, Any]:
    """Реализовать внутренний этап imports step. Вспомогательная функция сохраняет
    детерминированность и тестируемость процесса.
    """
    try:
        from telethon import TelegramClient, errors, utils
        from telethon.sessions import StringSession
    except ImportError as exc:
        raise TelegramDependencyMissing(
            "Установите Telethon для работы с пользовательским аккаунтом"
        ) from exc
    return TelegramClient, StringSession, errors, utils


@dataclass(frozen=True)
class AuthStartResult:
    session_string: str
    phone_code_hash: str


@dataclass(frozen=True)
class AuthCompleteResult:
    session_string: str
    identity: TelegramIdentity


class MTProtoAuth:
    @staticmethod
    def start(*, api_id: int, api_hash: str, phone: str) -> AuthStartResult:
        """Выполнить операцию start класса MTProtoAuth. Аргументы интерпретируются в контексте
        модуля, результат возвращается вызывающему коду.
        """
        return _run(MTProtoAuth._start(api_id=api_id, api_hash=api_hash, phone=phone))

    @staticmethod
    async def _start(*, api_id: int, api_hash: str, phone: str) -> AuthStartResult:
        """Реализовать внутренний этап start step класса MTProtoAuth. Вспомогательная функция
        сохраняет детерминированность и тестируемость процесса.
        """
        TelegramClient, StringSession, errors, _utils = _imports()
        client = TelegramClient(
            StringSession(),
            api_id,
            api_hash,
            **_CLIENT_METADATA,
        )
        try:
            await client.connect()
            sent = await client.send_code_request(phone)
            session_string = client.session.save()
            return AuthStartResult(
                session_string=session_string, phone_code_hash=sent.phone_code_hash
            )
        except errors.PhoneNumberInvalidError as exc:
            raise TelegramInvalidRequest("Некорректный номер Telegram") from exc
        except errors.PhoneNumberBannedError as exc:
            raise TelegramAuthError("Номер заблокирован Telegram") from exc
        except errors.FloodWaitError as exc:
            raise TelegramFloodWait(
                "Telegram временно ограничил запрос кодов", retry_after=int(exc.seconds)
            ) from exc
        finally:
            await client.disconnect()

    @staticmethod
    def complete(
        *,
        api_id: int,
        api_hash: str,
        phone: str,
        session_string: str,
        phone_code_hash: str,
        code: str,
        password: str | None,
    ) -> AuthCompleteResult:
        """Выполнить операцию complete класса MTProtoAuth. Аргументы интерпретируются в контексте
        модуля, результат возвращается вызывающему коду.
        """
        return _run(
            MTProtoAuth._complete(
                api_id=api_id,
                api_hash=api_hash,
                phone=phone,
                session_string=session_string,
                phone_code_hash=phone_code_hash,
                code=code,
                password=password,
            )
        )

    @staticmethod
    async def _complete(
        *,
        api_id: int,
        api_hash: str,
        phone: str,
        session_string: str,
        phone_code_hash: str,
        code: str,
        password: str | None,
    ) -> AuthCompleteResult:
        """Реализовать внутренний этап complete step класса MTProtoAuth. Вспомогательная функция
        сохраняет детерминированность и тестируемость процесса.
        """
        TelegramClient, StringSession, errors, _utils = _imports()
        client = TelegramClient(
            StringSession(session_string),
            api_id,
            api_hash,
            **_CLIENT_METADATA,
        )
        try:
            await client.connect()
            try:
                await client.sign_in(phone=phone, code=code, phone_code_hash=phone_code_hash)
            except errors.SessionPasswordNeededError:
                if not password:
                    raise TelegramInvalidRequest(
                        "Для аккаунта требуется пароль 2FA Telegram"
                    ) from None
                await client.sign_in(password=password)
            me = await client.get_me()
            if me is None:
                raise TelegramAuthError("Telegram не вернул данные аккаунта")
            identity = TelegramIdentity(
                account_id=int(me.id),
                username=getattr(me, "username", None),
                display_name=" ".join(
                    part
                    for part in [getattr(me, "first_name", None), getattr(me, "last_name", None)]
                    if part
                )
                or str(me.id),
                is_bot=bool(getattr(me, "bot", False)),
            )
            return AuthCompleteResult(session_string=client.session.save(), identity=identity)
        except errors.PhoneCodeInvalidError as exc:
            raise TelegramInvalidRequest("Неверный код подтверждения Telegram") from exc
        except errors.PhoneCodeExpiredError as exc:
            raise TelegramInvalidRequest("Код подтверждения Telegram истёк") from exc
        except errors.PasswordHashInvalidError as exc:
            raise TelegramInvalidRequest("Неверный пароль 2FA Telegram") from exc
        except errors.FloodWaitError as exc:
            raise TelegramFloodWait(
                "Telegram потребовал сделать паузу", retry_after=int(exc.seconds)
            ) from exc
        finally:
            await client.disconnect()


class MTProtoGateway(TelegramGateway):
    def __init__(self, *, api_id: int, api_hash: str, session_string: str):
        """Инициализировать MTProtoGateway with its explicit dependencies. Сохраняется только
        состояние, необходимое последующим операциям.
        """
        self.api_id = api_id
        self.api_hash = api_hash
        self.session_string = session_string

    def get_identity(self) -> TelegramIdentity:
        """Прочитать identity класса MTProtoGateway. Значение возвращается без несвязанных
        изменений состояния.
        """
        return _run(self._get_identity())

    async def _get_identity(self) -> TelegramIdentity:
        """Реализовать внутренний этап get identity step класса MTProtoGateway. Вспомогательная
        функция сохраняет детерминированность и тестируемость процесса.
        """
        TelegramClient, StringSession, _errors, _utils = _imports()
        client = TelegramClient(
            StringSession(self.session_string), self.api_id, self.api_hash, **_CLIENT_METADATA
        )
        try:
            await client.connect()
            if not await client.is_user_authorized():
                raise TelegramAuthError("MTProto-сессия больше не авторизована")
            me = await client.get_me()
            return TelegramIdentity(
                account_id=int(me.id),
                username=getattr(me, "username", None),
                display_name=" ".join(
                    part
                    for part in [getattr(me, "first_name", None), getattr(me, "last_name", None)]
                    if part
                )
                or str(me.id),
                is_bot=bool(getattr(me, "bot", False)),
            )
        finally:
            await client.disconnect()

    def resolve_destination(
        self, *, chat_id: int | None, username: str | None, topic_id: int | None = None
    ) -> TelegramDestinationInfo:
        """Прочитать destination класса MTProtoGateway. Значение возвращается без несвязанных
        изменений состояния.
        """
        return _run(
            self._resolve_destination(chat_id=chat_id, username=username, topic_id=topic_id)
        )

    async def _resolve_destination(
        self, *, chat_id: int | None, username: str | None, topic_id: int | None
    ) -> TelegramDestinationInfo:
        """Реализовать внутренний этап resolve destination step класса MTProtoGateway.
        Вспомогательная функция сохраняет детерминированность и тестируемость процесса.
        """
        TelegramClient, StringSession, errors, utils = _imports()
        client = TelegramClient(
            StringSession(self.session_string), self.api_id, self.api_hash, **_CLIENT_METADATA
        )
        try:
            await client.connect()
            if not await client.is_user_authorized():
                raise TelegramAuthError("MTProto-сессия больше не авторизована")
            target: int | str
            if chat_id is not None:
                target = chat_id
            elif username:
                target = username if username.startswith("@") else f"@{username}"
            else:
                raise TelegramNotFound("Не указан чат")
            entity = await client.get_entity(target)
            peer_id = int(utils.get_peer_id(entity))
            is_channel = bool(getattr(entity, "broadcast", False))
            is_group = bool(getattr(entity, "megagroup", False))
            if is_channel and not is_group:
                kind = DestinationKind.CHANNEL
            elif is_group:
                kind = DestinationKind.SUPERGROUP
            elif entity.__class__.__name__.lower().endswith("chat"):
                kind = DestinationKind.GROUP
            else:
                raise TelegramInvalidRequest("Личные чаты не поддерживаются как назначения")
            if topic_id:
                kind = DestinationKind.FORUM_TOPIC
            return TelegramDestinationInfo(
                chat_id=peer_id,
                username=getattr(entity, "username", None),
                title=getattr(entity, "title", None) or str(peer_id),
                kind=kind,
                capabilities={
                    "can_send_messages": True,
                    "is_forum": bool(getattr(entity, "forum", False)),
                },
            )
        except (ValueError, errors.UsernameInvalidError, errors.UsernameNotOccupiedError) as exc:
            raise TelegramNotFound("Группа не найдена или недоступна аккаунту") from exc
        finally:
            await client.disconnect()

    def list_destinations(self) -> list[TelegramDestinationInfo]:
        """Прочитать destinations класса MTProtoGateway. Значение возвращается без несвязанных
        изменений состояния.
        """
        return _run(self._list_destinations())

    async def _list_destinations(self) -> list[TelegramDestinationInfo]:
        """Реализовать внутренний этап list destinations step класса MTProtoGateway.
        Вспомогательная функция сохраняет детерминированность и тестируемость процесса.
        """
        TelegramClient, StringSession, _errors, utils = _imports()
        client = TelegramClient(
            StringSession(self.session_string), self.api_id, self.api_hash, **_CLIENT_METADATA
        )
        results: list[TelegramDestinationInfo] = []
        try:
            await client.connect()
            if not await client.is_user_authorized():
                raise TelegramAuthError("MTProto-сессия больше не авторизована")
            async for dialog in client.iter_dialogs():
                entity = dialog.entity
                is_channel = bool(getattr(entity, "broadcast", False))
                is_group = bool(getattr(entity, "megagroup", False)) or bool(dialog.is_group)
                if not (is_channel or is_group):
                    continue
                if is_channel and not is_group:
                    kind = DestinationKind.CHANNEL
                elif bool(getattr(entity, "megagroup", False)):
                    kind = DestinationKind.SUPERGROUP
                else:
                    kind = DestinationKind.GROUP
                results.append(
                    TelegramDestinationInfo(
                        chat_id=int(utils.get_peer_id(entity)),
                        username=getattr(entity, "username", None),
                        title=dialog.name or str(dialog.id),
                        kind=kind,
                        capabilities={
                            "can_send_messages": True,
                            "is_forum": bool(getattr(entity, "forum", False)),
                        },
                    )
                )
            return results
        finally:
            await client.disconnect()

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
        """Выполнить операцию send message класса MTProtoGateway. Аргументы интерпретируются в
        контексте модуля, результат возвращается вызывающему коду.
        """
        return _run(
            self._send_message(
                chat_id=chat_id,
                topic_id=topic_id,
                body=body,
                parse_mode=parse_mode,
                link_preview=link_preview,
                media_path=media_path,
            )
        )

    async def _send_message(
        self,
        *,
        chat_id: int,
        topic_id: int | None,
        body: str,
        parse_mode: ParseMode,
        link_preview: bool,
        media_path: Path | None,
    ) -> TelegramSendResult:
        """Реализовать внутренний этап send message step класса MTProtoGateway. Вспомогательная
        функция сохраняет детерминированность и тестируемость процесса.
        """
        TelegramClient, StringSession, errors, _utils = _imports()
        client = TelegramClient(
            StringSession(self.session_string), self.api_id, self.api_hash, **_CLIENT_METADATA
        )
        parse_value = {
            ParseMode.PLAIN: None,
            ParseMode.HTML: "html",
            ParseMode.MARKDOWN: "md",
        }[parse_mode]
        try:
            await client.connect()
            if not await client.is_user_authorized():
                raise TelegramAuthError("MTProto-сессия больше не авторизована")
            if media_path:
                message = await client.send_file(
                    chat_id,
                    file=str(media_path),
                    caption=body,
                    parse_mode=parse_value,
                    reply_to=topic_id,
                )
            else:
                message = await client.send_message(
                    chat_id,
                    message=body,
                    parse_mode=parse_value,
                    link_preview=link_preview,
                    reply_to=topic_id,
                )
            return TelegramSendResult(
                message_id=str(message.id),
                date=message.date.isoformat() if getattr(message, "date", None) else None,
            )
        except errors.FloodWaitError as exc:
            raise TelegramFloodWait(
                "Telegram потребовал остановить отправку", retry_after=int(exc.seconds)
            ) from exc
        except errors.SlowModeWaitError as exc:
            raise TelegramSlowModeWait(
                "В группе включён медленный режим", retry_after=int(exc.seconds)
            ) from exc
        except (
            errors.ChatWriteForbiddenError,
            errors.UserBannedInChannelError,
            errors.ChannelPrivateError,
        ) as exc:
            raise TelegramWriteForbidden("Аккаунту запрещено писать в эту группу") from exc
        except errors.PeerFloodError as exc:
            raise TelegramAntiSpamRestriction(
                "Telegram применил антиспам-ограничение к аккаунту"
            ) from exc
        except (
            errors.AuthKeyUnregisteredError,
            errors.SessionRevokedError,
            errors.AuthKeyDuplicatedError,
        ) as exc:
            raise TelegramAuthError("MTProto-сессия отозвана или недействительна") from exc
        except (TimeoutError, ConnectionError, OSError) as exc:
            raise TelegramDeliveryUncertain(
                "Не удалось подтвердить результат MTProto-отправки; проверьте чат вручную"
            ) from exc
        finally:
            await client.disconnect()
