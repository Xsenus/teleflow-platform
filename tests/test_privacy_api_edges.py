from __future__ import annotations

from fastapi.testclient import TestClient

from tests.conftest import csrf_headers
from tests.test_execution_fencing import _create_user_client


def test_privacy_export_lifecycle_and_error_paths(auth_client: TestClient) -> None:
    """Проверить полный пустой export и отказ для неготовых, повторных и неверных запросов."""
    created = auth_client.post(
        "/api/v1/privacy/requests",
        headers=csrf_headers(auth_client),
        json={"request_type": "export", "telegram_user_id": 777001},
    )
    assert created.status_code == 201, created.text
    request_id = created.json()["id"]
    listed = auth_client.get("/api/v1/privacy/requests")
    assert listed.status_code == 200, listed.text
    assert [item["id"] for item in listed.json()] == [request_id]

    not_ready = auth_client.get(f"/api/v1/privacy/requests/{request_id}/download")
    assert not_ready.status_code == 404
    processed = auth_client.post(
        f"/api/v1/privacy/requests/{request_id}/process",
        headers=csrf_headers(auth_client),
    )
    assert processed.status_code == 200, processed.text
    assert processed.json()["status"] == "completed"
    repeated = auth_client.post(
        f"/api/v1/privacy/requests/{request_id}/process",
        headers=csrf_headers(auth_client),
    )
    assert repeated.status_code == 409

    downloaded = auth_client.get(f"/api/v1/privacy/requests/{request_id}/download")
    assert downloaded.status_code == 200, downloaded.text
    assert downloaded.headers["content-type"].startswith("application/json")
    assert f"privacy-export-{request_id}.json" in downloaded.headers["content-disposition"]

    delete_request = auth_client.post(
        "/api/v1/privacy/requests",
        headers=csrf_headers(auth_client),
        json={"request_type": "delete", "telegram_chat_id": 777002},
    )
    assert delete_request.status_code == 201, delete_request.text
    wrong_type = auth_client.get(f"/api/v1/privacy/requests/{delete_request.json()['id']}/download")
    assert wrong_type.status_code == 409

    missing_subject = auth_client.post(
        "/api/v1/privacy/requests",
        headers=csrf_headers(auth_client),
        json={"request_type": "export", "conversation_id": "missing-conversation"},
    )
    assert missing_subject.status_code == 404
    assert (
        auth_client.post(
            "/api/v1/privacy/requests/missing-request/process",
            headers=csrf_headers(auth_client),
        ).status_code
        == 404
    )
    assert auth_client.get("/api/v1/privacy/requests/missing-request/download").status_code == 404


def test_privacy_delete_rbac_and_retention_endpoint(auth_client: TestClient, monkeypatch) -> None:
    """Проверить Owner-only удаление и возврат статистики ручного retention-запуска."""
    admin = _create_user_client(
        auth_client,
        email="privacy-admin@example.com",
        password="PrivacyAdmin_123!",
        role="admin",
    )
    try:
        denied = admin.post(
            "/api/v1/privacy/requests",
            headers=csrf_headers(admin),
            json={"request_type": "delete", "telegram_user_id": 88001},
        )
        assert denied.status_code == 403
    finally:
        admin.close()

    expected = {
        "conversations_scrubbed": 2,
        "updates_scrubbed": 3,
        "exports_deleted": 1,
        "support_bundles_deleted": 4,
        "recovery_evidence_expired": 5,
    }
    monkeypatch.setattr("app.api.privacy.RetentionService.run", lambda _service: expected)
    response = auth_client.post("/api/v1/privacy/retention/run", headers=csrf_headers(auth_client))
    assert response.status_code == 200, response.text
    assert response.json() == expected
