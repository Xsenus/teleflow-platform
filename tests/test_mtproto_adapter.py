from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from app.enums import DestinationKind, ParseMode
from app.services.telegram import mtproto
from app.services.telegram.errors import (
    TelegramAntiSpamRestriction,
    TelegramAuthError,
    TelegramDeliveryUncertain,
    TelegramFloodWait,
    TelegramInvalidRequest,
    TelegramNotFound,
    TelegramSlowModeWait,
    TelegramWriteForbidden,
)


class SecondsError(Exception):
    """Имитировать исключение Telethon с длительностью ограничения."""

    def __init__(self, seconds: int = 17) -> None:
        """Сохранить число секунд ожидания Telegram."""

        super().__init__(f"wait {seconds}")
        self.seconds = seconds


class FakeErrors:
    """Предоставить типы исключений с именами Telethon API."""

    PhoneNumberInvalidError = type("PhoneNumberInvalidError", (Exception,), {})
    PhoneNumberBannedError = type("PhoneNumberBannedError", (Exception,), {})
    FloodWaitError = type("FloodWaitError", (SecondsError,), {})
    SessionPasswordNeededError = type("SessionPasswordNeededError", (Exception,), {})
    PhoneCodeInvalidError = type("PhoneCodeInvalidError", (Exception,), {})
    PhoneCodeExpiredError = type("PhoneCodeExpiredError", (Exception,), {})
    PasswordHashInvalidError = type("PasswordHashInvalidError", (Exception,), {})
    UsernameInvalidError = type("UsernameInvalidError", (Exception,), {})
    UsernameNotOccupiedError = type("UsernameNotOccupiedError", (Exception,), {})
    SlowModeWaitError = type("SlowModeWaitError", (SecondsError,), {})
    ChatWriteForbiddenError = type("ChatWriteForbiddenError", (Exception,), {})
    UserBannedInChannelError = type("UserBannedInChannelError", (Exception,), {})
    ChannelPrivateError = type("ChannelPrivateError", (Exception,), {})
    PeerFloodError = type("PeerFloodError", (Exception,), {})
    AuthKeyUnregisteredError = type("AuthKeyUnregisteredError", (Exception,), {})
    SessionRevokedError = type("SessionRevokedError", (Exception,), {})
    AuthKeyDuplicatedError = type("AuthKeyDuplicatedError", (Exception,), {})


class FakeStringSession:
    """Имитировать сериализованную Telethon-сессию."""

    def __init__(self, value: str = "") -> None:
        """Сохранить исходное значение сессии."""

        self.value = value

    def save(self) -> str:
        """Вернуть детерминированную обновлённую сессию."""

        return f"saved:{self.value or 'new'}"


class FakeUtils:
    """Предоставить вычисление peer id, совместимое с тестовыми entity."""

    @staticmethod
    def get_peer_id(entity: Any) -> int:
        """Вернуть идентификатор тестовой Telegram-сущности."""

        return int(entity.id)


class Chat:
    """Представить обычную Telegram-группу для проверки определения типа."""

    def __init__(self, entity_id: int = -100) -> None:
        """Создать группу с минимальным набором Telethon-полей."""

        self.id = entity_id
        self.broadcast = False
        self.megagroup = False
        self.username = "group"
        self.title = "Group"
        self.forum = False


