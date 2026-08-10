from __future__ import annotations

from datetime import time

import pytest

from app.enums import DestinationKind
from app.services.destination_bulk import (
    DestinationImportError,
    _from_mapping,
    _parse_bool,
    _parse_table,
    _parse_target,
    _parse_time,
    _parse_weekdays,
    parse_destination_file,
)


@pytest.mark.parametrize(
    ("content", "filename", "message"),
    [
        (b"\xff", "groups.txt", "UTF-8"),
        (b"group\x00name", "groups.txt", "Бинарные"),
        (b"# only comment\n", "groups.txt", "не содержит"),
        (b"group", "groups.xlsx", "TXT, CSV и TSV"),
        (b"unknown\nvalue\n", "groups.csv", "поддерживаемые заголовки"),
    ],
)
def test_destination_file_rejects_container_and_header_errors(
    content: bytes, filename: str, message: str
) -> None:
    """Проверить extension, encoding, binary, empty и unsupported header guards."""
    with pytest.raises(DestinationImportError, match=message):
        parse_destination_file(content, filename)


def test_destination_file_auto_detects_table_and_skips_blank_rows() -> None:
    """Проверить header auto-detection, Sniffer fallback и пустые CSV rows."""
    auto = parse_destination_file(
        b"username,title\npublic_group,Public\n,\n",
        "groups.txt",
    )
    assert len(auto) == 1
    assert auto[0].username == "public_group"

    fallback = parse_destination_file(
        b"username\npublic_group\n",
        "groups.csv",
    )
    assert len(fallback) == 1
    assert fallback[0].username == "public_group"
    with pytest.raises(DestinationImportError, match="строка заголовков"):
        _parse_table("", ".csv")


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("-100123", (None, -100123)),
        ("@public_group", ("public_group", None)),
        ("plain_group", ("plain_group", None)),
        ("https://t.me/s/public_group/12", ("public_group", None)),
    ],
)
def test_parse_target_accepts_supported_telegram_references(
    value: str, expected: tuple[str | None, int | None]
) -> None:
    """Проверить chat id, @username, plain username и public channel link."""
    assert _parse_target(value) == expected


@pytest.mark.parametrize(
    ("value", "message"),
    [
        ("https://example.com/group", "только ссылки t.me"),
        ("https://t.me/", "отсутствует username"),
        ("https://t.me/c/123/4", "не содержит публичного username"),
    ],
)
def test_parse_target_rejects_unsafe_or_unresolvable_links(value: str, message: str) -> None:
    """Проверить external host, empty path и private t.me/c reference."""
    with pytest.raises(DestinationImportError, match=message):
        _parse_target(value)


def mapping(**overrides: str) -> dict[str, str]:
    """Создать базовую import mapping с валидным публичным username."""
    values = {"username": "public_group", "enabled": "true"}
    values.update(overrides)
    return values


@pytest.mark.parametrize(
    ("values", "message"),
    [
        ({"username": "bad"}, "Некорректный username"),
        ({"username": "", "telegram_chat_id": ""}, "Укажите ссылку"),
        ({"topic_id": "0"}, "topic_id"),
        ({"kind": "unknown"}, "Неизвестный тип"),
        ({"permission_confirmed": "maybe"}, "true/false"),
        ({"allowed_start_time": "09:00"}, "задаются вместе"),
        (
            {"allowed_start_time": "09:00", "allowed_end_time": "09:00"},
            "не должны совпадать",
        ),
        ({"cooldown_minutes_override": "0"}, "cooldown"),
        (
            {"permission_expires_at": "2035-01-01T00:00:00Z"},
            "только для подтверждённой",
        ),
        (
            {
                "permission_confirmed": "true",
                "permission_expires_at": "2020-01-01T00:00:00Z",
                "permission_note": "Allowed",
            },
            "должен быть в будущем",
        ),
        ({"permission_confirmed": "true"}, "добавьте permission_note"),
        ({"allowed_weekdays": "unknown"}, "Неизвестный день"),
        ({"allowed_weekdays": "7"}, "от 0 до 6"),
        ({"allowed_start_time": "bad", "allowed_end_time": "10:00"}, "формате HH:MM"),
    ],
)
def test_mapping_converts_validation_failures_to_row_errors(
    values: dict[str, str], message: str
) -> None:
    """Проверить controlled per-row errors без остановки всего bulk import."""
    row = _from_mapping(2, mapping(**values), "source")
    assert row.error is not None
    assert message in row.error


def test_mapping_normalizes_aliases_expiry_windows_and_payload() -> None:
    """Проверить kind aliases, naive future expiry, weekdays/time и API payload."""
    row = _from_mapping(
        2,
        mapping(
            link="-100123",
            username="",
            topic_id="42",
            kind="канал",
            permission_confirmed="да",
            permission_note="Owner allowed",
            permission_expires_at="2035-01-01T00:00:00",
            enabled="нет",
            allowed_weekdays="пн,2,вс,,",
            allowed_start_time="09:30:45",
            allowed_end_time="18:15:30",
            cooldown_minutes_override="60",
        ),
        "source",
    )
    assert row.error is None
    assert row.telegram_chat_id == -100123
    assert row.kind == DestinationKind.FORUM_TOPIC
    assert row.allowed_weekdays == [0, 2, 6]
    assert row.allowed_start_time == time(9, 30)
    assert row.allowed_end_time == time(18, 15)
    assert row.enabled is False
    payload = row.payload("connection-id")
    assert payload["connection_id"] == "connection-id"
    assert payload["cooldown_minutes_override"] == 60

    channel = _from_mapping(3, mapping(kind="канал"), "channel")
    assert channel.error is None
    assert channel.kind == DestinationKind.CHANNEL


@pytest.mark.parametrize(
    ("value", "expected"),
    [("да", True), ("no", False), ("", False)],
)
def test_bool_parser_accepts_localized_values(value: str, expected: bool) -> None:
    """Проверить локализованные true/false aliases."""
    assert _parse_bool(value, "flag") is expected


def test_weekday_and_time_helpers_handle_empty_and_invalid_values() -> None:
    """Проверить empty helpers и самостоятельные controlled parse errors."""
    assert _parse_weekdays("") == []
    assert _parse_time("", "time") is None
    with pytest.raises(DestinationImportError, match="true/false"):
        _parse_bool("unknown", "flag")
    with pytest.raises(DestinationImportError, match="HH:MM"):
        _parse_time("25:00", "time")
