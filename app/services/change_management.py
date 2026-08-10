from __future__ import annotations

import hashlib
import json
from datetime import timedelta
from pathlib import Path
from typing import Any

from alembic.config import Config as AlembicConfig
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.audit import verify_audit_chain
from app.config import Settings
from app.enums import (
    ChangeRequestType,
    DeploymentVerificationPhase,
    JobStatus,
    ReadinessStatus,
)
from app.models import (
    ChangeRequest,
    DeliveryJob,
    DeploymentVerificationReport,
    Organization,
    User,
    WorkerHeartbeat,
    utcnow,
)
from app.security import aware_utc
from app.services.operations import slo_gate_decision
from app.services.release_trust import validate_change_release_attestation
from app.services.supply_chain import (
    validate_change_dependency_assurance,
    validate_change_release_transparency,
)


def change_fingerprint(change: ChangeRequest) -> str:
    """Вычислить change fingerprint. Канонический ввод обеспечивает детерминированное сравнение
    целостности между процессами.
    """
    payload = {
        "title": change.title,
        "change_type": change.change_type.value,
        "current_version": change.current_version,
        "target_version": change.target_version,
        "reason": change.reason,
        "risk_summary": change.risk_summary,
        "rollback_plan": change.rollback_plan,
        "planned_start_at": change.planned_start_at.isoformat()
        if change.planned_start_at
        else None,
        "planned_end_at": change.planned_end_at.isoformat() if change.planned_end_at else None,
        "release_attestation_id": change.release_attestation_id,
        "release_dependency_assessment_id": change.release_dependency_assessment_id,
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode()).hexdigest()


def _migration_state(db: Session) -> tuple[str | None, str | None]:
    """Реализовать внутренний этап migration state step. Вспомогательная функция сохраняет
    детерминированность и тестируемость процесса.
    """
    root = Path(__file__).resolve().parents[2]
    cfg = AlembicConfig(str(root / "alembic.ini"))
    cfg.set_main_option("script_location", str(root / "migrations"))
    head = ScriptDirectory.from_config(cfg).get_current_head()
    current = MigrationContext.configure(db.connection()).get_current_revision()
    return current, head


