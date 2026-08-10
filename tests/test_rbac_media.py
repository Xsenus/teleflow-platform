from __future__ import annotations

from fastapi.testclient import TestClient

from tests.conftest import csrf_headers


def test_viewer_is_read_only(auth_client: TestClient) -> None:
    """Проверить сценарий viewer is read only. Тест завершается ошибкой при нарушении
    зафиксированного инварианта.
    """
    created = auth_client.post(
        "/api/v1/users",
        headers=csrf_headers(auth_client),
        json={
            "email": "viewer@example.com",
            "display_name": "Viewer",
            "password": "ViewerPassword_123!",
            "role": "viewer",
        },
    )
    assert created.status_code == 201, created.text

    viewer = TestClient(auth_client.app)
    login = viewer.post(
        "/api/v1/auth/login",
        json={"email": "viewer@example.com", "password": "ViewerPassword_123!"},
    )
    assert login.status_code == 200
    assert viewer.get("/api/v1/dashboard/summary").status_code == 200
    denied = viewer.post(
        "/api/v1/templates",
        headers=csrf_headers(viewer),
        json={"name": "Denied", "body": "No"},
    )
    assert denied.status_code == 403
    viewer.close()


def test_media_mime_allowlist(auth_client: TestClient) -> None:
    """Проверить сценарий media mime allowlist. Тест завершается ошибкой при нарушении
    зафиксированного инварианта.
    """
    denied = auth_client.post(
        "/api/v1/media",
        headers=csrf_headers(auth_client),
        files={"file": ("payload.exe", b"MZ", "application/x-msdownload")},
    )
    assert denied.status_code == 415
    allowed = auth_client.post(
        "/api/v1/media",
        headers=csrf_headers(auth_client),
        files={"file": ("note.txt", b"safe text", "text/plain")},
    )
    assert allowed.status_code == 201, allowed.text
    assert allowed.json()["size_bytes"] == 9
