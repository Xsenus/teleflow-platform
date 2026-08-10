from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from app.config import Settings


def test_production_rejects_default_secrets() -> None:
    """Проверить сценарий production rejects default secrets. Тест завершается ошибкой при
    нарушении зафиксированного инварианта.
    """
    settings = Settings(
        _env_file=None,
        environment="production",
        debug=False,
        public_base_url="https://panel.example.com",
        telegram_fake_mode=False,
    )
    with pytest.raises(RuntimeError):
        settings.validate_runtime_security()


def test_production_accepts_hardened_settings() -> None:
    """Проверить сценарий production accepts hardened settings. Тест завершается ошибкой при
    нарушении зафиксированного инварианта.
    """
    settings = Settings(
        _env_file=None,
        environment="production",
        debug=False,
        master_key="base64:MDEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODlhYmNkZWY=",
        jwt_secret="a" * 64,
        bootstrap_admin_password="A-strong-bootstrap-password-123!",
        public_base_url="https://panel.example.com",
        database_url="postgresql+psycopg://teleflow:secret@db/teleflow",
        telegram_fake_mode=False,
        require_admin_totp=True,
        pilot_readiness_required=True,
        pilot_stage_enforcement_required=True,
        slo_gate_required=True,
        capacity_assurance_required=True,
        execution_fencing_required=True,
        execution_site_key="primary",
        execution_primary_site_key="primary",
        artifact_signature_policy="require_trusted",
        require_trusted_release_attestation=True,
        require_release_transparency=True,
        require_dependency_assessment=True,
        dependency_require_vulnerability_scan=True,
        dependency_require_trusted_report=True,
        recovery_require_encrypted_backup=True,
        recovery_require_trusted_signature=True,
        recovery_age_recipient="age1qql3f0exampleonlyrecipientxxxxxxxxxxxxxxxxxxxxxxxxx",
        continuity_assurance_required=True,
        continuity_require_distinct_signoff=True,
        continuity_default_require_live_drill=True,
        storage_backend="s3",
        s3_endpoint_url="https://s3.example.com",
        s3_bucket="teleflow",
        s3_access_key="access",
        s3_secret_key="secret",
    )
    settings.validate_runtime_security()


@pytest.mark.parametrize(
    ("field", "message"),
    [
        ("require_release_transparency", "REQUIRE_RELEASE_TRANSPARENCY"),
        ("require_dependency_assessment", "REQUIRE_DEPENDENCY_ASSESSMENT"),
        ("dependency_require_vulnerability_scan", "DEPENDENCY_REQUIRE_VULNERABILITY_SCAN"),
        ("dependency_require_trusted_report", "DEPENDENCY_REQUIRE_TRUSTED_REPORT"),
    ],
)
def test_production_rejects_disabled_supply_chain_gate(field: str, message: str) -> None:
    """Проверить сценарий production rejects disabled supply chain gate. Тест завершается ошибкой
    при нарушении зафиксированного инварианта.
    """
    values = {
        "environment": "production",
        "debug": False,
        "master_key": "base64:MDEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODlhYmNkZWY=",
        "jwt_secret": "a" * 64,
        "bootstrap_admin_password": "A-strong-bootstrap-password-123!",
        "public_base_url": "https://panel.example.com",
        "database_url": "postgresql+psycopg://teleflow:secret@db/teleflow",
        "telegram_fake_mode": False,
        "require_admin_totp": True,
        "pilot_readiness_required": True,
        "pilot_stage_enforcement_required": True,
        "slo_gate_required": True,
        "artifact_signature_policy": "require_trusted",
        "require_trusted_release_attestation": True,
        "require_release_transparency": True,
        "require_dependency_assessment": True,
        "dependency_require_vulnerability_scan": True,
        "dependency_require_trusted_report": True,
        "recovery_require_encrypted_backup": True,
        "recovery_require_trusted_signature": True,
        "recovery_age_recipient": "age1qql3f0exampleonlyrecipientxxxxxxxxxxxxxxxxxxxxxxxxx",
    }
    values[field] = False
    settings = Settings(_env_file=None, **values)
    with pytest.raises(RuntimeError, match=message):
        settings.validate_runtime_security()


def test_production_rejects_disabled_pilot_readiness_gate() -> None:
    """Проверить сценарий production rejects disabled pilot readiness gate. Тест завершается
    ошибкой при нарушении зафиксированного инварианта.
    """
    settings = Settings(
        _env_file=None,
        environment="production",
        debug=False,
        master_key="base64:MDEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODlhYmNkZWY=",
        jwt_secret="a" * 64,
        bootstrap_admin_password="A-strong-bootstrap-password-123!",
        public_base_url="https://panel.example.com",
        database_url="postgresql+psycopg://teleflow:secret@db/teleflow",
        telegram_fake_mode=False,
        require_admin_totp=True,
        pilot_readiness_required=False,
    )
    with pytest.raises(RuntimeError, match="PILOT_READINESS_REQUIRED"):
        settings.validate_runtime_security()