def run_deployment_verification(
    db: Session,
    *,
    change: ChangeRequest,
    phase: DeploymentVerificationPhase,
    user: User,
    settings: Settings,
) -> DeploymentVerificationReport:
    """Выполнить run deployment verification. Операция координирует ограниченные побочные эффекты и
    возвращает детерминированный результат.
    """
    now = utcnow()
    checks: list[dict[str, Any]] = []
    blockers = []
    warnings = []
    org = db.get(Organization, change.organization_id)
    current, head = _migration_state(db)

    def add(code, title, status, message, details=None):
        """Выполнить операцию add. Аргументы интерпретируются в контексте модуля, результат
        возвращается вызывающему коду.
        """
        checks.append(
            {
                "code": code,
                "title": title,
                "status": status,
                "message": message,
                "details": details or {},
            }
        )
        if status == "blocked":
            blockers.append(message)
        elif status == "warning":
            warnings.append(message)

    if current == head:
        migration_status = "passed"
        migration_message = "Схема соответствует Alembic head."
    elif current is None and settings.effective_auto_create_schema and not settings.is_production:
        migration_status = "warning"
        migration_message = (
            "Локальная схема создана автоматически; production требует Alembic upgrade."
        )
    else:
        migration_status = "blocked"
        migration_message = "Схема базы не соответствует Alembic head."
    add(
        "database_migrations",
        "Схема базы",
        migration_status,
        migration_message,
        {"current_revision": current, "head_revision": head},
    )
    if phase == DeploymentVerificationPhase.PRE_CHANGE:
        need_maintenance = change.change_type in {
            ChangeRequestType.UPGRADE,
            ChangeRequestType.DATABASE_MIGRATION,
            ChangeRequestType.INFRASTRUCTURE,
        }
        add(
            "maintenance_mode",
            "Режим обслуживания",
            "passed" if (org and org.maintenance_mode) or not need_maintenance else "blocked",
            "Режим обслуживания активен."
            if org and org.maintenance_mode
            else (
                "Для этого изменения требуется режим обслуживания."
                if need_maintenance
                else "Режим обслуживания не обязателен."
            ),
        )
        processing = int(
            db.scalar(
                select(func.count(DeliveryJob.id)).where(
                    DeliveryJob.organization_id == change.organization_id,
                    DeliveryJob.status == JobStatus.PROCESSING,
                )
            )
            or 0
        )
        add(
            "active_delivery",
            "Активные отправки",
            "passed" if processing == 0 else "blocked",
            "Активных сетевых отправок нет."
            if processing == 0
            else f"Обнаружено активных отправок: {processing}.",
            {"processing_jobs": processing},
        )
    expected = (
        change.target_version
        if phase == DeploymentVerificationPhase.POST_CHANGE
        else change.current_version
    )
    if expected:
        add(
            "application_version",
            "Версия приложения",
            "passed" if settings.version == expected else "blocked",
            f"Версия приложения: {settings.version}."
            if settings.version == expected
            else f"Ожидалась версия {expected}, обнаружена {settings.version}.",
            {"expected": expected, "observed": settings.version},
        )
    trusted_release, release_message = validate_change_release_attestation(
        db, change=change, settings=settings
    )
    add(
        "release_attestation",
        "Доверие к релизу",
        "passed" if trusted_release else "blocked",
        release_message,
        {"release_attestation_id": change.release_attestation_id},
    )
    transparent_release, transparency_message = validate_change_release_transparency(
        db, change=change, settings=settings
    )
    add(
        "release_transparency",
        "Transparency log",
        "passed" if transparent_release else "blocked",
        transparency_message,
        {"release_attestation_id": change.release_attestation_id},
    )
    dependency_ok, dependency_message, dependency_assessment_id = (
        validate_change_dependency_assurance(db, change=change, settings=settings)
    )
    add(
        "dependency_assurance",
        "Зависимости и уязвимости",
        "passed" if dependency_ok else "blocked",
        dependency_message,
        {"dependency_assessment_id": dependency_assessment_id},
    )
    slo_gate = slo_gate_decision(
        db,
        organization_id=change.organization_id,
        settings=settings,
        gate="changes",
        now=now,
    )
    add(
        "operational_slo",
        "Операционный SLO",
        "passed" if slo_gate.allowed else "blocked",
        slo_gate.message,
        {"slo_assessment_id": slo_gate.assessment.id if slo_gate.assessment else None},
    )
    audit = verify_audit_chain(db, organization_id=change.organization_id)
    add(
        "audit_chain",
        "Целостность аудита",
        "passed" if audit.valid else "blocked",
        "Hash-chain аудита подтверждена." if audit.valid else "Нарушена целостность audit-chain.",
    )
    hb = db.scalar(select(WorkerHeartbeat).order_by(WorkerHeartbeat.last_seen_at.desc()).limit(1))
    hb_time = aware_utc(hb.last_seen_at) if hb else None
    fresh = bool(
        hb_time and hb_time > now - timedelta(seconds=settings.worker_readiness_max_age_seconds)
    )
    if phase == DeploymentVerificationPhase.POST_CHANGE:
        add(
            "worker_heartbeat",
            "Worker",
            "passed" if fresh else ("blocked" if settings.is_production else "warning"),
            "Worker отвечает." if fresh else "Свежий heartbeat worker не найден.",
        )
    status = (
        ReadinessStatus.BLOCKED
        if blockers
        else ReadinessStatus.WARNING
        if warnings
        else ReadinessStatus.PASSED
    )
    payload = {
        "change_id": change.id,
        "phase": phase.value,
        "version": settings.version,
        "checks": checks,
    }
    fp = hashlib.sha256(
        json.dumps(
            payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str
        ).encode()
    ).hexdigest()
    report = DeploymentVerificationReport(
        organization_id=change.organization_id,
        change_request_id=change.id,
        phase=phase,
        status=status,
        expected_version=expected,
        observed_version=settings.version,
        checks=checks,
        blockers=blockers,
        warnings=warnings,
        fingerprint=fp,
        created_by_id=user.id,
        expires_at=now + timedelta(minutes=settings.change_verification_ttl_minutes),
    )
    db.add(report)
    db.flush()
    return report
