from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.api.destinations import _bulk_destination_result
from app.enums import ConnectionStatus, DestinationKind
from app.models import TelegramConnection
from app.services.destination_bulk import ParsedDestinationRow
from app.services.telegram.base import TelegramDestinationInfo
from app.services.telegram.errors import TelegramFloodWait, TelegramNotFound
from tests.conftest import csrf_headers
from tests.test_api_workflow import (
    create_campaign,
    create_connection,
    create_destination,
    create_template,
)


def _set_connection_status(
    client: TestClient, connection_id: str, status: ConnectionStatus
) -> None:
    """Установить состояние тестового подключения напрямую в изолированной базе."""

    with client.app.state.session_factory() as session:
        connection = session.scalar(
            select(TelegramConnection).where(TelegramConnection.id == connection_id)
        )
        assert connection is not None
        connection.status = status
        session.commit()


def _upload(client: TestClient, path: str, connection_id: str, filename: str, content: bytes):
    """Отправить файл импорта с обязательной CSRF-защитой."""

    return client.post(
        path,
        headers=csrf_headers(client),
        data={"connection_id": connection_id},
        files={"file": (filename, content, "text/plain")},
    )


def test_bulk_endpoints_reject_bad_files_and_inactive_connections(
    auth_client: TestClient,
) -> None:
    """Проверить ограничения размера, формата и состояния подключения для всех импортов."""

    connection = create_connection(auth_client)
    invalid = _upload(
        auth_client,
        "/api/v1/destinations/bulk/preview",
        connection["id"],
        "groups.xlsx",
        b"invalid",
    )
    assert invalid.status_code == 422

    oversized = _upload(
        auth_client,
        "/api/v1/destinations/bulk/preview",
        connection["id"],
        "groups.txt",
        b"x" * (2 * 1024 * 1024 + 1),
    )
    assert oversized.status_code == 413

    _set_connection_status(auth_client, connection["id"], ConnectionStatus.PAUSED)
    for path in ("/bulk/preview", "/bulk/apply"):
        response = _upload(
            auth_client,
            f"/api/v1/destinations{path}",
            connection["id"],
            "groups.txt",
            b"@allowed_group",
        )
        assert response.status_code == 409

    discovery = auth_client.post(
        "/api/v1/destinations/bulk/discovery",
        headers=csrf_headers(auth_client),
        json={
            "connection_id": connection["id"],
            "items": [{"telegram_chat_id": -1001, "title": "Группа"}],
        },
    )
    assert discovery.status_code == 409
    create = auth_client.post(
        "/api/v1/destinations",
        headers=csrf_headers(auth_client),
        json={"connection_id": connection["id"], "username": "inactive_group"},
    )
    assert create.status_code == 409


