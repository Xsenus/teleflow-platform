from __future__ import annotations

"""Static release-asset validation that also works after a clean Git export."""

import argparse
import json
import re
import subprocess
from pathlib import Path
from typing import Any
from urllib.parse import unquote

import yaml

ROOT = Path(__file__).resolve().parents[1]
EXPECTED_VERSION = "2.5.0"
EXPECTED_COMPOSE_SERVICES = {
    "docker-compose.yml": {
        "api",
        "worker",
        "migrate",
        "db",
        "redis",
        "nginx",
        "prometheus",
        "grafana",
    },
    "docker-compose.prod.yml": {"api", "worker", "migrate", "nginx"},
}
FORBIDDEN_NAMES = {
    ".env",
    ".coverage",
    "coverage.xml",
    ".pytest_cache",
    "__pycache__",
}
FORBIDDEN_SUFFIXES = {
    ".db",
    ".sqlite",
    ".sqlite3",
    ".session",
    ".pem",
    ".key",
    ".p12",
    ".pfx",
    ".pyc",
}
ALLOWED_SECRET_EXAMPLES = {
    Path(".env.example"),
    Path("deploy/.env.production.example"),
}
MARKDOWN_LINK_RE = re.compile(r"!?\[[^\]]*\]\(([^)]+)\)")