class FakeClient:
    """Имитировать асинхронный TelegramClient и записывать все вызовы."""

    scenario: dict[str, Any] = {}
    instances: list[FakeClient] = []

    def __init__(self, session: FakeStringSession, *_args: object, **_kwargs: object) -> None:
        """Сохранить сессию и зарегистрировать созданный клиент."""

        self.session = session
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.disconnected = False
        self.__class__.instances.append(self)

    def result(self, name: str, default: Any) -> Any:
        """Вернуть настроенный результат операции либо выбросить исключение."""

        value = self.scenario.get(name, default)
        if isinstance(value, BaseException):
            raise value
        return value

    async def connect(self) -> None:
        """Записать подключение клиента."""

        self.calls.append(("connect", {}))

    async def disconnect(self) -> None:
        """Зафиксировать гарантированное отключение клиента."""

        self.disconnected = True

    async def send_code_request(self, phone: str) -> Any:
        """Вернуть hash кода либо настроенную Telegram-ошибку."""

        self.calls.append(("send_code_request", {"phone": phone}))
        return self.result("send_code_request", SimpleNamespace(phone_code_hash="phone-hash"))

    async def sign_in(self, **kwargs: Any) -> None:
        """Выполнить управляемый вход по коду или 2FA-паролю."""

        self.calls.append(("sign_in", kwargs))
        key = "sign_in_password" if "password" in kwargs else "sign_in_code"
        self.result(key, None)

    async def get_me(self) -> Any:
        """Вернуть управляемую Telegram identity."""

        return self.result(
            "get_me",
            SimpleNamespace(
                id=42, username="tester", first_name="Test", last_name="User", bot=False
            ),
        )

    async def is_user_authorized(self) -> bool:
        """Вернуть состояние авторизации сессии."""

        return bool(self.result("authorized", True))

    async def get_entity(self, target: int | str) -> Any:
        """Разрешить тестовый chat id или username."""

        self.calls.append(("get_entity", {"target": target}))
        return self.result("get_entity", Chat())

    async def iter_dialogs(self) -> Any:
        """Асинхронно перечислить настроенные диалоги."""

        for dialog in self.result("dialogs", []):
            yield dialog

    async def send_message(self, chat_id: int, **kwargs: Any) -> Any:
        """Записать текстовую отправку и вернуть Telegram message."""

        self.calls.append(("send_message", {"chat_id": chat_id, **kwargs}))
        return self.result(
            "send_message",
            SimpleNamespace(id=501, date=datetime(2026, 8, 10, tzinfo=UTC)),
        )

    async def send_file(self, chat_id: int, **kwargs: Any) -> Any:
        """Записать отправку медиа и вернуть Telegram message."""

        self.calls.append(("send_file", {"chat_id": chat_id, **kwargs}))
        return self.result("send_file", SimpleNamespace(id=502, date=None))


@pytest.fixture(autouse=True)
def fake_telethon(monkeypatch: pytest.MonkeyPatch) -> None:
    """Подменить динамические Telethon-импорты для каждого теста."""

    FakeClient.scenario = {}
    FakeClient.instances = []
    monkeypatch.setattr(
        mtproto,
        "_imports",
        lambda: (FakeClient, FakeStringSession, FakeErrors, FakeUtils),
    )


def gateway() -> mtproto.MTProtoGateway:
    """Создать MTProto gateway с безопасными тестовыми реквизитами."""

    return mtproto.MTProtoGateway(api_id=123, api_hash="test-hash", session_string="session")


def test_auth_start_and_complete_with_2fa() -> None:
    """Проверить запрос кода, 2FA и построение identity пользователя."""

    start = mtproto.MTProtoAuth.start(api_id=123, api_hash="hash", phone="+79990000000")
    assert start.phone_code_hash == "phone-hash"
    assert start.session_string == "saved:new"
    assert FakeClient.instances[-1].disconnected is True

    FakeClient.scenario["sign_in_code"] = FakeErrors.SessionPasswordNeededError()
    complete = mtproto.MTProtoAuth.complete(
        api_id=123,
        api_hash="hash",
        phone="+79990000000",
        session_string="seed",
        phone_code_hash="phone-hash",
        code="12345",
        password="2fa-secret",
    )
    assert complete.session_string == "saved:seed"
    assert complete.identity.account_id == 42
    assert complete.identity.display_name == "Test User"
    assert FakeClient.instances[-1].calls[-1][1] == {"password": "2fa-secret"}
    assert FakeClient.instances[-1].disconnected is True


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (FakeErrors.PhoneNumberInvalidError(), TelegramInvalidRequest),
        (FakeErrors.PhoneNumberBannedError(), TelegramAuthError),
        (FakeErrors.FloodWaitError(19), TelegramFloodWait),
    ],
)
def test_auth_start_maps_telegram_errors(error: Exception, expected: type[Exception]) -> None:
    """Проверить доменное отображение ошибок запроса Telegram-кода."""

    FakeClient.scenario["send_code_request"] = error
    with pytest.raises(expected) as caught:
        mtproto.MTProtoAuth.start(api_id=1, api_hash="hash", phone="bad")
    if isinstance(caught.value, TelegramFloodWait):
        assert caught.value.retry_after == 19
    assert FakeClient.instances[-1].disconnected is True


