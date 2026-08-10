from __future__ import annotations

import base64
import hashlib
import hmac
import os
import struct
import time
from urllib.parse import quote


def generate_secret(length: int = 20) -> str:
    """Выполнить операцию generate secret. Аргументы интерпретируются в контексте модуля, результат
    возвращается вызывающему коду.
    """
    return base64.b32encode(os.urandom(length)).decode("ascii").rstrip("=")


def _decode_secret(secret: str) -> bytes:
    """Реализовать внутренний этап decode secret step. Вспомогательная функция сохраняет
    детерминированность и тестируемость процесса.
    """
    padding = "=" * ((8 - len(secret) % 8) % 8)
    return base64.b32decode(secret.upper() + padding)


def code_at(secret: str, timestamp: int | None = None, period: int = 30, digits: int = 6) -> str:
    """Выполнить операцию code at. Аргументы интерпретируются в контексте модуля, результат
    возвращается вызывающему коду.
    """
    timestamp = int(time.time()) if timestamp is None else timestamp
    counter = timestamp // period
    digest = hmac.new(_decode_secret(secret), struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    value = struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF
    return str(value % (10**digits)).zfill(digits)


def verify_code(
    secret: str,
    code: str,
    *,
    timestamp: int | None = None,
    valid_window: int = 1,
    period: int = 30,
) -> bool:
    """Проверить code. Некорректные данные или состояние отклоняются до побочного эффекта."""
    if not code.isdigit() or len(code) != 6:
        return False
    now = int(time.time()) if timestamp is None else timestamp
    for offset in range(-valid_window, valid_window + 1):
        expected = code_at(secret, now + offset * period, period=period)
        if hmac.compare_digest(expected, code):
            return True
    return False


def provisioning_uri(secret: str, account_name: str, issuer: str) -> str:
    """Выполнить операцию provisioning uri. Аргументы интерпретируются в контексте модуля,
    результат возвращается вызывающему коду.
    """
    label = quote(f"{issuer}:{account_name}")
    return (
        f"otpauth://totp/{label}?secret={secret}&issuer={quote(issuer)}"
        "&algorithm=SHA1&digits=6&period=30"
    )