def _candidate_files() -> list[Path]:
    """Реализовать внутренний этап candidate files step. Вспомогательная функция сохраняет
    детерминированность и тестируемость процесса.
    """
    try:
        completed = subprocess.run(
            ["git", "ls-files", "-co", "--exclude-standard", "-z"],
            cwd=ROOT,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
        paths = [Path(item.decode()) for item in completed.stdout.split(b"\0") if item]
        if paths:
            return paths
    except (FileNotFoundError, subprocess.CalledProcessError):
        pass
    return [
        path.relative_to(ROOT)
        for path in ROOT.rglob("*")
        if path.is_file() and ".git" not in path.relative_to(ROOT).parts
    ]


def _validate_version(errors: list[str], results: dict[str, Any]) -> None:
    """Реализовать внутренний этап validate version step. Вспомогательная функция сохраняет
    детерминированность и тестируемость процесса.
    """
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    config = (ROOT / "app/config.py").read_text(encoding="utf-8")
    sw = (ROOT / "app/static/sw.js").read_text(encoding="utf-8")
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    checks = {
        "pyproject": f'version = "{EXPECTED_VERSION}"' in pyproject,
        "settings": f'version: str = "{EXPECTED_VERSION}"' in config,
        "service_worker": f"teleflow-shell-v{EXPECTED_VERSION}" in sw,
        "docker_label": f'org.opencontainers.image.version="{EXPECTED_VERSION}"' in dockerfile,
    }
    for name, ok in checks.items():
        if not ok:
            errors.append(f"version marker missing or stale: {name}")
    results["version"] = checks


def _validate_compose(errors: list[str], results: dict[str, Any]) -> None:
    """Реализовать внутренний этап validate compose step. Вспомогательная функция сохраняет
    детерминированность и тестируемость процесса.
    """
    compose_results: dict[str, Any] = {}
    for filename, expected in EXPECTED_COMPOSE_SERVICES.items():
        data = yaml.safe_load((ROOT / filename).read_text(encoding="utf-8")) or {}
        services = set((data.get("services") or {}).keys())
        if services != expected:
            errors.append(
                f"{filename}: services mismatch, expected={sorted(expected)}, actual={sorted(services)}"
            )
        compose_results[filename] = sorted(services)

        if filename == "docker-compose.yml":
            api_environment = (data.get("services") or {}).get("api", {}).get("environment") or {}
            if not str(api_environment.get("TELEFLOW_VERSION", "")).endswith(
                f":-{EXPECTED_VERSION}}}"
            ):
                errors.append("docker-compose.yml: TELEFLOW_VERSION default is stale")
            for key in (
                "TELEFLOW_COMMISSIONING_TTL_MINUTES",
                "TELEFLOW_CHANGE_VERIFICATION_TTL_MINUTES",
                "TELEFLOW_PILOT_READINESS_REQUIRED",
                "TELEFLOW_PILOT_STAGE_ENFORCEMENT_REQUIRED",
                "TELEFLOW_SUPPORT_BUNDLE_TTL_HOURS",
                "TELEFLOW_ARTIFACT_SIGNATURE_POLICY",
                "TELEFLOW_REQUIRE_TRUSTED_RELEASE_ATTESTATION",
                "TELEFLOW_REQUIRE_RELEASE_TRANSPARENCY",
                "TELEFLOW_REQUIRE_DEPENDENCY_ASSESSMENT",
                "TELEFLOW_DEPENDENCY_ASSESSMENT_TTL_HOURS",
                "TELEFLOW_DEPENDENCY_REQUIRE_VULNERABILITY_SCAN",
                "TELEFLOW_DEPENDENCY_REQUIRE_TRUSTED_REPORT",
                "TELEFLOW_DEPENDENCY_REPORT_MAX_BYTES",
                "TELEFLOW_SLO_GATE_REQUIRED",
                "TELEFLOW_SLO_AUTO_EVALUATE_MINUTES",
                "TELEFLOW_SLO_ASSESSMENT_TTL_MINUTES",
                "TELEFLOW_SLO_DEFAULT_DELIVERY_SUCCESS_TARGET_BPS",
                "TELEFLOW_SLO_DEFAULT_MAX_QUEUE_AGE_SECONDS",
                "TELEFLOW_SLO_DEFAULT_MAX_WORKER_HEARTBEAT_AGE_SECONDS",
                "TELEFLOW_CAPACITY_ASSURANCE_REQUIRED",
                "TELEFLOW_CAPACITY_AUTO_EVALUATE_MINUTES",
                "TELEFLOW_CAPACITY_ASSESSMENT_TTL_MINUTES",
                "TELEFLOW_CAPACITY_DEFAULT_MAX_ACTIVE_JOBS",
                "TELEFLOW_CAPACITY_DEFAULT_MAX_READY_JOBS",
                "TELEFLOW_CAPACITY_DEFAULT_MAX_PROCESSING_JOBS",
                "TELEFLOW_CAPACITY_DEFAULT_MAX_ACTIVE_RUNS",
                "TELEFLOW_CAPACITY_DEFAULT_MAX_JOBS_PER_RUN",
                "TELEFLOW_CAPACITY_DEFAULT_MAX_NETWORK_STARTS_PER_MINUTE",
                "TELEFLOW_CAPACITY_DEFAULT_MAX_NETWORK_STARTS_PER_HOUR",
                "TELEFLOW_CAPACITY_DEFAULT_MAX_ESTIMATED_DRAIN_SECONDS",
                "TELEFLOW_CAPACITY_DEFAULT_WARNING_UTILIZATION_PERCENT",
                "TELEFLOW_CAPACITY_DEFAULT_ADMISSION_BLOCK_UTILIZATION_PERCENT",
                "TELEFLOW_CAPACITY_RETRY_DELAY_SECONDS",
                "TELEFLOW_EXECUTION_FENCING_REQUIRED",
                "TELEFLOW_EXECUTION_SITE_KEY",
                "TELEFLOW_EXECUTION_SITE_NAME",
                "TELEFLOW_EXECUTION_PRIMARY_SITE_KEY",
                "TELEFLOW_EXECUTION_LEASE_TTL_SECONDS",
                "TELEFLOW_EXECUTION_SITE_HEARTBEAT_TTL_SECONDS",
                "TELEFLOW_EXECUTION_REQUIRE_DISTINCT_FAILOVER_APPROVER",
                "TELEFLOW_CONTINUITY_ASSURANCE_REQUIRED",
                "TELEFLOW_CONTINUITY_REQUIRE_DISTINCT_SIGNOFF",
                "TELEFLOW_CONTINUITY_DEFAULT_REQUIRE_LIVE_DRILL",
                "TELEFLOW_CONTINUITY_DEFAULT_MAX_RTO_SECONDS",
                "TELEFLOW_CONTINUITY_DEFAULT_EVIDENCE_VALID_DAYS",
                "TELEFLOW_CONTINUITY_SYNC_INTERVAL_SECONDS",
                "TELEFLOW_RECOVERY_DEFAULT_RPO_HOURS",
                "TELEFLOW_RECOVERY_DEFAULT_RTO_MINUTES",
                "TELEFLOW_RECOVERY_REQUIRE_ENCRYPTED_BACKUP",
                "TELEFLOW_RECOVERY_REQUIRE_TRUSTED_SIGNATURE",
                "TELEFLOW_RECOVERY_AGE_RECIPIENT",
            ):
                if key not in api_environment:
                    errors.append(f"docker-compose.yml: missing app environment {key}")

        if filename == "docker-compose.prod.yml":
            for service in ("migrate", "api", "worker"):
                environment = (data.get("services") or {}).get(service, {}).get("environment") or {}
                for key in (
                    "TELEFLOW_PILOT_READINESS_REQUIRED",
                    "TELEFLOW_PILOT_STAGE_ENFORCEMENT_REQUIRED",
                ):
                    if str(environment.get(key, "")).lower() != "true":
                        errors.append(f"docker-compose.prod.yml: {service} must set {key}=true")
                if environment.get("TELEFLOW_ARTIFACT_SIGNATURE_POLICY") != "require_trusted":
                    errors.append(
                        "docker-compose.prod.yml: "
                        f"{service} must set TELEFLOW_ARTIFACT_SIGNATURE_POLICY=require_trusted"
                    )
                if (
                    str(environment.get("TELEFLOW_REQUIRE_TRUSTED_RELEASE_ATTESTATION", "")).lower()
                    != "true"
                ):
                    errors.append(
                        f"docker-compose.prod.yml: {service} must set TELEFLOW_REQUIRE_TRUSTED_RELEASE_ATTESTATION=true"
                    )
                for key in (
                    "TELEFLOW_REQUIRE_RELEASE_TRANSPARENCY",
                    "TELEFLOW_REQUIRE_DEPENDENCY_ASSESSMENT",
                    "TELEFLOW_DEPENDENCY_REQUIRE_VULNERABILITY_SCAN",
                    "TELEFLOW_DEPENDENCY_REQUIRE_TRUSTED_REPORT",
                    "TELEFLOW_SLO_GATE_REQUIRED",
                    "TELEFLOW_CAPACITY_ASSURANCE_REQUIRED",
                    "TELEFLOW_EXECUTION_FENCING_REQUIRED",
                    "TELEFLOW_EXECUTION_REQUIRE_DISTINCT_FAILOVER_APPROVER",
                    "TELEFLOW_CONTINUITY_ASSURANCE_REQUIRED",
                    "TELEFLOW_CONTINUITY_REQUIRE_DISTINCT_SIGNOFF",
                    "TELEFLOW_CONTINUITY_DEFAULT_REQUIRE_LIVE_DRILL",
                ):
                    if str(environment.get(key, "")).lower() != "true":
                        errors.append(f"docker-compose.prod.yml: {service} must set {key}=true")
                for key in (
                    "TELEFLOW_RECOVERY_REQUIRE_ENCRYPTED_BACKUP",
                    "TELEFLOW_RECOVERY_REQUIRE_TRUSTED_SIGNATURE",
                ):
                    if str(environment.get(key, "")).lower() != "true":
                        errors.append(f"docker-compose.prod.yml: {service} must set {key}=true")
                if not str(environment.get("TELEFLOW_RECOVERY_AGE_RECIPIENT", "")).strip():
                    errors.append(
                        f"docker-compose.prod.yml: {service} must require TELEFLOW_RECOVERY_AGE_RECIPIENT"
                    )
                for key in ("TELEFLOW_EXECUTION_SITE_KEY", "TELEFLOW_EXECUTION_PRIMARY_SITE_KEY"):
                    if not str(environment.get(key, "")).strip():
                        errors.append(f"docker-compose.prod.yml: {service} must set {key}")
    results["compose"] = compose_results


def _validate_json(files: list[Path], errors: list[str], results: dict[str, Any]) -> None:
    """Реализовать внутренний этап validate json step. Вспомогательная функция сохраняет
    детерминированность и тестируемость процесса.
    """
    checked = 0
    for relative in files:
        if relative.suffix.lower() != ".json":
            continue
        try:
            json.loads((ROOT / relative).read_text(encoding="utf-8"))
            checked += 1
        except Exception as exc:  # noqa: BLE001 - aggregate release diagnostics
            errors.append(f"invalid JSON {relative}: {exc}")
    results["json_files"] = checked


def _normalize_link(raw: str) -> str:
    """Реализовать внутренний этап normalize link step. Вспомогательная функция сохраняет
    детерминированность и тестируемость процесса.
    """
    value = raw.strip()
    if value.startswith("<") and value.endswith(">"):
        value = value[1:-1]
    # Markdown may append an optional quoted title after the path.
    if ' "' in value:
        value = value.split(' "', 1)[0]
    return unquote(value.split("#", 1)[0])


def _validate_markdown_links(files: list[Path], errors: list[str], results: dict[str, Any]) -> None:
    """Реализовать внутренний этап validate markdown links step. Вспомогательная функция сохраняет
    детерминированность и тестируемость процесса.
    """
    checked = 0
    for relative in files:
        if relative.suffix.lower() != ".md":
            continue
        text = (ROOT / relative).read_text(encoding="utf-8")
        for match in MARKDOWN_LINK_RE.finditer(text):
            target = _normalize_link(match.group(1))
            if not target or target.startswith(("http://", "https://", "mailto:", "#")):
                continue
            # Ignore URI schemes and template placeholders rather than treating
            # them as local files.
            if ":" in target.split("/", 1)[0] or target.startswith("<"):
                continue
            checked += 1
            resolved = ((ROOT / relative).parent / target).resolve()
            try:
                resolved.relative_to(ROOT.resolve())
            except ValueError:
                errors.append(f"markdown link escapes project: {relative} -> {target}")
                continue
            if not resolved.exists():
                errors.append(f"broken markdown link: {relative} -> {target}")
    results["local_markdown_links"] = checked


def _validate_forbidden_files(
    files: list[Path], errors: list[str], results: dict[str, Any]
) -> None:
    """Реализовать внутренний этап validate forbidden files step. Вспомогательная функция сохраняет
    детерминированность и тестируемость процесса.
    """
    checked = 0
    for relative in files:
        checked += 1
        if relative in ALLOWED_SECRET_EXAMPLES:
            continue
        if any(part in FORBIDDEN_NAMES for part in relative.parts):
            errors.append(f"forbidden release path: {relative}")
            continue
        lower_name = relative.name.lower()
        if lower_name.startswith(".env.") and not lower_name.endswith(".example"):
            errors.append(f"forbidden environment file: {relative}")
            continue
        if relative.suffix.lower() in FORBIDDEN_SUFFIXES:
            errors.append(f"forbidden release suffix: {relative}")
    results["files_scanned"] = checked


def _validate_artifact_trust(errors: list[str], results: dict[str, Any]) -> None:
    """Реализовать внутренний этап validate artifact trust step. Вспомогательная функция сохраняет
    детерминированность и тестируемость процесса.
    """
    required = [
        Path("docs/ARTIFACT_TRUST.md"),
        Path("docs/RELEASE_NOTES_1.7.md"),
        Path("scripts/verify_artifact.py"),
        Path("app/services/artifact_signing.py"),
        Path("app/services/artifact_verifier.py"),
        Path("migrations/versions/3b7d9f1a2c4e_v1_7_artifact_trust.py"),
    ]
    existence = {str(path): (ROOT / path).is_file() for path in required}
    for path, exists in existence.items():
        if not exists:
            errors.append(f"artifact trust release file missing: {path}")

    static_app = (ROOT / "app/static/app.js").read_text(encoding="utf-8")
    ui_checks = {
        "route": 'artifactTrust: ["Подписи и доверие"' in static_app,
        "title": "Подписи и доверие" in static_app,
        "no_private_ciphertext_field": "private_key_enc" not in static_app,
    }
    for name, ok in ui_checks.items():
        if not ok:
            errors.append(f"artifact trust UI check failed: {name}")

    config = (ROOT / "app/config.py").read_text(encoding="utf-8")
    production_env = (ROOT / "deploy/.env.production.example").read_text(encoding="utf-8")
    policy_checks = {
        "settings_policy": "artifact_signature_policy" in config,
        "production_require_trusted": (
            "TELEFLOW_ARTIFACT_SIGNATURE_POLICY=require_trusted" in production_env
        ),
    }
    for name, ok in policy_checks.items():
        if not ok:
            errors.append(f"artifact trust policy check failed: {name}")

    results["artifact_trust"] = {
        "files": existence,
        "ui": ui_checks,
        "policy": policy_checks,
    }


def _validate_release_trust(errors: list[str], results: dict[str, Any]) -> None:
    """Реализовать внутренний этап validate release trust step. Вспомогательная функция сохраняет
    детерминированность и тестируемость процесса.
    """
    required = [
        Path("docs/RELEASE_TRUST.md"),
        Path("docs/RELEASE_NOTES_2.0.md"),
        Path("app/services/release_trust.py"),
        Path("app/api/release_trust.py"),
        Path("migrations/versions/6e0a2c4f8b1d_v2_0_release_trust.py"),
    ]
    existence = {str(path): (ROOT / path).is_file() for path in required}
    for path, exists in existence.items():
        if not exists:
            errors.append(f"release trust file missing: {path}")
    static_app = (ROOT / "app/static/app.js").read_text(encoding="utf-8")
    ui = {
        "route": 'releases: ["Доверие к релизам"' in static_app,
        "nav": '"releases", "◉", "Доверие к релизам"' in static_app,
        "private_material_absent": "private_key_enc" not in static_app,
    }
    for name, ok in ui.items():
        if not ok:
            errors.append(f"release trust UI check failed: {name}")
    results["release_trust"] = {"files": existence, "ui": ui}


def _validate_recovery_assurance(errors: list[str], results: dict[str, Any]) -> None:
    """Реализовать внутренний этап validate recovery assurance step. Вспомогательная функция
    сохраняет детерминированность и тестируемость процесса.
    """
    required = [
        Path("docs/RECOVERY_ASSURANCE.md"),
        Path("docs/RELEASE_NOTES_1.8.md"),
        Path("scripts/recovery_backup.py"),
        Path("scripts/recovery_verify.py"),
        Path("scripts/recovery_restore_drill.py"),
        Path("app/services/recovery.py"),
        Path("app/api/recovery.py"),
        Path("migrations/versions/4c8e0a2b6d1f_v1_8_recovery_assurance.py"),
        Path("tests/test_recovery_assurance.py"),
    ]
    existence = {str(path): (ROOT / path).is_file() for path in required}
    for path, exists in existence.items():
        if not exists:
            errors.append(f"recovery assurance release file missing: {path}")

    static_app = (ROOT / "app/static/app.js").read_text(encoding="utf-8")
    service = (ROOT / "app/services/recovery.py").read_text(encoding="utf-8")
    config = (ROOT / "app/config.py").read_text(encoding="utf-8")
    production_env = (ROOT / "deploy/.env.production.example").read_text(encoding="utf-8")
    checks = {
        "ui_route": 'recovery: ["Восстановление"' in static_app,
        "ui_page": "async recovery()" in static_app,
        "registered_vs_verified": "RecoveryBackupStatus.REGISTERED" in service,
        "safe_zip_paths": "_safe_zip_members" in service,
        "manifest_signature": "BACKUP_MANIFEST_PURPOSE" in service,
        "production_config_validation": "recovery_require_encrypted_backup" in config,
        "production_encryption_required": "TELEFLOW_RECOVERY_REQUIRE_ENCRYPTED_BACKUP=true"
        in production_env,
        "production_trusted_signature_required": "TELEFLOW_RECOVERY_REQUIRE_TRUSTED_SIGNATURE=true"
        in production_env,
        "production_age_recipient": "TELEFLOW_RECOVERY_AGE_RECIPIENT=" in production_env,
    }
    for name, ok in checks.items():
        if not ok:
            errors.append(f"recovery assurance check failed: {name}")
    results["recovery_assurance"] = {"files": existence, "checks": checks}


def _validate_change_management(errors: list[str], results: dict[str, Any]) -> None:
    """Реализовать внутренний этап validate change management step. Вспомогательная функция
    сохраняет детерминированность и тестируемость процесса.
    """
    required = [
        Path("docs/CHANGE_MANAGEMENT.md"),
        Path("docs/RELEASE_NOTES_1.9.md"),
        Path("app/services/change_management.py"),
        Path("app/api/changes.py"),
        Path("migrations/versions/5d9f1b3c7e2a_v1_9_change_management.py"),
        Path("tests/test_change_management.py"),
    ]
    existence = {str(path): (ROOT / path).is_file() for path in required}
    for path, exists in existence.items():
        if not exists:
            errors.append(f"change management release file missing: {path}")
    static_app = (ROOT / "app/static/app.js").read_text(encoding="utf-8")
    scheduler = (ROOT / "app/services/scheduler.py").read_text(encoding="utf-8")
    safety = (ROOT / "app/services/safety.py").read_text(encoding="utf-8")
    checks = {
        "ui_route": 'changes: ["Изменения"' in static_app,
        "ui_page": "async changes()" in static_app,
        "maintenance_scheduler_gate": "Organization.maintenance_mode.is_(False)" in scheduler,
        "maintenance_safety_gate": "ORG_MAINTENANCE_MODE" in safety,
        "independent_approval": "Автор изменения не может сам его утвердить"
        in (ROOT / "app/api/changes.py").read_text(encoding="utf-8"),
    }
    for name, ok in checks.items():
        if not ok:
            errors.append(f"change management check failed: {name}")
    results["change_management"] = {"files": existence, "checks": checks}


def _validate_supply_chain_assurance(errors: list[str], results: dict[str, Any]) -> None:
    """Реализовать внутренний этап validate supply chain assurance step. Вспомогательная функция
    сохраняет детерминированность и тестируемость процесса.
    """
    required = [
        Path("docs/SUPPLY_CHAIN_ASSURANCE.md"),
        Path("docs/RELEASE_NOTES_2.1.md"),
        Path("app/services/supply_chain.py"),
        Path("app/api/supply_chain.py"),
        Path("migrations/versions/7f1b3d5e9a2c_v2_1_supply_chain_assurance.py"),
        Path("tests/test_supply_chain.py"),
        Path("scripts/run_pytest_coverage.py"),
    ]
    existence = {str(path): (ROOT / path).is_file() for path in required}
    for path, exists in existence.items():
        if not exists:
            errors.append(f"supply-chain assurance release file missing: {path}")

    static_app = (ROOT / "app/static/app.js").read_text(encoding="utf-8")
    service = (ROOT / "app/services/supply_chain.py").read_text(encoding="utf-8")
    config = (ROOT / "app/config.py").read_text(encoding="utf-8")
    production_env = (ROOT / "deploy/.env.production.example").read_text(encoding="utf-8")
    checks = {
        "ui_route": 'supplyChain: ["Поставка и зависимости"' in static_app,
        "ui_page": "async supplyChain()" in static_app,
        "transparency_hash_chain": "TRANSPARENCY_CHAIN_DOMAIN" in service,
        "signed_transparency_entries": "TRANSPARENCY_ENTRY_PURPOSE" in service,
        "cyclonedx_sbom": '"bomFormat": "CycloneDX"' in service,
        "immutable_assessment": "policy_sha256" in service and "report_sha256" in service,
        "change_binding": "validate_change_dependency_assurance" in service,
        "production_runtime_gate": "require_dependency_assessment" in config,
        "production_transparency_required": "TELEFLOW_REQUIRE_RELEASE_TRANSPARENCY=true"
        in production_env,
        "production_scan_required": "TELEFLOW_DEPENDENCY_REQUIRE_VULNERABILITY_SCAN=true"
        in production_env,
        "production_trusted_report_required": "TELEFLOW_DEPENDENCY_REQUIRE_TRUSTED_REPORT=true"
        in production_env,
    }
    for name, ok in checks.items():
        if not ok:
            errors.append(f"supply-chain assurance check failed: {name}")
    results["supply_chain_assurance"] = {"files": existence, "checks": checks}


def _validate_operational_slo(errors: list[str], results: dict[str, Any]) -> None:
    """Реализовать внутренний этап validate operational slo step. Вспомогательная функция сохраняет
    детерминированность и тестируемость процесса.
    """
    required = [
        Path("docs/OPERATIONAL_SLO.md"),
        Path("docs/RELEASE_NOTES_2.2.md"),
        Path("app/services/operations.py"),
        Path("app/api/operations.py"),
        Path("migrations/versions/8a2c4e6f0b3d_v2_2_operational_slo_incidents.py"),
        Path("tests/test_operations_slo.py"),
    ]
    existence = {str(path): (ROOT / path).is_file() for path in required}
    for path, exists in existence.items():
        if not exists:
            errors.append(f"operational SLO release file missing: {path}")

    static_app = (ROOT / "app/static/app.js").read_text(encoding="utf-8")
    service = (ROOT / "app/services/operations.py").read_text(encoding="utf-8")
    safety = (ROOT / "app/services/safety.py").read_text(encoding="utf-8")
    scheduler = (ROOT / "app/services/scheduler.py").read_text(encoding="utf-8")
    config = (ROOT / "app/config.py").read_text(encoding="utf-8")
    production_env = (ROOT / "deploy/.env.production.example").read_text(encoding="utf-8")
    alerts = (ROOT / "deploy/prometheus/alerts.yml").read_text(encoding="utf-8")
    checks = {
        "ui_route": 'operations: ["Надёжность и инциденты"' in static_app,
        "ui_page": "async operations()" in static_app,
        "error_budget": "error_budget_consumed_bps" in service,
        "immutable_fingerprint": "fingerprint=_sha256_json" in service,
        "incident_lifecycle": "transition_incident" in service,
        "publishing_gate": "SLO_GATE_BLOCKED" in safety,
        "scheduler_gate": "slo_gate_decision" in scheduler,
        "production_runtime_gate": "slo_gate_required" in config,
        "production_env_gate": "TELEFLOW_SLO_GATE_REQUIRED=true" in production_env,
        "prometheus_alert": "TeleFlowSLOBlocked" in alerts,
    }
    for name, ok in checks.items():
        if not ok:
            errors.append(f"operational SLO check failed: {name}")
    results["operational_slo"] = {"files": existence, "checks": checks}


def _validate_execution_fencing(errors: list[str], results: dict[str, Any]) -> None:
    """Реализовать внутренний этап validate execution fencing step. Вспомогательная функция
    сохраняет детерминированность и тестируемость процесса.
    """
    required = [
        Path("docs/EXECUTION_FENCING.md"),
        Path("docs/RELEASE_NOTES_2.3.md"),
        Path("app/services/execution.py"),
        Path("app/api/execution.py"),
        Path("migrations/versions/9b3d5f7a1c4e_v2_3_execution_fencing_failover.py"),
        Path("tests/test_execution_fencing.py"),
    ]
    existence = {str(path): (ROOT / path).is_file() for path in required}
    for path, exists in existence.items():
        if not exists:
            errors.append(f"execution fencing release file missing: {path}")

    static_app = (ROOT / "app/static/app.js").read_text(encoding="utf-8")
    execution = (ROOT / "app/services/execution.py").read_text(encoding="utf-8")
    delivery = (ROOT / "app/services/delivery.py").read_text(encoding="utf-8")
    safety = (ROOT / "app/services/safety.py").read_text(encoding="utf-8")
    scheduler = (ROOT / "app/services/scheduler.py").read_text(encoding="utf-8")
    config = (ROOT / "app/config.py").read_text(encoding="utf-8")
    production_env = (ROOT / "deploy/.env.production.example").read_text(encoding="utf-8")
    alerts = (ROOT / "deploy/prometheus/alerts.yml").read_text(encoding="utf-8")
    checks = {
        "ui_route": 'execution: ["Active / Standby"' in static_app,
        "ui_page": "async execution()" in static_app,
        "epoch_fence": "lock_delivery_fence" in execution,
        "controlled_failover": "approve_failover" in execution,
        "durable_network_marker": "_mark_attempt_network_started_durable" in delivery,
        "network_started_status": "DeliveryAttemptStatus.NETWORK_STARTED" in delivery,
        "crash_uncertainty": "WORKER_CRASH_DURING_SEND" in delivery,
        "safety_gate": "execution_gate_decision" in safety,
        "scheduler_gate": "scheduler_site_allowed" in scheduler,
        "production_runtime_gate": "execution_fencing_required" in config,
        "production_env_gate": "TELEFLOW_EXECUTION_FENCING_REQUIRED=true" in production_env,
        "distinct_approver": "TELEFLOW_EXECUTION_REQUIRE_DISTINCT_FAILOVER_APPROVER=true"
        in production_env,
        "prometheus_alert": "TeleFlowExecutionLeaseStale" in alerts,
    }
    for name, ok in checks.items():
        if not ok:
            errors.append(f"execution fencing check failed: {name}")
    results["execution_fencing"] = {"files": existence, "checks": checks}


def _validate_continuity_assurance(errors: list[str], results: dict[str, Any]) -> None:
    """Проверить the complete Continuity Assurance 2.4 release surface."""

    required = [
        Path("docs/CONTINUITY_ASSURANCE.md"),
        Path("docs/RELEASE_NOTES_2.4.md"),
        Path("app/services/continuity.py"),
        Path("app/services/runtime_evidence.py"),
        Path("app/api/continuity.py"),
        Path("migrations/versions/ac4e6f8b2d5f_v2_4_continuity_drills.py"),
        Path("tests/test_continuity_assurance.py"),
        Path("tests/test_function_documentation.py"),
        Path("scripts/check_function_docs.py"),
    ]
    existence = {str(path): (ROOT / path).is_file() for path in required}
    for path, exists in existence.items():
        if not exists:
            errors.append(f"continuity assurance release file missing: {path}")

    static_app = (ROOT / "app/static/app.js").read_text(encoding="utf-8")
    service = (ROOT / "app/services/continuity.py").read_text(encoding="utf-8")
    execution = (ROOT / "app/services/execution.py").read_text(encoding="utf-8")
    worker = (ROOT / "app/worker.py").read_text(encoding="utf-8")
    commissioning = (ROOT / "app/services/commissioning.py").read_text(encoding="utf-8")
    config = (ROOT / "app/config.py").read_text(encoding="utf-8")
    production_env = (ROOT / "deploy/.env.production.example").read_text(encoding="utf-8")
    alerts = (ROOT / "deploy/prometheus/alerts.yml").read_text(encoding="utf-8")
    checks = {
        "ui_route": 'continuity: ["Непрерывность"' in static_app,
        "ui_page": "async continuity()" in static_app,
        "event_hash_chain": "verify_event_chain" in service and "previous_hash" in service,
        "simulation_no_network": '"network_calls": 0' in service,
        "live_failback": "request_failback" in service,
        "independent_signoff": "require_distinct_signoff" in service,
        "runtime_compatibility": "compare_site_runtime" in service
        and "runtime_fingerprint" in execution,
        "worker_projection": "synchronize_open_drills" in worker,
        "commissioning_gate": '"continuity_assurance"' in commissioning,
        "production_runtime_gate": "continuity_assurance_required" in config,
        "production_env_gate": "TELEFLOW_CONTINUITY_ASSURANCE_REQUIRED=true" in production_env,
        "production_signoff_gate": "TELEFLOW_CONTINUITY_REQUIRE_DISTINCT_SIGNOFF=true"
        in production_env,
        "prometheus_alert": "TeleFlowContinuityEvidenceStale" in alerts,
    }
    for name, ok in checks.items():
        if not ok:
            errors.append(f"continuity assurance check failed: {name}")
    results["continuity_assurance"] = {"files": existence, "checks": checks}


def _validate_capacity_assurance(errors: list[str], results: dict[str, Any]) -> None:
    """Проверить the complete Capacity & Backpressure Assurance 2.5 release surface."""

    required = [
        Path("docs/CAPACITY_ASSURANCE.md"),
        Path("docs/RELEASE_NOTES_2.5.md"),
        Path("app/services/capacity.py"),
        Path("app/api/capacity.py"),
        Path("migrations/versions/bd5f7a9c3e6f_v2_5_capacity_backpressure.py"),
        Path("tests/test_capacity_backpressure.py"),
    ]
    existence = {str(path): (ROOT / path).is_file() for path in required}
    for path, exists in existence.items():
        if not exists:
            errors.append(f"capacity assurance release file missing: {path}")

    static_app = (ROOT / "app/static/app.js").read_text(encoding="utf-8")
    service = (ROOT / "app/services/capacity.py").read_text(encoding="utf-8")
    scheduler = (ROOT / "app/services/scheduler.py").read_text(encoding="utf-8")
    safety = (ROOT / "app/services/safety.py").read_text(encoding="utf-8")
    delivery = (ROOT / "app/services/delivery.py").read_text(encoding="utf-8")
    rollouts = (ROOT / "app/services/rollouts.py").read_text(encoding="utf-8")
    jobs_api = (ROOT / "app/api/jobs.py").read_text(encoding="utf-8")
    worker = (ROOT / "app/worker.py").read_text(encoding="utf-8")
    runtime_evidence = (ROOT / "app/services/runtime_evidence.py").read_text(encoding="utf-8")
    config = (ROOT / "app/config.py").read_text(encoding="utf-8")
    production_env = (ROOT / "deploy/.env.production.example").read_text(encoding="utf-8")
    alerts = (ROOT / "deploy/prometheus/alerts.yml").read_text(encoding="utf-8")
    dashboard = (ROOT / "deploy/grafana/dashboards/teleflow-overview.json").read_text(
        encoding="utf-8"
    )
    checks = {
        "ui_route": 'capacity: ["Нагрузка и лимиты"' in static_app,
        "ui_page": "async capacity()" in static_app,
        "immutable_assessment": "CapacityAssessment" in service and "policy_sha256" in service,
        "admission_control": "capacity_admission_decision" in scheduler,
        "ready_release_control": "capacity_ready_release_decision" in rollouts,
        "safety_precheck": "capacity_dispatch_decision" in safety,
        "durable_reservation": "reservation_attempt_id" in delivery and "ABANDONED" in delivery,
        "manual_retry_gate": "capacity_admission_decision" in jobs_api,
        "worker_evaluation": "evaluate_due_capacity_policies" in worker,
        "runtime_fencing": "capacity_assurance_required" in runtime_evidence
        and "capacity_default_max_ready_jobs" in runtime_evidence,
        "runtime_release_head": 'EXPECTED_ALEMBIC_REVISION = "bd5f7a9c3e6f"' in runtime_evidence,
        "production_runtime_gate": "capacity_assurance_required" in config,
        "production_env_gate": "TELEFLOW_CAPACITY_ASSURANCE_REQUIRED=true" in production_env,
        "prometheus_alert": "TeleFlowCapacityBackpressureActive" in alerts,
        "grafana_panel": "teleflow_capacity_backpressure_organizations" in dashboard,
    }
    for name, ok in checks.items():
        if not ok:
            errors.append(f"capacity assurance check failed: {name}")
    results["capacity_assurance"] = {"files": existence, "checks": checks}


def _validate_function_documentation(errors: list[str], results: dict[str, Any]) -> None:
    """Запустить the source documentation gate used by release QA."""

    completed = subprocess.run(
        ["python", "scripts/check_function_docs.py"],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    ok = completed.returncode == 0
    if not ok:
        errors.append(
            "function documentation gate failed: " + (completed.stdout + completed.stderr).strip()
        )
    results["function_documentation"] = {
        "ok": ok,
        "summary": completed.stdout.strip(),
    }


def run() -> tuple[dict[str, Any], bool]:
    """Выполнить операцию run. Аргументы интерпретируются в контексте модуля, результат
    возвращается вызывающему коду.
    """
    errors: list[str] = []
    results: dict[str, Any] = {}
    files = _candidate_files()
    _validate_version(errors, results)
    _validate_compose(errors, results)
    _validate_json(files, errors, results)
    _validate_markdown_links(files, errors, results)
    _validate_forbidden_files(files, errors, results)
    _validate_artifact_trust(errors, results)
    _validate_release_trust(errors, results)
    _validate_recovery_assurance(errors, results)
    _validate_change_management(errors, results)
    _validate_supply_chain_assurance(errors, results)
    _validate_operational_slo(errors, results)
    _validate_execution_fencing(errors, results)
    _validate_continuity_assurance(errors, results)
    _validate_capacity_assurance(errors, results)
    _validate_function_documentation(errors, results)
    results["errors"] = errors
    results["ok"] = not errors
    return results, not errors


def main() -> None:
    """Запустить the validate release assets command-line workflow. Arguments, exit status and
    user- visible diagnostics are handled here.
    """
    parser = argparse.ArgumentParser(description="Validate TeleFlow release assets")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    results, ok = run()
    if args.json:
        print(json.dumps(results, ensure_ascii=False, indent=2))
    else:
        for key, value in results.items():
            if key not in {"errors", "ok"}:
                print(f"{key}: {value}")
        if results["errors"]:
            print("Errors:")
            for error in results["errors"]:
                print(f"- {error}")
        print("RELEASE ASSETS OK" if ok else "RELEASE ASSETS FAILED")
    raise SystemExit(0 if ok else 1)


if __name__ == "__main__":
    main()
