from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.enums import (
    BusinessConnectionStatus,
    ConsentStatus,
    ConversationStatus,
    InboundUpdateStatus,
)
from app.models import (
    Conversation,
    ConversationMessage,
    InboundTelegramUpdate,
    TelegramBusinessConnection,
)
from app.services.inbound import InboundService
from tests.conftest import csrf_headers
from tests.test_business_automation import (
    _business_connection_update,
    _business_message,
    _configure_business,
    _post_update,
)


def inbound_service(client: TestClient) -> InboundService:
    """Создать inbound worker на зависимостях тестового приложения."""
    return InboundService(
        client.app.state.session_factory,
        client.app.state.settings,
        client.app.state.cipher,
        worker_id="inbound-lifecycle-edge",
    )


def create_policy(
    client: TestClient,
    connection_id: str,
    *,
    allowed_chat_types: list[str],
    require_consent: bool = False,
) -> dict:
    """Создать минимальную rule-based policy для проверки защитных ветвей inbound."""
    provider = client.post(
        "/api/v1/automation/providers",
        headers=csrf_headers(client),
        json={
            "name": "Inbound edge provider",
            "kind": "rule_based",
            "enabled": True,
            "system_prompt": "Безопасно обрабатывай тестовые сообщения.",
        },
    )
    assert provider.status_code == 201, provider.text
    policy = client.post(
        "/api/v1/automation/policies",
        headers=csrf_headers(client),
        json={
            "name": "Inbound edge policy",
            "telegram_connection_id": connection_id,
            "ai_provider_config_id": provider.json()["id"],
            "enabled": True,
            "timezone_name": "UTC",
            "active_hours": {},
            "allowed_chat_types": allowed_chat_types,
            "consent_notice": "Ответьте «да», если согласны на автоматическую обработку.",
            "fallback_message": "Диалог передан оператору.",
            "max_auto_replies_per_day": 20,
            "require_consent_before_ai": require_consent,
            "handoff_keywords": ["оператор"],
            "stop_words": ["стоп"],
            "vacancy_detection_rules": {},
        },
    )
    assert policy.status_code == 201, policy.text
    return policy.json()


def test_business_connection_disable_ignore_and_reconnect(auth_client: TestClient) -> None:
    """Проверить disconnect, игнорирование сообщения и повторное включение Business connection."""
    connection, path_token, secret = _configure_business(auth_client)
    service = inbound_service(auth_client)

    first = _business_connection_update(10001)
    first["business_connection"]["id"] = "bc-lifecycle"
    _post_update(auth_client, connection["id"], path_token, secret, first)
    assert service.process_next() is True

    disabled = _business_connection_update(10002)
    disabled["business_connection"].update({"id": "bc-lifecycle", "is_enabled": False})
    _post_update(auth_client, connection["id"], path_token, secret, disabled)
    assert service.process_next() is True
    with auth_client.app.state.session_factory() as db:
        business = db.scalar(select(TelegramBusinessConnection))
        assert business is not None
        assert business.status == BusinessConnectionStatus.DISCONNECTED
        assert business.disconnected_at is not None

    ignored_message = _business_message(10003, 501, "Сообщение при отключённом Business")
    ignored_message["business_message"]["business_connection_id"] = "bc-lifecycle"
    _post_update(auth_client, connection["id"], path_token, secret, ignored_message)
    assert service.process_next() is True
    with auth_client.app.state.session_factory() as db:
        update = db.scalar(
            select(InboundTelegramUpdate).where(InboundTelegramUpdate.telegram_update_id == 10003)
        )
        assert update is not None and update.status == InboundUpdateStatus.IGNORED
        assert db.scalar(select(Conversation)) is None

    enabled = _business_connection_update(10004)
    enabled["business_connection"].update(
        {"id": "bc-lifecycle", "is_enabled": True, "user_chat_id": 999001}
    )
    _post_update(auth_client, connection["id"], path_token, secret, enabled)
    assert service.process_next() is True
    with auth_client.app.state.session_factory() as db:
        business = db.scalar(select(TelegramBusinessConnection))
        assert business is not None
        assert business.status == BusinessConnectionStatus.ACTIVE
        assert business.user_chat_id == 999001


