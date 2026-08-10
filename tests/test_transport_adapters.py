from __future__ import annotations

import io
from pathlib import Path
from typing import Any

import httpx
import pytest

from app.enums import DestinationKind, ParseMode
from app.services.ai.openai_compatible import OpenAICompatibleProvider
from app.services.media_validation import (
    MediaValidationError,
    read_validated_stream,
    signature_matches,
    validate_media_bytes,
)
from app.services.telegram.bot_api import BotApiGateway
from app.services.telegram.errors import (
    TelegramAuthError,
    TelegramDeliveryUncertain,
    TelegramFloodWait,
    TelegramInvalidRequest,
    TelegramNotFound,
    TelegramTransientError,
    TelegramWriteForbidden,
)


class StubClient:
    """Имитировать синхронный httpx.Client без сетевых обращений."""

    def __init__(self, result: httpx.Response | Exception):
        """Сохранить ответ или исключение, которое вернёт метод post."""

        self.result = result
        self.calls: list[dict[str, Any]] = []

    def __enter__(self) -> StubClient:
        """Вернуть stub при входе в контекстный менеджер."""

        return self

    def __exit__(self, *_args: object) -> None:
        """Завершить контекст без внешних ресурсов."""

    def post(self, url: str, **kwargs: Any) -> httpx.Response:
        """Записать запрос и вернуть заранее заданный результат."""

        self.calls.append({"url": url, **kwargs})
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def response(
    status: int, payload: dict[str, Any] | None = None, content: bytes | None = None
) -> httpx.Response:
    """Создать httpx.Response с Request, пригодный для raise_for_status."""

    request = httpx.Request("POST", "https://service.example/api")
    if content is not None:
        return httpx.Response(status, content=content, request=request)
    return httpx.Response(status, json=payload or {}, request=request)


def install_stub(
    monkeypatch: pytest.MonkeyPatch, module: Any, result: httpx.Response | Exception
) -> StubClient:
    """Подменить httpx.Client в целевом модуле и вернуть записывающий stub."""

    client = StubClient(result)
    monkeypatch.setattr(module.httpx, "Client", lambda **_kwargs: client)
    return client