@pytest.mark.parametrize(
    ("key", "error"),
    [
        ("sign_in_code", FakeErrors.PhoneCodeInvalidError()),
        ("sign_in_code", FakeErrors.PhoneCodeExpiredError()),
        ("sign_in_password", FakeErrors.PasswordHashInvalidError()),
        ("sign_in_code", FakeErrors.FloodWaitError(23)),
    ],
)
def test_auth_complete_maps_errors(key: str, error: Exception) -> None:
    """Проверить ошибки кода, 2FA и flood wait при завершении авторизации."""

    FakeClient.scenario["sign_in_code"] = (
        FakeErrors.SessionPasswordNeededError() if key == "sign_in_password" else error
    )
    if key == "sign_in_password":
        FakeClient.scenario[key] = error
    with pytest.raises((TelegramInvalidRequest, TelegramFloodWait)):
        mtproto.MTProtoAuth.complete(
            api_id=1,
            api_hash="hash",
            phone="+7000",
            session_string="seed",
            phone_code_hash="phone-hash",
            code="00000",
            password="wrong",
        )


def test_auth_complete_requires_2fa_and_identity() -> None:
    """Проверить обязательность 2FA-пароля и наличие Telegram identity."""

    FakeClient.scenario["sign_in_code"] = FakeErrors.SessionPasswordNeededError()
    with pytest.raises(TelegramInvalidRequest, match="2FA"):
        mtproto.MTProtoAuth.complete(
            api_id=1,
            api_hash="hash",
            phone="+7000",
            session_string="seed",
            phone_code_hash="hash",
            code="00000",
            password=None,
        )

    FakeClient.scenario = {"get_me": None}
    with pytest.raises(TelegramAuthError, match="данные аккаунта"):
        mtproto.MTProtoAuth.complete(
            api_id=1,
            api_hash="hash",
            phone="+7000",
            session_string="seed",
            phone_code_hash="hash",
            code="00000",
            password=None,
        )


def test_gateway_identity_and_authorization() -> None:
    """Проверить identity авторизованной и отозванной MTProto-сессии."""

    identity = gateway().get_identity()
    assert identity.username == "tester"
    assert identity.is_bot is False

    FakeClient.scenario["authorized"] = False
    with pytest.raises(TelegramAuthError, match="не авторизована"):
        gateway().get_identity()
    assert FakeClient.instances[-1].disconnected is True


@pytest.mark.parametrize(
    ("entity", "topic_id", "expected"),
    [
        (
            SimpleNamespace(
                id=-1, broadcast=True, megagroup=False, username="news", title="News", forum=False
            ),
            None,
            DestinationKind.CHANNEL,
        ),
        (
            SimpleNamespace(
                id=-2, broadcast=False, megagroup=True, username="super", title="Super", forum=True
            ),
            None,
            DestinationKind.SUPERGROUP,
        ),
        (Chat(-3), None, DestinationKind.GROUP),
        (Chat(-4), 7, DestinationKind.FORUM_TOPIC),
    ],
)
def test_resolve_destination_kinds(
    entity: Any, topic_id: int | None, expected: DestinationKind
) -> None:
    """Проверить классификацию channel, supergroup, group и forum topic."""

    FakeClient.scenario["get_entity"] = entity
    result = gateway().resolve_destination(chat_id=None, username="group", topic_id=topic_id)
    assert result.kind is expected
    assert FakeClient.instances[-1].calls[-1][1]["target"] == "@group"


def test_resolve_destination_rejects_missing_private_and_unknown() -> None:
    """Проверить отсутствие target, личный чат и неизвестный username."""

    with pytest.raises(TelegramNotFound, match="Не указан"):
        gateway().resolve_destination(chat_id=None, username=None)

    FakeClient.scenario["get_entity"] = SimpleNamespace(
        id=5, broadcast=False, megagroup=False, username="person", title=None, forum=False
    )
    with pytest.raises(TelegramInvalidRequest, match="Личные чаты"):
        gateway().resolve_destination(chat_id=5, username=None)

    FakeClient.scenario["get_entity"] = FakeErrors.UsernameNotOccupiedError()
    with pytest.raises(TelegramNotFound, match="не найдена"):
        gateway().resolve_destination(chat_id=None, username="missing")


