from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass
from datetime import UTC, datetime, time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from app.enums import DestinationKind

_TME_HOSTS = {"t.me", "telegram.me", "www.t.me", "www.telegram.me"}
_USERNAME_RE = re.compile(r"^[A-Za-z0-9_]{5,128}$")
_TRUE_VALUES = {"1", "true", "yes", "y", "да", "д", "confirmed", "разрешено"}
_FALSE_VALUES = {"0", "false", "no", "n", "нет", "н", ""}

_HEADER_ALIASES = {
    "link": {"link", "url", "ссылка", "telegram", "telegram_link"},
    "username": {"username", "user", "имя", "юзернейм", "group"},
    "telegram_chat_id": {"telegram_chat_id", "chat_id", "id", "идентификатор"},
    "topic_id": {"topic_id", "thread_id", "тема", "topic"},
    "title": {"title", "name", "название"},
    "kind": {"kind", "type", "тип"},
    "permission_confirmed": {
        "permission_confirmed",
        "confirmed",
        "permission",
        "разрешено",
        "подтверждено",
    },
    "permission_note": {"permission_note", "note", "примечание", "основание"},
    "rules_url": {"rules_url", "rules", "правила", "rules_link"},
    "permission_expires_at": {
        "permission_expires_at",
        "permission_expiry",
        "expires_at",
        "срок_разрешения",
    },
    "enabled": {"enabled", "active", "активно", "включено"},
    "timezone_name": {"timezone_name", "timezone", "tz", "часовой_пояс"},
    "allowed_weekdays": {"allowed_weekdays", "weekdays", "days", "дни", "дни_недели"},
    "allowed_start_time": {"allowed_start_time", "start_time", "time_from", "время_с"},
    "allowed_end_time": {"allowed_end_time", "end_time", "time_to", "время_до"},
    "cooldown_minutes_override": {
        "cooldown_minutes_override",
        "cooldown_minutes",
        "cooldown",
        "кулдаун",
    },
}
_ALIAS_LOOKUP = {
    alias.casefold(): canonical
    for canonical, aliases in _HEADER_ALIASES.items()
    for alias in aliases
}


@dataclass(slots=True)
class ParsedDestinationRow:
    row_number: int
    source: str
    username: str | None = None
    telegram_chat_id: int | None = None
    topic_id: int | None = None
    title: str | None = None
    kind: DestinationKind = DestinationKind.SUPERGROUP
    permission_confirmed: bool = False
    permission_note: str | None = None
    rules_url: str | None = None
    permission_expires_at: datetime | None = None
    enabled: bool = True
    timezone_name: str | None = None
    allowed_weekdays: list[int] | None = None
    allowed_start_time: time | None = None
    allowed_end_time: time | None = None
    cooldown_minutes_override: int | None = None
    error: str | None = None

    def payload(self, connection_id: str) -> dict[str, Any]:
        """Выполнить операцию payload класса ParsedDestinationRow. Аргументы интерпретируются в
        контексте модуля, результат возвращается вызывающему коду.
        """
        return {
            "connection_id": connection_id,
            "telegram_chat_id": self.telegram_chat_id,
            "username": self.username,
            "title": self.title,
            "kind": self.kind,
            "topic_id": self.topic_id,
            "permission_confirmed": self.permission_confirmed,
            "permission_note": self.permission_note,
            "rules_url": self.rules_url,
            "permission_expires_at": self.permission_expires_at,
            "timezone_name": self.timezone_name,
            "allowed_weekdays": self.allowed_weekdays or [],
            "allowed_start_time": self.allowed_start_time,
            "allowed_end_time": self.allowed_end_time,
            "cooldown_minutes_override": self.cooldown_minutes_override,
        }


class DestinationImportError(ValueError):
    pass


def parse_destination_file(
    content: bytes,
    filename: str | None,
    *,
    max_rows: int = 500,
) -> list[ParsedDestinationRow]:
    """Выполнить операцию parse destination file. Аргументы интерпретируются в контексте модуля,
    результат возвращается вызывающему коду.
    """
    suffix = Path(filename or "destinations.txt").suffix.lower()
    if suffix not in {".txt", ".csv", ".tsv"}:
        raise DestinationImportError("Поддерживаются только файлы TXT, CSV и TSV")
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise DestinationImportError("Файл должен быть в кодировке UTF-8") from exc
    if "\x00" in text:
        raise DestinationImportError("Бинарные файлы не поддерживаются")
    rows = _parse_table(text, suffix) if suffix in {".csv", ".tsv"} else _parse_auto(text)
    if len(rows) > max_rows:
        raise DestinationImportError(f"За один импорт разрешено не более {max_rows} строк")
    if not rows:
        raise DestinationImportError("Файл не содержит назначений")
    return rows


