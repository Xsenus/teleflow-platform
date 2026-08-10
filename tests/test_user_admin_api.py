from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError

from app.api import users as user_api
from app.config import Settings
from app.enums import UserRole
from app.models import User
from app.schemas import UserCreate
from tests.conftest import csrf_headers


def create_user(
    client: TestClient,
    *,
    email: str,
    role: str = "operator",
    display_name: str = "Test User",
) -> dict:
    """Создать пользователя через административную форму и вернуть response body."""

    response = client.post(
        "/api/v1/users",
        headers=csrf_headers(client),
        json={
            "email": email,
            "display_name": display_name,
            "password": "UserPassword_123!",
            "role": role,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_user_admin_full_lifecycle_and_owner_invariants(auth_client: TestClient) -> None:
    """Проверить list/create/update/deactivate и защиту последнего владельца."""

    initial = auth_client.get("/api/v1/users")
    assert initial.status_code == 200 and len(initial.json()) == 1
    owner = initial.json()[0]

    operator = create_user(
        auth_client,
        email="OPERATOR@EXAMPLE.COM",
        display_name="Lifecycle Operator",
    )
    assert operator["email"] == "operator@example.com"
    assert operator["must_change_password"] is True
    assert "password_hash" not in operator

    duplicate = auth_client.post(
        "/api/v1/users",
        headers=csrf_headers(auth_client),
        json={
            "email": "operator@example.com",
            "display_name": "Duplicate",
            "password": "UserPassword_123!",
            "role": "viewer",
        },
    )
    assert duplicate.status_code == 409
    assert (
        auth_client.patch(
            "/api/v1/users/missing",
            headers=csrf_headers(auth_client),
            json={"display_name": "Missing"},
        ).status_code
        == 404
    )

    updated = auth_client.patch(
        f"/api/v1/users/{operator['id']}",
        headers=csrf_headers(auth_client),
        json={"display_name": "Lifecycle Admin", "role": "admin", "is_active": True},
    )
    assert updated.status_code == 200
    assert updated.json()["display_name"] == "Lifecycle Admin"
    assert updated.json()["role"] == "admin"

    self_disable = auth_client.patch(
        f"/api/v1/users/{owner['id']}",
        headers=csrf_headers(auth_client),
        json={"is_active": False},
    )
    assert self_disable.status_code == 400 and "собственную" in self_disable.text
    last_owner = auth_client.patch(
        f"/api/v1/users/{owner['id']}",
        headers=csrf_headers(auth_client),
        json={"role": "admin"},
    )
    assert last_owner.status_code == 400 and "хотя бы один владелец" in last_owner.text

    assert (
        auth_client.delete("/api/v1/users/missing", headers=csrf_headers(auth_client)).status_code
        == 404
    )
    self_delete = auth_client.delete(
        f"/api/v1/users/{owner['id']}", headers=csrf_headers(auth_client)
    )
    assert self_delete.status_code == 400 and "собственную" in self_delete.text
    deleted = auth_client.delete(
        f"/api/v1/users/{operator['id']}", headers=csrf_headers(auth_client)
    )
    assert deleted.status_code == 200 and deleted.json()["message"] == "Пользователь отключён"
    listed = auth_client.get("/api/v1/users").json()
    stored = next(item for item in listed if item["id"] == operator["id"])
    assert stored["is_active"] is False


def test_owner_can_be_demoted_when_second_owner_exists(auth_client: TestClient) -> None:
    """Проверить разрешённое понижение владельца при наличии второго Owner."""

    current = auth_client.get("/api/v1/users").json()[0]
    second = create_user(
        auth_client,
        email="second-owner@example.com",
        role="owner",
        display_name="Second Owner",
    )
    assert second["role"] == "owner"
    demoted = auth_client.patch(
        f"/api/v1/users/{current['id']}",
        headers=csrf_headers(auth_client),
        json={"role": "admin"},
    )
    assert demoted.status_code == 200, demoted.text
    assert demoted.json()["role"] == "admin"


def test_create_user_maps_concurrent_unique_race_to_conflict(
    monkeypatch: pytest.MonkeyPatch, settings: Settings
) -> None:
    """Проверить HTTP 409 при unique race между precheck и database flush."""

    db = MagicMock()
    db.scalar.return_value = None
    db.flush.side_effect = IntegrityError("INSERT users", {}, RuntimeError("duplicate"))
    monkeypatch.setattr(user_api, "hash_password", lambda *_args: "hashed-password")
    owner = User(
        id="owner-id",
        organization_id="organization-id",
        email="owner@example.com",
        display_name="Owner",
        password_hash="owner-hash",
        role=UserRole.OWNER,
    )
    payload = UserCreate(
        email="race@example.com",
        display_name="Race User",
        password="UserPassword_123!",
        role=UserRole.VIEWER,
    )

    with pytest.raises(HTTPException) as caught:
        user_api.create_user(
            payload,
            SimpleNamespace(),  # type: ignore[arg-type]
            owner,
            db,
            settings,
        )
    assert caught.value.status_code == 409
    db.rollback.assert_called_once_with()
    db.commit.assert_not_called()
