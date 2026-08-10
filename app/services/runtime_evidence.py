"""Runtime compatibility evidence used by execution fencing and continuity drills.

Only non-secret safety settings are fingerprinted.  Credentials, encryption
keys and personal data are never included in the runtime payload.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import ExecutionSite

EXPECTED_ALEMBIC_REVISION = "bd5f7a9c3e6f"


@dataclass(slots=True)
class RuntimeCompatibility:
    """Result of comparing the safety-relevant state of two execution sites."""

    allowed: bool
    blockers: list[str]
    warnings: list[str]
    source: dict[str, Any]
    target: dict[str, Any]


def canonical_sha256(payload: Any) -> str:
    """Хешировать JSON-compatible data with stable key ordering and separators."""

    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def critical_config_payload(settings: Settings) -> dict[str, Any]:
    """Вернуть safety controls that must match on active and standby sites. Secret-bearing fields
    are deliberately absent. A change to any value that can alter publishing, approval,
    recovery, fencing or continuity behavior changes the resulting fingerprint and blocks
    failover until both sites are deployed with the same configuration.
    """

    return {
        "environment": settings.environment,
        "telegram_fake_mode": settings.telegram_fake_mode,
        "execution_fencing_required": settings.execution_fencing_required,
        "execution_primary_site_key": settings.execution_primary_site_key,
        "execution_lease_ttl_seconds": settings.execution_lease_ttl_seconds,
        "execution_site_heartbeat_ttl_seconds": settings.execution_site_heartbeat_ttl_seconds,
        "execution_require_distinct_failover_approver": settings.execution_require_distinct_failover_approver,
        "continuity_assurance_required": settings.continuity_assurance_required,
        "continuity_require_distinct_signoff": settings.continuity_require_distinct_signoff,
        "continuity_default_require_live_drill": settings.continuity_default_require_live_drill,
        "continuity_default_max_rto_seconds": settings.continuity_default_max_rto_seconds,
        "continuity_default_evidence_valid_days": settings.continuity_default_evidence_valid_days,
        "pilot_readiness_required": settings.pilot_readiness_required,
        "pilot_stage_enforcement_required": settings.pilot_stage_enforcement_required,
        "require_trusted_release_attestation": settings.require_trusted_release_attestation,
        "require_release_transparency": settings.require_release_transparency,
        "require_dependency_assessment": settings.require_dependency_assessment,
        "slo_gate_required": settings.slo_gate_required,
        "capacity_assurance_required": settings.capacity_assurance_required,
        "capacity_auto_evaluate_minutes": settings.capacity_auto_evaluate_minutes,
        "capacity_assessment_ttl_minutes": settings.capacity_assessment_ttl_minutes,
        "capacity_default_max_active_jobs": settings.capacity_default_max_active_jobs,
        "capacity_default_max_ready_jobs": settings.capacity_default_max_ready_jobs,
        "capacity_default_max_processing_jobs": settings.capacity_default_max_processing_jobs,
        "capacity_default_max_active_runs": settings.capacity_default_max_active_runs,
        "capacity_default_max_jobs_per_run": settings.capacity_default_max_jobs_per_run,
        "capacity_default_max_network_starts_per_minute": settings.capacity_default_max_network_starts_per_minute,
        "capacity_default_max_network_starts_per_hour": settings.capacity_default_max_network_starts_per_hour,
        "capacity_default_max_estimated_drain_seconds": settings.capacity_default_max_estimated_drain_seconds,
        "capacity_default_warning_utilization_percent": settings.capacity_default_warning_utilization_percent,
        "capacity_default_admission_block_utilization_percent": settings.capacity_default_admission_block_utilization_percent,
        "capacity_retry_delay_seconds": settings.capacity_retry_delay_seconds,
        "artifact_signature_policy": settings.artifact_signature_policy,
        "recovery_require_encrypted_backup": settings.recovery_require_encrypted_backup,
        "recovery_require_trusted_signature": settings.recovery_require_trusted_signature,
        "global_hard_daily_cap": settings.global_hard_daily_cap,
        "user_hard_min_interval_seconds": settings.user_hard_min_interval_seconds,
        "bot_hard_min_interval_seconds": settings.bot_hard_min_interval_seconds,
    }


def current_database_revision(db: Session) -> str | None:
    """Прочитать the current Alembic revision without modifying the database."""

    try:
        return db.execute(text("SELECT version_num FROM alembic_version")).scalar_one_or_none()
    except Exception:
        # A fresh local development database may be auto-created without an
        # Alembic table.  Such a site is explicitly marked schema_current=false.
        return None


def release_payload_sha256(root: Path | None = None) -> str | None:
    """Хешировать BUILD_INFO.json so both sites prove they run the same release payload."""

    base = root or Path(__file__).resolve().parents[2]
    path = base / "BUILD_INFO.json"
    if not path.is_file():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


def collect_runtime_evidence(
    db: Session,
    settings: Settings,
    *,
    checked_at: datetime | None = None,
) -> dict[str, Any]:
    """Собрать one canonical, non-secret runtime evidence snapshot."""

    current = checked_at or datetime.now(UTC)
    revision = current_database_revision(db)
    if revision is None and settings.effective_auto_create_schema and not settings.is_production:
        # Local/test installations may create the current ORM schema directly.
        # Production never uses this shortcut and must expose a real Alembic row.
        revision = EXPECTED_ALEMBIC_REVISION
    payload = {
        "version": settings.version,
        "environment": settings.environment,
        "current_revision": revision,
        "expected_revision": EXPECTED_ALEMBIC_REVISION,
        "schema_current": revision == EXPECTED_ALEMBIC_REVISION,
        "critical_config_sha256": canonical_sha256(critical_config_payload(settings)),
        "release_payload_sha256": release_payload_sha256(),
        "runtime_checked_at": current.isoformat(),
    }
    payload["runtime_fingerprint"] = canonical_sha256(
        {key: value for key, value in payload.items() if key != "runtime_checked_at"}
    )
    return payload


def site_runtime_payload(site: ExecutionSite | None) -> dict[str, Any] | None:
    """Сериализовать runtime evidence from an execution site without operator metadata."""

    if site is None:
        return None
    return {
        "site_key": site.site_key,
        "version": site.version,
        "environment": site.environment,
        "current_revision": site.current_revision,
        "expected_revision": site.expected_revision,
        "schema_current": site.schema_current,
        "critical_config_sha256": site.critical_config_sha256,
        "release_payload_sha256": site.release_payload_sha256,
        "runtime_fingerprint": site.runtime_fingerprint,
        "runtime_checked_at": site.runtime_checked_at.isoformat()
        if site.runtime_checked_at
        else None,
    }


def compare_site_runtime(source: ExecutionSite, target: ExecutionSite) -> RuntimeCompatibility:
    """Сравнить two site snapshots and list every failover blocker explicitly."""

    source_payload = site_runtime_payload(source) or {}
    target_payload = site_runtime_payload(target) or {}
    blockers: list[str] = []
    warnings: list[str] = []
    required_fields = {
        "version": "версия приложения",
        "environment": "environment",
        "current_revision": "текущая Alembic-ревизия",
        "expected_revision": "ожидаемая Alembic-ревизия",
        "critical_config_sha256": "критическая конфигурация",
        "release_payload_sha256": "release payload",
        "runtime_fingerprint": "runtime fingerprint",
    }
    for field, label in required_fields.items():
        source_value = source_payload.get(field)
        target_value = target_payload.get(field)
        if not source_value or not target_value:
            blockers.append(f"Не заполнено runtime evidence: {label}")
        elif source_value != target_value:
            blockers.append(f"Не совпадает {label} площадок")
    if source.schema_current is not True:
        blockers.append("Схема БД исходной площадки не соответствует release head")
    if target.schema_current is not True:
        blockers.append("Схема БД целевой площадки не соответствует release head")
    return RuntimeCompatibility(
        allowed=not blockers,
        blockers=blockers,
        warnings=warnings,
        source=source_payload,
        target=target_payload,
    )
