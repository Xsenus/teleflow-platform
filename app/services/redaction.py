from __future__ import annotations

import hashlib
import re
from typing import Any

_EMAIL_RE = re.compile(r"(?<![\w.+-])([\w.+-]{1,64})@([\w.-]+\.[A-Za-z]{2,})")
_PHONE_RE = re.compile(r"(?<!\d)(?:\+?\d[\d\s().-]{7,}\d)(?!\d)")
_TOKEN_RE = re.compile(r"(?i)\b(?:sk|tg|token|api[_-]?key)[-_]?[A-Za-z0-9._-]{12,}\b")
_LONG_NUMBER_RE = re.compile(r"(?<!\d)\d{12,19}(?!\d)")

PROMPT_INJECTION_PATTERNS = [
    re.compile(pattern, re.IGNORECASE)
    for pattern in [
        r"ignore\s+(all\s+)?previous\s+instructions",
        r"игнорируй\s+(все\s+)?предыдущие\s+инструкции",
        r"show\s+(me\s+)?(the\s+)?system\s+prompt",
        r"покажи\s+системн(?:ый|ые)\s+(?:промпт|инструкц)",
        r"reveal\s+(?:api|secret|token|password)",
        r"раскрой\s+(?:ключ|токен|пароль|секрет)",
        r"execute\s+(?:shell|command|code)",
        r"выполни\s+(?:команду|код|скрипт)",
    ]
]


def redact_text(value: str, *, preview_length: int = 240) -> str:
    """Выполнить операцию redact text. Аргументы интерпретируются в контексте модуля, результат
    возвращается вызывающему коду.
    """
    text = value or ""
    text = _EMAIL_RE.sub(lambda m: f"{m.group(1)[:1]}***@{m.group(2)}", text)
    text = _PHONE_RE.sub("[PHONE]", text)
    text = _TOKEN_RE.sub("[SECRET]", text)
    text = _LONG_NUMBER_RE.sub("[NUMBER]", text)
    text = " ".join(text.split())
    if len(text) > preview_length:
        text = text[:preview_length].rstrip() + "…"
    return text


def redact_payload(value: Any, *, preview_length: int = 240) -> Any:
    """Выполнить операцию redact payload. Аргументы интерпретируются в контексте модуля, результат
    возвращается вызывающему коду.
    """
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for key, item in value.items():
            lowered = key.lower()
            if any(part in lowered for part in ("token", "secret", "password", "api_key")):
                result[key] = "[REDACTED]"
            elif lowered in {"text", "caption", "phone_number", "email"} and isinstance(item, str):
                result[key] = redact_text(item, preview_length=preview_length)
            else:
                result[key] = redact_payload(item, preview_length=preview_length)
        return result
    if isinstance(value, list):
        return [redact_payload(item, preview_length=preview_length) for item in value[:100]]
    if isinstance(value, str):
        return redact_text(value, preview_length=preview_length)
    return value


def detect_prompt_injection(text: str) -> list[str]:
    """Выполнить операцию detect prompt injection. Аргументы интерпретируются в контексте модуля,
    результат возвращается вызывающему коду.
    """
    return [pattern.pattern for pattern in PROMPT_INJECTION_PATTERNS if pattern.search(text or "")]


def stable_hash(value: str) -> str:
    """Вычислить stable hash. Канонический ввод обеспечивает детерминированное сравнение
    целостности между процессами.
    """
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def minimize_for_ai(text: str, *, max_chars: int) -> str:
    """Удалить obvious secrets while retaining candidate-provided contact details as placeholders."""
    clean = _TOKEN_RE.sub("[SECRET_REMOVED]", text or "")
    clean = clean.replace("\x00", "")
    return clean[:max_chars]
