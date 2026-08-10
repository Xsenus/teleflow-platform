from __future__ import annotations

from fastapi.testclient import TestClient

from tests.conftest import csrf_headers
from tests.test_api_workflow import create_connection


def test_knowledge_provider_and_policy_forms_cover_admin_lifecycle(
    auth_client: TestClient,
) -> None:
    """Проверить CRUD административных форм automation и их конфликтные состояния."""

    headers = csrf_headers(auth_client)
    article = auth_client.post(
        "/api/v1/automation/knowledge",
        headers=headers,
        json={
            "title": "Python vacancy",
            "content": "Требуется знание Python и SQL.",
            "tags": ["python", "backend"],
            "vacancy_key": "python-dev",
            "is_active": True,
        },
    )
    assert article.status_code == 201, article.text
    article_id = article.json()["id"]
    listed_articles = auth_client.get(
        "/api/v1/automation/knowledge", params={"vacancy_key": "python-dev"}
    )
    assert listed_articles.status_code == 200
    assert [item["id"] for item in listed_articles.json()] == [article_id]
    updated_article = auth_client.patch(
        f"/api/v1/automation/knowledge/{article_id}",
        headers=headers,
        json={"content": "Требуется Python, SQL и FastAPI.", "is_active": False},
    )
    assert updated_article.status_code == 200
    assert updated_article.json()["revision"] == 2
    assert (
        auth_client.patch(
            "/api/v1/automation/knowledge/missing", headers=headers, json={"is_active": False}
        ).status_code
        == 404
    )

    provider = auth_client.post(
        "/api/v1/automation/providers",
        headers=headers,
        json={
            "name": "Rule based admin",
            "kind": "rule_based",
            "enabled": True,
            "system_prompt": "Собирай данные кандидата безопасно.",
        },
    )
    assert provider.status_code == 201, provider.text
    provider_id = provider.json()["id"]
    duplicate = auth_client.post(
        "/api/v1/automation/providers",
        headers=headers,
        json={"name": "Rule based admin", "kind": "rule_based"},
    )
    assert duplicate.status_code == 409
    providers = auth_client.get("/api/v1/automation/providers")
    assert providers.status_code == 200 and providers.json()[0]["id"] == provider_id
    patched_provider = auth_client.patch(
        f"/api/v1/automation/providers/{provider_id}",
        headers=headers,
        json={
            "name": "Rule based updated",
            "temperature": 0.4,
            "timeout_seconds": 15,
            "max_output_tokens": 600,
            "api_key": "stored-but-unused-rule-key",
        },
    )
    assert patched_provider.status_code == 200, patched_provider.text
    assert patched_provider.json()["temperature_milli"] == 400
    tested_provider = auth_client.post(
        f"/api/v1/automation/providers/{provider_id}/test", headers=headers
    )
    assert tested_provider.status_code == 200, tested_provider.text
    assert tested_provider.json()["ok"] is True

    connection = create_connection(auth_client, name="Automation admin bot")
    policy = auth_client.post(
        "/api/v1/automation/policies",
        headers=headers,
        json={
            "name": "Admin lifecycle policy",
            "telegram_connection_id": connection["id"],
            "ai_provider_config_id": provider_id,
            "enabled": False,
            "timezone_name": "Asia/Novosibirsk",
            "active_hours": {},
            "allowed_chat_types": ["private"],
            "consent_notice": "Согласны на обработку данных?",
            "fallback_message": "Передаю оператору.",
            "max_auto_replies_per_day": 10,
            "require_consent_before_ai": True,
            "handoff_keywords": ["оператор"],
            "stop_words": ["стоп"],
            "vacancy_detection_rules": {},
        },
    )
    assert policy.status_code == 201, policy.text
    policy_id = policy.json()["id"]
    policies = auth_client.get("/api/v1/automation/policies")
    assert policies.status_code == 200 and policies.json()[0]["id"] == policy_id
    bad_timezone = auth_client.patch(
        f"/api/v1/automation/policies/{policy_id}",
        headers=headers,
        json={"timezone_name": "Invalid/Timezone"},
    )
    assert bad_timezone.status_code == 422
    enabled = auth_client.patch(
        f"/api/v1/automation/policies/{policy_id}",
        headers=headers,
        json={"enabled": True, "max_auto_replies_per_day": 12},
    )
    assert enabled.status_code == 200 and enabled.json()["enabled"] is True
    assert (
        auth_client.delete(f"/api/v1/automation/policies/{policy_id}", headers=headers).status_code
        == 409
    )
    assert (
        auth_client.delete(
            f"/api/v1/automation/providers/{provider_id}", headers=headers
        ).status_code
        == 409
    )
    disabled = auth_client.patch(
        f"/api/v1/automation/policies/{policy_id}",
        headers=headers,
        json={"enabled": False},
    )
    assert disabled.status_code == 200
    assert (
        auth_client.delete(f"/api/v1/automation/policies/{policy_id}", headers=headers).status_code
        == 200
    )
    assert (
        auth_client.delete(
            f"/api/v1/automation/providers/{provider_id}", headers=headers
        ).status_code
        == 200
    )
    assert (
        auth_client.delete(
            f"/api/v1/automation/knowledge/{article_id}", headers=headers
        ).status_code
        == 200
    )
    assert (
        auth_client.delete(
            f"/api/v1/automation/knowledge/{article_id}", headers=headers
        ).status_code
        == 404
    )