def _parse_auto(text: str) -> list[ParsedDestinationRow]:
    """Реализовать внутренний этап parse auto step. Вспомогательная функция сохраняет
    детерминированность и тестируемость процесса.
    """
    meaningful = [
        line for line in text.splitlines() if line.strip() and not line.lstrip().startswith("#")
    ]
    if not meaningful:
        return []
    first = meaningful[0].casefold()
    looks_like_header = any(alias in first for alias in _ALIAS_LOOKUP)
    if looks_like_header and any(delimiter in meaningful[0] for delimiter in (",", ";", "\t")):
        return _parse_table("\n".join(meaningful), "")
    return [
        _from_mapping(index, {"link": line.strip()}, line.strip())
        for index, line in enumerate(meaningful, 1)
    ]


def _parse_table(text: str, suffix: str) -> list[ParsedDestinationRow]:
    """Реализовать внутренний этап parse table step. Вспомогательная функция сохраняет
    детерминированность и тестируемость процесса.
    """
    sample = text[:4096]
    delimiter = "\t" if suffix == ".tsv" else None
    if delimiter is None:
        try:
            delimiter = csv.Sniffer().sniff(sample, delimiters=",;\t").delimiter
        except csv.Error:
            delimiter = ","
    reader = csv.DictReader(io.StringIO(text), delimiter=delimiter)
    if not reader.fieldnames:
        raise DestinationImportError("В CSV отсутствует строка заголовков")
    canonical_headers = {
        original: _ALIAS_LOOKUP.get(str(original or "").strip().casefold())
        for original in reader.fieldnames
    }
    if not any(canonical_headers.values()):
        raise DestinationImportError("Не найдены поддерживаемые заголовки CSV")
    result: list[ParsedDestinationRow] = []
    for index, raw in enumerate(reader, 2):
        mapping = {
            canonical: str(raw.get(original) or "").strip()
            for original, canonical in canonical_headers.items()
            if canonical
        }
        if not any(mapping.values()):
            continue
        source = (
            mapping.get("link")
            or mapping.get("username")
            or mapping.get("telegram_chat_id")
            or f"строка {index}"
        )
        result.append(_from_mapping(index, mapping, source))
    return result


def _from_mapping(row_number: int, values: dict[str, str], source: str) -> ParsedDestinationRow:
    """Реализовать внутренний этап from mapping step. Вспомогательная функция сохраняет
    детерминированность и тестируемость процесса.
    """
    row = ParsedDestinationRow(row_number=row_number, source=source[:500])
    try:
        link = values.get("link", "").strip()
        username = values.get("username", "").strip().removeprefix("@") or None
        chat_id_text = values.get("telegram_chat_id", "").strip()
        if link:
            parsed_username, parsed_chat_id = _parse_target(link)
            username = username or parsed_username
            if not chat_id_text and parsed_chat_id is not None:
                chat_id_text = str(parsed_chat_id)
        if username:
            if not _USERNAME_RE.fullmatch(username):
                raise DestinationImportError("Некорректный username Telegram")
            row.username = username
        if chat_id_text:
            row.telegram_chat_id = int(chat_id_text)
        if row.username is None and row.telegram_chat_id is None:
            raise DestinationImportError("Укажите ссылку, username или telegram_chat_id")
        topic = values.get("topic_id", "").strip()
        row.topic_id = int(topic) if topic else None
        if row.topic_id is not None and row.topic_id < 1:
            raise DestinationImportError("topic_id должен быть положительным")
        row.title = values.get("title", "").strip()[:255] or None
        kind = values.get("kind", "").strip().casefold()
        if row.topic_id:
            row.kind = DestinationKind.FORUM_TOPIC
        elif kind:
            kind_aliases = {
                "group": DestinationKind.GROUP,
                "группа": DestinationKind.GROUP,
                "supergroup": DestinationKind.SUPERGROUP,
                "супергруппа": DestinationKind.SUPERGROUP,
                "channel": DestinationKind.CHANNEL,
                "канал": DestinationKind.CHANNEL,
                "forum_topic": DestinationKind.FORUM_TOPIC,
                "тема": DestinationKind.FORUM_TOPIC,
            }
            if kind not in kind_aliases:
                raise DestinationImportError("Неизвестный тип назначения")
            row.kind = kind_aliases[kind]
        row.permission_confirmed = _parse_bool(
            values.get("permission_confirmed", ""), "permission_confirmed"
        )
        row.permission_note = values.get("permission_note", "").strip()[:4000] or None
        row.rules_url = values.get("rules_url", "").strip() or None
        expiry = values.get("permission_expires_at", "").strip()
        if expiry:
            normalized_expiry = expiry[:-1] + "+00:00" if expiry.endswith("Z") else expiry
            row.permission_expires_at = datetime.fromisoformat(normalized_expiry)
        row.enabled = _parse_bool(values.get("enabled", "true"), "enabled")
        row.timezone_name = values.get("timezone_name", "").strip() or None
        row.allowed_weekdays = _parse_weekdays(values.get("allowed_weekdays", ""))
        row.allowed_start_time = _parse_time(
            values.get("allowed_start_time", ""), "allowed_start_time"
        )
        row.allowed_end_time = _parse_time(values.get("allowed_end_time", ""), "allowed_end_time")
        if (row.allowed_start_time is None) != (row.allowed_end_time is None):
            raise DestinationImportError("Начало и окончание временного окна задаются вместе")
        if row.allowed_start_time and row.allowed_start_time == row.allowed_end_time:
            raise DestinationImportError("Начало и окончание временного окна не должны совпадать")
        cooldown = values.get("cooldown_minutes_override", "").strip()
        row.cooldown_minutes_override = int(cooldown) if cooldown else None
        if row.cooldown_minutes_override is not None and row.cooldown_minutes_override < 1:
            raise DestinationImportError("cooldown должен быть положительным")
        if row.permission_expires_at and not row.permission_confirmed:
            raise DestinationImportError(
                "Срок разрешения задаётся только для подтверждённой строки"
            )
        if row.permission_expires_at:
            expiry_utc = (
                row.permission_expires_at.astimezone(UTC)
                if row.permission_expires_at.tzinfo
                else row.permission_expires_at.replace(tzinfo=UTC)
            )
            if expiry_utc <= datetime.now(UTC):
                raise DestinationImportError("Срок разрешения должен быть в будущем")
        if row.permission_confirmed and not (row.permission_note or row.rules_url):
            raise DestinationImportError(
                "Для подтверждения разрешения добавьте permission_note или rules_url"
            )
    except (ValueError, DestinationImportError) as exc:
        row.error = str(exc)
    return row