def test_inbound_without_policy_and_policy_safety_branches(auth_client: TestClient) -> None:
    """Проверить handoff без policy, stop-word, неподдерживаемый chat и media-only сообщение."""
    connection, path_token, secret = _configure_business(auth_client)
    service = inbound_service(auth_client)
    _post_update(
        auth_client, connection["id"], path_token, secret, _business_connection_update(10100)
    )
    assert service.process_next() is True

    without_policy = _business_message(10101, 601, "Сообщение без automation policy")
    without_policy["business_message"]["chat"]["id"] = 401001
    _post_update(auth_client, connection["id"], path_token, secret, without_policy)
    assert service.process_next() is True
    with auth_client.app.state.session_factory() as db:
        conversation = db.scalar(
            select(Conversation).where(Conversation.telegram_chat_id == 401001)
        )
        assert conversation is not None
        assert conversation.status == ConversationStatus.HUMAN_HANDOFF

    create_policy(auth_client, connection["id"], allowed_chat_types=["private"])
    attach_policy = _business_message(10105, 605, "Повторный контакт после настройки policy")
    attach_policy["business_message"]["chat"]["id"] = 401001
    _post_update(auth_client, connection["id"], path_token, secret, attach_policy)
    assert service.process_next() is True
    with auth_client.app.state.session_factory() as db:
        attached = db.scalar(select(Conversation).where(Conversation.telegram_chat_id == 401001))
        assert attached is not None and attached.automation_policy_id is not None

    stopped = _business_message(10102, 602, "Пожалуйста, СТОП автоматизацию")
    stopped["business_message"]["chat"]["id"] = 401002
    _post_update(auth_client, connection["id"], path_token, secret, stopped)
    assert service.process_next() is True

    group = _business_message(10103, 603, "Сообщение из группы")
    group["business_message"]["chat"] = {"id": 401003, "type": "group"}
    _post_update(auth_client, connection["id"], path_token, secret, group)
    assert service.process_next() is True

    media = _business_message(10104, 604, "")
    media["business_message"]["chat"]["id"] = 401004
    media["business_message"].pop("text")
    media["business_message"]["photo"] = [{"file_id": "photo-1"}]
    _post_update(auth_client, connection["id"], path_token, secret, media)
    assert service.process_next() is True

    with auth_client.app.state.session_factory() as db:
        by_chat = {item.telegram_chat_id: item for item in db.scalars(select(Conversation)).all()}
        assert by_chat[401002].status == ConversationStatus.BLOCKED
        assert by_chat[401002].consent_status == ConsentStatus.REVOKED
        assert by_chat[401003].status == ConversationStatus.HUMAN_HANDOFF
        assert by_chat[401004].status == ConversationStatus.HUMAN_HANDOFF
        media_messages = list(
            db.scalars(
                select(ConversationMessage).where(
                    ConversationMessage.conversation_id == by_chat[401004].id
                )
            ).all()
        )
        assert any(item.content_type == "photo" for item in media_messages)
        assert any(item.direction.value == "outbound" for item in media_messages)


