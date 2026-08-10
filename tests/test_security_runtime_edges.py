from __future__ import annotations

import asyncio
import io
import socket
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import HTTPException
from fastapi.responses import Response
from fastapi.security import HTTPAuthorizationCredentials
from starlette.datastructures import Headers, UploadFile
from starlette.requests import Request

from app import dependencies
from app.api import media
from app.enums import OrganizationStatus, UserRole
from app.middleware import CSRFMiddleware, IPAllowlistMiddleware, SecurityHeadersMiddleware
from app.models import MediaAsset
from app.services.url_security import UnsafeURL, validate_outbound_url


def request_for(
    method: str = "GET",
    path: str = "/",
    *,
    headers: dict[str, str] | None = None,
    client: tuple[str, int] | None = ("127.0.0.1", 1234),
) -> Request:
    """Создать Starlette Request с контролируемыми headers/client."""
    raw_headers = [
        (key.lower().encode("latin-1"), value.encode("latin-1"))
        for key, value in (headers or {}).items()
    ]
    return Request(
        {
            "type": "http",
            "http_version": "1.1",
            "method": method,
            "scheme": "https",
            "path": path,
            "raw_path": path.encode(),
            "query_string": b"",
            "headers": raw_headers,
            "client": client,
            "server": ("testserver", 443),
        }
    )


async def dummy_app(_scope: object, _receive: object, _send: object) -> None:
    """Предоставить пустое ASGI-приложение для middleware unit-тестов."""


async def ok_response(_request: Request) -> Response:
    """Вернуть успешный ответ из middleware call_next."""
    return Response("ok")


def test_validate_outbound_url_enforces_scheme_credentials_dns_and_ip_classes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Проверить SSRF-защиту для scheme, credentials, local DNS и служебных адресов."""
    for value in ("ftp://example.com", "https:///missing-host"):
        with pytest.raises(UnsafeURL, match="схему"):
            validate_outbound_url(value)
    with pytest.raises(UnsafeURL, match="Credentials"):
        validate_outbound_url("https://user:pass@example.com")
    with pytest.raises(UnsafeURL, match="Локальные"):
        validate_outbound_url("https://service.local")

    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(socket.gaierror("dns")),
    )
    with pytest.raises(UnsafeURL, match="разрешить hostname"):
        validate_outbound_url("https://missing.example")

    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: [(None, None, None, None, ("127.0.0.1", 443))],
    )
    with pytest.raises(UnsafeURL, match="приватный"):
        validate_outbound_url("https://example.com")
    assert validate_outbound_url("http://localhost", allow_private=True, require_https=False) == (
        "http://localhost"
    )


@pytest.mark.parametrize(
    ("content_type", "header"),
    [
        ("image/jpeg", b"\xff\xd8\xffpayload"),
        ("image/png", b"\x89PNG\r\n\x1a\npayload"),
        ("image/webp", b"RIFF0000WEBPpayload"),
        ("image/gif", b"GIF89apayload"),
        ("video/mp4", b"0000ftyp0000"),
        ("application/pdf", b"%PDF-1.7"),
        ("application/zip", b"PK\x03\x04payload"),
        ("text/plain", "Текст".encode()),
    ],
)
def test_media_signature_accepts_each_allowed_format(content_type: str, header: bytes) -> None:
    """Проверить magic bytes всех разрешённых upload MIME."""
    assert media._signature_matches(content_type, header) is True
    assert media._signature_matches("application/octet-stream", header) is False


def upload_file(payload: bytes, filename: str = "file.txt") -> UploadFile:
    """Создать синхронно читаемый UploadFile для streaming validator."""
    return UploadFile(
        io.BytesIO(payload),
        filename=filename,
        headers=Headers({"content-type": "text/plain"}),
    )


def test_read_validated_upload_rejects_size_empty_and_incomplete_utf8() -> None:
    """Проверить streaming size gate, empty payload и final UTF-8 decoder state."""
    with pytest.raises(HTTPException) as too_large:
        media._read_validated_upload(upload_file(b"abc"), "text/plain", 2)
    assert too_large.value.status_code == 413

    with pytest.raises(HTTPException) as empty:
        media._read_validated_upload(upload_file(b""), "text/plain", 10)
    assert empty.value.status_code == 400

    with pytest.raises(HTTPException) as incomplete:
        media._read_validated_upload(upload_file(b"text\xe2\x82"), "text/plain", 100)
    assert incomplete.value.status_code == 415


def test_security_headers_add_hsts_only_in_production() -> None:
    """Проверить обязательные browser headers и production HSTS."""
    middleware = SecurityHeadersMiddleware(
        dummy_app,
        SimpleNamespace(is_production=True),  # type: ignore[arg-type]
    )
    response = asyncio.run(middleware.dispatch(request_for(), ok_response))
    assert response.headers["strict-transport-security"].startswith("max-age=")
    assert response.headers["content-security-policy"].startswith("default-src")


def test_csrf_middleware_bypasses_non_cookie_clients_and_checks_cookie_pair() -> None:
    """Проверить bearer/API-key/no-cookie bypass и отказ неверной cookie CSRF-пары."""
    settings = SimpleNamespace(access_cookie_name="access", csrf_cookie_name="csrf")
    middleware = CSRFMiddleware(dummy_app, settings)  # type: ignore[arg-type]
    for headers in (
        {"authorization": "Bearer token"},
        {"x-api-key": "key"},
        {},
    ):
        response = asyncio.run(
            middleware.dispatch(request_for("POST", "/api/v1/action", headers=headers), ok_response)
        )
        assert response.status_code == 200

    denied = asyncio.run(
        middleware.dispatch(
            request_for(
                "POST",
                "/api/v1/action",
                headers={"cookie": "access=session; csrf=cookie", "x-csrf-token": "wrong"},
            ),
            ok_response,
        )
    )
    assert denied.status_code == 403


def test_ip_allowlist_handles_host_fallback_invalid_denied_and_allowed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Проверить normalization allowlist и все решения по client/forwarded IP."""
    original = __import__("ipaddress").ip_network
    calls = 0

    def first_parse_fails(value: str, *, strict: bool = False) -> Any:
        """Имитировать первый parse failure и успешный /32 fallback."""
        nonlocal calls
        calls += 1
        if calls == 1:
            raise ValueError("raw host")
        return original(value, strict=strict)

    monkeypatch.setattr("app.middleware.ipaddress.ip_network", first_parse_fails)
    middleware = IPAllowlistMiddleware(
        dummy_app,
        SimpleNamespace(ip_allowlist_values=["127.0.0.1"], trust_proxy_headers=True),  # type: ignore[arg-type]
    )
    assert calls == 2

    invalid = asyncio.run(middleware.dispatch(request_for(client=None), ok_response))
    assert invalid.status_code == 403
    denied = asyncio.run(middleware.dispatch(request_for(client=("192.0.2.1", 1234)), ok_response))
    assert denied.status_code == 403
    allowed = asyncio.run(middleware.dispatch(request_for(), ok_response))
    assert allowed.status_code == 200
    forwarded = asyncio.run(
        middleware.dispatch(
            request_for(headers={"x-forwarded-for": "127.0.0.1, 10.0.0.1"}), ok_response
        )
    )
    assert forwarded.status_code == 200


