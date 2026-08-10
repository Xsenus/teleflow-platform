from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest

from app.enums import (
    ArtifactSignatureStatus,
    ChangeRequestType,
    DependencyReportKind,
    ReadinessStatus,
    ReleaseTransparencyEventType,
)
from app.services import supply_chain
from app.services.artifact_signing import ArtifactSigningError
from app.services.supply_chain import (
    SupplyChainError,
    _evaluate_report,
    _lock_organization,
    _parse_timestamp,
    _requirement_inventory,
    append_transparency_event,
    build_release_sbom,
    create_dependency_assessment,
    dependency_policy_payload,
    get_or_create_dependency_policy,
    normalize_dependency_report,
    sha256_json,
    update_dependency_policy,
    validate_change_dependency_assurance,
    validate_change_release_transparency,
    verify_dependency_assessment,
    verify_transparency_chain,
)


def attestation(*dependencies: str) -> SimpleNamespace:
    """Создать минимальный release attestation для чистых supply-chain алгоритмов."""
    return SimpleNamespace(
        id="attestation-id",
        organization_id="organization-id",
        version="2.5.0",
        source_commit="a" * 40,
        payload={"dependencies": list(dependencies)},
        payload_sha256="b" * 64,
    )


def valid_report(item: SimpleNamespace) -> dict[str, Any]:
    """Создать корректный vulnerability report, связанный с заданным attestation и SBOM."""
    return {
        "schema_version": 1,
        "kind": DependencyReportKind.VULNERABILITY_SCAN.value,
        "release_payload_sha256": item.payload_sha256,
        "sbom_sha256": sha256_json(build_release_sbom(item)),
        "generated_at": datetime.now(UTC).isoformat(),
        "scanner": {"name": "edge-scanner", "version": "1.0"},
        "findings": [],
    }


@pytest.mark.parametrize(
    ("value", "message"),
    [
        (None, "ISO 8601"),
        ("", "ISO 8601"),
        ("not-a-time", "некорректное время"),
        ("2026-08-10T10:00:00", "часовой пояс"),
    ],
)
def test_parse_timestamp_rejects_ambiguous_values(value: Any, message: str) -> None:
    """Проверить строгий отказ от пустых, повреждённых и timezone-naive timestamps."""
    with pytest.raises(SupplyChainError, match=message):
        _parse_timestamp(value, field="generated_at")
    assert _parse_timestamp("2026-08-10T10:00:00Z", field="generated_at").tzinfo == UTC


def test_requirement_inventory_validates_and_normalizes_dependencies() -> None:
    """Проверить grammar, pinning, prerelease, invalid version и дедупликацию inventory."""
    malformed_container = attestation()
    malformed_container.payload["dependencies"] = "fastapi==1"
    with pytest.raises(SupplyChainError, match="dependency inventory"):
        _requirement_inventory(malformed_container)  # type: ignore[arg-type]
    with pytest.raises(SupplyChainError, match="Некорректная зависимость"):
        _requirement_inventory(attestation("not a valid requirement ???"))  # type: ignore[arg-type]

    components, unpinned, prerelease = _requirement_inventory(
        attestation(
            "",
            "Demo>=1",
            "Preview==2.0rc1",
            "Odd===not-a-version",
            "Stable==3.0",
            "Stable==3.0",
        )  # type: ignore[arg-type]
    )
    assert [item["name"] for item in components] == ["demo", "odd", "preview", "stable"]
    assert unpinned == ["Demo>=1"]
    assert prerelease == ["Odd===not-a-version", "Preview==2.0rc1"]
    assert components[-1]["purl"] == "pkg:pypi/stable@3.0"