def test_bot_api_success_paths_and_payloads(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Проверить identity, destination, send, webhook и Business методы Bot API adapter."""

    from app.services.telegram import bot_api as module

    gateway = BotApiGateway("test-token", timeout_seconds=1)
    results: dict[str, Any] = {
        "getMe": {"id": 10, "username": "teleflow_bot", "first_name": "TeleFlow"},
        "getChat": {"id": -1001, "username": "allowed", "title": "Allowed", "type": "supergroup"},
        "getChatMember": {"status": "administrator", "can_post_messages": True},
        "sendMessage": {"message_id": 501, "date": 123},
        "sendPhoto": {"message_id": 502, "date": 124},
        "setWebhook": True,
        "deleteWebhook": True,
        "getWebhookInfo": {"url": "https://example.com/hook"},
        "getBusinessConnection": {"id": "bc-1"},
        "readBusinessMessage": True,
    }

    class RoutingClient(StubClient):
        """Возвращать Bot API result по имени метода в URL."""

        def post(self, url: str, **kwargs: Any) -> httpx.Response:
            """Сформировать успешный Telegram envelope для вызываемого метода."""

            method = url.rsplit("/", 1)[-1]
            self.calls.append({"url": url, **kwargs})
            return response(200, {"ok": True, "result": results[method]})

    client = RoutingClient(response(200))
    monkeypatch.setattr(module.httpx, "Client", lambda **_kwargs: client)
    identity = gateway.get_identity()
    assert identity.account_id == 10 and gateway.get_identity() is identity
    destination = gateway.resolve_destination(chat_id=None, username="allowed", topic_id=7)
    assert destination.kind == DestinationKind.FORUM_TOPIC
    assert destination.capabilities["can_send_messages"] is True
    assert gateway.list_destinations() == []
    sent = gateway.send_message(
        chat_id=-1001,
        topic_id=7,
        body="Сообщение",
        parse_mode=ParseMode.HTML,
        link_preview=False,
    )
    assert sent.message_id == "501"
    media = tmp_path / "photo.png"
    media.write_bytes(b"png")
    photo = gateway.send_message(
        chat_id=-1001,
        topic_id=None,
        body="Подпись",
        parse_mode=ParseMode.PLAIN,
        link_preview=True,
        media_path=media,
        media_content_type="image/png",
    )
    assert photo.message_id == "502"
    assert gateway.set_webhook(
        url="https://example.com/hook", secret_token="secret", allowed_updates=["message"]
    )
    assert gateway.delete_webhook(drop_pending_updates=True)
    assert gateway.get_webhook_info()["url"].endswith("/hook")
    assert gateway.get_business_connection("bc-1")["id"] == "bc-1"
    assert (
        gateway.send_business_message(
            business_connection_id="bc-1", chat_id=1, text="Ответ", reply_to_message_id=5
        )["message_id"]
        == 501
    )
    assert gateway.read_business_message(business_connection_id="bc-1", chat_id=1, message_id=5)
    assert any("files" in call for call in client.calls)


@pytest.mark.parametrize(
    ("status", "payload", "method", "error"),
    [
        (429, {"parameters": {"retry_after": 7}}, "sendMessage", TelegramFloodWait),
        (401, {}, "getMe", TelegramAuthError),
        (404, {}, "getMe", TelegramAuthError),
        (403, {"description": "forbidden"}, "sendMessage", TelegramWriteForbidden),
        (500, {}, "sendMessage", TelegramDeliveryUncertain),
        (500, {}, "getChat", TelegramTransientError),
        (400, {"ok": False, "description": "chat not found"}, "getChat", TelegramNotFound),
        (400, {"ok": False, "description": "not enough rights"}, "getChat", TelegramWriteForbidden),
        (400, {"ok": False, "description": "bad request"}, "getChat", TelegramInvalidRequest),
    ],
)
def test_bot_api_maps_http_and_domain_errors(
    monkeypatch: pytest.MonkeyPatch,
    status: int,
    payload: dict[str, Any],
    method: str,
    error: type[Exception],
) -> None:
    """Проверить детерминированное преобразование Telegram error envelope."""

    from app.services.telegram import bot_api as module

    install_stub(monkeypatch, module, response(status, payload))
    with pytest.raises(error):
        BotApiGateway("token")._call(method)


@pytest.mark.parametrize(
    ("network_error", "expected"),
    [
        (httpx.ConnectError("connect"), TelegramTransientError),
        (httpx.ReadTimeout("read"), TelegramDeliveryUncertain),
    ],
)
def test_bot_api_maps_network_errors(
    monkeypatch: pytest.MonkeyPatch,
    network_error: Exception,
    expected: type[Exception],
) -> None:
    """Проверить различие безопасного retry и неопределённого результата сети."""

    from app.services.telegram import bot_api as module

    install_stub(monkeypatch, module, network_error)
    with pytest.raises(expected):
        BotApiGateway("token")._call("sendMessage")


def test_bot_api_handles_non_json_and_invalid_destination(monkeypatch: pytest.MonkeyPatch) -> None:
    """Проверить некорректный JSON и отказ от личных/неуказанных назначений."""

    from app.services.telegram import bot_api as module

    install_stub(monkeypatch, module, response(200, content=b"not-json"))
    gateway = BotApiGateway("token")
    with pytest.raises(TelegramDeliveryUncertain):
        gateway._call("sendMessage")
    with pytest.raises(TelegramTransientError):
        gateway._call("getChat")
    with pytest.raises(TelegramNotFound):
        gateway.resolve_destination(chat_id=None, username=None)
    gateway._call = lambda *_args, **_kwargs: {"id": 1, "type": "private"}  # type: ignore[method-assign]
    with pytest.raises(TelegramInvalidRequest):
        gateway.resolve_destination(chat_id=1, username=None)


def test_openai_compatible_contract_and_validation(monkeypatch: pytest.MonkeyPatch) -> None:
    """Проверить payload, JSON-контракт и ограничения OpenAI-compatible adapter."""

    from app.services.ai import openai_compatible as module

    with pytest.raises(ValueError, match="URL"):
        OpenAICompatibleProvider(
            base_url="invalid",
            api_key="key",
            model_name="model",
            timeout_seconds=1,
            max_output_tokens=100,
            temperature=0.2,
        )
    with pytest.raises(ValueError, match="allowlist"):
        OpenAICompatibleProvider(
            base_url="https://ai.example.com/v1",
            api_key="key",
            model_name="blocked",
            timeout_seconds=1,
            max_output_tokens=100,
            temperature=0.2,
            allowed_models=["allowed"],
        )
    raw = {
        "choices": [
            {
                "message": {
                    "content": '{"reply_text":"Ответ","candidate_updates":{"city":"Омск"},"vacancy_key":"python","handoff":true,"safety_flags":{"safe":true}}'
                }
            }
        ],
        "usage": {"prompt_tokens": 10, "completion_tokens": 4},
    }
    client = install_stub(monkeypatch, module, response(200, raw))
    provider = OpenAICompatibleProvider(
        base_url="https://ai.example.com/v1/",
        api_key="key",
        model_name="allowed",
        timeout_seconds=1,
        max_output_tokens=100,
        temperature=0.2,
        allowed_models=["allowed"],
    )
    result = provider.generate(
        message="Привет",
        history=[{"role": "tool", "content": "untrusted"}],
        knowledge=[{"title": "Вакансия", "content": "Python"}],
        candidate={"name": "Тест"},
        system_prompt="Задавай один вопрос",
    )
    assert result.reply_text == "Ответ" and result.handoff is True
    assert result.candidate_updates == {"city": "Омск"}
    assert result.input_tokens == 10 and result.output_tokens == 4
    assert client.calls[0]["url"] == "https://ai.example.com/v1/chat/completions"
    assert client.calls[0]["json"]["messages"][1]["role"] == "user"

    install_stub(
        monkeypatch,
        module,
        response(200, {"choices": [{"message": {"content": '{"reply_text":""}'}}]}),
    )
    with pytest.raises(ValueError, match="reply_text"):
        provider.generate(message="x", history=[], knowledge=[], candidate={}, system_prompt="")


@pytest.mark.parametrize(
    ("content_type", "header"),
    [
        ("image/jpeg", b"\xff\xd8\xffx"),
        ("image/png", b"\x89PNG\r\n\x1a\n"),
        ("image/webp", b"RIFFxxxxWEBP"),
        ("image/gif", b"GIF89a"),
        ("video/mp4", b"xxxxftypxxxx"),
        ("application/pdf", b"%PDF-1.7"),
        ("application/zip", b"PK\x03\x04"),
        ("text/plain", "текст".encode()),
    ],
)
def test_media_signatures_and_valid_bytes(content_type: str, header: bytes) -> None:
    """Проверить magic-signature всех разрешённых форматов и SHA-256 результата."""

    assert signature_matches(content_type, header)
    assert len(validate_media_bytes(header, content_type, 1024)) == 64


def test_media_validation_rejects_invalid_inputs_and_streams() -> None:
    """Проверить тип, размер, UTF-8, пустой stream и потоковую валидацию."""

    with pytest.raises(MediaValidationError, match="Тип"):
        validate_media_bytes(b"x", "application/x-executable", 100)
    with pytest.raises(MediaValidationError, match="Пустой"):
        validate_media_bytes(b"", "text/plain", 100)
    with pytest.raises(MediaValidationError, match="размер"):
        validate_media_bytes(b"abc", "text/plain", 2)
    with pytest.raises(MediaValidationError, match="соответствует"):
        validate_media_bytes(b"not-pdf", "application/pdf", 100)
    with pytest.raises(MediaValidationError, match="UTF-8"):
        validate_media_bytes(b"\xff", "text/plain", 100)
    data, digest = read_validated_stream(io.BytesIO("поток".encode()), "text/plain", 100)
    assert data.decode() == "поток" and len(digest) == 64
    with pytest.raises(MediaValidationError, match="Пустой"):
        read_validated_stream(io.BytesIO(b""), "text/plain", 100)
    with pytest.raises(MediaValidationError, match="размер"):
        read_validated_stream(io.BytesIO(b"abcd"), "text/plain", 3)
    with pytest.raises(MediaValidationError, match="UTF-8"):
        read_validated_stream(io.BytesIO(b"\xff"), "text/plain", 100)
