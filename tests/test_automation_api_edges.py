from __future__ import annotations

from fastapi.testclient import TestClient

from tests.conftest import csrf_headers
from tests.test_api_workflow import create_connection


def _definition(message: str = "Готово") -> dict:
    """Создать минимальный корректный flow с одним конечным узлом."""

    return {
        "version": 1,
        "start_node_id": "end",
        "nodes": [{"id": "end", "type": "end", "text": message, "completion_mode": "ai"}],
    }


def _create_flow(client: TestClient, name: str, *, active: bool = False) -> dict:
    """Создать flow через публичную административную форму API."""

    response = client.post(
        "/api/v1/automation/flows",
        headers=csrf_headers(client),
        json={"name": name, "is_active": active, "definition": _definition()},
    )
    assert response.status_code == 201, response.text
    return response.json()


def _policy_payload(connection_id: str, name: str, **overrides) -> dict:  # type: ignore[no-untyped-def]
    """Собрать полный валидный payload политики с возможностью точечной настройки."""

    payload = {
        "name": name,
        "telegram_connection_id": connection_id,
        "enabled": False,
        "timezone_name": "UTC",
        "active_hours": {},
        "allowed_chat_types": ["private"],
        "consent_notice": "Согласны на автоматизированную обработку данных кандидата?",
        "fallback_message": "Передаю диалог оператору.",
        "max_auto_replies_per_day": 20,
        "require_consent_before_ai": True,
        "handoff_keywords": ["оператор"],
        "stop_words": ["стоп"],
        "vacancy_detection_rules": {},
    }
    payload.update(overrides)
    return payload


def test_missing_automation_resources_return_scoped_404(auth_client: TestClient) -> None:
    """Проверить tenant-scoped 404 для connection, flow, policy и provider форм."""

    headers = csrf_headers(auth_client)
    missing = "00000000-0000-0000-0000-000000000000"
    assert auth_client.get(f"/api/v1/automation/flows/{missing}").status_code == 404
    assert (
        auth_client.patch(
            f"/api/v1/automation/policies/{missing}", headers=headers, json={"enabled": False}
        ).status_code
        == 404
    )
    assert (
        auth_client.patch(
            f"/api/v1/automation/providers/{missing}", headers=headers, json={"enabled": False}
        ).status_code
        == 404
    )
    assert (
        auth_client.post(
            f"/api/v1/automation/providers/{missing}/test", headers=headers
        ).status_code
        == 404
    )
    missing_connection = auth_client.post(
        "/api/v1/automation/policies",
        headers=headers,
        json=_policy_payload(missing, "Missing connection"),
    )
    assert missing_connection.status_code == 404


def test_flow_form_handles_duplicates_revision_links_and_delete(auth_client: TestClient) -> None:
    """Проверить duplicate/revision/activation/linkage ограничения полного flow lifecycle."""

    headers = csrf_headers(auth_client)
    flow = _create_flow(auth_client, "Flow lifecycle")
    duplicate = auth_client.post(
        "/api/v1/automation/flows",
        headers=headers,
        json={"name": "Flow lifecycle", "definition": _definition()},
    )
    assert duplicate.status_code == 409

    revised = auth_client.patch(
        f"/api/v1/automation/flows/{flow['id']}",
        headers=headers,
        json={"definition": _definition("Обновлено")},
    )
    assert revised.status_code == 200
    assert revised.json()["revision"] == 2
    assert auth_client.get(f"/api/v1/automation/flows/{flow['id']}").status_code == 200

    other = _create_flow(auth_client, "Other flow")
    rename_conflict = auth_client.patch(
        f"/api/v1/automation/flows/{other['id']}",
        headers=headers,
        json={"name": "Flow lifecycle"},
    )
    assert rename_conflict.status_code == 409

    activated = auth_client.patch(
        f"/api/v1/automation/flows/{flow['id']}", headers=headers, json={"is_active": True}
    )
    assert activated.status_code == 200
    connection = create_connection(auth_client, name="Flow linkage bot")
    policy = auth_client.post(
        "/api/v1/automation/policies",
        headers=headers,
        json=_policy_payload(
            connection["id"],
            "Flow linkage policy",
            automation_flow_id=flow["id"],
            enabled=True,
        ),
    )
    assert policy.status_code == 201, policy.text
    assert (
        auth_client.patch(
            f"/api/v1/automation/flows/{flow['id']}",
            headers=headers,
            json={"is_active": False},
        ).status_code
        == 409
    )
    assert (
        auth_client.delete(f"/api/v1/automation/flows/{flow['id']}", headers=headers).status_code
        == 409
    )

    policy_id = policy.json()["id"]
    assert (
        auth_client.patch(
            f"/api/v1/automation/policies/{policy_id}",
            headers=headers,
            json={"enabled": False},
        ).status_code
        == 200
    )
    assert (
        auth_client.delete(f"/api/v1/automation/policies/{policy_id}", headers=headers).status_code
        == 200
    )
    assert (
        auth_client.delete(f"/api/v1/automation/flows/{flow['id']}", headers=headers).status_code
        == 200
    )