def _parse_target(value: str) -> tuple[str | None, int | None]:
    """Реализовать внутренний этап parse target step. Вспомогательная функция сохраняет
    детерминированность и тестируемость процесса.
    """
    target = value.strip()
    if target.lstrip("-").isdigit():
        return None, int(target)
    if target.startswith("@"):
        return target[1:], None
    if "://" not in target:
        return target, None
    parsed = urlparse(target)
    if parsed.hostname and parsed.hostname.casefold() not in _TME_HOSTS:
        raise DestinationImportError("Поддерживаются только ссылки t.me")
    path = parsed.path.strip("/")
    if not path:
        raise DestinationImportError("В ссылке отсутствует username")
    if path.startswith("+") or path.startswith("joinchat/"):
        raise DestinationImportError(
            "Invite-ссылка не импортируется автоматически. Сначала вступите в группу и укажите её ID или username"
        )
    segments = path.split("/")
    if segments[0] == "s" and len(segments) > 1:
        segments.pop(0)
    username = segments[0]
    if username == "c":
        raise DestinationImportError(
            "Ссылка t.me/c не содержит публичного username. Укажите telegram_chat_id отдельной колонкой"
        )
    return username, None


def _parse_bool(value: str, field: str) -> bool:
    """Реализовать внутренний этап parse bool step. Вспомогательная функция сохраняет
    детерминированность и тестируемость процесса.
    """
    normalized = str(value or "").strip().casefold()
    if normalized in _TRUE_VALUES:
        return True
    if normalized in _FALSE_VALUES:
        return False
    raise DestinationImportError(f"Поле {field} должно содержать true/false")


def _parse_weekdays(value: str) -> list[int]:
    """Реализовать внутренний этап parse weekdays step. Вспомогательная функция сохраняет
    детерминированность и тестируемость процесса.
    """
    normalized = str(value or "").strip()
    if not normalized:
        return []
    aliases = {
        "mon": 0,
        "monday": 0,
        "пн": 0,
        "tue": 1,
        "tuesday": 1,
        "вт": 1,
        "wed": 2,
        "wednesday": 2,
        "ср": 2,
        "thu": 3,
        "thursday": 3,
        "чт": 3,
        "fri": 4,
        "friday": 4,
        "пт": 4,
        "sat": 5,
        "saturday": 5,
        "сб": 5,
        "sun": 6,
        "sunday": 6,
        "вс": 6,
    }
    result: set[int] = set()
    for token in re.split(r"[|,;\s]+", normalized.casefold()):
        if not token:
            continue
        if token in aliases:
            result.add(aliases[token])
            continue
        try:
            day = int(token)
        except ValueError as exc:
            raise DestinationImportError(f"Неизвестный день недели: {token}") from exc
        if day < 0 or day > 6:
            raise DestinationImportError("Дни недели должны быть числами от 0 до 6")
        result.add(day)
    return sorted(result)


def _parse_time(value: str, field: str) -> time | None:
    """Реализовать внутренний этап parse time step. Вспомогательная функция сохраняет
    детерминированность и тестируемость процесса.
    """
    normalized = str(value or "").strip()
    if not normalized:
        return None
    try:
        parsed = time.fromisoformat(normalized)
    except ValueError as exc:
        raise DestinationImportError(f"Поле {field} должно быть в формате HH:MM") from exc
    return parsed.replace(tzinfo=None, second=0, microsecond=0)
