from __future__ import annotations

import csv
import io

from fastapi.testclient import TestClient

from app.services.destination_bulk import DestinationImportError, parse_destination_file
from tests.conftest import csrf_headers
from tests.test_api_workflow import create_connection


def _upload(
    client: TestClient,
    path: str,
    *,
    connection_id: str,
    filename: str,
    content: str,
):
    """Реализовать внутренний этап upload step. Вспомогательная функция сохраняет
    детерминированность и тестируемость процесса.
    """
    return client.post(
        path,
        headers=csrf_headers(client),
        data={"connection_id": connection_id},
        files={"file": (filename, content.encode("utf-8"), "text/plain")},
    )


def test_parse_txt_rejects_invites_and_marks_plain_rows_unverified() -> None:
    """Проверить сценарий parse txt rejects invites and marks plain rows unverified. Тест
    завершается ошибкой при нарушении зафиксированного инварианта.
    """
    rows = parse_destination_file(
        """
        # комментарий
        https://t.me/allowed_jobs_one
        @allowed_jobs_two
        https://t.me/+privateInviteHash
        """.encode(),
        "groups.txt",
    )

    assert len(rows) == 3
    assert rows[0].username == "allowed_jobs_one"
    assert rows[0].permission_confirmed is False
    assert rows[1].username == "allowed_jobs_two"
    assert rows[2].error
    assert "Invite-ссылка" in rows[2].error


def test_parse_csv_supports_russian_headers_and_forum_topics() -> None:
    """Проверить сценарий parse csv supports russian headers and forum topics. Тест завершается
    ошибкой при нарушении зафиксированного инварианта.
    """
    content = (
        "ссылка;тема;название;тип;разрешено;основание;активно;часовой_пояс;дни_недели;время_с;время_до;кулдаун\n"
        "https://t.me/jobs_forum;42;Вакансии — разработка;тема;да;Разрешено администратором;да;Europe/Amsterdam;пн|вт|ср|чт|пт;09:00;18:00;720\n"
    )
    rows = parse_destination_file(content.encode(), "groups.csv")

    assert len(rows) == 1
    assert rows[0].username == "jobs_forum"
    assert rows[0].topic_id == 42
    assert rows[0].kind.value == "forum_topic"
    assert rows[0].permission_confirmed is True
    assert rows[0].permission_note == "Разрешено администратором"
    assert rows[0].timezone_name == "Europe/Amsterdam"
    assert rows[0].allowed_weekdays == [0, 1, 2, 3, 4]
    assert rows[0].allowed_start_time is not None
    assert rows[0].allowed_start_time.isoformat(timespec="minutes") == "09:00"
    assert rows[0].allowed_end_time is not None
    assert rows[0].allowed_end_time.isoformat(timespec="minutes") == "18:00"
    assert rows[0].cooldown_minutes_override == 720


def test_parse_file_limits_rows() -> None:
    """Проверить сценарий parse file limits rows. Тест завершается ошибкой при нарушении
    зафиксированного инварианта.
    """
    content = "\n".join(f"group_{index:04d}" for index in range(501)).encode()
    try:
        parse_destination_file(content, "groups.txt", max_rows=500)
    except DestinationImportError as exc:
        assert "не более 500" in str(exc)
    else:  # pragma: no cover - explicit failure message
        raise AssertionError("Expected DestinationImportError")


def test_preview_and_apply_bulk_import(auth_client: TestClient) -> None:
    """Проверить сценарий preview and apply bulk import. Тест завершается ошибкой при нарушении
    зафиксированного инварианта.
    """
    connection = create_connection(auth_client)
    csv_content = (
        "link,title,permission_confirmed,permission_note,enabled,timezone_name,allowed_weekdays,allowed_start_time,allowed_end_time,cooldown_minutes_override\n"
        'https://t.me/allowed_jobs_one,Первая группа,true,Публикации согласованы,true,Europe/Amsterdam,"0|1|2|3|4",09:00,18:00,720\n'
        "https://t.me/allowed_jobs_two,Вторая группа,false,,true,,,,,\n"
        "https://t.me/allowed_jobs_one,Повтор,true,Публикации согласованы,true,,,,,\n"
        "https://t.me/+privateInviteHash,Приватная группа,false,,true,,,,,\n"
    )

    preview = _upload(
        auth_client,
        "/api/v1/destinations/bulk/preview",
        connection_id=connection["id"],
        filename="destinations.csv",
        content=csv_content,
    )
    assert preview.status_code == 200, preview.text
    preview_body = preview.json()
    assert preview_body["dry_run"] is True
    assert preview_body["ready"] == 2
    assert preview_body["duplicates"] == 1
    assert preview_body["errors"] == 1
    assert {row["status"] for row in preview_body["rows"]} == {
        "ready",
        "duplicate",
        "error",
    }

    destinations_before = auth_client.get("/api/v1/destinations")
    assert destinations_before.status_code == 200
    assert destinations_before.json() == []

    applied = _upload(
        auth_client,
        "/api/v1/destinations/bulk/apply",
        connection_id=connection["id"],
        filename="destinations.csv",
        content=csv_content,
    )
    assert applied.status_code == 200, applied.text
    body = applied.json()
    assert body["dry_run"] is False
    assert body["created"] == 2
    assert body["duplicates"] == 1
    assert body["errors"] == 1

    destinations = auth_client.get("/api/v1/destinations").json()
    assert len(destinations) == 2
    by_title = {item["title"]: item for item in destinations}
    assert by_title["Первая группа"]["permission_status"] == "confirmed"
    assert by_title["Первая группа"]["allowed_weekdays"] == [0, 1, 2, 3, 4]
    assert by_title["Первая группа"]["cooldown_minutes_override"] == 720
    assert by_title["Вторая группа"]["permission_status"] == "unverified"

    repeated = _upload(
        auth_client,
        "/api/v1/destinations/bulk/apply",
        connection_id=connection["id"],
        filename="destinations.csv",
        content=csv_content,
    )
    assert repeated.status_code == 200
    assert repeated.json()["created"] == 0
    assert repeated.json()["duplicates"] == 3
    assert repeated.json()["errors"] == 1


