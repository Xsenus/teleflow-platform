from __future__ import annotations

import socket
import struct
from dataclasses import dataclass

from app.config import Settings


class AntivirusError(RuntimeError):
    """Base error raised when malware scanning cannot produce a trusted result."""


class AntivirusUnavailable(AntivirusError):
    """The configured scanner cannot be reached or returned a protocol error."""


class MalwareDetected(AntivirusError):
    def __init__(self, signature: str) -> None:
        """Инициализировать MalwareDetected with its explicit dependencies. Сохраняется только
        состояние, необходимое последующим операциям.
        """
        self.signature = signature or "unknown"
        super().__init__(f"Обнаружен вредоносный объект: {self.signature}")


@dataclass(frozen=True, slots=True)
class AntivirusScanResult:
    status: str
    engine: str
    signature: str | None = None
    detail: str | None = None


_DISABLED_RESULT = AntivirusScanResult(status="disabled", engine="disabled")


def _clamav_response(sock: socket.socket, max_bytes: int = 8192) -> bytes:
    """Реализовать внутренний этап clamav response step. Вспомогательная функция сохраняет
    детерминированность и тестируемость процесса.
    """
    chunks: list[bytes] = []
    total = 0
    while total < max_bytes:
        chunk = sock.recv(min(2048, max_bytes - total))
        if not chunk:
            break
        chunks.append(chunk)
        total += len(chunk)
        if b"\x00" in chunk or b"\n" in chunk:
            break
    if total >= max_bytes:
        raise AntivirusUnavailable("Ответ ClamAV превышает допустимый размер")
    return b"".join(chunks)


def scan_with_clamav(data: bytes, settings: Settings) -> AntivirusScanResult:
    """Выполнить операцию scan with clamav. Аргументы интерпретируются в контексте модуля,
    результат возвращается вызывающему коду.
    """
    try:
        with socket.create_connection(
            (settings.clamav_host, settings.clamav_port),
            timeout=settings.clamav_timeout_seconds,
        ) as sock:
            sock.settimeout(settings.clamav_timeout_seconds)
            sock.sendall(b"zINSTREAM\x00")
            view = memoryview(data)
            chunk_size = 1024 * 1024
            for offset in range(0, len(view), chunk_size):
                chunk = view[offset : offset + chunk_size]
                sock.sendall(struct.pack(">I", len(chunk)))
                sock.sendall(chunk)
            sock.sendall(struct.pack(">I", 0))
            raw = _clamav_response(sock)
    except (OSError, TimeoutError) as exc:
        raise AntivirusUnavailable(f"ClamAV недоступен: {exc}") from exc

    try:
        response = raw.rstrip(b"\x00\r\n").decode("utf-8", errors="strict").strip()
    except UnicodeDecodeError as exc:
        raise AntivirusUnavailable("ClamAV вернул некорректный ответ") from exc
    if not response:
        raise AntivirusUnavailable("ClamAV вернул пустой ответ")
    if response.endswith(" OK") or response == "OK":
        return AntivirusScanResult(status="clean", engine="clamav", detail=response)
    if response.endswith(" FOUND"):
        description = response[: -len(" FOUND")]
        signature = description.split(":", 1)[-1].strip() or "unknown"
        raise MalwareDetected(signature)
    if response.endswith(" ERROR"):
        raise AntivirusUnavailable(response)
    raise AntivirusUnavailable(f"Неизвестный ответ ClamAV: {response[:500]}")


def scan_media_bytes(data: bytes, settings: Settings) -> AntivirusScanResult:
    """Выполнить операцию scan media bytes. Аргументы интерпретируются в контексте модуля,
    результат возвращается вызывающему коду.
    """
    if settings.antivirus_mode == "disabled":
        return _DISABLED_RESULT
    if settings.antivirus_mode == "clamav":
        return scan_with_clamav(data, settings)
    raise AntivirusUnavailable("Неизвестный antivirus mode")
