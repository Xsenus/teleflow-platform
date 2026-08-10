from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.api import recovery as recovery_api
from app.schemas import RecoveryPolicyPatch
from app.services.operations import OperationsError
from tests.conftest import csrf_headers


def _create_incident(client: TestClient) -> dict:
    """Создать ручной incident через публичный Operations API."""

    response = client.post(
        "/api/v1/operations/incidents",
        headers=csrf_headers(client),
        json={
            "title": "API edge incident",
            "summary": "Проверка редактирования и доменных ошибок incident API",
            "severity": "warning",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_incident_filters_patch_and_comment_forms(auth_client: TestClient) -> None:
    """Проверить status/severity filters, patch и comment формы incident lifecycle."""

    incident = _create_incident(auth_client)
    headers = csrf_headers(auth_client)
    filtered = auth_client.get(
        "/api/v1/operations/incidents",
        params={"status": "open", "severity": "warning"},
    )
    assert filtered.status_code == 200
    assert [item["id"] for item in filtered.json()] == [incident["id"]]

    patched = auth_client.patch(
        f"/api/v1/operations/incidents/{incident['id']}",
        headers=headers,
        json={"severity": "critical", "impact": "Влияние подтверждено оператором"},
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["severity"] == "critical"
    assert patched.json()["impact"] == "Влияние подтверждено оператором"

    commented = auth_client.post(
        f"/api/v1/operations/incidents/{incident['id']}/comment",
        headers=headers,
        json={"note": "Добавлена диагностическая информация"},
    )
    assert commented.status_code == 200, commented.text
    assert commented.json()["status"] == "open"
    events = auth_client.get(f"/api/v1/operations/incidents/{incident['id']}/events").json()
    assert events[-1]["event_type"] == "commented"


def test_operations_api_converts_all_domain_errors_to_conflicts(
    auth_client: TestClient,
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    """Проверить HTTP 409 для policy, assessment, create, patch и transition ошибок домена."""

    incident = _create_incident(auth_client)
    headers = csrf_headers(auth_client)

    cases = [
        (
            "app.api.operations.update_slo_policy",
            "patch",
            "/api/v1/operations/slo-policy",
            {"enabled": False},
        ),
        (
            "app.api.operations.evaluate_slo",
            "post",
            "/api/v1/operations/slo-assessments",
            None,
        ),
        (
            "app.api.operations.create_incident",
            "post",
            "/api/v1/operations/incidents",
            {"title": "Blocked create", "summary": "Домен запрещает создание incident"},
        ),
        (
            "app.api.operations.update_incident",
            "patch",
            f"/api/v1/operations/incidents/{incident['id']}",
            {"impact": "Запрещённое изменение"},
        ),
        (
            "app.api.operations.transition_incident",
            "post",
            f"/api/v1/operations/incidents/{incident['id']}/resolve",
            {"note": "Переход запрещён текущим состоянием"},
        ),
    ]
    for target, method, path, payload in cases:
        with monkeypatch.context() as context:
            context.setattr(
                target,
                lambda *_args, **_kwargs: (_ for _ in ()).throw(OperationsError("domain conflict")),
            )
            response = auth_client.request(method, path, headers=headers, json=payload)
        assert response.status_code == 409, response.text
        assert "domain conflict" in response.text


@pytest.mark.parametrize(
    "field",
    [
        "enabled",
        "require_encrypted_backup",
        "require_trusted_signature",
        "require_restore_drill",
    ],
)
def test_production_recovery_policy_cannot_disable_required_control(
    monkeypatch,
    field: str,
) -> None:  # type: ignore[no-untyped-def]
    """Проверить каждый обязательный production recovery control до изменения policy."""

    policy = SimpleNamespace(id="policy-id")
    monkeypatch.setattr(
        recovery_api, "get_or_create_recovery_policy", lambda *_args, **_kwargs: policy
    )
    payload = RecoveryPolicyPatch.model_validate({field: False})

    with pytest.raises(HTTPException) as captured:
        recovery_api.update_recovery_policy(
            payload=payload,
            request=SimpleNamespace(),  # type: ignore[arg-type]
            user=SimpleNamespace(id="user-id", organization_id="organization-id"),  # type: ignore[arg-type]
            db=SimpleNamespace(),  # type: ignore[arg-type]
            settings=SimpleNamespace(is_production=True),  # type: ignore[arg-type]
        )

    assert captured.value.status_code == 400
    assert "production" in str(captured.value.detail).lower()
