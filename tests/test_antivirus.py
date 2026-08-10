from __future__ import annotations

import struct
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.services.antivirus import (
    AntivirusUnavailable,
    MalwareDetected,
    scan_media_bytes,
    scan_with_clamav,
)
from tests.conftest import csrf_headers


class _FakeSocket:
    def __init__(self, response: bytes) -> None:
        """Инициализировать _FakeSocket with its explicit dependencies. Сохраняется только
        состояние, необходимое последующим операциям.
        """
        self.response = response
        self.sent = bytearray()
        self.timeout: float | None = None
        self._read = False

    def __enter__(self) -> _FakeSocket:
        """Войти в the managed context and return the resource exposed to the caller."""
        return self

    def __exit__(self, *_args: Any) -> None:
        """Покинуть the managed context and release resources even when the body fails."""
        return None

    def settimeout(self, value: float) -> None:
        """Выполнить операцию settimeout for _FakeSocket. Аргументы интерпретируются в контексте
        модуля, результат возвращается вызывающему коду.
        """
        self.timeout = value

    def sendall(self, data: bytes | memoryview) -> None:
        """Выполнить операцию sendall for _FakeSocket. Аргументы интерпретируются в контексте
        модуля, результат возвращается вызывающему коду.
        """
        self.sent.extend(data)

    def recv(self, _size: int) -> bytes:
        """Выполнить операцию recv for _FakeSocket. Аргументы интерпретируются в контексте модуля,
        результат возвращается вызывающему коду.
        """
        if self._read:
            return b""
        self._read = True
        return self.response


def _clamav_settings() -> Settings:
    """Реализовать внутренний этап clamav settings step. Вспомогательная функция сохраняет
    детерминированность и тестируемость процесса.
    """
    return Settings(
        _env_file=None,
        environment="test",
        antivirus_mode="clamav",
        clamav_host="clamav",
        clamav_port=3310,
        clamav_timeout_seconds=2,
    )


def test_antivirus_disabled_does_not_open_socket() -> None:
    """Проверить сценарий antivirus disabled does not open socket. Тест завершается ошибкой при
    нарушении зафиксированного инварианта.
    """
    settings = Settings(_env_file=None, environment="test", antivirus_mode="disabled")
    result = scan_media_bytes(b"safe", settings)
    assert result.status == "disabled"
    assert result.engine == "disabled"


def test_clamav_instream_protocol_and_clean_response(monkeypatch: pytest.MonkeyPatch) -> None:
    """Проверить сценарий clamav instream protocol and clean response. Тест завершается ошибкой при
    нарушении зафиксированного инварианта.
    """
    fake = _FakeSocket(b"stream: OK\x00")
    monkeypatch.setattr("app.services.antivirus.socket.create_connection", lambda *_a, **_kw: fake)
    payload = b"safe text"
    result = scan_with_clamav(payload, _clamav_settings())
    assert result.status == "clean"
    assert bytes(fake.sent).startswith(b"zINSTREAM\x00")
    assert struct.pack(">I", len(payload)) + payload in bytes(fake.sent)
    assert bytes(fake.sent).endswith(struct.pack(">I", 0))


def test_clamav_found_response_raises_signature(monkeypatch: pytest.MonkeyPatch) -> None:
    """Проверить сценарий clamav found response raises signature. Тест завершается ошибкой при
    нарушении зафиксированного инварианта.
    """
    fake = _FakeSocket(b"stream: Eicar-Test-Signature FOUND\x00")
    monkeypatch.setattr("app.services.antivirus.socket.create_connection", lambda *_a, **_kw: fake)
    with pytest.raises(MalwareDetected, match="Eicar-Test-Signature") as exc_info:
        scan_with_clamav(b"test", _clamav_settings())
    assert exc_info.value.signature == "Eicar-Test-Signature"


def test_media_upload_blocks_malware_before_storage(
    auth_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Проверить сценарий media upload blocks malware before storage. Тест завершается ошибкой при
    нарушении зафиксированного инварианта.
    """

    def infected(*_args: Any, **_kwargs: Any) -> None:
        """Выполнить операцию infected. Аргументы интерпретируются в контексте модуля, результат
        возвращается вызывающему коду.
        """
        raise MalwareDetected("Test.Signature")

    monkeypatch.setattr("app.api.media.scan_media_bytes", infected)
    response = auth_client.post(
        "/api/v1/media",
        headers=csrf_headers(auth_client),
        files={"file": ("note.txt", b"safe text", "text/plain")},
    )
    assert response.status_code == 422
    assert auth_client.get("/api/v1/media").json() == []
    audits = auth_client.get("/api/v1/audit?limit=100").json()
    assert any(item["action"] == "media.upload_blocked_malware" for item in audits)


def test_media_upload_fails_closed_when_scanner_is_unavailable(
    auth_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Проверить сценарий media upload fails closed when scanner is unavailable. Тест завершается
    ошибкой при нарушении зафиксированного инварианта.
    """
    auth_client.app.state.settings.antivirus_mode = "clamav"
    auth_client.app.state.settings.antivirus_fail_closed = True

    def unavailable(*_args: Any, **_kwargs: Any) -> None:
        """Выполнить операцию unavailable. Аргументы интерпретируются в контексте модуля, результат
        возвращается вызывающему коду.
        """
        raise AntivirusUnavailable("scanner offline")

    monkeypatch.setattr("app.api.media.scan_media_bytes", unavailable)
    response = auth_client.post(
        "/api/v1/media",
        headers=csrf_headers(auth_client),
        files={"file": ("note.txt", b"safe text", "text/plain")},
    )
    assert response.status_code == 503
    assert auth_client.get("/api/v1/media").json() == []


def test_media_upload_can_fail_open_only_when_explicitly_configured(
    auth_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Проверить сценарий media upload can fail open only when explicitly configured. Тест
    завершается ошибкой при нарушении зафиксированного инварианта.
    """
    auth_client.app.state.settings.antivirus_mode = "clamav"
    auth_client.app.state.settings.antivirus_fail_closed = False

    def unavailable(*_args: Any, **_kwargs: Any) -> None:
        """Выполнить операцию unavailable. Аргументы интерпретируются в контексте модуля, результат
        возвращается вызывающему коду.
        """
        raise AntivirusUnavailable("scanner offline")

    monkeypatch.setattr("app.api.media.scan_media_bytes", unavailable)
    response = auth_client.post(
        "/api/v1/media",
        headers=csrf_headers(auth_client),
        files={"file": ("note.txt", b"safe text", "text/plain")},
    )
    assert response.status_code == 201, response.text
    audits = auth_client.get("/api/v1/audit?limit=100").json()
    assert any(item["action"] == "media.antivirus_unavailable" for item in audits)
