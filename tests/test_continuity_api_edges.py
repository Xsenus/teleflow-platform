from __future__ import annotations

from fastapi.testclient import TestClient

from tests.conftest import csrf_headers
from tests.test_continuity_assurance import _register_runtime_pair


def test_continuity_api_empty_missing_conflict_and_history_lifecycle(
    auth_client: TestClient,
) -> None:
    """Проверить policy/list, tenant 404, service conflicts, events, verify и draft cancel."""
    policy = auth_client.get("/api/v1/continuity/policy")
    assert policy.status_code == 200, policy.text
    assert policy.json()["organization_id"]

    empty = auth_client.get("/api/v1/continuity/drills?limit=10")
    assert empty.status_code == 200
    assert empty.json() == []

    for path in (
        "/api/v1/continuity/drills/missing",
        "/api/v1/continuity/drills/missing/events",
        "/api/v1/continuity/drills/missing/verify",
    ):
        response = auth_client.get(path)
        assert response.status_code == 404, response.text

    for suffix in ("start", "failback"):
        response = auth_client.post(
            f"/api/v1/continuity/drills/missing/{suffix}",
            headers=csrf_headers(auth_client),
        )
        assert response.status_code == 409, response.text

    _register_runtime_pair(auth_client)
    created = auth_client.post(
        "/api/v1/continuity/drills",
        headers=csrf_headers(auth_client),
        json={"mode": "simulation", "target_site_key": "standby"},
    )
    assert created.status_code == 201, created.text
    drill_id = created.json()["id"]

    listed = auth_client.get("/api/v1/continuity/drills?limit=10")
    assert listed.status_code == 200
    assert [item["id"] for item in listed.json()] == [drill_id]

    events = auth_client.get(f"/api/v1/continuity/drills/{drill_id}/events")
    assert events.status_code == 200, events.text
    assert events.json()
    verified = auth_client.get(f"/api/v1/continuity/drills/{drill_id}/verify")
    assert verified.status_code == 200, verified.text
    assert verified.json()["valid"] is True

    cancelled = auth_client.post(
        f"/api/v1/continuity/drills/{drill_id}/cancel",
        headers=csrf_headers(auth_client),
    )
    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json()["status"] == "cancelled"