def test_export_destinations_csv_has_utf8_bom_and_roundtrips(auth_client: TestClient) -> None:
    """Проверить сценарий export destinations csv has utf8 bom and roundtrips. Тест завершается
    ошибкой при нарушении зафиксированного инварианта.
    """
    connection = create_connection(auth_client)
    csv_content = (
        "link,title,permission_confirmed,permission_note,enabled,timezone_name,allowed_weekdays,allowed_start_time,allowed_end_time,cooldown_minutes_override\n"
        "https://t.me/allowed_jobs_export,Группа экспорта,true,Разрешено правилами,true,Europe/Amsterdam,0|1|2|3|4,09:00,18:00,360\n"
    )
    applied = _upload(
        auth_client,
        "/api/v1/destinations/bulk/apply",
        connection_id=connection["id"],
        filename="destinations.csv",
        content=csv_content,
    )
    assert applied.status_code == 200, applied.text

    response = auth_client.get(
        "/api/v1/destinations/export.csv",
        params={"connection_id": connection["id"]},
    )
    assert response.status_code == 200
    assert response.content.startswith(b"\xef\xbb\xbf")
    assert "attachment" in response.headers["content-disposition"]

    decoded = response.content.decode("utf-8-sig")
    rows = list(csv.DictReader(io.StringIO(decoded)))
    assert len(rows) == 1
    assert rows[0]["username"] == "allowed_jobs_export"
    assert rows[0]["title"] == "Группа экспорта"
    assert rows[0]["permission_confirmed"] == "True"
    assert rows[0]["timezone_name"] == "Europe/Amsterdam"
    assert rows[0]["allowed_weekdays"] == "0|1|2|3|4"
    assert rows[0]["allowed_start_time"] == "09:00"
    assert rows[0]["allowed_end_time"] == "18:00"
    assert rows[0]["cooldown_minutes_override"] == "360"


def test_bulk_import_requires_active_owned_connection(auth_client: TestClient) -> None:
    """Проверить сценарий bulk import requires active owned connection. Тест завершается ошибкой
    при нарушении зафиксированного инварианта.
    """
    response = _upload(
        auth_client,
        "/api/v1/destinations/bulk/preview",
        connection_id="00000000-0000-0000-0000-000000000000",
        filename="groups.txt",
        content="https://t.me/allowed_jobs_one\n",
    )
    assert response.status_code == 404


def test_parse_file_rejects_unsupported_extension() -> None:
    """Проверить сценарий parse file rejects unsupported extension. Тест завершается ошибкой при
    нарушении зафиксированного инварианта.
    """
    try:
        parse_destination_file(b"https://t.me/allowed_jobs_one", "groups.xlsx")
    except DestinationImportError as exc:
        assert "TXT, CSV и TSV" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("Expected DestinationImportError")


def test_discovery_import_adds_only_selected_existing_dialogs(auth_client: TestClient) -> None:
    """Проверить сценарий discovery import adds only selected existing dialogs. Тест завершается
    ошибкой при нарушении зафиксированного инварианта.
    """
    connection = create_connection(auth_client)
    discovered_response = auth_client.get(f"/api/v1/connections/{connection['id']}/discover")
    assert discovered_response.status_code == 200, discovered_response.text
    discovered = discovered_response.json()
    assert len(discovered) == 2

    selected = [discovered[0]]
    imported = auth_client.post(
        "/api/v1/destinations/bulk/discovery",
        headers=csrf_headers(auth_client),
        json={
            "connection_id": connection["id"],
            "items": selected,
            "permission_confirmed": False,
            "timezone_name": "Europe/Amsterdam",
            "allowed_weekdays": [0, 1, 2, 3, 4],
            "allowed_start_time": "09:00",
            "allowed_end_time": "18:00",
        },
    )
    assert imported.status_code == 200, imported.text
    body = imported.json()
    assert body["created"] == 1
    assert body["errors"] == 0

    destinations = auth_client.get(
        "/api/v1/destinations", params={"connection_id": connection["id"]}
    ).json()
    assert len(destinations) == 1
    assert destinations[0]["telegram_chat_id"] == selected[0]["telegram_chat_id"]
    assert destinations[0]["permission_status"] == "unverified"
    assert destinations[0]["allowed_weekdays"] == [0, 1, 2, 3, 4]

    repeated = auth_client.post(
        "/api/v1/destinations/bulk/discovery",
        headers=csrf_headers(auth_client),
        json={"connection_id": connection["id"], "items": selected},
    )
    assert repeated.status_code == 200
    assert repeated.json()["duplicates"] == 1


def test_discovery_import_requires_permission_evidence_when_confirmed(
    auth_client: TestClient,
) -> None:
    """Проверить сценарий discovery import requires permission evidence when confirmed. Тест
    завершается ошибкой при нарушении зафиксированного инварианта.
    """
    connection = create_connection(auth_client)
    discovered = auth_client.get(f"/api/v1/connections/{connection['id']}/discover").json()
    response = auth_client.post(
        "/api/v1/destinations/bulk/discovery",
        headers=csrf_headers(auth_client),
        json={
            "connection_id": connection["id"],
            "items": [discovered[0]],
            "permission_confirmed": True,
        },
    )
    assert response.status_code == 422
    assert "примечание" in response.text