def test_list_destinations_filters_and_classifies_dialogs() -> None:
    """Проверить фильтрацию личных диалогов и классификацию доступных групп."""

    channel = SimpleNamespace(id=-10, broadcast=True, megagroup=False, username="news", forum=False)
    supergroup = SimpleNamespace(
        id=-20, broadcast=False, megagroup=True, username="team", forum=True
    )
    private = SimpleNamespace(id=30, broadcast=False, megagroup=False, username="user")
    FakeClient.scenario["dialogs"] = [
        SimpleNamespace(entity=channel, is_group=False, name="News", id=-10),
        SimpleNamespace(entity=supergroup, is_group=True, name="Team", id=-20),
        SimpleNamespace(entity=private, is_group=False, name="Private", id=30),
    ]

    results = gateway().list_destinations()

    assert [item.kind for item in results] == [DestinationKind.CHANNEL, DestinationKind.SUPERGROUP]
    assert results[1].capabilities["is_forum"] is True


def test_send_text_and_media_payloads(tmp_path: Path) -> None:
    """Проверить payload текстовой и медиа-отправки, parse mode и topic reply."""

    text_result = gateway().send_message(
        chat_id=-100,
        topic_id=7,
        body="<b>Hello</b>",
        parse_mode=ParseMode.HTML,
        link_preview=False,
    )
    assert text_result.message_id == "501"
    assert text_result.date == "2026-08-10T00:00:00+00:00"
    call = FakeClient.instances[-1].calls[-1]
    assert call[0] == "send_message"
    assert call[1]["parse_mode"] == "html"
    assert call[1]["reply_to"] == 7

    media = tmp_path / "image.png"
    media.write_bytes(b"png")
    media_result = gateway().send_message(
        chat_id=-100,
        topic_id=None,
        body="Caption",
        parse_mode=ParseMode.MARKDOWN,
        link_preview=True,
        media_path=media,
        media_content_type="image/png",
    )
    assert media_result.message_id == "502"
    assert media_result.date is None
    call = FakeClient.instances[-1].calls[-1]
    assert call[0] == "send_file"
    assert call[1]["file"] == str(media)
    assert call[1]["parse_mode"] == "md"


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (FakeErrors.FloodWaitError(31), TelegramFloodWait),
        (FakeErrors.SlowModeWaitError(29), TelegramSlowModeWait),
        (FakeErrors.ChatWriteForbiddenError(), TelegramWriteForbidden),
        (FakeErrors.UserBannedInChannelError(), TelegramWriteForbidden),
        (FakeErrors.ChannelPrivateError(), TelegramWriteForbidden),
        (FakeErrors.PeerFloodError(), TelegramAntiSpamRestriction),
        (FakeErrors.AuthKeyUnregisteredError(), TelegramAuthError),
        (FakeErrors.SessionRevokedError(), TelegramAuthError),
        (FakeErrors.AuthKeyDuplicatedError(), TelegramAuthError),
        (TimeoutError(), TelegramDeliveryUncertain),
        (ConnectionError(), TelegramDeliveryUncertain),
        (OSError(), TelegramDeliveryUncertain),
    ],
)
def test_send_maps_transport_errors(error: Exception, expected: type[Exception]) -> None:
    """Проверить классификацию rate limit, access, auth и uncertain-ошибок."""

    FakeClient.scenario["send_message"] = error
    with pytest.raises(expected) as caught:
        gateway().send_message(
            chat_id=-100,
            topic_id=None,
            body="Hello",
            parse_mode=ParseMode.PLAIN,
            link_preview=True,
        )
    if isinstance(caught.value, (TelegramFloodWait, TelegramSlowModeWait)):
        assert caught.value.retry_after in {29, 31}
    assert FakeClient.instances[-1].disconnected is True
