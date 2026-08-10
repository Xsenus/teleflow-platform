from __future__ import annotations

import hashlib
import io
import json
import platform
import zipfile
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import urlsplit

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.audit import verify_audit_chain
from app.config import Settings
from app.enums import ArtifactSignatureStatus, JobStatus, RunStatus, SupportBundleStatus
from app.models import (
    Campaign,
    CampaignRun,
    DeliveryJob,
    Destination,
    Notification,
    Organization,
    PilotCanaryAttempt,
    PilotReadinessReport,
    PilotStageAssessment,
    SupportBundle,
    TelegramConnection,
    User,
    WorkerHeartbeat,
    new_id,
    utcnow,
)
from app.security import aware_utc
from app.services.artifact_signing import (
    SIGNATURE_FILENAME,
    ArtifactSigningError,
    get_default_signing_key,
    sign_bytes,
)
from app.services.crypto import SecretCipher
from app.services.storage import StorageService

SUPPORT_SECTIONS = [
    "runtime",
    "organization",
    "connections",
    "destinations",
    "campaigns",
    "queue",
    "pilot",
    "notifications",
    "audit_integrity",
]


def _iso(value: datetime | None) -> str | None:
    """Реализовать внутренний этап iso step. Вспомогательная функция сохраняет детерминированность
    и тестируемость процесса.
    """
    normalized = aware_utc(value)
    return normalized.isoformat() if normalized else None


def _json_bytes(value: Any) -> bytes:
    """Реализовать внутренний этап json bytes step. Вспомогательная функция сохраняет
    детерминированность и тестируемость процесса.
    """
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        indent=2,
        default=str,
    ).encode("utf-8")


def _ref(organization_id: str, entity_id: str | None) -> str | None:
    """Реализовать внутренний этап ref step. Вспомогательная функция сохраняет детерминированность
    и тестируемость процесса.
    """
    if not entity_id:
        return None
    return hashlib.sha256(f"{organization_id}:{entity_id}".encode()).hexdigest()[:16]


def _database_backend(database_url: str) -> str:
    """Реализовать внутренний этап database backend step. Вспомогательная функция сохраняет
    детерминированность и тестируемость процесса.
    """
    return database_url.split(":", 1)[0].lower()


def _safe_runtime(settings: Settings) -> dict[str, Any]:
    """Реализовать внутренний этап safe runtime step. Вспомогательная функция сохраняет
    детерминированность и тестируемость процесса.
    """
    parsed = urlsplit(settings.public_base_url)
    return {
        "application": settings.app_name,
        "version": settings.version,
        "environment": settings.environment,
        "python": platform.python_version(),
        "platform": platform.platform(),
        "database_backend": _database_backend(settings.database_url),
        "storage_backend": settings.storage_backend,
        "telegram_fake_mode": settings.telegram_fake_mode,
        "metrics_enabled": settings.metrics_enabled,
        "otel_enabled": settings.otel_enabled,
        "sentry_configured": bool(settings.sentry_dsn),
        "public_scheme": parsed.scheme,
        "public_host_configured": bool(parsed.hostname),
        "worker_poll_seconds": settings.worker_poll_seconds,
        "global_hard_daily_cap": settings.global_hard_daily_cap,
        "pilot_readiness_required": settings.pilot_readiness_required,
        "pilot_stage_enforcement_required": settings.pilot_stage_enforcement_required,
        "pilot_staged_threshold": settings.pilot_staged_threshold,
        "destination_validation_ttl_hours": settings.destination_validation_ttl_hours,
        "connection_health_ttl_hours": settings.connection_health_ttl_hours,
    }


