from __future__ import annotations

from datetime import UTC, datetime

from fastapi.testclient import TestClient

from app.enums import OrganizationStatus, UserRole
from app.models import Organization, User
from app.security import hash_password
from tests.conftest import csrf_headers
from tests.test_api_workflow import create_connection


def test_tenant_isolation_for_connections(auth_client: TestClient) -> None:
    """Проверить сценарий tenant isolation for connections. Тест завершается ошибкой при нарушении
    зафиксированного инварианта.
    """
    foreign_connection = create_connection(auth_client)
    with auth_client.app.state.session_factory() as db:
        org = Organization(
            name="Second tenant",
            slug="second-tenant",
            status=OrganizationStatus.ACTIVE,
            timezone_name="Europe/Helsinki",
            retention_days=30,
            ai_enabled=False,
            settings={},
        )
        db.add(org)
        db.flush()
        user = User(
            organization_id=org.id,
            email="second-owner@example.com",
            display_name="Second Owner",
            password_hash=hash_password("SecondOwner_123!"),
            role=UserRole.OWNER,
            is_active=True,
            must_change_password=False,
        )
        db.add(user)
        db.commit()

    second = TestClient(auth_client.app)
    login = second.post(
        "/api/v1/auth/login",
        json={"email": "second-owner@example.com", "password": "SecondOwner_123!"},
    )
    assert login.status_code == 200, login.text
    assert second.get("/api/v1/connections").json() == []
    hidden = second.post(
        f"/api/v1/connections/{foreign_connection['id']}/health",
        headers=csrf_headers(second),
    )
    assert hidden.status_code == 404
    second.close()


def test_api_key_scopes_and_revocation(auth_client: TestClient) -> None:
    """Проверить сценарий api key scopes and revocation. Тест завершается ошибкой при нарушении
    зафиксированного инварианта.
    """
    created = auth_client.post(
        "/api/v1/api-keys",
        headers=csrf_headers(auth_client),
        json={
            "name": "CRM read-only",
            "scopes": ["candidates:read"],
            "expires_at": datetime(2030, 1, 1, tzinfo=UTC).isoformat(),
        },
    )
    assert created.status_code == 201, created.text
    secret = created.json()["secret"]
    key_id = created.json()["api_key"]["id"]
    assert secret.startswith("tfp_")

    allowed = auth_client.get("/api/v1/external/candidates", headers={"X-API-Key": secret})
    assert allowed.status_code == 200
    denied = auth_client.get(
        "/api/v1/external/candidates/nonexistent/contact",
        headers={"X-API-Key": secret},
    )
    assert denied.status_code == 403

    revoked = auth_client.post(
        f"/api/v1/api-keys/{key_id}/revoke", headers=csrf_headers(auth_client)
    )
    assert revoked.status_code == 200
    after = auth_client.get("/api/v1/external/candidates", headers={"X-API-Key": secret})
    assert after.status_code == 401


def test_ssrf_guards_and_metrics(auth_client: TestClient) -> None:
    """Проверить сценарий ssrf guards and metrics. Тест завершается ошибкой при нарушении
    зафиксированного инварианта.
    """
    webhook = auth_client.post(
        "/api/v1/integrations",
        headers=csrf_headers(auth_client),
        json={
            "name": "Unsafe local webhook",
            "kind": "webhook",
            "config": {"url": "http://127.0.0.1:8080/internal"},
            "event_types": [],
            "is_active": False,
        },
    )
    assert webhook.status_code == 422

    provider = auth_client.post(
        "/api/v1/automation/providers",
        headers=csrf_headers(auth_client),
        json={
            "name": "Unsafe AI",
            "kind": "openai_compatible",
            "base_url": "http://127.0.0.1:8000/v1",
            "model_name": "test",
            "api_key": "secret",
            "enabled": True,
        },
    )
    assert provider.status_code == 422

    metrics = auth_client.get("/metrics")
    assert metrics.status_code == 200
    assert "teleflow_http_requests_total" in metrics.text
    ready = auth_client.get("/api/v1/health/ready")
    assert ready.status_code == 200, ready.text
    assert ready.json()["checks"]["database"] is True
    assert ready.json()["checks"]["storage"] is True
