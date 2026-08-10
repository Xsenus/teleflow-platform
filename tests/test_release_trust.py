from __future__ import annotations

from fastapi.testclient import TestClient

from tests.conftest import csrf_headers


def _generate_key(client: TestClient) -> dict:
    """Реализовать внутренний этап generate key step. Вспомогательная функция сохраняет
    детерминированность и тестируемость процесса.
    """
    response = client.post(
        "/api/v1/artifact-signing-keys/generate",
        headers=csrf_headers(client),
        json={"name": "Release signing key", "make_default": True, "trusted_for_import": True},
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_local_release_attestation_is_signed_and_trusted(auth_client: TestClient):
    """Проверить сценарий local release attestation is signed and trusted. Тест завершается ошибкой
    при нарушении зафиксированного инварианта.
    """
    _generate_key(auth_client)
    created = auth_client.post(
        "/api/v1/release-attestations/local", headers=csrf_headers(auth_client)
    )
    assert created.status_code == 201, created.text
    item = created.json()
    assert item["version"] == "2.5.0"
    assert item["signature_status"] == "valid_trusted"
    assert len(item["payload_sha256"]) == 64
    assert item["payload"]["dependency_count"] > 0
    verified = auth_client.post(
        f"/api/v1/release-attestations/{item['id']}/verify", headers=csrf_headers(auth_client)
    )
    assert verified.status_code == 200, verified.text
    assert verified.json()["signature_status"] == "valid_trusted"


def test_release_attestation_import_rejects_tampered_payload(auth_client: TestClient):
    """Проверить сценарий release attestation import rejects tampered payload. Тест завершается
    ошибкой при нарушении зафиксированного инварианта.
    """
    _generate_key(auth_client)
    created = auth_client.post(
        "/api/v1/release-attestations/local", headers=csrf_headers(auth_client)
    ).json()
    payload = dict(created["payload"])
    payload["version"] = "2.0.1-tampered"
    imported = auth_client.post(
        "/api/v1/release-attestations/import",
        headers=csrf_headers(auth_client),
        json={"payload": payload, "signature": created["signature_info"]},
    )
    assert imported.status_code == 409


def test_upgrade_gate_requires_matching_trusted_attestation(auth_client: TestClient):
    """Проверить сценарий upgrade gate requires matching trusted attestation. Тест завершается
    ошибкой при нарушении зафиксированного инварианта.
    """
    _generate_key(auth_client)
    attestation = auth_client.post(
        "/api/v1/release-attestations/local", headers=csrf_headers(auth_client)
    ).json()
    settings = auth_client.app.state.settings
    previous = settings.require_trusted_release_attestation
    settings.require_trusted_release_attestation = True
    try:
        without = auth_client.post(
            "/api/v1/changes",
            headers=csrf_headers(auth_client),
            json={
                "title": "Upgrade without provenance",
                "change_type": "upgrade",
                "current_version": "2.5.0",
                "target_version": "2.5.0",
                "reason": "Проверка release trust gate",
                "risk_summary": "Неизвестный артефакт",
                "rollback_plan": "Вернуть предыдущую сборку",
            },
        )
        assert without.status_code == 201, without.text
        report = auth_client.post(
            f"/api/v1/changes/{without.json()['id']}/verify/pre_change",
            headers=csrf_headers(auth_client),
        )
        assert report.status_code == 201
        assert report.json()["status"] == "blocked"
        assert any("release attestation" in x.lower() for x in report.json()["blockers"])

        with_attestation = auth_client.post(
            "/api/v1/changes",
            headers=csrf_headers(auth_client),
            json={
                "title": "Upgrade with provenance",
                "change_type": "upgrade",
                "current_version": "2.5.0",
                "target_version": "2.5.0",
                "release_attestation_id": attestation["id"],
                "reason": "Проверка доверенного provenance",
                "risk_summary": "Контролируемый риск",
                "rollback_plan": "Вернуть предыдущую сборку",
            },
        )
        assert with_attestation.status_code == 201, with_attestation.text
        report2 = auth_client.post(
            f"/api/v1/changes/{with_attestation.json()['id']}/verify/pre_change",
            headers=csrf_headers(auth_client),
        )
        assert report2.status_code == 201
        checks = {x["code"]: x for x in report2.json()["checks"]}
        assert checks["release_attestation"]["status"] == "passed"
    finally:
        settings.require_trusted_release_attestation = previous


def test_release_attestations_are_tenant_scoped(auth_client: TestClient):
    """Проверить сценарий release attestations are tenant scoped. Тест завершается ошибкой при
    нарушении зафиксированного инварианта.
    """
    _generate_key(auth_client)
    created = auth_client.post(
        "/api/v1/release-attestations/local", headers=csrf_headers(auth_client)
    )
    assert created.status_code == 201
    listed = auth_client.get("/api/v1/release-attestations")
    assert listed.status_code == 200
    assert len(listed.json()) == 1