def test_inbound_edit_and_delete_existing_message(auth_client: TestClient) -> None:
    """Проверить изменение ciphertext и последующее стирание Telegram-сообщения."""
    connection, path_token, secret = _configure_business(auth_client)
    service = inbound_service(auth_client)
    _post_update(
        auth_client, connection["id"], path_token, secret, _business_connection_update(10200)
    )
    assert service.process_next() is True
    original = _business_message(10201, 701, "Первоначальный текст")
    original["business_message"]["chat"]["id"] = 402001
    _post_update(auth_client, connection["id"], path_token, secret, original)
    assert service.process_next() is True

    missing_edit = {
        "update_id": 10204,
        "edited_business_message": {
            "business_connection_id": "bc-test-001",
            "message_id": 999999,
            "chat": {"id": 402001, "type": "private"},
            "text": "Несуществующее сообщение",
        },
    }
    _post_update(auth_client, connection["id"], path_token, secret, missing_edit)
    assert service.process_next() is True
    with auth_client.app.state.session_factory() as db:
        ignored = db.scalar(
            select(InboundTelegramUpdate).where(InboundTelegramUpdate.telegram_update_id == 10204)
        )
        assert ignored is not None and ignored.status == InboundUpdateStatus.IGNORED

    edited = {
        "update_id": 10202,
        "edited_business_message": {
            "business_connection_id": "bc-test-001",
            "message_id": 701,
            "chat": {"id": 402001, "type": "private"},
            "text": "Исправленный текст",
        },
    }
    _post_update(auth_client, connection["id"], path_token, secret, edited)
    assert service.process_next() is True
    with auth_client.app.state.session_factory() as db:
        stored = db.scalar(
            select(ConversationMessage).where(ConversationMessage.telegram_message_id == 701)
        )
        assert stored is not None and stored.body_enc
        assert stored.raw_metadata["edited"] is True
        assert (
            auth_client.app.state.cipher.decrypt(
                stored.body_enc, context=f"conversation-message:{stored.id}:body"
            )
            == "Исправленный текст"
        )

    deleted = {
        "update_id": 10203,
        "deleted_business_messages": {
            "business_connection_id": "bc-test-001",
            "chat_id": 402001,
            "message_ids": [701, "ignored"],
        },
    }
    _post_update(auth_client, connection["id"], path_token, secret, deleted)
    assert service.process_next() is True
    with auth_client.app.state.session_factory() as db:
        stored = db.scalar(
            select(ConversationMessage).where(ConversationMessage.telegram_message_id == 701)
        )
        assert stored is not None
        assert stored.body_enc is None
        assert stored.body_preview == "[Сообщение удалено в Telegram]"
        assert stored.raw_metadata["deleted_in_telegram"] is True


def test_inbound_static_matching_content_and_schedule_helpers() -> None:
    """Проверить распознавание update/content, keyword matching и active-hours fallback."""
    assert InboundService._detect_update_type({"business_message": {}}) == "business_message"
    assert InboundService._detect_update_type({"unknown": {}}) == "unsupported"
    assert InboundService._matches_any(" Нужен ОПЕРАТОР сейчас ", ["", "оператор"])
    assert not InboundService._matches_any("обычный текст", ["стоп"])
    for key in ("photo", "video", "document", "voice", "audio", "sticker", "location", "contact"):
        assert InboundService._content_type({key: {}}) == key
    assert InboundService._content_type({"animation": {}}) == "unsupported"

    always = type("Policy", (), {"active_hours": {}})()
    assert InboundService._policy_is_active(always, datetime.now(UTC)) is True
    closed = type(
        "Policy",
        (),
        {"active_hours": {"mon": [["00:00", "23:59"]]}, "timezone_name": "UTC"},
    )()
    sunday = datetime(2026, 8, 9, 12, 0, tzinfo=UTC)
    assert InboundService._policy_is_active(closed, sunday) is False
    open_policy = type(
        "Policy",
        (),
        {"active_hours": {"6": [["00:00", "23:59"]]}, "timezone_name": "Invalid/Zone"},
    )()
    assert InboundService._policy_is_active(open_policy, sunday) is True
    invalid_window = type(
        "Policy",
        (),
        {"active_hours": {"sun": [["bad", "time"]]}, "timezone_name": "UTC"},
    )()
    assert InboundService._policy_is_active(invalid_window, sunday) is False