def test_policy_form_rejects_timezone_name_and_active_conflicts(auth_client: TestClient) -> None:
    """Проверить IANA timezone, unique name и одну активную policy для Bot API connection."""

    headers = csrf_headers(auth_client)
    connection = create_connection(auth_client, name="Policy conflict bot")
    invalid_timezone = auth_client.post(
        "/api/v1/automation/policies",
        headers=headers,
        json=_policy_payload(
            connection["id"], "Invalid timezone policy", timezone_name="Invalid/Timezone"
        ),
    )
    assert invalid_timezone.status_code == 422

    provider = auth_client.post(
        "/api/v1/automation/providers",
        headers=headers,
        json={"name": "Policy provider", "kind": "rule_based"},
    )
    assert provider.status_code == 201, provider.text
    flow = _create_flow(auth_client, "Policy flow", active=True)
    first = auth_client.post(
        "/api/v1/automation/policies",
        headers=headers,
        json=_policy_payload(connection["id"], "Primary policy", enabled=True),
    )
    assert first.status_code == 201, first.text
    active_conflict = auth_client.post(
        "/api/v1/automation/policies",
        headers=headers,
        json=_policy_payload(connection["id"], "Second active policy", enabled=True),
    )
    assert active_conflict.status_code == 409
    duplicate_name = auth_client.post(
        "/api/v1/automation/policies",
        headers=headers,
        json=_policy_payload(connection["id"], "Primary policy", enabled=False),
    )
    assert duplicate_name.status_code == 409

    second = auth_client.post(
        "/api/v1/automation/policies",
        headers=headers,
        json=_policy_payload(connection["id"], "Secondary policy", enabled=False),
    )
    assert second.status_code == 201, second.text
    patch_conflict = auth_client.patch(
        f"/api/v1/automation/policies/{second.json()['id']}",
        headers=headers,
        json={
            "ai_provider_config_id": provider.json()["id"],
            "automation_flow_id": flow["id"],
            "enabled": True,
        },
    )
    assert patch_conflict.status_code == 409


def test_provider_form_enforces_allowlist_ssrf_encryption_and_probe_failure(
    auth_client: TestClient,
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    """Проверить allowlist, SSRF-защиту, шифрование ключа и контролируемый health-probe отказ."""

    headers = csrf_headers(auth_client)
    settings = auth_client.app.state.settings
    monkeypatch.setattr(settings, "ai_provider_allowlist", "")
    forbidden = auth_client.post(
        "/api/v1/automation/providers",
        headers=headers,
        json={"name": "Forbidden provider", "kind": "rule_based"},
    )
    assert forbidden.status_code == 422
    monkeypatch.setattr(settings, "ai_provider_allowlist", "rule_based,openai_compatible")

    created = auth_client.post(
        "/api/v1/automation/providers",
        headers=headers,
        json={"name": "Encrypted provider", "kind": "rule_based", "api_key": "secret-key"},
    )
    assert created.status_code == 201, created.text
    provider_id = created.json()["id"]
    unsafe = auth_client.patch(
        f"/api/v1/automation/providers/{provider_id}",
        headers=headers,
        json={"base_url": "http://127.0.0.1/internal"},
    )
    assert unsafe.status_code == 422

    monkeypatch.setattr(
        "app.api.automation.AIService._build_provider",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("probe failed")),
    )
    failed_probe = auth_client.post(
        f"/api/v1/automation/providers/{provider_id}/test", headers=headers
    )
    assert failed_probe.status_code == 502
    assert "probe failed" in failed_probe.text