class GetSession:
    """Вернуть подготовленные значения db.get/scalar для dependency/media тестов."""

    def __init__(
        self,
        get_values: list[object | None] | None = None,
        scalar_values: list[object | None] | None = None,
    ) -> None:
        """Сохранить последовательности ответов и mutation counters."""
        self.get_values = list(get_values or [])
        self.scalar_values = list(scalar_values or [])
        self.deleted: list[object] = []
        self.commits = 0

    def get(self, _model: object, _key: object) -> object | None:
        """Вернуть следующий db.get результат."""
        return self.get_values.pop(0) if self.get_values else None

    def scalar(self, _statement: object) -> object | None:
        """Вернуть следующий db.scalar результат."""
        return self.scalar_values.pop(0) if self.scalar_values else None

    def delete(self, item: object) -> None:
        """Зафиксировать удаляемую модель."""
        self.deleted.append(item)

    def commit(self) -> None:
        """Зафиксировать commit."""
        self.commits += 1


def active_user(**overrides: Any) -> SimpleNamespace:
    """Создать authenticated user для dependency guards."""
    values = {
        "id": "user-id",
        "organization_id": "organization-id",
        "is_active": True,
        "role": UserRole.OWNER,
        "totp_enabled": True,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def dependency_settings(**overrides: Any) -> SimpleNamespace:
    """Создать настройки authentication dependency."""
    values = {
        "access_cookie_name": "access",
        "require_admin_totp": False,
        "api_prefix": "/api/v1",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


@pytest.mark.parametrize(
    ("user", "organization", "payload", "settings", "status_code"),
    [
        (active_user(is_active=False), None, {"sub": "u", "org": "organization-id"}, {}, 401),
        (active_user(), None, {"sub": "u", "org": "other"}, {}, 401),
        (
            active_user(),
            SimpleNamespace(status="suspended"),
            {"sub": "u", "org": "organization-id"},
            {},
            403,
        ),
        (
            active_user(totp_enabled=False),
            SimpleNamespace(status=OrganizationStatus.ACTIVE),
            {"sub": "u", "org": "organization-id"},
            {"require_admin_totp": True},
            428,
        ),
    ],
)
def test_current_user_dependency_rejects_invalid_runtime_state(
    monkeypatch: pytest.MonkeyPatch,
    user: SimpleNamespace,
    organization: SimpleNamespace | None,
    payload: dict[str, str],
    settings: dict[str, Any],
    status_code: int,
) -> None:
    """Проверить disabled user, session tenant, organization status и обязательную 2FA."""
    monkeypatch.setattr(dependencies, "decode_access_token", lambda *_args: payload)
    with pytest.raises(HTTPException) as error:
        dependencies.get_current_user(
            request_for(path="/api/v1/dashboard"),
            db=GetSession([user, organization]),  # type: ignore[arg-type]
            credentials=HTTPAuthorizationCredentials(scheme="Bearer", credentials="token"),
            settings=dependency_settings(**settings),  # type: ignore[arg-type]
        )
    assert error.value.status_code == status_code


def test_organization_and_tenant_dependencies_return_controlled_404() -> None:
    """Проверить отсутствие organization и tenant-scoped entity."""
    with pytest.raises(HTTPException) as missing_org:
        dependencies.get_current_organization(
            user=active_user(),  # type: ignore[arg-type]
            db=GetSession([None]),  # type: ignore[arg-type]
        )
    assert missing_org.value.status_code == 404

    with pytest.raises(HTTPException, match="Custom missing"):
        dependencies.tenant_get_or_404(
            GetSession(scalar_values=[None]),  # type: ignore[arg-type]
            MediaAsset,
            "asset-id",
            "organization-id",
            detail="Custom missing",
        )
    entity = object()
    assert (
        dependencies.tenant_get_or_404(
            GetSession(scalar_values=[entity]),  # type: ignore[arg-type]
            MediaAsset,
            "asset-id",
            "organization-id",
        )
        is entity
    )


def test_delete_media_handles_missing_in_use_and_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Проверить 404/409 и согласованное DB/storage удаление media asset."""
    request = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(storage=SimpleNamespace(deleted=[])))
    )
    request.app.state.storage.delete = request.app.state.storage.deleted.append
    user = active_user()
    with pytest.raises(HTTPException) as missing:
        media.delete_media(
            "missing",
            request,
            user=user,
            db=GetSession(),  # type: ignore[arg-type]
        )
    assert missing.value.status_code == 404

    asset = SimpleNamespace(id="asset-id", storage_key=None, relative_path="media/key")
    with pytest.raises(HTTPException) as in_use:
        media.delete_media(
            "asset-id",
            request,  # type: ignore[arg-type]
            user=user,  # type: ignore[arg-type]
            db=GetSession(scalar_values=[asset, 1, 0]),  # type: ignore[arg-type]
        )
    assert in_use.value.status_code == 409

    monkeypatch.setattr(media, "write_audit", lambda *_args, **_kwargs: None)
    db = GetSession(scalar_values=[asset, 0, 0])
    result = media.delete_media(
        "asset-id",
        request,  # type: ignore[arg-type]
        user=user,  # type: ignore[arg-type]
        db=db,  # type: ignore[arg-type]
    )
    assert result.message == "Файл удалён"
    assert db.deleted == [asset]
    assert request.app.state.storage.deleted == ["media/key"]


class UploadFailSession(GetSession):
    """Имитировать ошибку БД после успешной записи upload в storage."""

    def __init__(self) -> None:
        """Инициализировать признаки rollback."""
        super().__init__()
        self.rollbacks = 0

    def add(self, _item: object) -> None:
        """Выбросить исходную ошибку persistence."""
        raise RuntimeError("database failed")

    def rollback(self) -> None:
        """Зафиксировать откат DB-транзакции."""
        self.rollbacks += 1


def test_upload_media_removes_storage_object_after_database_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Проверить compensating cleanup upload при ошибке persistence."""
    storage = SimpleNamespace(written={}, deleted=[])
    storage.put_bytes = lambda key, data, *, content_type: storage.written.update({key: data})
    storage.delete = storage.deleted.append
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(storage=storage)))
    monkeypatch.setattr(
        media,
        "scan_media_bytes",
        lambda *_args, **_kwargs: SimpleNamespace(status="disabled"),
    )
    db = UploadFailSession()
    with pytest.raises(RuntimeError, match="database failed"):
        media.upload_media(
            request,  # type: ignore[arg-type]
            upload_file(b"plain text", "note.txt"),
            user=active_user(),  # type: ignore[arg-type]
            db=db,  # type: ignore[arg-type]
            settings=SimpleNamespace(
                max_media_bytes=1024,
                storage_backend="local",
                antivirus_mode="disabled",
                antivirus_fail_closed=True,
            ),  # type: ignore[arg-type]
        )
    assert db.rollbacks == 1
    assert len(storage.deleted) == 1
