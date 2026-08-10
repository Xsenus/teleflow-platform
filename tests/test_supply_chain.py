from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime

from fastapi.testclient import TestClient

from app.models import ReleaseDependencyAssessment, ReleaseTransparencyEvent
from tests.conftest import csrf_headers


def _generate_key(client: TestClient) -> dict:
    """Реализовать внутренний этап generate key step. Вспомогательная функция сохраняет
    детерминированность и тестируемость процесса.
    """
    response = client.post(
        "/api/v1/artifact-signing-keys/generate",
        headers=csrf_headers(client),
        json={
            "name": "Supply chain signing key",
            "make_default": True,
            "trusted_for_import": True,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def _attestation(client: TestClient) -> dict:
    """Реализовать внутренний этап attestation step. Вспомогательная функция сохраняет
    детерминированность и тестируемость процесса.
    """
    _generate_key(client)
    response = client.post("/api/v1/release-attestations/local", headers=csrf_headers(client))
    assert response.status_code == 201, response.text
    return response.json()


def _publish(client: TestClient, attestation_id: str) -> dict:
    """Реализовать внутренний этап publish step. Вспомогательная функция сохраняет
    детерминированность и тестируемость процесса.
    """
    response = client.post(
        f"/api/v1/supply-chain/transparency/{attestation_id}/publish",
        headers=csrf_headers(client),
        json={"reason": "Публикация проверенной сборки"},
    )
    assert response.status_code == 201, response.text
    return response.json()


def _scan_report(client: TestClient, attestation: dict, *, severity: str | None = None) -> dict:
    """Реализовать внутренний этап scan report step. Вспомогательная функция сохраняет
    детерминированность и тестируемость процесса.
    """
    findings: list[dict] = []
    if severity:
        findings.append(
            {
                "id": "TEST-ADVISORY-1",
                "package": "fastapi",
                "installed_version": "0.128.2",
                "severity": severity,
                "fixed_versions": ["999.0.0"],
                "aliases": [],
                "url": "https://example.invalid/advisory/1",
            }
        )
    sbom_response = client.get(f"/api/v1/supply-chain/attestations/{attestation['id']}/sbom")
    assert sbom_response.status_code == 200, sbom_response.text
    sbom_sha256 = hashlib.sha256(
        json.dumps(
            sbom_response.json(),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    return {
        "schema_version": 1,
        "kind": "vulnerability_scan",
        "release_payload_sha256": attestation["payload_sha256"],
        "sbom_sha256": sbom_sha256,
        "generated_at": datetime.now(UTC).isoformat(),
        "scanner": {"name": "test-scanner", "version": "1.0"},
        "findings": findings,
    }


def test_transparency_entries_are_signed_and_chain_is_verified(auth_client: TestClient):
    """Проверить сценарий transparency entries are signed and chain is verified. Тест завершается
    ошибкой при нарушении зафиксированного инварианта.
    """
    attestation = _attestation(auth_client)
    published = _publish(auth_client, attestation["id"])
    assert published["event_type"] == "published"
    assert published["signature_status"] == "valid_trusted"
    assert len(published["entry_hash"]) == 64
    assert published["previous_hash"] == "0" * 64

    verified = auth_client.get("/api/v1/supply-chain/transparency/verify")
    assert verified.status_code == 200, verified.text
    assert verified.json() == {
        "valid": True,
        "entry_count": 1,
        "last_sequence": 1,
        "last_hash": published["entry_hash"],
        "active_release_count": 1,
        "errors": [],
    }

    withdrawn = auth_client.post(
        f"/api/v1/supply-chain/transparency/{attestation['id']}/withdraw",
        headers=csrf_headers(auth_client),
        json={"reason": "Сборка заменена исправленным релизом"},
    )
    assert withdrawn.status_code == 201, withdrawn.text
    assert withdrawn.json()["event_type"] == "withdrawn"
    after = auth_client.get("/api/v1/supply-chain/transparency/verify").json()
    assert after["valid"] is True
    assert after["entry_count"] == 2
    assert after["active_release_count"] == 0


def test_transparency_tampering_is_detected(auth_client: TestClient):
    """Проверить сценарий transparency tampering is detected. Тест завершается ошибкой при
    нарушении зафиксированного инварианта.
    """
    attestation = _attestation(auth_client)
    event = _publish(auth_client, attestation["id"])
    factory = auth_client.app.state.session_factory
    with factory() as db:
        item = db.get(ReleaseTransparencyEvent, event["id"])
        assert item is not None
        item.payload = {**item.payload, "reason": "tampered"}
        db.commit()

    verified = auth_client.get("/api/v1/supply-chain/transparency/verify")
    assert verified.status_code == 200
    body = verified.json()
    assert body["valid"] is False
    assert any("entry_hash" in error or "подпись" in error for error in body["errors"])


def test_inventory_assessment_produces_signed_sbom(auth_client: TestClient):
    """Проверить сценарий inventory assessment produces signed sbom. Тест завершается ошибкой при
    нарушении зафиксированного инварианта.
    """
    attestation = _attestation(auth_client)
    created = auth_client.post(
        f"/api/v1/supply-chain/assessments/{attestation['id']}/inventory",
        headers=csrf_headers(auth_client),
        json={"scanner_name": "TeleFlow inventory", "scanner_version": "2.1"},
    )
    assert created.status_code == 201, created.text
    item = created.json()
    assert item["report_kind"] == "inventory_only"
    assert item["signature_status"] == "valid_trusted"
    assert item["status"] in {"passed", "warning"}
    assert item["sbom_payload"]["bomFormat"] == "CycloneDX"
    assert len(item["sbom_sha256"]) == 64

    downloaded = auth_client.get(f"/api/v1/supply-chain/assessments/{item['id']}/sbom")
    assert downloaded.status_code == 200
    assert downloaded.headers["content-type"].startswith("application/vnd.cyclonedx+json")


def test_dependency_policy_blocks_findings_and_inventory_only(auth_client: TestClient):
    """Проверить сценарий dependency policy blocks findings and inventory only. Тест завершается
    ошибкой при нарушении зафиксированного инварианта.
    """
    attestation = _attestation(auth_client)
    policy = auth_client.patch(
        "/api/v1/supply-chain/policy",
        headers=csrf_headers(auth_client),
        json={
            "require_vulnerability_scan": True,
            "require_trusted_report": True,
            "max_critical": 0,
            "max_high": 0,
            "max_medium": 0,
        },
    )
    assert policy.status_code == 200, policy.text

    inventory = auth_client.post(
        f"/api/v1/supply-chain/assessments/{attestation['id']}/inventory",
        headers=csrf_headers(auth_client),
        json={},
    )
    assert inventory.status_code == 201
    assert inventory.json()["status"] == "blocked"
    assert any("vulnerability scan" in x for x in inventory.json()["blockers"])

    scan = auth_client.post(
        f"/api/v1/supply-chain/assessments/{attestation['id']}",
        headers=csrf_headers(auth_client),
        json={
            "report": _scan_report(auth_client, attestation, severity="high"),
            "sign_with_default_key": True,
        },
    )
    assert scan.status_code == 201, scan.text
    assert scan.json()["status"] == "blocked"
    assert scan.json()["high_count"] == 1
    assert any("High findings" in x for x in scan.json()["blockers"])


def test_assessment_tampering_does_not_rewrite_evidence_hash(auth_client: TestClient):
    """Проверить сценарий assessment tampering does not rewrite evidence hash. Тест завершается
    ошибкой при нарушении зафиксированного инварианта.
    """
    attestation = _attestation(auth_client)
    created = auth_client.post(
        f"/api/v1/supply-chain/assessments/{attestation['id']}",
        headers=csrf_headers(auth_client),
        json={"report": _scan_report(auth_client, attestation), "sign_with_default_key": True},
    )
    assert created.status_code == 201, created.text
    original = created.json()

    factory = auth_client.app.state.session_factory
    with factory() as db:
        item = db.get(ReleaseDependencyAssessment, original["id"])
        assert item is not None
        tampered = dict(item.report_payload)
        tampered["scanner"] = {"name": "tampered-scanner", "version": "9"}
        item.report_payload = tampered
        db.commit()

    verified = auth_client.post(
        f"/api/v1/supply-chain/assessments/{original['id']}/verify",
        headers=csrf_headers(auth_client),
    )
    assert verified.status_code == 200, verified.text
    body = verified.json()
    assert body["status"] == "blocked"
    assert body["report_sha256"] == original["report_sha256"]
    assert any("SHA-256 dependency report" in x for x in body["blockers"])


def test_upgrade_verification_requires_transparency_and_dependency_evidence(
    auth_client: TestClient,
):
    """Проверить сценарий upgrade verification requires transparency and dependency evidence. Тест
    завершается ошибкой при нарушении зафиксированного инварианта.
    """
    attestation = _attestation(auth_client)
    settings = auth_client.app.state.settings
    previous = (
        settings.require_trusted_release_attestation,
        settings.require_release_transparency,
        settings.require_dependency_assessment,
    )
    settings.require_trusted_release_attestation = True
    settings.require_release_transparency = True
    settings.require_dependency_assessment = True
    try:
        without_evidence = auth_client.post(
            "/api/v1/changes",
            headers=csrf_headers(auth_client),
            json={
                "title": "Upgrade without supply-chain evidence",
                "change_type": "upgrade",
                "current_version": settings.version,
                "target_version": settings.version,
                "release_attestation_id": attestation["id"],
                "reason": "Проверка отсутствующего evidence",
                "risk_summary": "Непроверенная цепочка поставки",
                "rollback_plan": "Вернуть предыдущую доверенную сборку",
            },
        )
        assert without_evidence.status_code == 201, without_evidence.text
        first = auth_client.post(
            f"/api/v1/changes/{without_evidence.json()['id']}/verify/pre_change",
            headers=csrf_headers(auth_client),
        )
        assert first.status_code == 201, first.text
        checks = {x["code"]: x for x in first.json()["checks"]}
        assert checks["release_transparency"]["status"] == "blocked"
        assert checks["dependency_assurance"]["status"] == "blocked"

        _publish(auth_client, attestation["id"])
        assessment = auth_client.post(
            f"/api/v1/supply-chain/assessments/{attestation['id']}",
            headers=csrf_headers(auth_client),
            json={
                "report": _scan_report(auth_client, attestation),
                "sign_with_default_key": True,
            },
        )
        assert assessment.status_code == 201, assessment.text
        assert assessment.json()["status"] != "blocked"

        with_evidence = auth_client.post(
            "/api/v1/changes",
            headers=csrf_headers(auth_client),
            json={
                "title": "Upgrade with immutable supply-chain evidence",
                "change_type": "upgrade",
                "current_version": settings.version,
                "target_version": settings.version,
                "release_attestation_id": attestation["id"],
                "release_dependency_assessment_id": assessment.json()["id"],
                "reason": "Проверка прозрачности и зависимостей",
                "risk_summary": "Контролируемая цепочка поставки",
                "rollback_plan": "Вернуть предыдущую доверенную сборку",
            },
        )
        assert with_evidence.status_code == 201, with_evidence.text
        change_id = with_evidence.json()["id"]
        second = auth_client.post(
            f"/api/v1/changes/{change_id}/verify/pre_change",
            headers=csrf_headers(auth_client),
        )
        assert second.status_code == 201, second.text
        checks = {x["code"]: x for x in second.json()["checks"]}
        assert checks["release_transparency"]["status"] == "passed"
        assert checks["dependency_assurance"]["status"] == "passed"
        change = auth_client.get(f"/api/v1/changes/{change_id}").json()
        assert change["release_dependency_assessment_id"] == assessment.json()["id"]
    finally:
        (
            settings.require_trusted_release_attestation,
            settings.require_release_transparency,
            settings.require_dependency_assessment,
        ) = previous


def test_supply_chain_writes_require_admin_role(auth_client: TestClient):
    """Проверить сценарий supply chain writes require admin role. Тест завершается ошибкой при
    нарушении зафиксированного инварианта.
    """
    attestation = _attestation(auth_client)
    created = auth_client.post(
        "/api/v1/users",
        headers=csrf_headers(auth_client),
        json={
            "email": "supply-operator@example.com",
            "display_name": "Supply Operator",
            "password": "SupplyOperator_123!",
            "role": "operator",
        },
    )
    assert created.status_code == 201
    auth_client.cookies.clear()
    login = auth_client.post(
        "/api/v1/auth/login",
        json={
            "email": "supply-operator@example.com",
            "password": "SupplyOperator_123!",
        },
    )
    assert login.status_code == 200
    assert auth_client.get("/api/v1/supply-chain/policy").status_code == 200
    denied = auth_client.post(
        f"/api/v1/supply-chain/transparency/{attestation['id']}/publish",
        headers=csrf_headers(auth_client),
        json={},
    )
    assert denied.status_code == 403


def test_transparency_detects_release_attestation_payload_tampering(auth_client: TestClient):
    """Проверить сценарий transparency detects release attestation payload tampering. Тест
    завершается ошибкой при нарушении зафиксированного инварианта.
    """
    from app.models import ReleaseAttestation

    attestation = _attestation(auth_client)
    _publish(auth_client, attestation["id"])
    factory = auth_client.app.state.session_factory
    with factory() as db:
        item = db.get(ReleaseAttestation, attestation["id"])
        assert item is not None
        item.payload = {**item.payload, "dependency_count": 999999}
        db.commit()

    verified = auth_client.get("/api/v1/supply-chain/transparency/verify")
    assert verified.status_code == 200
    body = verified.json()
    assert body["valid"] is False
    assert any("release attestation" in error.lower() for error in body["errors"])


def test_dependency_policy_change_requires_new_assessment(auth_client: TestClient):
    """Проверить сценарий dependency policy change requires new assessment. Тест завершается
    ошибкой при нарушении зафиксированного инварианта.
    """
    attestation = _attestation(auth_client)
    created = auth_client.post(
        f"/api/v1/supply-chain/assessments/{attestation['id']}",
        headers=csrf_headers(auth_client),
        json={"report": _scan_report(auth_client, attestation), "sign_with_default_key": True},
    )
    assert created.status_code == 201, created.text
    assert created.json()["status"] != "blocked"

    changed = auth_client.patch(
        "/api/v1/supply-chain/policy",
        headers=csrf_headers(auth_client),
        json={"max_medium": 0, "report_ttl_hours": 12},
    )
    assert changed.status_code == 200, changed.text
    verified = auth_client.post(
        f"/api/v1/supply-chain/assessments/{created.json()['id']}/verify",
        headers=csrf_headers(auth_client),
    )
    assert verified.status_code == 200, verified.text
    assert verified.json()["status"] == "blocked"
    assert any("policy изменилась" in blocker for blocker in verified.json()["blockers"])


def test_unsigned_report_is_blocked_when_trusted_signature_required(auth_client: TestClient):
    """Проверить сценарий unsigned report is blocked when trusted signature required. Тест
    завершается ошибкой при нарушении зафиксированного инварианта.
    """
    attestation = _attestation(auth_client)
    policy = auth_client.patch(
        "/api/v1/supply-chain/policy",
        headers=csrf_headers(auth_client),
        json={"require_trusted_report": True},
    )
    assert policy.status_code == 200, policy.text
    created = auth_client.post(
        f"/api/v1/supply-chain/assessments/{attestation['id']}",
        headers=csrf_headers(auth_client),
        json={"report": _scan_report(auth_client, attestation), "sign_with_default_key": False},
    )
    assert created.status_code == 201, created.text
    assert created.json()["signature_status"] == "unsigned"
    assert created.json()["status"] == "blocked"
    assert any("доверенным" in blocker.lower() for blocker in created.json()["blockers"])


def test_denied_direct_package_blocks_release(auth_client: TestClient):
    """Проверить сценарий denied direct package blocks release. Тест завершается ошибкой при
    нарушении зафиксированного инварианта.
    """
    attestation = _attestation(auth_client)
    policy = auth_client.patch(
        "/api/v1/supply-chain/policy",
        headers=csrf_headers(auth_client),
        json={"denied_packages": ["FastAPI"]},
    )
    assert policy.status_code == 200, policy.text
    created = auth_client.post(
        f"/api/v1/supply-chain/assessments/{attestation['id']}",
        headers=csrf_headers(auth_client),
        json={"report": _scan_report(auth_client, attestation), "sign_with_default_key": True},
    )
    assert created.status_code == 201, created.text
    assert created.json()["status"] == "blocked"
    assert created.json()["denied_count"] == 1
    assert any("fastapi" in blocker.lower() for blocker in created.json()["blockers"])


def test_finding_aliases_are_bounded_in_immutable_report(auth_client: TestClient):
    """Проверить сценарий finding aliases are bounded in immutable report. Тест завершается ошибкой
    при нарушении зафиксированного инварианта.
    """
    attestation = _attestation(auth_client)
    report = _scan_report(auth_client, attestation)
    report["findings"] = [
        {
            "id": "TEST-BOUNDED-1",
            "package": "fastapi",
            "installed_version": "0.128.2",
            "severity": "low",
            "fixed_versions": ["9" * 500],
            "aliases": ["A" * 500],
            "url": None,
        }
    ]
    created = auth_client.post(
        f"/api/v1/supply-chain/assessments/{attestation['id']}",
        headers=csrf_headers(auth_client),
        json={"report": report, "sign_with_default_key": True},
    )
    assert created.status_code == 201, created.text
    finding = created.json()["report_payload"]["findings"][0]
    assert len(finding["fixed_versions"][0]) == 160
    assert len(finding["aliases"][0]) == 160


def test_dependency_policy_ttl_cannot_exceed_server_limit(auth_client: TestClient):
    """Проверить сценарий dependency policy ttl cannot exceed server limit. Тест завершается
    ошибкой при нарушении зафиксированного инварианта.
    """
    settings = auth_client.app.state.settings
    response = auth_client.patch(
        "/api/v1/supply-chain/policy",
        headers=csrf_headers(auth_client),
        json={"report_ttl_hours": settings.dependency_assessment_ttl_hours + 1},
    )
    assert response.status_code == 409, response.text
    assert "серверный лимит" in response.json()["detail"]


def test_dependency_report_must_match_server_generated_sbom(auth_client: TestClient):
    """Проверить сценарий dependency report must match server generated sbom. Тест завершается
    ошибкой при нарушении зафиксированного инварианта.
    """
    attestation = _attestation(auth_client)
    report = _scan_report(auth_client, attestation)
    report["sbom_sha256"] = "f" * 64
    response = auth_client.post(
        f"/api/v1/supply-chain/assessments/{attestation['id']}",
        headers=csrf_headers(auth_client),
        json={"report": report, "sign_with_default_key": True},
    )
    assert response.status_code == 409, response.text
    assert "другому SBOM" in response.json()["detail"]


def test_dependency_report_size_limit_is_enforced_before_persistence(auth_client: TestClient):
    """Проверить сценарий dependency report size limit is enforced before persistence. Тест
    завершается ошибкой при нарушении зафиксированного инварианта.
    """
    attestation = _attestation(auth_client)
    settings = auth_client.app.state.settings
    previous = settings.dependency_report_max_bytes
    settings.dependency_report_max_bytes = 1024
    try:
        report = _scan_report(auth_client, attestation)
        report["findings"] = [
            {
                "id": f"TEST-LARGE-{index}",
                "package": "fastapi",
                "installed_version": "0.128.2",
                "severity": "low",
                "fixed_versions": ["999.0.0"],
                "aliases": ["A" * 120],
                "url": "https://example.invalid/" + ("x" * 400),
            }
            for index in range(4)
        ]
        response = auth_client.post(
            f"/api/v1/supply-chain/assessments/{attestation['id']}",
            headers=csrf_headers(auth_client),
            json={"report": report, "sign_with_default_key": True},
        )
        assert response.status_code == 409, response.text
        assert "превышает" in response.json()["detail"]
    finally:
        settings.dependency_report_max_bytes = previous
