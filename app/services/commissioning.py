from __future__ import annotations

import hashlib
import json
import os
import shutil
import uuid
from datetime import timedelta
from pathlib import Path
from typing import Any

from alembic.config import Config as AlembicConfig
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.audit import verify_audit_chain
from app.config import Settings
from app.enums import (
    ArtifactSigningKeyStatus,
    ExecutionLeaseStatus,
    ReadinessStatus,
    UserRole,
)
from app.models import (
    ArtifactSigningKey,
    CommissioningCheckRun,
    ExecutionLease,
    ExecutionSite,
    SLOPolicy,
    User,
    WorkerHeartbeat,
    utcnow,
)
from app.security import aware_utc
from app.services.continuity import continuity_compliance
from app.services.locks import DistributedLockManager
from app.services.operations import assessment_is_current, latest_slo_assessment, slo_gate_decision
from app.services.recovery import evaluate_recovery_compliance
from app.services.storage import StorageService


class CommissioningError(RuntimeError):
    pass


def _check(
    code: str,
    title: str,
    status: str,
    message: str,
    *,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Реализовать внутренний этап check step. Вспомогательная функция сохраняет
    детерминированность и тестируемость процесса.
    """
    return {
        "code": code,
        "title": title,
        "status": status,
        "message": message,
        "details": details or {},
    }


def _migration_state(db: Session) -> tuple[str | None, str | None]:
    """Реализовать внутренний этап migration state step. Вспомогательная функция сохраняет
    детерминированность и тестируемость процесса.
    """
    project_root = Path(__file__).resolve().parents[2]
    config = AlembicConfig(str(project_root / "alembic.ini"))
    config.set_main_option("script_location", str(project_root / "migrations"))
    script = ScriptDirectory.from_config(config)
    head = script.get_current_head()
    current = MigrationContext.configure(db.connection()).get_current_revision()
    return current, head


def _latest_backup_age_hours(settings: Settings) -> float | None:
    """Реализовать внутренний этап latest backup age hours step. Вспомогательная функция сохраняет
    детерминированность и тестируемость процесса.
    """
    if settings.storage_backend != "local":
        return None
    root = settings.backups_path
    if not root.exists():
        return None
    files = [item for item in root.rglob("*") if item.is_file()]
    if not files:
        return None
    latest = max(item.stat().st_mtime for item in files)
    return max(0.0, (utcnow().timestamp() - latest) / 3600)


def _fingerprint(checks: list[dict[str, Any]], settings: Settings) -> str:
    """Вычислить fingerprint. Канонический ввод обеспечивает детерминированное сравнение
    целостности между процессами.
    """
    payload = {
        "version": settings.version,
        "environment": settings.environment,
        "database_backend": settings.database_url.split(":", 1)[0],
        "storage_backend": settings.storage_backend,
        "checks": [
            {
                "code": item["code"],
                "status": item["status"],
                "details": item.get("details") or {},
            }
            for item in checks
        ],
    }
    raw = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def run_commissioning_checks(
    db: Session,
    *,
    organization_id: str,
    created_by: User,
    settings: Settings,
    storage: StorageService,
    lock_manager: DistributedLockManager,
) -> CommissioningCheckRun:
    """Выполнить run commissioning checks. Операция координирует ограниченные побочные эффекты и
    возвращает детерминированный результат.
    """
    now = utcnow()
    checks: list[dict[str, Any]] = []

    try:
        value = db.execute(text("SELECT 1")).scalar_one()
        checks.append(
            _check(
                "database_connectivity",
                "Подключение к базе данных",
                "passed" if value == 1 else "blocked",
                "База данных отвечает на запрос.",
                details={"dialect": db.bind.dialect.name if db.bind else "unknown"},
            )
        )
    except Exception as exc:
        checks.append(
            _check(
                "database_connectivity",
                "Подключение к базе данных",
                "blocked",
                "База данных недоступна.",
                details={"error_type": type(exc).__name__},
            )
        )

    try:
        current, head = _migration_state(db)
        if current and current == head:
            status = "passed"
            message = "Схема соответствует migration head."
        elif (
            current is None and settings.effective_auto_create_schema and not settings.is_production
        ):
            status = "warning"
            message = (
                "Схема создана автоматически для локальной среды; перед production "
                "развёртыванием выполните Alembic upgrade."
            )
        else:
            status = "blocked"
            message = "Требуется выполнить Alembic upgrade."
        checks.append(
            _check(
                "database_migrations",
                "Актуальность схемы базы данных",
                status,
                message,
                details={
                    "current_revision": current,
                    "head_revision": head,
                    "auto_create_schema": settings.effective_auto_create_schema,
                },
            )
        )
    except Exception as exc:
        checks.append(
            _check(
                "database_migrations",
                "Актуальность схемы базы данных",
                "blocked",
                "Не удалось определить ревизию Alembic.",
                details={"error_type": type(exc).__name__},
            )
        )

    probe_key = f"commissioning/{organization_id}/{uuid.uuid4().hex}.probe"
    probe = os.urandom(32)
    try:
        storage.put_bytes(probe_key, probe)
        stored = storage.read_bytes(probe_key)
        if stored != probe:
            raise CommissioningError("Контрольное содержимое storage изменилось")
        checks.append(
            _check(
                "storage_roundtrip",
                "Чтение и запись файлов",
                "passed",
                "Контрольный файл записан, прочитан и проверен.",
                details={"backend": storage.backend},
            )
        )
    except Exception as exc:
        checks.append(
            _check(
                "storage_roundtrip",
                "Чтение и запись файлов",
                "blocked",
                "Файловое хранилище не прошло контрольную запись.",
                details={"backend": storage.backend, "error_type": type(exc).__name__},
            )
        )
    finally:
        try:
            storage.delete(probe_key)
        except Exception:
            pass

    lock_ok = lock_manager.ping()
    lock_status = "passed" if lock_ok else "blocked"
    checks.append(
        _check(
            "distributed_lock",
            "Координация фоновых задач",
            lock_status,
            (
                "Redis lock backend отвечает."
                if settings.use_redis_locks and lock_ok
                else "Используются локальные блокировки процесса."
                if not settings.use_redis_locks
                else "Redis lock backend недоступен."
            ),
            details={"redis_enabled": settings.use_redis_locks},
        )
    )

    heartbeat = db.scalar(
        select(WorkerHeartbeat).order_by(WorkerHeartbeat.last_seen_at.desc()).limit(1)
    )
    heartbeat_time = aware_utc(heartbeat.last_seen_at) if heartbeat else None
    worker_fresh = bool(
        heartbeat_time
        and heartbeat_time > now - timedelta(seconds=settings.worker_readiness_max_age_seconds)
    )
    worker_required = settings.require_worker_for_readiness or settings.is_production
    worker_status = "passed" if worker_fresh else "blocked" if worker_required else "warning"
    checks.append(
        _check(
            "worker_heartbeat",
            "Worker очереди",
            worker_status,
            "Heartbeat worker актуален." if worker_fresh else "Свежий heartbeat worker не найден.",
            details={
                "worker_id": heartbeat.worker_id if heartbeat else None,
                "last_seen_at": heartbeat_time.isoformat() if heartbeat_time else None,
                "maximum_age_seconds": settings.worker_readiness_max_age_seconds,
            },
        )
    )

    execution_lease = db.scalar(
        select(ExecutionLease).where(ExecutionLease.organization_id == organization_id)
    )
    execution_sites = list(
        db.scalars(
            select(ExecutionSite).where(
                ExecutionSite.organization_id == organization_id,
                ExecutionSite.enabled.is_(True),
            )
        ).all()
    )
    active_site = next(
        (
            item
            for item in execution_sites
            if execution_lease is not None and item.site_key == execution_lease.active_site_key
        ),
        None,
    )
    active_seen = aware_utc(active_site.last_seen_at) if active_site else None
    active_fresh = bool(
        active_seen
        and active_seen > now - timedelta(seconds=settings.execution_site_heartbeat_ttl_seconds)
    )
    lease_expiry = aware_utc(execution_lease.lease_expires_at) if execution_lease else None
    holder_expired = bool(
        execution_lease
        and execution_lease.holder_worker_id
        and (lease_expiry is None or lease_expiry <= now)
    )
    if not settings.execution_fencing_required:
        execution_status = "blocked" if settings.is_production else "warning"
        execution_message = "Execution fencing отключён."
    elif execution_lease is None:
        execution_status = "blocked" if settings.is_production else "warning"
        execution_message = "Execution lease ещё не создан worker-процессом."
    elif execution_lease.status != ExecutionLeaseStatus.ACTIVE:
        execution_status = "blocked"
        execution_message = f"Execution lease находится в состоянии {execution_lease.status.value}."
    elif not active_fresh:
        execution_status = "blocked" if settings.is_production else "warning"
        execution_message = "Heartbeat active-площадки отсутствует или устарел."
    elif holder_expired:
        execution_status = "blocked"
        execution_message = "Active worker не обновил execution lease до истечения TTL."
    else:
        execution_status = "passed"
        execution_message = "Active site, heartbeat и fencing epoch подтверждены."
    checks.append(
        _check(
            "execution_fencing",
            "Active/standby execution fencing",
            execution_status,
            execution_message,
            details={
                "required": settings.execution_fencing_required,
                "configured_site_key": settings.execution_site_key,
                "primary_site_key": settings.execution_primary_site_key,
                "active_site_key": (execution_lease.active_site_key if execution_lease else None),
                "lease_status": (execution_lease.status.value if execution_lease else None),
                "epoch": int(execution_lease.epoch) if execution_lease else None,
                "holder_present": bool(execution_lease and execution_lease.holder_worker_id),
                "active_site_fresh": active_fresh,
                "registered_sites": len(execution_sites),
            },
        )
    )

    audit_result = verify_audit_chain(db, organization_id=organization_id)
    checks.append(
        _check(
            "audit_chain",
            "Целостность журнала аудита",
            "passed" if audit_result.valid else "blocked",
            "Hash-chain журнала подтверждена."
            if audit_result.valid
            else "Обнаружена проблема целостности журнала.",
            details={
                "checked_entries": audit_result.checked_entries,
                "legacy_entries": audit_result.legacy_entries,
                "head_sequence": audit_result.head_sequence,
                "head_hash": audit_result.computed_head_hash,
                "first_error": audit_result.first_error,
            },
        )
    )

    admins = list(
        db.scalars(
            select(User).where(
                User.organization_id == organization_id,
                User.is_active.is_(True),
                User.role.in_([UserRole.OWNER, UserRole.ADMIN]),
            )
        ).all()
    )
    without_totp = [item.id for item in admins if not item.totp_enabled]
    totp_status = (
        "passed" if not without_totp else "blocked" if settings.is_production else "warning"
    )
    checks.append(
        _check(
            "admin_totp",
            "Двухфакторная защита администраторов",
            totp_status,
            "У всех активных администраторов включён TOTP."
            if not without_totp
            else "Не у всех владельцев и администраторов включён TOTP.",
            details={"admin_count": len(admins), "without_totp_count": len(without_totp)},
        )
    )

    https_ok = settings.public_base_url.startswith("https://")
    https_status = "passed" if https_ok else "blocked" if settings.is_production else "warning"
    checks.append(
        _check(
            "public_https",
            "Публичный HTTPS-адрес",
            https_status,
            "Панель настроена на HTTPS." if https_ok else "Для live-режима требуется HTTPS.",
            details={"scheme": settings.public_base_url.split(":", 1)[0]},
        )
    )

    checks.append(
        _check(
            "telegram_runtime_mode",
            "Режим Telegram gateway",
            "warning" if settings.telegram_fake_mode else "passed",
            "Включён безопасный fake mode."
            if settings.telegram_fake_mode
            else "Включены реальные Telegram gateway.",
            details={"fake_mode": settings.telegram_fake_mode},
        )
    )

    backup_age = _latest_backup_age_hours(settings)
    backup_status = "passed" if backup_age is not None and backup_age <= 24 else "warning"
    checks.append(
        _check(
            "backup_freshness",
            "Резервная копия",
            backup_status,
            (
                f"Последний локальный backup создан {backup_age:.1f} ч. назад."
                if backup_age is not None
                else "Локальная резервная копия не обнаружена; выполните backup/restore drill."
            ),
            details={
                "latest_backup_age_hours": round(backup_age, 2) if backup_age is not None else None
            },
        )
    )

    try:
        recovery_policy, recovery = evaluate_recovery_compliance(
            db,
            organization_id=organization_id,
            actor=created_by,
            settings=settings,
            now=now,
        )
        recovery_status = recovery.status.value
        if recovery_status == "blocked" and not settings.is_production:
            recovery_status = "warning"
        checks.append(
            _check(
                "recovery_assurance",
                "Готовность восстановления",
                recovery_status,
                (
                    "RPO/RTO и подписанные доказательства восстановления соответствуют политике."
                    if recovery.status == ReadinessStatus.PASSED
                    else "Требуется актуальный подписанный backup и успешный restore drill."
                ),
                details={
                    "policy_id": recovery_policy.id,
                    "rpo_hours": recovery_policy.rpo_hours,
                    "rto_minutes": recovery_policy.rto_minutes,
                    "latest_backup_id": recovery.latest_backup.backup_id
                    if recovery.latest_backup
                    else None,
                    "latest_drill_id": recovery.latest_drill.drill_id
                    if recovery.latest_drill
                    else None,
                    "compliance_status": recovery.status.value,
                    "blocker_count": len(recovery.blockers),
                    "warning_count": len(recovery.warnings),
                },
            )
        )
    except Exception as exc:
        checks.append(
            _check(
                "recovery_assurance",
                "Готовность восстановления",
                "blocked" if settings.is_production else "warning",
                "Не удалось проверить подписанные доказательства восстановления.",
                details={"error_type": type(exc).__name__},
            )
        )

    if settings.database_url.startswith("postgresql"):
        pg_dump = shutil.which("pg_dump")
        checks.append(
            _check(
                "postgres_backup_tool",
                "Инструмент PostgreSQL backup",
                "passed" if pg_dump else "warning",
                "pg_dump доступен." if pg_dump else "pg_dump не найден в PATH API-контейнера.",
                details={"available": bool(pg_dump)},
            )
        )

    antivirus_status = "passed" if settings.antivirus_mode == "clamav" else "warning"
    checks.append(
        _check(
            "antivirus_policy",
            "Проверка загружаемых файлов",
            antivirus_status,
            "ClamAV включён в конфигурации."
            if settings.antivirus_mode == "clamav"
            else "Антивирусная проверка отключена.",
            details={
                "mode": settings.antivirus_mode,
                "fail_closed": settings.antivirus_fail_closed,
            },
        )
    )

    try:
        continuity = continuity_compliance(
            db,
            organization_id=organization_id,
            settings=settings,
            now=now,
        )
        continuity_status = (
            "passed"
            if continuity.compliant
            else "blocked"
            if continuity.required or settings.is_production
            else "warning"
        )
        checks.append(
            _check(
                "continuity_assurance",
                "Непрерывность и failback",
                continuity_status,
                (
                    "Актуальное continuity-evidence и совместимая резервная площадка подтверждены."
                    if continuity.compliant
                    else "Требуется актуальное принятое учение и совместимая standby-площадка."
                ),
                details={
                    "required": continuity.required,
                    "policy_id": continuity.policy.id,
                    "latest_drill_id": (
                        continuity.latest_drill.id if continuity.latest_drill else None
                    ),
                    "blocker_count": len(continuity.blockers),
                    "warning_count": len(continuity.warnings),
                    "runtime_snapshot": continuity.runtime_snapshot,
                },
            )
        )
    except Exception as exc:
        checks.append(
            _check(
                "continuity_assurance",
                "Непрерывность и failback",
                "blocked"
                if settings.continuity_assurance_required or settings.is_production
                else "warning",
                "Не удалось проверить continuity-evidence и совместимость площадок.",
                details={"error_type": type(exc).__name__},
            )
        )

    try:
        slo_decision = slo_gate_decision(
            db,
            organization_id=organization_id,
            settings=settings,
            gate="publishing",
            now=now,
        )
        slo_assessment = latest_slo_assessment(db, organization_id=organization_id)
        slo_policy = getattr(slo_assessment, "policy_snapshot", None)
        if settings.slo_gate_required:
            slo_status = "passed" if slo_decision.allowed else "blocked"
        else:
            slo_status = "passed" if slo_decision.allowed else "warning"
        checks.append(
            _check(
                "operational_slo",
                "Операционные SLO и error budget",
                slo_status,
                slo_decision.message,
                details={
                    "gate_required": settings.slo_gate_required,
                    "assessment_id": (slo_assessment.id if slo_assessment is not None else None),
                    "assessment_status": (
                        slo_assessment.status.value if slo_assessment is not None else None
                    ),
                    "assessment_current": bool(
                        slo_assessment
                        and assessment_is_current(
                            slo_assessment,
                            db.scalar(
                                select(SLOPolicy).where(
                                    SLOPolicy.organization_id == organization_id
                                )
                            ),
                            now=now,
                        )
                    ),
                    "error_budget_consumed_bps": (
                        slo_assessment.error_budget_consumed_bps
                        if slo_assessment is not None
                        else None
                    ),
                    "policy_snapshot_available": bool(slo_policy),
                },
            )
        )
    except Exception as exc:
        checks.append(
            _check(
                "operational_slo",
                "Операционные SLO и error budget",
                "blocked" if settings.slo_gate_required else "warning",
                "Не удалось проверить актуальную SLO-оценку.",
                details={"error_type": type(exc).__name__},
            )
        )

    signing_keys = list(
        db.scalars(
            select(ArtifactSigningKey).where(
                ArtifactSigningKey.organization_id == organization_id,
                ArtifactSigningKey.status == ArtifactSigningKeyStatus.ACTIVE,
            )
        ).all()
    )
    default_signing_key = next(
        (item for item in signing_keys if item.is_default and item.private_key_enc),
        None,
    )
    trusted_count = sum(item.trusted_for_import for item in signing_keys)
    if (
        default_signing_key is not None
        and settings.artifact_signature_policy == "require_trusted"
        and not default_signing_key.trusted_for_import
    ):
        signing_status = "blocked"
        signing_message = "Основной Ed25519-ключ не отмечен доверенным для организации."
    elif default_signing_key is not None:
        signing_status = "passed"
        signing_message = "Основной Ed25519-ключ подписи настроен."
    elif settings.artifact_signature_policy == "optional":
        signing_status = "warning"
        signing_message = "Основной Ed25519-ключ не настроен; артефакты будут без подписи."
    else:
        signing_status = "blocked"
        signing_message = (
            "Политика требует подписи, но основной приватный Ed25519-ключ отсутствует."
        )
    checks.append(
        _check(
            "artifact_signing",
            "Криптографическая подпись артефактов",
            signing_status,
            signing_message,
            details={
                "policy": settings.artifact_signature_policy,
                "active_key_count": len(signing_keys),
                "trusted_key_count": trusted_count,
                "default_key_configured": default_signing_key is not None,
                "default_fingerprint": (
                    default_signing_key.fingerprint if default_signing_key else None
                ),
            },
        )
    )

    blockers = [item["message"] for item in checks if item["status"] == "blocked"]
    warnings = [item["message"] for item in checks if item["status"] == "warning"]
    status = (
        ReadinessStatus.BLOCKED
        if blockers
        else ReadinessStatus.WARNING
        if warnings
        else ReadinessStatus.PASSED
    )
    summary = {
        "passed_checks": sum(item["status"] == "passed" for item in checks),
        "warning_checks": sum(item["status"] == "warning" for item in checks),
        "blocked_checks": sum(item["status"] == "blocked" for item in checks),
        "environment": settings.environment,
        "product_version": settings.version,
        "database_backend": settings.database_url.split(":", 1)[0],
        "storage_backend": settings.storage_backend,
    }
    report = CommissioningCheckRun(
        organization_id=organization_id,
        status=status,
        fingerprint=_fingerprint(checks, settings),
        checks=checks,
        blockers=blockers,
        warnings=warnings,
        summary=summary,
        created_by_id=created_by.id,
        expires_at=now + timedelta(minutes=settings.commissioning_ttl_minutes),
        created_at=now,
    )
    db.add(report)
    db.flush()
    return report