def report_case(case: str) -> tuple[dict[str, Any] | object, str]:
    """Построить один некорректный dependency report и ожидаемый фрагмент ошибки."""
    item = attestation("fastapi==1.0")
    report: dict[str, Any] = valid_report(item)
    finding: dict[str, Any] = {
        "id": "ADV-1",
        "package": "fastapi",
        "installed_version": "1.0",
        "severity": "low",
        "fixed_versions": [],
        "aliases": [],
        "url": None,
    }
    cases: dict[str, tuple[dict[str, Any] | object, str]] = {
        "not_object": ([], "JSON-объектом"),
        "extra_top": ({**report, "unexpected": True}, "неподдерживаемые поля"),
        "schema": ({**report, "schema_version": 2}, "версия dependency"),
        "kind": ({**report, "kind": "unknown"}, "тип dependency"),
        "release": ({**report, "release_payload_sha256": "c" * 64}, "другой release"),
        "sha_format": ({**report, "sbom_sha256": "bad"}, "корректный SHA-256"),
        "sha_other": ({**report, "sbom_sha256": "f" * 64}, "другому SBOM"),
        "future": (
            {**report, "generated_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat()},
            "будущим временем",
        ),
        "scanner_type": ({**report, "scanner": []}, "scanner должно быть объектом"),
        "scanner_name": ({**report, "scanner": {"name": "x"}}, "название"),
        "scanner_version": (
            {**report, "scanner": {"name": "ok", "version": "x" * 81}},
            "слишком длинная",
        ),
        "findings_type": ({**report, "findings": {}}, "findings должно быть массивом"),
        "findings_limit": ({**report, "findings": [{}] * 5001}, "слишком много findings"),
        "finding_type": ({**report, "findings": ["bad"]}, "должен быть объектом"),
        "finding_extra": (
            {**report, "findings": [{**finding, "unexpected": True}]},
            "неподдерживаемые поля",
        ),
        "finding_id": ({**report, "findings": [{**finding, "id": ""}]}, "advisory id"),
        "finding_package": (
            {**report, "findings": [{**finding, "package": ""}]},
            "имя пакета",
        ),
        "finding_version": (
            {**report, "findings": [{**finding, "installed_version": "1" * 121}]},
            "версия пакета",
        ),
        "finding_severity": (
            {**report, "findings": [{**finding, "severity": "urgent"}]},
            "неизвестный severity",
        ),
        "finding_lists": (
            {**report, "findings": [{**finding, "aliases": "CVE-1"}]},
            "должны быть массивами",
        ),
    }
    return cases[case]


@pytest.mark.parametrize(
    "case",
    [
        "not_object",
        "extra_top",
        "schema",
        "kind",
        "release",
        "sha_format",
        "sha_other",
        "future",
        "scanner_type",
        "scanner_name",
        "scanner_version",
        "findings_type",
        "findings_limit",
        "finding_type",
        "finding_extra",
        "finding_id",
        "finding_package",
        "finding_version",
        "finding_severity",
        "finding_lists",
    ],
)
def test_normalize_dependency_report_rejects_invalid_contract(case: str) -> None:
    """Проверить все границы внешнего scanner report до сохранения evidence."""
    report, message = report_case(case)
    with pytest.raises(SupplyChainError, match=message):
        normalize_dependency_report(
            report,  # type: ignore[arg-type]
            attestation=attestation("fastapi==1.0"),  # type: ignore[arg-type]
        )


def test_normalize_dependency_report_canonicalizes_findings() -> None:
    """Проверить канонизацию package, severity, aliases, fixed versions и URL."""
    item = attestation("fastapi==1.0")
    report = valid_report(item)
    report["findings"] = [
        {
            "id": " ADV-1 ",
            "package": "FastAPI",
            "installed_version": " 1.0 ",
            "severity": " LOW ",
            "fixed_versions": [" 2.0 ", "2.0", ""],
            "aliases": [" CVE-1 ", "CVE-1"],
            "url": " ",
        }
    ]
    normalized = normalize_dependency_report(report, attestation=item)  # type: ignore[arg-type]
    assert normalized["findings"][0] == {
        "id": "ADV-1",
        "package": "fastapi",
        "installed_version": "1.0",
        "severity": "low",
        "fixed_versions": ["2.0"],
        "aliases": ["CVE-1"],
        "url": None,
    }


class FlushOnlySession:
    """Предоставить минимальный Session-контракт для изменения policy."""

    def __init__(self, scalar_values: list[object | None] | None = None) -> None:
        """Сохранить последовательность результатов scalar и счётчики мутаций."""
        self.scalar_values = list(scalar_values or [])
        self.flush_calls = 0
        self.added: object | None = None

    def scalar(self, _statement: object) -> object | None:
        """Вернуть очередной подготовленный результат запроса."""
        return self.scalar_values.pop(0) if self.scalar_values else None

    def add(self, item: object) -> None:
        """Запомнить добавленную модель."""
        self.added = item

    def flush(self) -> None:
        """Зафиксировать вызов flush без реальной базы."""
        self.flush_calls += 1

    def scalars(self, _statement: object) -> SimpleNamespace:
        """Вернуть очередную коллекцию для SQLAlchemy scalars().all()."""
        value = self.scalar_values.pop(0) if self.scalar_values else []
        return SimpleNamespace(all=lambda: value)


def policy(**overrides: Any) -> SimpleNamespace:
    """Создать mutable dependency policy для unit-проверок."""
    values = {
        "require_exact_pins": False,
        "allow_prerelease": False,
        "require_vulnerability_scan": False,
        "require_trusted_report": False,
        "max_critical": 0,
        "max_high": 0,
        "max_medium": 10,
        "report_ttl_hours": 24,
        "denied_packages": [],
        "updated_by_id": None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


@pytest.mark.parametrize(
    ("values", "message"),
    [
        ({"require_vulnerability_scan": False}, "vulnerability scan"),
        ({"require_trusted_report": False}, "доверенную подпись"),
        ({"max_critical": 1}, "critical"),
        ({"max_high": 1}, "high"),
    ],
)
def test_production_policy_rejects_weakened_controls(values: dict[str, Any], message: str) -> None:
    """Проверить невозможность ослабить обязательные production supply-chain gates."""
    settings = SimpleNamespace(is_production=True, dependency_assessment_ttl_hours=48)
    with pytest.raises(SupplyChainError, match=message):
        update_dependency_policy(
            FlushOnlySession(),  # type: ignore[arg-type]
            policy=policy(require_vulnerability_scan=True, require_trusted_report=True),  # type: ignore[arg-type]
            user=SimpleNamespace(id="user-id"),  # type: ignore[arg-type]
            settings=settings,  # type: ignore[arg-type]
            values=values,
        )


def test_policy_update_normalizes_denied_packages_and_bounds_names() -> None:
    """Проверить очистку, дедупликацию и ограничение имён запрещённых пакетов."""
    db = FlushOnlySession()
    item = policy()
    updated = update_dependency_policy(
        db,  # type: ignore[arg-type]
        policy=item,  # type: ignore[arg-type]
        user=SimpleNamespace(id="user-id"),  # type: ignore[arg-type]
        settings=SimpleNamespace(is_production=False, dependency_assessment_ttl_hours=48),  # type: ignore[arg-type]
        values={"denied_packages": ["", " FastAPI ", "fastapi", "HTTPX"]},
    )
    assert updated.denied_packages == ["fastapi", "httpx"]
    assert updated.updated_by_id == "user-id"
    assert db.flush_calls == 1
    with pytest.raises(SupplyChainError, match="160"):
        update_dependency_policy(
            db,  # type: ignore[arg-type]
            policy=item,  # type: ignore[arg-type]
            user=SimpleNamespace(id="user-id"),  # type: ignore[arg-type]
            settings=SimpleNamespace(is_production=False, dependency_assessment_ttl_hours=48),  # type: ignore[arg-type]
            values={"denied_packages": ["x" * 161]},
        )


def test_policy_creation_handles_lock_race_and_missing_organization(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Проверить повторное чтение после tenant-lock и отсутствие организации."""
    winner = policy()
    racing_db = FlushOnlySession([None, winner])
    monkeypatch.setattr(supply_chain, "_lock_organization", lambda *_args: object())
    assert (
        get_or_create_dependency_policy(
            racing_db,  # type: ignore[arg-type]
            organization_id="organization-id",
            settings=SimpleNamespace(),  # type: ignore[arg-type]
        )
        is winner
    )
    assert racing_db.added is None
    with pytest.raises(SupplyChainError, match="Организация не найдена"):
        _lock_organization(FlushOnlySession(), "missing")  # type: ignore[arg-type]


def test_report_evaluation_covers_blockers_warnings_and_passed_state() -> None:
    """Проверить все классы policy finding и статусов подписи в readiness оценке."""
    item = attestation("Range>=1", "Preview==2.0rc1", "Denied==3.0")
    report = valid_report(item)
    report["generated_at"] = (datetime.now(UTC) - timedelta(hours=3)).isoformat()
    report["kind"] = DependencyReportKind.INVENTORY_ONLY.value
    report["findings"] = [
        {"severity": "critical", "package": "critical-package"},
        {"severity": "high", "package": "high-package"},
        {"severity": "medium", "package": "medium-package"},
        {"severity": "unknown", "package": "transitive-package"},
    ]
    strict = policy(
        require_exact_pins=True,
        require_vulnerability_scan=True,
        require_trusted_report=True,
        denied_packages=["denied"],
        max_medium=0,
        report_ttl_hours=1,
    )
    result = _evaluate_report(
        attestation=item,  # type: ignore[arg-type]
        report=report,
        policy=strict,  # type: ignore[arg-type]
        signature_status=ArtifactSignatureStatus.UNSIGNED,
        attestation_trusted=False,
        max_ttl_hours=2,
    )
    assert result["status"] == ReadinessStatus.BLOCKED
    assert len(result["blockers"]) >= 8
    assert result["denied_hits"] == ["denied"]
    assert any("необъявленных" in warning for warning in result["warnings"])
    assert any("неизвестной" in warning for warning in result["warnings"])

    stable = attestation("Stable==1.0")
    clean_report = valid_report(stable)
    clean = _evaluate_report(
        attestation=stable,  # type: ignore[arg-type]
        report=clean_report,
        policy=policy(allow_prerelease=True),  # type: ignore[arg-type]
        signature_status=ArtifactSignatureStatus.VALID_TRUSTED,
        attestation_trusted=True,
        max_ttl_hours=48,
    )
    assert clean["status"] == ReadinessStatus.PASSED

    for signature_status, expected in (
        (ArtifactSignatureStatus.INVALID, "недействительна"),
        (ArtifactSignatureStatus.REVOKED, "отозванным"),
        (ArtifactSignatureStatus.UNSIGNED, "не подписан"),
        (ArtifactSignatureStatus.VALID_UNTRUSTED, "не отмечен доверенным"),
    ):
        evaluated = _evaluate_report(
            attestation=stable,  # type: ignore[arg-type]
            report=clean_report,
            policy=policy(allow_prerelease=True),  # type: ignore[arg-type]
            signature_status=signature_status,
            attestation_trusted=True,
            max_ttl_hours=48,
        )
        messages = [*evaluated["blockers"], *evaluated["warnings"]]
        assert any(expected in message for message in messages)


def verification(
    status: ArtifactSignatureStatus = ArtifactSignatureStatus.VALID_TRUSTED,
    *,
    valid: bool = True,
    error: str | None = None,
) -> SimpleNamespace:
    """Создать результат проверки подписи для детерминированных error-path тестов."""
    return SimpleNamespace(
        status=status,
        cryptographically_valid=valid,
        fingerprint="fingerprint",
        error=error,
    )


def assessment_settings(**overrides: Any) -> SimpleNamespace:
    """Создать минимальные настройки dependency assessment."""
    values = {
        "dependency_report_max_bytes": 1_000_000,
        "dependency_assessment_ttl_hours": 48,
        "dependency_require_trusted_report": False,
        "dependency_require_vulnerability_scan": False,
        "require_dependency_assessment": True,
        "require_release_transparency": True,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_create_assessment_rejects_foreign_and_non_json_reports() -> None:
    """Проверить tenant isolation и сериализуемость отчёта до любых записей в БД."""
    item = attestation("Stable==1.0")
    item.organization_id = "foreign"
    with pytest.raises(SupplyChainError, match="другой организации"):
        create_dependency_assessment(
            FlushOnlySession(),  # type: ignore[arg-type]
            organization_id="organization-id",
            attestation=item,  # type: ignore[arg-type]
            user=SimpleNamespace(id="user-id"),  # type: ignore[arg-type]
            settings=assessment_settings(),  # type: ignore[arg-type]
            cipher=SimpleNamespace(),  # type: ignore[arg-type]
            report={},
            signature=None,
            sign_with_default_key=False,
        )

    item.organization_id = "organization-id"
    with pytest.raises(SupplyChainError, match="корректный JSON"):
        create_dependency_assessment(
            FlushOnlySession(),  # type: ignore[arg-type]
            organization_id="organization-id",
            attestation=item,  # type: ignore[arg-type]
            user=SimpleNamespace(id="user-id"),  # type: ignore[arg-type]
            settings=assessment_settings(),  # type: ignore[arg-type]
            cipher=SimpleNamespace(),  # type: ignore[arg-type]
            report={"bad": {1, 2}},  # type: ignore[dict-item]
            signature=None,
            sign_with_default_key=False,
        )


def test_create_assessment_handles_size_and_signature_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Проверить post-normalization size, conflicting signatures и signing failures."""
    item = attestation("Stable==1.0")
    report = valid_report(item)
    monkeypatch.setattr(
        supply_chain,
        "normalize_dependency_report",
        lambda *_args, **_kwargs: {"expanded": "x" * 500},
    )
    with pytest.raises(SupplyChainError, match="допустимый размер"):
        create_dependency_assessment(
            FlushOnlySession(),  # type: ignore[arg-type]
            organization_id=item.organization_id,
            attestation=item,  # type: ignore[arg-type]
            user=SimpleNamespace(id="user-id"),  # type: ignore[arg-type]
            settings=assessment_settings(dependency_report_max_bytes=400),  # type: ignore[arg-type]
            cipher=SimpleNamespace(),  # type: ignore[arg-type]
            report=report,
            signature=None,
            sign_with_default_key=False,
        )
    monkeypatch.setattr(supply_chain, "normalize_dependency_report", normalize_dependency_report)

    with pytest.raises(SupplyChainError, match="одновременно"):
        create_dependency_assessment(
            FlushOnlySession(),  # type: ignore[arg-type]
            organization_id=item.organization_id,
            attestation=item,  # type: ignore[arg-type]
            user=SimpleNamespace(id="user-id"),  # type: ignore[arg-type]
            settings=assessment_settings(),  # type: ignore[arg-type]
            cipher=SimpleNamespace(),  # type: ignore[arg-type]
            report=report,
            signature={"signature": "external"},
            sign_with_default_key=True,
        )

    no_sbom = {key: value for key, value in report.items() if key != "sbom_sha256"}
    monkeypatch.setattr(supply_chain, "get_default_signing_key", lambda *_args, **_kwargs: None)
    with pytest.raises(SupplyChainError, match="основной Ed25519"):
        create_dependency_assessment(
            FlushOnlySession(),  # type: ignore[arg-type]
            organization_id=item.organization_id,
            attestation=item,  # type: ignore[arg-type]
            user=SimpleNamespace(id="user-id"),  # type: ignore[arg-type]
            settings=assessment_settings(),  # type: ignore[arg-type]
            cipher=SimpleNamespace(),  # type: ignore[arg-type]
            report=no_sbom,
            signature=None,
            sign_with_default_key=True,
        )

    monkeypatch.setattr(supply_chain, "get_default_signing_key", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(
        supply_chain,
        "sign_bytes",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(ArtifactSigningError("sign failed")),
    )
    with pytest.raises(SupplyChainError, match="sign failed"):
        create_dependency_assessment(
            FlushOnlySession(),  # type: ignore[arg-type]
            organization_id=item.organization_id,
            attestation=item,  # type: ignore[arg-type]
            user=SimpleNamespace(id="user-id"),  # type: ignore[arg-type]
            settings=assessment_settings(),  # type: ignore[arg-type]
            cipher=SimpleNamespace(),  # type: ignore[arg-type]
            report=no_sbom,
            signature=None,
            sign_with_default_key=True,
        )


def test_create_assessment_rejects_invalid_import_and_reuses_existing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Проверить недействительную внешнюю подпись и idempotent повтор assessment."""
    item = attestation("Stable==1.0")
    report = valid_report(item)
    monkeypatch.setattr(
        supply_chain,
        "verify_signature",
        lambda *_args, **_kwargs: verification(
            ArtifactSignatureStatus.INVALID, valid=False, error="bad signature"
        ),
    )
    with pytest.raises(SupplyChainError, match="bad signature"):
        create_dependency_assessment(
            FlushOnlySession(),  # type: ignore[arg-type]
            organization_id=item.organization_id,
            attestation=item,  # type: ignore[arg-type]
            user=SimpleNamespace(id="user-id"),  # type: ignore[arg-type]
            settings=assessment_settings(),  # type: ignore[arg-type]
            cipher=SimpleNamespace(),  # type: ignore[arg-type]
            report=report,
            signature={"signature": "external"},
            sign_with_default_key=False,
        )

    existing = object()
    db = FlushOnlySession([policy(), existing])
    monkeypatch.setattr(supply_chain, "verify_signature", lambda *_args, **_kwargs: verification())
    monkeypatch.setattr(supply_chain, "verify_release_attestation", lambda *_args: True)
    assert (
        create_dependency_assessment(
            db,  # type: ignore[arg-type]
            organization_id=item.organization_id,
            attestation=item,  # type: ignore[arg-type]
            user=SimpleNamespace(id="user-id"),  # type: ignore[arg-type]
            settings=assessment_settings(),  # type: ignore[arg-type]
            cipher=SimpleNamespace(),  # type: ignore[arg-type]
            report=report,
            signature=None,
            sign_with_default_key=False,
        )
        is existing
    )


def test_verify_assessment_fails_closed_for_missing_or_malformed_attestation() -> None:
    """Проверить блокировку assessment при потере attestation и повреждённом report."""
    missing = SimpleNamespace(
        release_attestation_id="missing",
        organization_id="organization-id",
    )
    db = FlushOnlySession([None])
    assert (
        verify_dependency_assessment(
            db,  # type: ignore[arg-type]
            item=missing,  # type: ignore[arg-type]
            settings=assessment_settings(),  # type: ignore[arg-type]
        )
        is False
    )
    assert missing.status == ReadinessStatus.BLOCKED
    assert missing.verified_at is None

    item = attestation("Stable==1.0")
    malformed = SimpleNamespace(
        release_attestation_id=item.id,
        organization_id=item.organization_id,
        report_payload={"bad": True},
    )
    db = FlushOnlySession([item])
    assert (
        verify_dependency_assessment(
            db,  # type: ignore[arg-type]
            item=malformed,  # type: ignore[arg-type]
            settings=assessment_settings(),  # type: ignore[arg-type]
        )
        is False
    )
    assert malformed.status == ReadinessStatus.BLOCKED


def test_verify_assessment_detects_every_immutable_evidence_mismatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Проверить hashes policy/SBOM/report и связь с release attestation без переписывания evidence."""
    attested = attestation("Stable==1.0")
    report = normalize_dependency_report(
        valid_report(attested),
        attestation=attested,  # type: ignore[arg-type]
    )
    current_policy = policy(allow_prerelease=True)
    assessed = SimpleNamespace(
        id="assessment-id",
        release_attestation_id=attested.id,
        organization_id=attested.organization_id,
        report_payload=report,
        report_sha256="0" * 64,
        policy_snapshot={"tampered": True},
        policy_sha256="1" * 64,
        sbom_payload={"tampered": True},
        sbom_sha256="2" * 64,
        attestation_payload_sha256="3" * 64,
        signature_info={},
    )
    db = FlushOnlySession([attested, current_policy])
    monkeypatch.setattr(supply_chain, "verify_signature", lambda *_args, **_kwargs: verification())
    monkeypatch.setattr(supply_chain, "verify_release_attestation", lambda *_args: True)
    assert (
        verify_dependency_assessment(
            db,  # type: ignore[arg-type]
            item=assessed,  # type: ignore[arg-type]
            settings=assessment_settings(),  # type: ignore[arg-type]
        )
        is False
    )
    assert assessed.status == ReadinessStatus.BLOCKED
    assert len(assessed.blockers) >= 6
    assert assessed.verified_at is None


def test_append_transparency_rejects_invalid_state_transitions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Проверить tenant, trust, duplicate publish, withdraw state и обязательную причину."""
    item = attestation("Stable==1.0")
    user = SimpleNamespace(id="user-id")
    monkeypatch.setattr(supply_chain, "_lock_organization", lambda *_args: object())
    monkeypatch.setattr(supply_chain, "verify_release_attestation", lambda *_args: True)

    item.organization_id = "foreign"
    with pytest.raises(SupplyChainError, match="другой организации"):
        append_transparency_event(
            FlushOnlySession(),  # type: ignore[arg-type]
            organization_id="organization-id",
            attestation=item,  # type: ignore[arg-type]
            user=user,  # type: ignore[arg-type]
            cipher=SimpleNamespace(),  # type: ignore[arg-type]
            event_type=ReleaseTransparencyEventType.PUBLISHED,
        )
    item.organization_id = "organization-id"
    item.signature_status = ArtifactSignatureStatus.INVALID
    monkeypatch.setattr(supply_chain, "verify_release_attestation", lambda *_args: False)
    with pytest.raises(SupplyChainError, match="доверенный"):
        append_transparency_event(
            FlushOnlySession(),  # type: ignore[arg-type]
            organization_id=item.organization_id,
            attestation=item,  # type: ignore[arg-type]
            user=user,  # type: ignore[arg-type]
            cipher=SimpleNamespace(),  # type: ignore[arg-type]
            event_type=ReleaseTransparencyEventType.PUBLISHED,
        )

    item.signature_status = ArtifactSignatureStatus.VALID_TRUSTED
    monkeypatch.setattr(supply_chain, "verify_release_attestation", lambda *_args: True)
    monkeypatch.setattr(
        supply_chain,
        "current_release_state",
        lambda *_args, **_kwargs: ReleaseTransparencyEventType.PUBLISHED,
    )
    with pytest.raises(SupplyChainError, match="уже опубликован"):
        append_transparency_event(
            FlushOnlySession(),  # type: ignore[arg-type]
            organization_id=item.organization_id,
            attestation=item,  # type: ignore[arg-type]
            user=user,  # type: ignore[arg-type]
            cipher=SimpleNamespace(),  # type: ignore[arg-type]
            event_type=ReleaseTransparencyEventType.PUBLISHED,
        )

    monkeypatch.setattr(supply_chain, "current_release_state", lambda *_args, **_kwargs: None)
    with pytest.raises(SupplyChainError, match="только опубликованный"):
        append_transparency_event(
            FlushOnlySession(),  # type: ignore[arg-type]
            organization_id=item.organization_id,
            attestation=item,  # type: ignore[arg-type]
            user=user,  # type: ignore[arg-type]
            cipher=SimpleNamespace(),  # type: ignore[arg-type]
            event_type=ReleaseTransparencyEventType.WITHDRAWN,
            reason="достаточная причина",
        )
    monkeypatch.setattr(
        supply_chain,
        "current_release_state",
        lambda *_args, **_kwargs: ReleaseTransparencyEventType.PUBLISHED,
    )
    with pytest.raises(SupplyChainError, match="не короче 5"):
        append_transparency_event(
            FlushOnlySession(),  # type: ignore[arg-type]
            organization_id=item.organization_id,
            attestation=item,  # type: ignore[arg-type]
            user=user,  # type: ignore[arg-type]
            cipher=SimpleNamespace(),  # type: ignore[arg-type]
            event_type=ReleaseTransparencyEventType.WITHDRAWN,
            reason="bad",
        )


def test_append_transparency_rejects_version_conflict_and_signing_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Проверить один active attestation на версию и fail-closed локальную подпись."""
    item = attestation("Stable==1.0")
    item.signature_status = ArtifactSignatureStatus.VALID_TRUSTED
    user = SimpleNamespace(id="user-id")
    monkeypatch.setattr(supply_chain, "_lock_organization", lambda *_args: object())
    monkeypatch.setattr(supply_chain, "verify_release_attestation", lambda *_args: True)
    monkeypatch.setattr(supply_chain, "current_release_state", lambda *_args, **_kwargs: None)
    active = SimpleNamespace(
        release_attestation_id="other-attestation",
        event_type=ReleaseTransparencyEventType.PUBLISHED,
    )
    with pytest.raises(SupplyChainError, match="этой версии"):
        append_transparency_event(
            FlushOnlySession([[active]]),  # type: ignore[arg-type]
            organization_id=item.organization_id,
            attestation=item,  # type: ignore[arg-type]
            user=user,  # type: ignore[arg-type]
            cipher=SimpleNamespace(),  # type: ignore[arg-type]
            event_type=ReleaseTransparencyEventType.PUBLISHED,
        )

    db = FlushOnlySession([[], None])
    monkeypatch.setattr(supply_chain, "get_default_signing_key", lambda *_args, **_kwargs: None)
    with pytest.raises(SupplyChainError, match="приватной частью"):
        append_transparency_event(
            db,  # type: ignore[arg-type]
            organization_id=item.organization_id,
            attestation=item,  # type: ignore[arg-type]
            user=user,  # type: ignore[arg-type]
            cipher=SimpleNamespace(),  # type: ignore[arg-type]
            event_type=ReleaseTransparencyEventType.PUBLISHED,
        )

    monkeypatch.setattr(supply_chain, "get_default_signing_key", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(
        supply_chain,
        "sign_bytes",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(ArtifactSigningError("sign failed")),
    )
    with pytest.raises(SupplyChainError, match="sign failed"):
        append_transparency_event(
            FlushOnlySession([[], None]),  # type: ignore[arg-type]
            organization_id=item.organization_id,
            attestation=item,  # type: ignore[arg-type]
            user=user,  # type: ignore[arg-type]
            cipher=SimpleNamespace(),  # type: ignore[arg-type]
            event_type=ReleaseTransparencyEventType.PUBLISHED,
        )

    monkeypatch.setattr(supply_chain, "sign_bytes", lambda *_args, **_kwargs: {"signature": "x"})
    monkeypatch.setattr(
        supply_chain,
        "verify_signature",
        lambda *_args, **_kwargs: verification(ArtifactSignatureStatus.INVALID, valid=False),
    )
    with pytest.raises(SupplyChainError, match="не подтверждён"):
        append_transparency_event(
            FlushOnlySession([[], None]),  # type: ignore[arg-type]
            organization_id=item.organization_id,
            attestation=item,  # type: ignore[arg-type]
            user=user,  # type: ignore[arg-type]
            cipher=SimpleNamespace(),  # type: ignore[arg-type]
            event_type=ReleaseTransparencyEventType.PUBLISHED,
        )


def test_transparency_verifier_reports_all_corrupted_entry_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Проверить диагностику sequence, hashes, payload, signature и потерянного attestation."""
    created_at = datetime.now(UTC)
    entry = SimpleNamespace(
        sequence=2,
        previous_hash="bad-previous",
        payload={
            "schema_version": 0,
            "sequence": 99,
            "event_type": "wrong",
            "release_attestation_id": "wrong",
            "previous_hash": "wrong",
            "created_by_id": "wrong",
            "created_at": "bad-time",
        },
        event_type=ReleaseTransparencyEventType.PUBLISHED,
        release_attestation_id="missing-attestation",
        created_by_id="user-id",
        created_at=created_at,
        entry_hash="bad-hash",
        signature_info={},
        signature_status=ArtifactSignatureStatus.VALID_TRUSTED,
        signer_fingerprint="stored-fingerprint",
    )
    db = FlushOnlySession([[entry], []])
    monkeypatch.setattr(
        supply_chain,
        "verify_signature",
        lambda *_args, **_kwargs: verification(ArtifactSignatureStatus.INVALID, valid=False),
    )
    result = verify_transparency_chain(db, organization_id="organization-id")  # type: ignore[arg-type]
    assert result["valid"] is False
    assert result["entry_count"] == 1
    assert len(result["errors"]) >= 11
    assert any("release attestation не найден" in error for error in result["errors"])


def test_transparency_verifier_detects_attestation_version_and_commit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Проверить binding transparency payload к version и source_commit attestation."""
    item = attestation("Stable==1.0")
    payload = {
        "schema_version": 1,
        "sequence": 1,
        "event_type": ReleaseTransparencyEventType.PUBLISHED.value,
        "release_attestation_id": item.id,
        "release_payload_sha256": item.payload_sha256,
        "version": "wrong-version",
        "source_commit": "wrong-commit",
        "previous_hash": supply_chain.ZERO_HASH,
        "created_by_id": "user-id",
        "created_at": datetime.now(UTC).isoformat(),
    }
    entry = SimpleNamespace(
        sequence=1,
        previous_hash=supply_chain.ZERO_HASH,
        payload=payload,
        event_type=ReleaseTransparencyEventType.PUBLISHED,
        release_attestation_id=item.id,
        created_by_id="user-id",
        created_at=datetime.fromisoformat(payload["created_at"]) + timedelta(seconds=1),
        entry_hash=supply_chain._transparency_hash(payload),
        signature_info={},
        signature_status=ArtifactSignatureStatus.VALID_TRUSTED,
        signer_fingerprint="fingerprint",
    )
    db = FlushOnlySession([[entry], [item]])
    monkeypatch.setattr(supply_chain, "verify_signature", lambda *_args, **_kwargs: verification())
    monkeypatch.setattr(
        supply_chain,
        "canonical_release_payload",
        lambda _payload: b"canonical",
    )
    item.payload_sha256 = supply_chain.hashlib.sha256(b"canonical").hexdigest()
    payload["release_payload_sha256"] = item.payload_sha256
    entry.entry_hash = supply_chain._transparency_hash(payload)
    result = verify_transparency_chain(db, organization_id=item.organization_id)  # type: ignore[arg-type]
    assert result["valid"] is False
    assert any("версия release" in error for error in result["errors"])
    assert any("source commit" in error for error in result["errors"])


def upgrade_change(**overrides: Any) -> SimpleNamespace:
    """Создать минимальный upgrade change для supply-chain release gates."""
    values = {
        "change_type": ChangeRequestType.UPGRADE,
        "organization_id": "organization-id",
        "release_attestation_id": "attestation-id",
        "release_dependency_assessment_id": "assessment-id",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_change_transparency_gate_handles_missing_attestation_and_broken_chain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Проверить обязательную release binding и целостность transparency chain."""
    valid, message = validate_change_release_transparency(
        FlushOnlySession(),  # type: ignore[arg-type]
        change=upgrade_change(release_attestation_id=None),  # type: ignore[arg-type]
        settings=assessment_settings(),  # type: ignore[arg-type]
    )
    assert valid is False and "отсутствует" in message

    monkeypatch.setattr(
        supply_chain,
        "verify_transparency_chain",
        lambda *_args, **_kwargs: {"valid": False},
    )
    valid, message = validate_change_release_transparency(
        FlushOnlySession(),  # type: ignore[arg-type]
        change=upgrade_change(),  # type: ignore[arg-type]
        settings=assessment_settings(),  # type: ignore[arg-type]
    )
    assert valid is False and "целостность" in message


def dependency_item(**overrides: Any) -> SimpleNamespace:
    """Создать assessment для прямой проверки change dependency gate."""
    current_policy = policy()
    values = {
        "id": "assessment-id",
        "policy_sha256": sha256_json(dependency_policy_payload(current_policy)),
        "expires_at": datetime.now(UTC) + timedelta(hours=1),
        "signature_status": ArtifactSignatureStatus.VALID_TRUSTED,
        "report_kind": DependencyReportKind.VULNERABILITY_SCAN,
        "status": ReadinessStatus.PASSED,
        "blockers": [],
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_change_dependency_gate_rejects_each_stale_or_blocked_binding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Проверить missing/stale/expired/untrusted/inventory/blocked assessment outcomes."""
    valid, message, assessment_id = validate_change_dependency_assurance(
        FlushOnlySession(),  # type: ignore[arg-type]
        change=upgrade_change(release_attestation_id=None),  # type: ignore[arg-type]
        settings=assessment_settings(),  # type: ignore[arg-type]
    )
    assert valid is False and "отсутствует" in message and assessment_id is None

    current_policy = policy()
    valid, message, assessment_id = validate_change_dependency_assurance(
        FlushOnlySession([current_policy, None]),  # type: ignore[arg-type]
        change=upgrade_change(),  # type: ignore[arg-type]
        settings=assessment_settings(),  # type: ignore[arg-type]
    )
    assert valid is False and "не найден" in message and assessment_id == "assessment-id"

    monkeypatch.setattr(
        supply_chain, "verify_dependency_assessment", lambda *_args, **_kwargs: True
    )
    cases = [
        (
            dependency_item(policy_sha256="0" * 64),
            assessment_settings(),
            "устаревшей политике",
        ),
        (
            dependency_item(expires_at=datetime.now(UTC) - timedelta(seconds=1)),
            assessment_settings(),
            "просрочен",
        ),
        (
            dependency_item(signature_status=ArtifactSignatureStatus.UNSIGNED),
            assessment_settings(dependency_require_trusted_report=True),
            "доверенным ключом",
        ),
        (
            dependency_item(report_kind=DependencyReportKind.INVENTORY_ONLY),
            assessment_settings(dependency_require_vulnerability_scan=True),
            "vulnerability scan",
        ),
        (
            dependency_item(status=ReadinessStatus.BLOCKED, blockers=["policy blocker"]),
            assessment_settings(),
            "policy blocker",
        ),
    ]
    for assessed, settings, expected in cases:
        valid, message, assessment_id = validate_change_dependency_assurance(
            FlushOnlySession([current_policy, assessed]),  # type: ignore[arg-type]
            change=upgrade_change(),  # type: ignore[arg-type]
            settings=settings,  # type: ignore[arg-type]
        )
        assert valid is False
        assert expected in message
        assert assessment_id == assessed.id