def test_missing_resources_and_gateway_errors_are_reported(
    auth_client: TestClient,
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    """Проверить стабильные HTTP-ошибки для отсутствующих ресурсов и Telegram-сбоев."""

    missing = "00000000-0000-0000-0000-000000000000"
    assert auth_client.get(f"/api/v1/destinations/{missing}").status_code == 404
    batch = auth_client.post(
        "/api/v1/destinations/validate-batch",
        headers=csrf_headers(auth_client),
        json={"destination_ids": [missing]},
    )
    assert batch.status_code == 200
    assert batch.json()["items"][0]["error_code"] == "DESTINATION_NOT_FOUND"

    payload = {"connection_id": missing, "username": "allowed_missing"}
    assert (
        auth_client.post(
            "/api/v1/destinations", headers=csrf_headers(auth_client), json=payload
        ).status_code
        == 404
    )
    for path in ("/bulk/apply",):
        assert (
            _upload(
                auth_client, f"/api/v1/destinations{path}", missing, "groups.txt", b"@x"
            ).status_code
            == 404
        )
    discovery = auth_client.post(
        "/api/v1/destinations/bulk/discovery",
        headers=csrf_headers(auth_client),
        json={"connection_id": missing, "items": [{"telegram_chat_id": -1001}]},
    )
    assert discovery.status_code == 404

    connection = create_connection(auth_client)

    class BrokenGateway:
        """Всегда возвращать заданную контролируемую ошибку Telegram."""

        def resolve_destination(self, **_kwargs):  # type: ignore[no-untyped-def]
            """Остановить разрешение назначения контролируемой transient-ошибкой."""

            raise TelegramFloodWait("Нужно подождать", retry_after=15)

    monkeypatch.setattr(
        "app.api.destinations.build_gateway", lambda *_args, **_kwargs: BrokenGateway()
    )
    failed = auth_client.post(
        "/api/v1/destinations",
        headers=csrf_headers(auth_client),
        json={"connection_id": connection["id"], "username": "allowed_error"},
    )
    assert failed.status_code == 503
    assert failed.json()["detail"]["code"] == "FLOOD_WAIT"


def test_patch_destination_enforces_permission_and_window_invariants(
    auth_client: TestClient,
) -> None:
    """Проверить нормализацию полей и все бизнес-ограничения формы редактирования."""

    connection = create_connection(auth_client)
    destination = create_destination(auth_client, connection["id"], confirmed=False)
    path = f"/api/v1/destinations/{destination['id']}"

    cases = [
        ({"allowed_start_time": "09:00"}, "задаются вместе"),
        (
            {"allowed_start_time": "09:00", "allowed_end_time": "09:00"},
            "не должны совпадать",
        ),
        ({"permission_status": "confirmed"}, "примечание"),
        (
            {
                "permission_status": "confirmed",
                "permission_note": "Разрешено",
                "permission_expires_at": (datetime.now(UTC) - timedelta(days=1)).isoformat(),
            },
            "в будущем",
        ),
    ]
    for payload, expected in cases:
        response = auth_client.patch(path, headers=csrf_headers(auth_client), json=payload)
        assert response.status_code == 422
        assert expected in response.text

    confirmed = auth_client.patch(
        path,
        headers=csrf_headers(auth_client),
        json={
            "permission_status": "confirmed",
            "rules_url": "https://example.com/rules",
            "allowed_weekdays": None,
            "allowed_start_time": "09:00",
            "allowed_end_time": "18:00",
        },
    )
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["rules_url"] == "https://example.com/rules"
    assert confirmed.json()["allowed_weekdays"] == []

    denied = auth_client.patch(
        path,
        headers=csrf_headers(auth_client),
        json={"permission_status": "denied"},
    )
    assert denied.status_code == 200
    assert denied.json()["enabled"] is False
    assert denied.json()["permission_expires_at"] is None


def test_manual_validation_records_success_and_failure(
    auth_client: TestClient,
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    """Проверить успешную и ошибочную ручную валидацию с историей и HTTP-кодом."""

    connection = create_connection(auth_client)
    destination = create_destination(auth_client, connection["id"])
    path = f"/api/v1/destinations/{destination['id']}/validate"
    success = auth_client.post(path, headers=csrf_headers(auth_client))
    assert success.status_code == 200, success.text
    assert success.json()["ok"] is True

    class MissingGateway:
        """Имитировать удалённое или недоступное Telegram-направление."""

        def resolve_destination(self, **_kwargs):  # type: ignore[no-untyped-def]
            """Вернуть контролируемую ошибку отсутствующего назначения."""

            raise TelegramNotFound("Группа не найдена")

    monkeypatch.setattr(
        "app.api.destinations.build_gateway", lambda *_args, **_kwargs: MissingGateway()
    )
    failure = auth_client.post(path, headers=csrf_headers(auth_client))
    assert failure.status_code == 400
    assert failure.json()["detail"]["code"] == "DESTINATION_NOT_FOUND"
    history = auth_client.get(
        f"/api/v1/destinations/{destination['id']}/validation-history", params={"limit": 500}
    )
    assert history.status_code == 200
    assert len(history.json()) >= 2


def test_delete_destination_blocks_linked_item_then_removes_free_item(
    auth_client: TestClient,
) -> None:
    """Проверить запрет удаления используемого назначения и удаление свободного."""

    connection = create_connection(auth_client)
    linked = create_destination(auth_client, connection["id"])
    template = create_template(auth_client)
    create_campaign(auth_client, connection["id"], template["id"], linked["id"])
    blocked = auth_client.delete(
        f"/api/v1/destinations/{linked['id']}", headers=csrf_headers(auth_client)
    )
    assert blocked.status_code == 409

    free = auth_client.post(
        "/api/v1/destinations",
        headers=csrf_headers(auth_client),
        json={"connection_id": connection["id"], "username": "allowed_free_group"},
    )
    assert free.status_code == 201, free.text
    removed = auth_client.delete(
        f"/api/v1/destinations/{free.json()['id']}", headers=csrf_headers(auth_client)
    )
    assert removed.status_code == 200
    assert auth_client.get(f"/api/v1/destinations/{free.json()['id']}").status_code == 404


def test_bulk_result_reports_invalid_gateway_and_deferred_rows(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Проверить изоляцию ошибочных строк и остановку после Telegram flood-wait."""

    class FloodGateway:
        """Отклонить разрешение корректной строки с обязательной паузой."""

        def resolve_destination(self, **_kwargs):  # type: ignore[no-untyped-def]
            """Вернуть flood-wait для проверки отложенных последующих строк."""

            raise TelegramFloodWait("Пауза", retry_after=20)

    class BulkDb:
        """Предоставить минимальный интерфейс сессии для dry-run импорта."""

        def scalar(self, _statement):  # type: ignore[no-untyped-def]
            """Сообщить, что дубликаты в базе отсутствуют."""

            return None

    monkeypatch.setattr(
        "app.api.destinations.build_gateway", lambda *_args, **_kwargs: FloodGateway()
    )
    rows = [
        ParsedDestinationRow(row_number=1, source="invalid"),
        ParsedDestinationRow(row_number=2, source="@flood", username="flood"),
        ParsedDestinationRow(row_number=3, source="@deferred", username="deferred"),
    ]
    result = _bulk_destination_result(
        rows=rows,
        connection=SimpleNamespace(id="connection-id"),
        user=SimpleNamespace(id="user-id", organization_id="organization-id"),
        request=SimpleNamespace(),
        db=BulkDb(),  # type: ignore[arg-type]
        settings=SimpleNamespace(),  # type: ignore[arg-type]
        cipher=SimpleNamespace(),  # type: ignore[arg-type]
        dry_run=True,
    )

    assert result.errors == 2
    assert result.deferred == 1
    assert [row.status for row in result.rows] == ["error", "error", "deferred"]


def test_bulk_result_converts_concurrent_insert_to_duplicate(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Проверить безопасную обработку гонки уникального индекса при массовом импорте."""

    class Gateway:
        """Разрешить тестовое назначение в стабильный Telegram-объект."""

        def resolve_destination(self, **_kwargs):  # type: ignore[no-untyped-def]
            """Вернуть детерминированные сведения о тестовой группе."""

            return TelegramDestinationInfo(
                chat_id=-100500,
                username="race_group",
                title="Группа с гонкой",
                kind=DestinationKind.SUPERGROUP,
            )

    class RaceDb:
        """Имитировать конфликт уникального индекса во вложенной транзакции."""

        def scalar(self, _statement):  # type: ignore[no-untyped-def]
            """Не находить существующее назначение до конкурентной вставки."""

            return None

        @contextmanager
        def begin_nested(self):  # type: ignore[no-untyped-def]
            """Открыть фиктивную вложенную транзакцию."""

            yield

        def add(self, _value) -> None:  # type: ignore[no-untyped-def]
            """Принять объект до имитации конфликта."""

        def flush(self) -> None:
            """Имитировать конкурентную вставку того же назначения."""

            raise IntegrityError("insert", {}, RuntimeError("duplicate"))

        def commit(self) -> None:
            """Зафиксировать внешний аудит без реальной базы."""

    monkeypatch.setattr("app.api.destinations.build_gateway", lambda *_args, **_kwargs: Gateway())
    monkeypatch.setattr("app.api.destinations.write_audit", lambda *_args, **_kwargs: None)
    result = _bulk_destination_result(
        rows=[ParsedDestinationRow(row_number=1, source="@race_group", username="race_group")],
        connection=SimpleNamespace(id="connection-id"),
        user=SimpleNamespace(id="user-id", organization_id="organization-id"),
        request=SimpleNamespace(),
        db=RaceDb(),  # type: ignore[arg-type]
        settings=SimpleNamespace(),  # type: ignore[arg-type]
        cipher=SimpleNamespace(),  # type: ignore[arg-type]
        dry_run=False,
    )

    assert result.created == 0
    assert result.duplicates == 1
    assert result.rows[0].message.startswith("Назначение было добавлено")