def _collect_files(
    db: Session,
    *,
    organization: Organization,
    settings: Settings,
    now: datetime,
) -> dict[str, bytes]:
    """Реализовать внутренний этап collect files step. Вспомогательная функция сохраняет
    детерминированность и тестируемость процесса.
    """
    org_id = organization.id
    connections = list(
        db.scalars(
            select(TelegramConnection)
            .where(TelegramConnection.organization_id == org_id)
            .order_by(TelegramConnection.created_at)
        ).all()
    )
    destinations = list(
        db.scalars(
            select(Destination)
            .where(Destination.organization_id == org_id)
            .order_by(Destination.created_at)
        ).all()
    )
    campaigns = list(
        db.scalars(
            select(Campaign).where(Campaign.organization_id == org_id).order_by(Campaign.created_at)
        ).all()
    )
    job_status_counts: dict[JobStatus, int] = {
        status: int(count)
        for status, count in db.execute(
            select(DeliveryJob.status, func.count(DeliveryJob.id))
            .where(DeliveryJob.organization_id == org_id)
            .group_by(DeliveryJob.status)
        ).all()
    }
    run_status_counts: dict[RunStatus, int] = {
        status: int(count)
        for status, count in db.execute(
            select(CampaignRun.status, func.count(CampaignRun.id))
            .where(CampaignRun.organization_id == org_id)
            .group_by(CampaignRun.status)
        ).all()
    }
    notification_rows = db.execute(
        select(
            Notification.event_type,
            Notification.severity,
            Notification.status,
            func.count(Notification.id),
        )
        .where(Notification.organization_id == org_id)
        .group_by(Notification.event_type, Notification.severity, Notification.status)
    ).all()
    heartbeat = db.scalar(
        select(WorkerHeartbeat).order_by(WorkerHeartbeat.last_seen_at.desc()).limit(1)
    )
    latest_readiness = db.scalar(
        select(PilotReadinessReport)
        .where(PilotReadinessReport.organization_id == org_id)
        .order_by(PilotReadinessReport.created_at.desc())
        .limit(1)
    )
    latest_stage_assessment = db.scalar(
        select(PilotStageAssessment)
        .where(PilotStageAssessment.organization_id == org_id)
        .order_by(PilotStageAssessment.created_at.desc())
        .limit(1)
    )
    latest_canary = db.scalar(
        select(PilotCanaryAttempt)
        .where(PilotCanaryAttempt.organization_id == org_id)
        .order_by(PilotCanaryAttempt.created_at.desc())
        .limit(1)
    )
    audit = verify_audit_chain(db, organization_id=org_id).to_dict()
    audit["organization_id"] = _ref(org_id, org_id)
    audit["first_error_entry_id"] = _ref(org_id, audit.get("first_error_entry_id"))

    files: dict[str, bytes] = {
        "README.txt": (
            "TeleFlow support bundle\n"
            "Этот архив сформирован без Telegram credentials, токенов, session strings, "
            "паролей, телефонов, email, текстов сообщений и пользовательских audit details.\n"
            "UUID и внутренние идентификаторы заменены псевдонимами.\n"
            f"Сформирован: {now.isoformat()}\n"
        ).encode(),
        "runtime.json": _json_bytes(_safe_runtime(settings)),
        "organization.json": _json_bytes(
            {
                "organization_ref": _ref(org_id, org_id),
                "status": organization.status.value,
                "timezone_name": organization.timezone_name,
                "retention_days": organization.retention_days,
                "publishing_paused": organization.publishing_paused,
                "pilot_stage": organization.pilot_stage.value,
                "pilot_stage_updated_at": _iso(organization.pilot_stage_updated_at),
                "approval_policy": {
                    "distinct_approver": organization.require_distinct_campaign_approver,
                    "high_risk_destination_threshold": organization.high_risk_destination_threshold,
                    "high_risk_required_approvals": organization.high_risk_required_approvals,
                },
            }
        ),
        "connections.json": _json_bytes(
            [
                {
                    "connection_ref": _ref(org_id, item.id),
                    "kind": item.kind.value,
                    "status": item.status.value,
                    "credentials_configured": bool(item.credentials_enc),
                    "identity_configured": item.telegram_account_id is not None,
                    "min_interval_seconds": item.min_interval_seconds,
                    "daily_cap": item.daily_cap,
                    "destination_cooldown_minutes": item.destination_cooldown_minutes,
                    "last_checked_at": _iso(item.last_checked_at),
                    "last_delivery_at": _iso(item.last_delivery_at),
                    "flood_blocked_until": _iso(item.flood_blocked_until),
                    "last_error_code": item.last_error_code,
                }
                for item in connections
            ]
        ),
        "destinations.json": _json_bytes(
            [
                {
                    "destination_ref": _ref(org_id, item.id),
                    "connection_ref": _ref(org_id, item.connection_id),
                    "kind": item.kind.value,
                    "enabled": item.enabled,
                    "validated": item.validated,
                    "validated_at": _iso(item.validated_at),
                    "validation_expires_at": _iso(item.validation_expires_at),
                    "permission_status": item.permission_status.value,
                    "permission_reviewed_at": _iso(item.permission_reviewed_at),
                    "permission_expires_at": _iso(item.permission_expires_at),
                    "timezone_configured": bool(item.timezone_name),
                    "schedule_configured": bool(item.allowed_weekdays),
                    "cooldown_override_configured": item.cooldown_minutes_override is not None,
                    "last_sent_at": _iso(item.last_sent_at),
                    "next_allowed_at": _iso(item.next_allowed_at),
                    "last_error_code": item.last_error_code,
                    "consecutive_failures": item.consecutive_failures,
                }
                for item in destinations
            ]
        ),
        "campaigns.json": _json_bytes(
            [
                {
                    "campaign_ref": _ref(org_id, item.id),
                    "connection_ref": _ref(org_id, item.connection_id),
                    "status": item.status.value,
                    "schedule_type": item.schedule_type.value,
                    "rollout_mode": item.rollout_mode.value,
                    "destination_count": sum(
                        1 for link in item.destinations if link.enabled and link.destination.enabled
                    ),
                    "approved": bool(item.approved_at and item.approved_fingerprint),
                    "next_run_at": _iso(item.next_run_at),
                    "last_run_at": _iso(item.last_run_at),
                }
                for item in campaigns
            ]
        ),
        "queue.json": _json_bytes(
            {
                "delivery_jobs": {
                    getattr(status, "value", str(status)): count
                    for status, count in job_status_counts.items()
                },
                "campaign_runs": {
                    getattr(status, "value", str(status)): count
                    for status, count in run_status_counts.items()
                },
                "worker": {
                    "present": heartbeat is not None,
                    "last_seen_at": _iso(heartbeat.last_seen_at) if heartbeat else None,
                    "state_keys": sorted((heartbeat.details or {}).keys()) if heartbeat else [],
                },
            }
        ),
        "pilot.json": _json_bytes(
            {
                "pilot_stage": organization.pilot_stage.value,
                "latest_readiness": {
                    "report_ref": _ref(org_id, latest_readiness.id),
                    "status": latest_readiness.status.value,
                    "created_at": _iso(latest_readiness.created_at),
                    "expires_at": _iso(latest_readiness.expires_at),
                    "blocker_count": len(latest_readiness.blockers),
                    "warning_count": len(latest_readiness.warnings),
                }
                if latest_readiness
                else None,
                "latest_stage_assessment": {
                    "assessment_ref": _ref(org_id, latest_stage_assessment.id),
                    "current_stage": latest_stage_assessment.current_stage.value,
                    "requested_stage": latest_stage_assessment.requested_stage.value,
                    "status": latest_stage_assessment.status.value,
                    "created_at": _iso(latest_stage_assessment.created_at),
                    "expires_at": _iso(latest_stage_assessment.expires_at),
                    "blocker_count": len(latest_stage_assessment.blockers),
                    "warning_count": len(latest_stage_assessment.warnings),
                }
                if latest_stage_assessment
                else None,
                "latest_canary": {
                    "canary_ref": _ref(org_id, latest_canary.id),
                    "status": latest_canary.status.value,
                    "is_fake": latest_canary.is_fake,
                    "error_code": latest_canary.error_code,
                    "created_at": _iso(latest_canary.created_at),
                    "completed_at": _iso(latest_canary.completed_at),
                }
                if latest_canary
                else None,
            }
        ),
        "notifications.json": _json_bytes(
            [
                {
                    "event_type": event_type,
                    "severity": getattr(severity, "value", str(severity)),
                    "status": getattr(status, "value", str(status)),
                    "count": count,
                }
                for event_type, severity, status, count in notification_rows
            ]
        ),
        "audit_integrity.json": _json_bytes(audit),
    }
    return files