def test_production_rejects_disabled_slo_gate() -> None:
    """Проверить сценарий production rejects disabled slo gate. Тест завершается ошибкой при
    нарушении зафиксированного инварианта.
    """
    settings = Settings(
        _env_file=None,
        environment="production",
        debug=False,
        master_key="base64:MDEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODlhYmNkZWY=",
        jwt_secret="a" * 64,
        bootstrap_admin_password="A-strong-bootstrap-password-123!",
        public_base_url="https://panel.example.com",
        database_url="postgresql+psycopg://teleflow:secret@db/teleflow",
        telegram_fake_mode=False,
        require_admin_totp=True,
        pilot_readiness_required=True,
        pilot_stage_enforcement_required=True,
        artifact_signature_policy="require_trusted",
        require_trusted_release_attestation=True,
        require_release_transparency=True,
        require_dependency_assessment=True,
        dependency_require_vulnerability_scan=True,
        dependency_require_trusted_report=True,
        recovery_require_encrypted_backup=True,
        recovery_require_trusted_signature=True,
        recovery_age_recipient="age1qql3f0exampleonlyrecipientxxxxxxxxxxxxxxxxxxxxxxxxx",
        slo_gate_required=False,
    )
    with pytest.raises(RuntimeError, match="SLO_GATE_REQUIRED"):
        settings.validate_runtime_security()