def test_inbound_validation_duplicate_truncation_and_outbound_message(
    auth_client: TestClient,
) -> None:
    """Проверить malformed message, duplicate id, ограничение длины и outbound-направление."""
    connection, path_token, secret = _configure_business(auth_client)
    service = inbound_service(auth_client)
    _post_update(
        auth_client, connection["id"], path_token, secret, _business_connection_update(10300)
    )
    assert service.process_next() is True

    missing_business = {
        "update_id": 10301,
        "business_message": {"message_id": 801, "chat": {"id": 403001}, "text": "bad"},
    }
    missing_chat = {
        "update_id": 10302,
        "business_message": {
            "business_connection_id": "bc-test-001",
            "message_id": 802,
            "text": "bad",
        },
    }
    for payload in (missing_business, missing_chat):
        _post_update(auth_client, connection["id"], path_token, secret, payload)
        assert service.process_next() is True
    with auth_client.app.state.session_factory() as db:
        failed = list(
            db.scalars(
                select(InboundTelegramUpdate).where(
                    InboundTelegramUpdate.telegram_update_id.in_([10301, 10302])
                )
            ).all()
        )
        assert len(failed) == 2 and all(
            item.status == InboundUpdateStatus.FAILED for item in failed
        )

    long_text = "я" * (auth_client.app.state.settings.inbound_message_max_chars + 10)
    long_message = _business_message(10303, 803, long_text)
    long_message["business_message"]["chat"]["id"] = 403003
    _post_update(auth_client, connection["id"], path_token, secret, long_message)
    assert service.process_next() is True

    duplicate = _business_message(10304, 803, "Повтор того же message_id")
    duplicate["business_message"]["chat"]["id"] = 403003
    _post_update(auth_client, connection["id"], path_token, secret, duplicate)
    assert service.process_next() is True

    outbound = _business_message(10305, 804, "Ответ владельца Business")
    outbound["business_message"]["chat"]["id"] = 403003
    outbound["business_message"]["sender_business_bot"] = True
    _post_update(auth_client, connection["id"], path_token, secret, outbound)
    assert service.process_next() is True
    with auth_client.app.state.session_factory() as db:
        conversation = db.scalar(
            select(Conversation).where(Conversation.telegram_chat_id == 403003)
        )
        assert conversation is not None and conversation.last_outbound_at is not None
        messages = list(
            db.scalars(
                select(ConversationMessage).where(
                    ConversationMessage.conversation_id == conversation.id
                )
            ).all()
        )
        inbound = next(item for item in messages if item.telegram_message_id == 803)
        assert inbound.body_enc
        body = auth_client.app.state.cipher.decrypt(
            inbound.body_enc, context=f"conversation-message:{inbound.id}:body"
        )
        assert len(body) == auth_client.app.state.settings.inbound_message_max_chars
        assert sum(item.telegram_message_id == 803 for item in messages) == 1
        ignored = db.scalar(
            select(InboundTelegramUpdate).where(InboundTelegramUpdate.telegram_update_id == 10304)
        )
        assert ignored is not None and ignored.status == InboundUpdateStatus.IGNORED