def _zip_files(
    files: dict[str, bytes],
    *,
    signing_key,
    cipher: SecretCipher,
) -> tuple[bytes, dict[str, Any] | None]:
    """Реализовать внутренний этап zip files step. Вспомогательная функция сохраняет
    детерминированность и тестируемость процесса.
    """
    manifest_lines: list[str] = []
    for name, data in sorted(files.items()):
        manifest_lines.append(f"{hashlib.sha256(data).hexdigest()}  {name}")
    complete = dict(files)
    manifest_bytes = ("\n".join(manifest_lines) + "\n").encode("ascii")
    complete["MANIFEST.sha256"] = manifest_bytes
    signature_envelope = None
    if signing_key is not None:
        signature_envelope = sign_bytes(
            manifest_bytes,
            key=signing_key,
            cipher=cipher,
            purpose="teleflow.support_bundle.manifest.v1",
        )
        complete[SIGNATURE_FILENAME] = json.dumps(
            signature_envelope,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for name, data in sorted(complete.items()):
            info = zipfile.ZipInfo(name)
            info.date_time = (2026, 1, 1, 0, 0, 0)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o600 << 16
            archive.writestr(info, data)
    return buffer.getvalue(), signature_envelope


def expire_support_bundles(
    db: Session,
    *,
    organization_id: str | None,
    storage: StorageService,
    now: datetime | None = None,
) -> int:
    """Выполнить операцию expire support bundles. Аргументы интерпретируются в контексте модуля,
    результат возвращается вызывающему коду.
    """
    now = aware_utc(now) or utcnow()
    stmt = select(SupportBundle).where(
        SupportBundle.status.in_([SupportBundleStatus.READY, SupportBundleStatus.EXPIRED]),
        SupportBundle.expires_at <= now,
        SupportBundle.storage_key.is_not(None),
    )
    if organization_id:
        stmt = stmt.where(SupportBundle.organization_id == organization_id)
    bundles = list(db.scalars(stmt).all())
    deleted = 0
    for bundle in bundles:
        bundle.status = SupportBundleStatus.EXPIRED
        bundle.deleted_at = now
        try:
            if bundle.storage_key:
                storage.delete(bundle.storage_key)
            bundle.storage_key = None
            bundle.error_message = None
            deleted += 1
        except Exception as exc:  # retry on the next retention cycle
            bundle.error_message = f"{type(exc).__name__}: storage deletion failed"
    return deleted


def create_support_bundle(
    db: Session,
    *,
    organization: Organization,
    created_by: User,
    settings: Settings,
    storage: StorageService,
    cipher: SecretCipher,
    now: datetime | None = None,
) -> SupportBundle:
    """Создать support bundle. Перед сохранением или возвратом нового значения проверяются
    связанные инварианты.
    """
    now = aware_utc(now) or utcnow()
    expire_support_bundles(
        db,
        organization_id=organization.id,
        storage=storage,
        now=now,
    )
    bundle = SupportBundle(
        id=new_id(),
        organization_id=organization.id,
        status=SupportBundleStatus.READY,
        sections=list(SUPPORT_SECTIONS),
        created_by_id=created_by.id,
        expires_at=now + timedelta(hours=settings.support_bundle_ttl_hours),
        created_at=now,
    )
    db.add(bundle)
    db.flush()
    try:
        files = _collect_files(
            db,
            organization=organization,
            settings=settings,
            now=now,
        )
        signing_key = get_default_signing_key(
            db, organization_id=organization.id, require_private=True
        )
        if signing_key is None and settings.artifact_signature_policy != "optional":
            raise ArtifactSigningError(
                "Для диагностического архива требуется основной Ed25519-ключ"
            )
        if (
            signing_key is not None
            and settings.artifact_signature_policy == "require_trusted"
            and not signing_key.trusted_for_import
        ):
            raise ArtifactSigningError("Основной Ed25519-ключ должен быть доверен организацией")
        payload, signature_envelope = _zip_files(
            files,
            signing_key=signing_key,
            cipher=cipher,
        )
        key = f"exports/support/{organization.id}/{bundle.id}.zip"
        storage.put_bytes(key, payload, content_type="application/zip")
        bundle.storage_key = key
        bundle.sha256 = hashlib.sha256(payload).hexdigest()
        bundle.size_bytes = len(payload)
        if signing_key is not None:
            bundle.signature_status = (
                ArtifactSignatureStatus.VALID_TRUSTED
                if signing_key.trusted_for_import
                else ArtifactSignatureStatus.VALID_UNTRUSTED
            )
            bundle.signature_info = signature_envelope or {}
            bundle.signer_fingerprint = signing_key.fingerprint
    except Exception as exc:
        bundle.status = SupportBundleStatus.FAILED
        bundle.error_message = f"{type(exc).__name__}: bundle generation failed"
        bundle.storage_key = None
        bundle.sha256 = None
        bundle.size_bytes = None
    db.flush()
    return bundle