def test_compose_passes_production_security_flags() -> None:
    """Проверить сценарий compose passes production security flags. Тест завершается ошибкой при
    нарушении зафиксированного инварианта.
    """
    root = Path(__file__).resolve().parents[1]
    development = yaml.safe_load((root / "docker-compose.yml").read_text(encoding="utf-8"))
    production = yaml.safe_load((root / "docker-compose.prod.yml").read_text(encoding="utf-8"))

    dockerfile = (root / "Dockerfile").read_text(encoding="utf-8")
    assert 'org.opencontainers.image.version="2.5.0"' in dockerfile

    api_environment = development["services"]["api"]["environment"]
    assert api_environment["TELEFLOW_VERSION"].endswith(":-2.5.0}")
    assert "TELEFLOW_COMMISSIONING_TTL_MINUTES" in api_environment
    assert "TELEFLOW_PILOT_READINESS_REQUIRED" in api_environment
    assert "TELEFLOW_PILOT_STAGE_ENFORCEMENT_REQUIRED" in api_environment
    assert "TELEFLOW_SUPPORT_BUNDLE_TTL_HOURS" in api_environment
    assert "TELEFLOW_ARTIFACT_SIGNATURE_POLICY" in api_environment
    assert "TELEFLOW_REQUIRE_TRUSTED_RELEASE_ATTESTATION" in api_environment
    assert "TELEFLOW_REQUIRE_RELEASE_TRANSPARENCY" in api_environment
    assert "TELEFLOW_REQUIRE_DEPENDENCY_ASSESSMENT" in api_environment
    assert "TELEFLOW_DEPENDENCY_REQUIRE_VULNERABILITY_SCAN" in api_environment
    assert "TELEFLOW_DEPENDENCY_REQUIRE_TRUSTED_REPORT" in api_environment
    assert "TELEFLOW_DEPENDENCY_REPORT_MAX_BYTES" in api_environment
    assert "TELEFLOW_SLO_GATE_REQUIRED" in api_environment
    assert "TELEFLOW_CAPACITY_ASSURANCE_REQUIRED" in api_environment
    assert "TELEFLOW_CAPACITY_AUTO_EVALUATE_MINUTES" in api_environment
    assert "TELEFLOW_CAPACITY_ASSESSMENT_TTL_MINUTES" in api_environment
    assert "TELEFLOW_CAPACITY_DEFAULT_MAX_ACTIVE_JOBS" in api_environment
    assert "TELEFLOW_CAPACITY_DEFAULT_MAX_READY_JOBS" in api_environment
    assert "TELEFLOW_CAPACITY_DEFAULT_MAX_PROCESSING_JOBS" in api_environment
    assert "TELEFLOW_CAPACITY_DEFAULT_MAX_NETWORK_STARTS_PER_MINUTE" in api_environment
    assert "TELEFLOW_CAPACITY_RETRY_DELAY_SECONDS" in api_environment
    assert "TELEFLOW_EXECUTION_FENCING_REQUIRED" in api_environment
    assert "TELEFLOW_EXECUTION_SITE_KEY" in api_environment
    assert "TELEFLOW_EXECUTION_PRIMARY_SITE_KEY" in api_environment
    assert "TELEFLOW_CONTINUITY_ASSURANCE_REQUIRED" in api_environment
    assert "TELEFLOW_CONTINUITY_REQUIRE_DISTINCT_SIGNOFF" in api_environment
    assert "TELEFLOW_CONTINUITY_DEFAULT_REQUIRE_LIVE_DRILL" in api_environment
    assert "TELEFLOW_CONTINUITY_SYNC_INTERVAL_SECONDS" in api_environment
    assert "TELEFLOW_SLO_AUTO_EVALUATE_MINUTES" in api_environment
    assert "TELEFLOW_SLO_ASSESSMENT_TTL_MINUTES" in api_environment
    assert "TELEFLOW_SLO_DEFAULT_DELIVERY_SUCCESS_TARGET_BPS" in api_environment
    assert "TELEFLOW_RECOVERY_DEFAULT_RPO_HOURS" in api_environment
    assert "TELEFLOW_RECOVERY_DEFAULT_RTO_MINUTES" in api_environment
    assert "TELEFLOW_RECOVERY_REQUIRE_ENCRYPTED_BACKUP" in api_environment
    assert "TELEFLOW_RECOVERY_REQUIRE_TRUSTED_SIGNATURE" in api_environment
    assert "TELEFLOW_RECOVERY_AGE_RECIPIENT" in api_environment

    for service in ("migrate", "api", "worker"):
        environment = production["services"][service]["environment"]
        assert environment["TELEFLOW_PILOT_READINESS_REQUIRED"] == "true"
        assert environment["TELEFLOW_PILOT_STAGE_ENFORCEMENT_REQUIRED"] == "true"
        assert environment["TELEFLOW_ARTIFACT_SIGNATURE_POLICY"] == "require_trusted"
        assert environment["TELEFLOW_REQUIRE_TRUSTED_RELEASE_ATTESTATION"] == "true"
        assert environment["TELEFLOW_REQUIRE_RELEASE_TRANSPARENCY"] == "true"
        assert environment["TELEFLOW_REQUIRE_DEPENDENCY_ASSESSMENT"] == "true"
        assert environment["TELEFLOW_DEPENDENCY_REQUIRE_VULNERABILITY_SCAN"] == "true"
        assert environment["TELEFLOW_DEPENDENCY_REQUIRE_TRUSTED_REPORT"] == "true"
        assert environment["TELEFLOW_SLO_GATE_REQUIRED"] == "true"
        assert environment["TELEFLOW_CAPACITY_ASSURANCE_REQUIRED"] == "true"
        assert environment["TELEFLOW_EXECUTION_FENCING_REQUIRED"] == "true"
        assert environment["TELEFLOW_CONTINUITY_ASSURANCE_REQUIRED"] == "true"
        assert environment["TELEFLOW_CONTINUITY_REQUIRE_DISTINCT_SIGNOFF"] == "true"
        assert environment["TELEFLOW_CONTINUITY_DEFAULT_REQUIRE_LIVE_DRILL"] == "true"
        assert environment["TELEFLOW_RECOVERY_REQUIRE_ENCRYPTED_BACKUP"] == "true"
        assert environment["TELEFLOW_RECOVERY_REQUIRE_TRUSTED_SIGNATURE"] == "true"
        assert "TELEFLOW_RECOVERY_AGE_RECIPIENT" in environment


def test_production_rejects_weak_recovery_policy() -> None:
    """Проверить сценарий production rejects weak recovery policy. Тест завершается ошибкой при
    нарушении зафиксированного инварианта.
    """
    settings = Settings(
        _env_file=None,
        environment="production",
        debug=False,
        master_key="base64:MDEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODlhYmNkZWY=",
        jwt_secret="a" * 64,
        bootstrap_admin_password="A-strong-bootstrap-password-123!",
        public_base_url="https://panel.example.com",
        database_url="postgresql+psycopg://teleflow:secret@db/teleflow",
        telegram_fake_mode=False,
        require_admin_totp=True,
        pilot_readiness_required=True,
        pilot_stage_enforcement_required=True,
        artifact_signature_policy="require_trusted",
        require_trusted_release_attestation=True,
        recovery_require_encrypted_backup=False,
        recovery_require_trusted_signature=False,
    )
    with pytest.raises(RuntimeError, match="RECOVERY_REQUIRE_ENCRYPTED_BACKUP"):
        settings.validate_runtime_security()