def test_inbound_consent_and_runtime_fallback_branches(
    auth_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Проверить decline consent, inactive hours, reply cap, AI rate, exception и handoff."""
    connection, path_token, secret = _configure_business(auth_client)
    service = inbound_service(auth_client)
    create_policy(
        auth_client,
        connection["id"],
        allowed_chat_types=["private"],
        require_consent=True,
    )
    _post_update(
        auth_client, connection["id"], path_token, secret, _business_connection_update(10400)
    )
    assert service.process_next() is True

    negative = _business_message(10401, 901, "нет")
    negative["business_message"]["chat"]["id"] = 404001
    _post_update(auth_client, connection["id"], path_token, secret, negative)
    assert service.process_next() is True
    with auth_client.app.state.session_factory() as db:
        policy = db.scalar(select(Conversation).where(Conversation.telegram_chat_id == 404001))
        assert policy is not None and policy.consent_status == ConsentStatus.DECLINED
        policy_row = policy.policy
        assert policy_row is not None
        policy_row.require_consent_before_ai = False
        db.commit()

    original_policy_active = service._policy_is_active
    monkeypatch.setattr(service, "_policy_is_active", lambda _policy, _now: False)
    inactive = _business_message(10402, 902, "Вне рабочего времени")
    inactive["business_message"]["chat"]["id"] = 404002
    _post_update(auth_client, connection["id"], path_token, secret, inactive)
    assert service.process_next() is True
    monkeypatch.setattr(service, "_policy_is_active", original_policy_active)

    original_daily_cap = service._daily_reply_cap_reached
    monkeypatch.setattr(service, "_daily_reply_cap_reached", lambda *_args: True)
    capped = _business_message(10403, 903, "Превышение дневного лимита")
    capped["business_message"]["chat"]["id"] = 404003
    _post_update(auth_client, connection["id"], path_token, secret, capped)
    assert service.process_next() is True
    monkeypatch.setattr(service, "_daily_reply_cap_reached", original_daily_cap)

    original_rate = service._ai_rate_limit_reached
    monkeypatch.setattr(service, "_ai_rate_limit_reached", lambda *_args: True)
    rate_limited = _business_message(10404, 904, "Превышение минутного лимита")
    rate_limited["business_message"]["chat"]["id"] = 404004
    _post_update(auth_client, connection["id"], path_token, secret, rate_limited)
    assert service.process_next() is True
    monkeypatch.setattr(service, "_ai_rate_limit_reached", original_rate)

    original_ai = service.ai.process
    monkeypatch.setattr(
        service.ai,
        "process",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("AI unavailable")),
    )
    failed_ai = _business_message(10405, 905, "Сбой AI")
    failed_ai["business_message"]["chat"]["id"] = 404005
    _post_update(auth_client, connection["id"], path_token, secret, failed_ai)
    assert service.process_next() is True

    monkeypatch.setattr(
        service.ai,
        "process",
        lambda *_args, **_kwargs: SimpleNamespace(handoff=True, reply_text="Нужен оператор"),
    )
    ai_handoff = _business_message(10406, 906, "Нужна дополнительная проверка")
    ai_handoff["business_message"]["chat"]["id"] = 404006
    _post_update(auth_client, connection["id"], path_token, secret, ai_handoff)
    assert service.process_next() is True
    monkeypatch.setattr(service.ai, "process", original_ai)

    with auth_client.app.state.session_factory() as db:
        statuses = {
            item.telegram_chat_id: item.status for item in db.scalars(select(Conversation)).all()
        }
        assert all(
            statuses[chat_id] == ConversationStatus.HUMAN_HANDOFF
            for chat_id in (404002, 404003, 404004, 404005, 404006)
        )


def test_inbound_operator_and_reply_validation(auth_client: TestClient) -> None:
    """Проверить отказ operator reply без policy и запрет пустого исходящего текста."""
    connection, path_token, secret = _configure_business(auth_client)
    service = inbound_service(auth_client)
    _post_update(
        auth_client, connection["id"], path_token, secret, _business_connection_update(10500)
    )
    assert service.process_next() is True
    message = _business_message(10501, 1001, "Диалог без policy")
    message["business_message"]["chat"]["id"] = 405001
    _post_update(auth_client, connection["id"], path_token, secret, message)
    assert service.process_next() is True
    with auth_client.app.state.session_factory() as db:
        conversation = db.scalar(
            select(Conversation).where(Conversation.telegram_chat_id == 405001)
        )
        business = db.scalar(select(TelegramBusinessConnection))
        assert conversation is not None and business is not None
        with pytest.raises(ValueError, match="automation policy"):
            service.send_operator_reply(db, conversation=conversation, text="Ответ")
        with pytest.raises(ValueError, match="Пустой ответ"):
            service._send_reply(
                db,
                business=business,
                conversation=conversation,
                policy=None,  # type: ignore[arg-type]
                text="   ",
                reply_to_message_id=None,
                author=conversation.messages[0].author,
            )


def test_inbound_business_connection_creation_and_missing_bot_guards(
    auth_client: TestClient,
) -> None:
    """Проверить подключение после initial disabled и ошибки при отсутствующем bot connection."""
    connection, _path_token, _secret = _configure_business(auth_client)
    service = inbound_service(auth_client)
    with auth_client.app.state.session_factory() as db:
        update = SimpleNamespace(
            organization_id=connection["organization_id"],
            telegram_connection_id=connection["id"],
        )
        business = service._handle_business_connection(
            db,
            update,  # type: ignore[arg-type]
            {
                "id": "bc-initial-disabled",
                "is_enabled": False,
                "user": {"id": 99001, "first_name": "Owner"},
            },
        )
        assert business.connected_at is None
        db.commit()
        business = service._handle_business_connection(
            db,
            update,  # type: ignore[arg-type]
            {
                "id": "bc-initial-disabled",
                "is_enabled": True,
                "user": {"id": 99001, "first_name": "Owner"},
            },
        )
        assert business.connected_at is not None
        db.commit()

        missing = SimpleNamespace(
            organization_id=connection["organization_id"],
            telegram_connection_id="missing-connection",
        )
        with pytest.raises(ValueError, match="Bot connection"):
            service._handle_business_connection(
                db,
                missing,  # type: ignore[arg-type]
                {"id": "bc-missing", "user": {"id": 1}},
            )
        with pytest.raises(ValueError, match="Bot connection"):
            service._ensure_business_connection(
                db,
                missing,  # type: ignore[arg-type]
                "bc-missing",
            )


def test_accept_update_recovers_from_concurrent_duplicate() -> None:
    """Проверить восстановление после гонки unique insert и защитное повторное исключение."""

    class RacingSession:
        """Имитировать сессию, проигравшую конкурентную вставку Telegram update."""

        def __init__(self, winner: object | None) -> None:
            """Сохранить объект победившей транзакции для повторного чтения."""
            self.winner = winner
            self.scalar_calls = 0
            self.added: object | None = None
            self.rolled_back = False

        def scalar(self, _statement: object) -> object | None:
            """Вернуть отсутствие записи до insert и победителя после rollback."""
            self.scalar_calls += 1
            return None if self.scalar_calls == 1 else self.winner

        def add(self, item: object) -> None:
            """Запомнить подготовленную inbound-запись."""
            self.added = item

        def commit(self) -> None:
            """Сымитировать нарушение уникальности от конкурентной транзакции."""
            raise IntegrityError("insert inbound update", {}, RuntimeError("duplicate"))

        def rollback(self) -> None:
            """Зафиксировать откат проигравшей транзакции."""
            self.rolled_back = True

    settings = SimpleNamespace(pii_preview_length=240)
    cipher = SimpleNamespace(
        encrypt_json=lambda payload, *, context: f"encrypted:{context}:{payload['update_id']}"
    )
    service = InboundService(None, settings, cipher)  # type: ignore[arg-type]
    connection = SimpleNamespace(id="connection-id", organization_id="organization-id")
    winner = object()
    racing_db = RacingSession(winner)

    stored, created = service.accept_update(
        racing_db,  # type: ignore[arg-type]
        connection=connection,  # type: ignore[arg-type]
        payload={"update_id": 10601, "message": {"text": "duplicate"}},
    )
    assert stored is winner
    assert created is False
    assert racing_db.added is not None
    assert racing_db.rolled_back is True

    missing_winner_db = RacingSession(None)
    with pytest.raises(IntegrityError):
        service.accept_update(
            missing_winner_db,  # type: ignore[arg-type]
            connection=connection,  # type: ignore[arg-type]
            payload={"update_id": 10602, "message": {"text": "lost duplicate"}},
        )
    assert missing_winner_db.rolled_back is True
