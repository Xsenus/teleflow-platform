from __future__ import annotations

from urllib.parse import parse_qs, urlparse

from fastapi.testclient import TestClient

from app.services.totp import code_at
from tests.conftest import csrf_headers


def test_cookie_write_requires_csrf(auth_client: TestClient) -> None:
    """Проверить сценарий cookie write requires csrf. Тест завершается ошибкой при нарушении
    зафиксированного инварианта.
    """
    denied = auth_client.post("/api/v1/auth/totp/start")
    assert denied.status_code == 403
    allowed = auth_client.post("/api/v1/auth/totp/start", headers=csrf_headers(auth_client))
    assert allowed.status_code == 200


def test_totp_enrollment_and_next_login(auth_client: TestClient) -> None:
    """Проверить сценарий totp enrollment and next login. Тест завершается ошибкой при нарушении
    зафиксированного инварианта.
    """
    headers = csrf_headers(auth_client)
    started = auth_client.post("/api/v1/auth/totp/start", headers=headers)
    assert started.status_code == 200, started.text
    uri = started.json()["provisioning_uri"]
    secret = parse_qs(urlparse(uri).query)["secret"][0]
    code = code_at(secret)
    confirmed = auth_client.post("/api/v1/auth/totp/confirm", json={"code": code}, headers=headers)
    assert confirmed.status_code == 200, confirmed.text

    logged_out = auth_client.post("/api/v1/auth/logout", headers=headers)
    assert logged_out.status_code == 200
    without_totp = auth_client.post(
        "/api/v1/auth/login",
        json={"email": "owner@example.com", "password": "OwnerPassword_123!"},
    )
    assert without_totp.status_code == 401
    with_totp = auth_client.post(
        "/api/v1/auth/login",
        json={
            "email": "owner@example.com",
            "password": "OwnerPassword_123!",
            "totp_code": code_at(secret),
        },
    )
    assert with_totp.status_code == 200, with_totp.text


def test_login_lockout(client: TestClient) -> None:
    """Проверить сценарий login lockout. Тест завершается ошибкой при нарушении зафиксированного
    инварианта.
    """
    for _ in range(5):
        response = client.post(
            "/api/v1/auth/login",
            json={"email": "owner@example.com", "password": "WrongPassword_123!"},
        )
        assert response.status_code == 401
    locked = client.post(
        "/api/v1/auth/login",
        json={"email": "owner@example.com", "password": "OwnerPassword_123!"},
    )
    assert locked.status_code == 423
