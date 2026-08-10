from __future__ import annotations

import hashlib
import json
import os
import shutil
import socket
import sqlite3
import stat
import subprocess
import tempfile
import uuid
import zipfile
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path, PurePosixPath
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import select
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from app.config import Settings
from app.enums import (
    ArtifactSignatureStatus,
    ReadinessStatus,
    RecoveryBackupStatus,
    RecoveryDrillMode,
    RecoveryDrillStatus,
)
from app.models import (
    ArtifactSigningKey,
    Organization,
    RecoveryBackupEvidence,
    RecoveryPolicy,
    RecoveryRestoreDrill,
    User,
    utcnow,
)
from app.services.artifact_signing import (
    ArtifactSigningError,
    enforce_signature_policy,
    get_default_signing_key,
    sign_bytes,
    verify_signature,
)
from app.services.crypto import SecretCipher

BACKUP_RECEIPT_PURPOSE = "teleflow-recovery-backup-receipt-v1"
BACKUP_MANIFEST_PURPOSE = "teleflow-recovery-backup-manifest-v1"
DRILL_RECEIPT_PURPOSE = "teleflow-recovery-restore-drill-v1"
BACKUP_RECEIPT_TYPE: Literal["teleflow_recovery_backup_receipt"] = (
    "teleflow_recovery_backup_receipt"
)
BACKUP_MANIFEST_TYPE: Literal["teleflow_recovery_backup_manifest"] = (
    "teleflow_recovery_backup_manifest"
)
DRILL_RECEIPT_TYPE: Literal["teleflow_recovery_restore_drill_receipt"] = (
    "teleflow_recovery_restore_drill_receipt"
)
RECOVERY_SCHEMA_VERSION: Literal[1] = 1
MAX_RECEIPT_BYTES = 2 * 1024 * 1024
MAX_ARCHIVE_MEMBERS = 100_000
MAX_ARCHIVE_UNCOMPRESSED_BYTES = 50 * 1024 * 1024 * 1024


class RecoveryError(RuntimeError):
    pass


def canonical_json_bytes(value: Any) -> bytes:
    """Преобразовать data for canonical json bytes using the project's canonical representation."""
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    """Вычислить sha256 bytes. Канонический ввод обеспечивает детерминированное сравнение
    целостности между процессами.
    """
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path, *, chunk_size: int = 1024 * 1024) -> str:
    """Вычислить sha256 file. Канонический ввод обеспечивает детерминированное сравнение
    целостности между процессами.
    """
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def _aware(value: datetime) -> datetime:
    """Реализовать внутренний этап aware step. Вспомогательная функция сохраняет
    детерминированность и тестируемость процесса.
    """
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _safe_relative_posix_path(value: str, *, label: str = "path") -> str:
    """Реализовать внутренний этап safe relative posix path step. Вспомогательная функция сохраняет
    детерминированность и тестируемость процесса.
    """
    if not value or "\x00" in value or "\\" in value:
        raise ValueError(f"Небезопасный {label}")
    path = PurePosixPath(value)
    if (
        path.is_absolute()
        or ".." in path.parts
        or not path.parts
        or any(part in {"", "."} for part in path.parts)
        or ":" in path.parts[0]
    ):
        raise ValueError(f"Небезопасный {label}")
    return path.as_posix()


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RecoveryArtifactClaim(StrictModel):
    filename: str = Field(min_length=1, max_length=255)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    size_bytes: int = Field(ge=1)
    encrypted: bool
    encryption_algorithm: str | None = Field(default=None, max_length=40)

    @field_validator("filename")
    @classmethod
    def safe_filename(cls, value: str) -> str:
        """Выполнить операцию safe filename класса RecoveryArtifactClaim. Аргументы
        интерпретируются в контексте модуля, результат возвращается вызывающему коду.
        """
        normalized = _safe_relative_posix_path(value, label="имя backup artifact")
        if "/" in normalized:
            raise ValueError("Имя backup artifact не должно содержать каталоги")
        return normalized

    @model_validator(mode="after")
    def validate_encryption(self) -> RecoveryArtifactClaim:
        """Проверить encryption класса RecoveryArtifactClaim. Некорректные данные или состояние
        отклоняются до побочного эффекта.
        """
        if self.encrypted and not self.encryption_algorithm:
            raise ValueError("Для зашифрованного backup требуется encryption_algorithm")
        if not self.encrypted and self.encryption_algorithm:
            raise ValueError("Незашифрованный backup не должен содержать encryption_algorithm")
        return self


class RecoveryDatabaseClaim(StrictModel):
    kind: Literal["sqlite", "postgresql"]
    alembic_revision: str | None = Field(default=None, max_length=80)
    integrity_check: str = Field(min_length=1, max_length=80)
    size_bytes: int = Field(ge=1)


class RecoveryStorageClaim(StrictModel):
    backend: Literal["local", "s3"]
    included: bool
    file_count: int = Field(ge=0)
    total_bytes: int = Field(ge=0)
    note: str | None = Field(default=None, max_length=1000)


class RecoveryBackupReceiptPayload(StrictModel):
    schema_version: Literal[1] = RECOVERY_SCHEMA_VERSION
    artifact_type: Literal["teleflow_recovery_backup_receipt"] = BACKUP_RECEIPT_TYPE
    organization_id: str = Field(min_length=36, max_length=36)
    backup_id: str = Field(min_length=8, max_length=80)
    product_version: str = Field(min_length=1, max_length=40)
    created_at: datetime
    artifact: RecoveryArtifactClaim
    database: RecoveryDatabaseClaim
    storage: RecoveryStorageClaim
    manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @field_validator("created_at")
    @classmethod
    def aware_created_at(cls, value: datetime) -> datetime:
        """Выполнить операцию aware created at класса RecoveryBackupReceiptPayload. Аргументы
        интерпретируются в контексте модуля, результат возвращается вызывающему коду.
        """
        if value.tzinfo is None:
            raise ValueError("created_at должен содержать часовой пояс")
        return value.astimezone(UTC)


class RecoveryBackupReceipt(StrictModel):
    payload: RecoveryBackupReceiptPayload
    signature: dict[str, Any] | None = None


class RecoveryCheck(StrictModel):
    code: str = Field(min_length=1, max_length=100)
    status: Literal["passed", "warning", "blocked"]
    message: str = Field(min_length=1, max_length=2000)
    details: dict[str, Any] = Field(default_factory=dict)


class RecoveryDrillReceiptPayload(StrictModel):
    schema_version: Literal[1] = RECOVERY_SCHEMA_VERSION
    artifact_type: Literal["teleflow_recovery_restore_drill_receipt"] = DRILL_RECEIPT_TYPE
    organization_id: str = Field(min_length=36, max_length=36)
    drill_id: str = Field(min_length=8, max_length=80)
    backup_id: str = Field(min_length=8, max_length=80)
    backup_artifact_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    product_version: str = Field(min_length=1, max_length=40)
    mode: RecoveryDrillMode
    status: RecoveryDrillStatus
    started_at: datetime
    completed_at: datetime
    duration_seconds: int = Field(ge=0, le=7 * 24 * 3600)
    target_rto_minutes: int = Field(ge=1, le=7 * 24 * 60)
    checks: list[RecoveryCheck] = Field(default_factory=list, max_length=500)
    blockers: list[str] = Field(default_factory=list, max_length=500)
    warnings: list[str] = Field(default_factory=list, max_length=500)
    executed_host_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")

    @field_validator("started_at", "completed_at")
    @classmethod
    def aware_times(cls, value: datetime) -> datetime:
        """Выполнить операцию aware times класса RecoveryDrillReceiptPayload. Аргументы
        интерпретируются в контексте модуля, результат возвращается вызывающему коду.
        """
        if value.tzinfo is None:
            raise ValueError("Время drill должно содержать часовой пояс")
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def validate_times_and_status(self) -> RecoveryDrillReceiptPayload:
        """Проверить times and status класса RecoveryDrillReceiptPayload. Некорректные данные или
        состояние отклоняются до побочного эффекта.
        """
        if self.completed_at < self.started_at:
            raise ValueError("completed_at не может быть раньше started_at")
        elapsed = int((self.completed_at - self.started_at).total_seconds())
        if abs(elapsed - self.duration_seconds) > 5:
            raise ValueError("duration_seconds не соответствует started_at/completed_at")
        blocked_checks = [item for item in self.checks if item.status == "blocked"]
        if self.status == RecoveryDrillStatus.PASSED and (self.blockers or blocked_checks):
            raise ValueError("Успешный drill не должен содержать blockers")
        return self


class RecoveryDrillReceipt(StrictModel):
    payload: RecoveryDrillReceiptPayload
    signature: dict[str, Any] | None = None


class ManifestFile(StrictModel):
    path: str = Field(min_length=1, max_length=1000)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    size_bytes: int = Field(ge=0)

    @field_validator("path")
    @classmethod
    def safe_path(cls, value: str) -> str:
        """Выполнить операцию safe path класса ManifestFile. Аргументы интерпретируются в контексте
        модуля, результат возвращается вызывающему коду.
        """
        return _safe_relative_posix_path(value, label="путь в manifest")


class RecoveryManifestDatabase(StrictModel):
    kind: Literal["sqlite", "postgresql"]
    path: str = Field(min_length=1, max_length=1000)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    size_bytes: int = Field(ge=1)
    integrity_check: str = Field(min_length=1, max_length=80)
    alembic_revision: str | None = Field(default=None, max_length=80)

    @field_validator("path")
    @classmethod
    def safe_database_path(cls, value: str) -> str:
        """Выполнить операцию safe database path класса RecoveryManifestDatabase. Аргументы
        интерпретируются в контексте модуля, результат возвращается вызывающему коду.
        """
        return _safe_relative_posix_path(value, label="путь database archive")


class RecoveryManifestStorage(StrictModel):
    backend: Literal["local", "s3"]
    included: bool
    file_count: int = Field(ge=0)
    total_bytes: int = Field(ge=0)
    note: str | None = Field(default=None, max_length=1000)


class RecoveryBackupManifest(StrictModel):
    schema_version: Literal[1] = RECOVERY_SCHEMA_VERSION
    artifact_type: Literal["teleflow_recovery_backup_manifest"] = BACKUP_MANIFEST_TYPE
    organization_id: str = Field(min_length=36, max_length=36)
    backup_id: str = Field(min_length=8, max_length=80)
    product_version: str = Field(min_length=1, max_length=40)
    created_at: datetime
    database: RecoveryManifestDatabase
    storage: RecoveryManifestStorage
    files: list[ManifestFile] = Field(max_length=MAX_ARCHIVE_MEMBERS)

    @field_validator("created_at")
    @classmethod
    def aware_manifest_time(cls, value: datetime) -> datetime:
        """Выполнить операцию aware manifest time класса RecoveryBackupManifest. Аргументы
        интерпретируются в контексте модуля, результат возвращается вызывающему коду.
        """
        if value.tzinfo is None:
            raise ValueError("created_at manifest должен содержать часовой пояс")
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def unique_file_paths(self) -> RecoveryBackupManifest:
        """Выполнить операцию unique file paths класса RecoveryBackupManifest. Аргументы
        интерпретируются в контексте модуля, результат возвращается вызывающему коду.
        """
        paths = [item.path for item in self.files]
        if len(paths) != len(set(paths)):
            raise ValueError("Manifest содержит повторяющиеся пути")
        return self


@dataclass(frozen=True)
class RecoveryCompliance:
    status: ReadinessStatus
    checks: list[dict[str, Any]]
    blockers: list[str]
    warnings: list[str]
    latest_backup: RecoveryBackupEvidence | None
    latest_drill: RecoveryRestoreDrill | None

    def to_dict(self) -> dict[str, Any]:
        """Преобразовать to dict класса RecoveryCompliance without changing the source object."""
        return {
            "status": self.status.value,
            "checks": self.checks,
            "blockers": self.blockers,
            "warnings": self.warnings,
            "latest_backup_id": self.latest_backup.id if self.latest_backup else None,
            "latest_drill_id": self.latest_drill.id if self.latest_drill else None,
        }


@dataclass(frozen=True)
class CreatedRecoveryBackup:
    artifact_path: Path
    receipt_path: Path
    evidence: RecoveryBackupEvidence
    manifest: RecoveryBackupManifest


@dataclass(frozen=True)
class CreatedRecoveryDrill:
    receipt_path: Path
    drill: RecoveryRestoreDrill
    payload: RecoveryDrillReceiptPayload


def get_or_create_recovery_policy(
    db: Session,
    *,
    organization_id: str,
    actor: User,
    settings: Settings,
) -> RecoveryPolicy:
    """Прочитать or create recovery policy. Значение возвращается без несвязанных изменений
    состояния.
    """
    policy = db.scalar(
        select(RecoveryPolicy).where(RecoveryPolicy.organization_id == organization_id)
    )
    if policy is not None:
        if settings.is_production:
            policy.enabled = True
            policy.require_encrypted_backup = True
            policy.require_trusted_signature = True
            policy.require_restore_drill = True
            policy.updated_by_id = actor.id
        return policy
    policy = RecoveryPolicy(
        organization_id=organization_id,
        enabled=True,
        rpo_hours=settings.recovery_default_rpo_hours,
        rto_minutes=settings.recovery_default_rto_minutes,
        restore_drill_max_age_days=settings.recovery_default_drill_max_age_days,
        minimum_retained_backups=settings.recovery_default_minimum_retained_backups,
        require_encrypted_backup=settings.recovery_require_encrypted_backup,
        require_trusted_signature=settings.recovery_require_trusted_signature,
        require_restore_drill=True,
        created_by_id=actor.id,
        updated_by_id=actor.id,
    )
    db.add(policy)
    db.flush()
    return policy


def _effective_signature_policy(policy: RecoveryPolicy, settings: Settings) -> str:
    """Реализовать внутренний этап effective signature policy step. Вспомогательная функция
    сохраняет детерминированность и тестируемость процесса.
    """
    if policy.require_trusted_signature:
        return "require_trusted"
    return settings.artifact_signature_policy


def _load_receipt(data: bytes, model: type[BaseModel]) -> BaseModel:
    """Реализовать внутренний этап load receipt step. Вспомогательная функция сохраняет
    детерминированность и тестируемость процесса.
    """
    if not data or len(data) > MAX_RECEIPT_BYTES:
        raise RecoveryError("Receipt пуст или превышает допустимый размер")
    try:
        raw = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RecoveryError("Receipt должен быть корректным UTF-8 JSON") from exc
    try:
        return model.model_validate(raw)
    except Exception as exc:  # pydantic exposes a verbose but useful validation message
        raise RecoveryError(f"Структура recovery receipt недействительна: {exc}") from exc


def import_backup_receipt(
    db: Session,
    *,
    data: bytes,
    organization_id: str,
    actor: User,
    settings: Settings,
) -> RecoveryBackupEvidence:
    """Создать backup receipt. Перед сохранением или возвратом нового значения проверяются
    связанные инварианты.
    """
    receipt = _load_receipt(data, RecoveryBackupReceipt)
    assert isinstance(receipt, RecoveryBackupReceipt)
    payload = receipt.payload
    if payload.organization_id != organization_id:
        raise RecoveryError("Receipt относится к другой организации")
    if payload.created_at > utcnow() + timedelta(minutes=5):
        raise RecoveryError("Время backup receipt находится в будущем")
    policy = get_or_create_recovery_policy(
        db, organization_id=organization_id, actor=actor, settings=settings
    )
    payload_bytes = canonical_json_bytes(payload.model_dump(mode="json"))
    verification = verify_signature(
        db,
        organization_id=organization_id,
        data=payload_bytes,
        envelope=receipt.signature,
        expected_purpose=BACKUP_RECEIPT_PURPOSE,
    )
    try:
        enforce_signature_policy(verification, policy=_effective_signature_policy(policy, settings))
    except ArtifactSigningError as exc:
        raise RecoveryError(str(exc)) from exc
    if policy.require_encrypted_backup and not payload.artifact.encrypted:
        raise RecoveryError("Политика восстановления требует зашифрованный backup")

    receipt_sha = sha256_bytes(
        canonical_json_bytes(receipt.model_dump(mode="json", exclude_none=False))
    )
    existing = db.scalar(
        select(RecoveryBackupEvidence).where(
            RecoveryBackupEvidence.organization_id == organization_id,
            RecoveryBackupEvidence.backup_id == payload.backup_id,
        )
    )
    if existing is not None:
        if existing.receipt_sha256 != receipt_sha:
            raise RecoveryError("Backup ID уже зарегистрирован с другим receipt")
        return existing

    now = utcnow()
    expires_at = payload.created_at + timedelta(
        days=max(1, settings.recovery_evidence_retention_days)
    )
    evidence = RecoveryBackupEvidence(
        organization_id=organization_id,
        backup_id=payload.backup_id,
        status=RecoveryBackupStatus.REGISTERED,
        product_version=payload.product_version,
        database_kind=payload.database.kind,
        storage_backend=payload.storage.backend,
        artifact_filename=payload.artifact.filename,
        artifact_sha256=payload.artifact.sha256,
        artifact_size_bytes=payload.artifact.size_bytes,
        artifact_encrypted=payload.artifact.encrypted,
        encryption_algorithm=payload.artifact.encryption_algorithm,
        backup_created_at=payload.created_at,
        receipt_sha256=receipt_sha,
        manifest_sha256=payload.manifest_sha256,
        manifest_summary={
            "database": payload.database.model_dump(mode="json"),
            "storage": payload.storage.model_dump(mode="json"),
        },
        signature_status=verification.status,
        signature_info=verification.to_dict(),
        signer_fingerprint=verification.fingerprint,
        imported_by_id=actor.id,
        verified_at=None,
        expires_at=expires_at,
        created_at=now,
    )
    db.add(evidence)
    db.flush()
    return evidence


def import_drill_receipt(
    db: Session,
    *,
    data: bytes,
    organization_id: str,
    actor: User,
    settings: Settings,
) -> RecoveryRestoreDrill:
    """Создать drill receipt. Перед сохранением или возвратом нового значения проверяются связанные
    инварианты.
    """
    receipt = _load_receipt(data, RecoveryDrillReceipt)
    assert isinstance(receipt, RecoveryDrillReceipt)
    payload = receipt.payload
    if payload.organization_id != organization_id:
        raise RecoveryError("Drill receipt относится к другой организации")
    if payload.completed_at > utcnow() + timedelta(minutes=5):
        raise RecoveryError("Время drill receipt находится в будущем")
    policy = get_or_create_recovery_policy(
        db, organization_id=organization_id, actor=actor, settings=settings
    )
    payload_bytes = canonical_json_bytes(payload.model_dump(mode="json"))
    verification = verify_signature(
        db,
        organization_id=organization_id,
        data=payload_bytes,
        envelope=receipt.signature,
        expected_purpose=DRILL_RECEIPT_PURPOSE,
    )
    try:
        enforce_signature_policy(verification, policy=_effective_signature_policy(policy, settings))
    except ArtifactSigningError as exc:
        raise RecoveryError(str(exc)) from exc

    backup = db.scalar(
        select(RecoveryBackupEvidence).where(
            RecoveryBackupEvidence.organization_id == organization_id,
            RecoveryBackupEvidence.backup_id == payload.backup_id,
        )
    )
    if backup is None:
        raise RecoveryError("Сначала зарегистрируйте backup receipt, использованный для drill")
    if backup.artifact_sha256 != payload.backup_artifact_sha256:
        raise RecoveryError("Drill относится к другому содержимому backup")
    if (
        payload.status == RecoveryDrillStatus.PASSED
        and backup.status != RecoveryBackupStatus.VERIFIED
    ):
        raise RecoveryError(
            "Успешный drill можно зарегистрировать только для физически проверенного backup"
        )

    existing = db.scalar(
        select(RecoveryRestoreDrill).where(
            RecoveryRestoreDrill.organization_id == organization_id,
            RecoveryRestoreDrill.drill_id == payload.drill_id,
        )
    )
    evidence_sha = sha256_bytes(payload_bytes)
    if existing is not None:
        if existing.evidence_sha256 != evidence_sha:
            raise RecoveryError("Drill ID уже зарегистрирован с другим receipt")
        return existing

    signed_target_rto = payload.target_rto_minutes
    actual_rto_met = payload.status == RecoveryDrillStatus.PASSED and (
        payload.duration_seconds <= signed_target_rto * 60
    )
    drill = RecoveryRestoreDrill(
        organization_id=organization_id,
        backup_evidence_id=backup.id,
        drill_id=payload.drill_id,
        backup_id=payload.backup_id,
        backup_artifact_sha256=payload.backup_artifact_sha256,
        product_version=payload.product_version,
        mode=payload.mode,
        status=payload.status,
        started_at=payload.started_at,
        completed_at=payload.completed_at,
        duration_seconds=payload.duration_seconds,
        target_rto_minutes=signed_target_rto,
        rto_met=actual_rto_met,
        checks=[item.model_dump(mode="json") for item in payload.checks],
        blockers=payload.blockers,
        warnings=payload.warnings,
        evidence_sha256=evidence_sha,
        signature_status=verification.status,
        signature_info=verification.to_dict(),
        signer_fingerprint=verification.fingerprint,
        executed_host_hash=payload.executed_host_hash,
        imported_by_id=actor.id,
        error_message="; ".join(payload.blockers)[:4000] or None,
        created_at=utcnow(),
    )
    db.add(drill)
    db.flush()
    return drill


def evaluate_recovery_compliance(
    db: Session,
    *,
    organization_id: str,
    actor: User,
    settings: Settings,
    now: datetime | None = None,
) -> tuple[RecoveryPolicy, RecoveryCompliance]:
    """Выполнить операцию evaluate recovery compliance. Аргументы интерпретируются в контексте
    модуля, результат возвращается вызывающему коду.
    """
    moment = _aware(now or utcnow())
    policy = get_or_create_recovery_policy(
        db, organization_id=organization_id, actor=actor, settings=settings
    )
    verified_backups = list(
        db.scalars(
            select(RecoveryBackupEvidence)
            .where(
                RecoveryBackupEvidence.organization_id == organization_id,
                RecoveryBackupEvidence.status == RecoveryBackupStatus.VERIFIED,
            )
            .order_by(RecoveryBackupEvidence.backup_created_at.desc())
        ).all()
    )
    # SQLite does not preserve timezone offsets consistently for DateTime columns;
    # normalize in Python so an expired evidence row never satisfies retention/RPO.
    backups = [
        item
        for item in verified_backups
        if item.expires_at is None or _aware(item.expires_at) > moment
    ]
    latest_backup = backups[0] if backups else None
    latest_drill = db.scalar(
        select(RecoveryRestoreDrill)
        .where(
            RecoveryRestoreDrill.organization_id == organization_id,
            RecoveryRestoreDrill.status == RecoveryDrillStatus.PASSED,
        )
        .order_by(RecoveryRestoreDrill.completed_at.desc())
        .limit(1)
    )
    checks: list[dict[str, Any]] = []

    def add(code: str, title: str, status: str, message: str, **details: Any) -> None:
        """Выполнить операцию add. Аргументы интерпретируются в контексте модуля, результат
        возвращается вызывающему коду.
        """
        checks.append(
            {
                "code": code,
                "title": title,
                "status": status,
                "message": message,
                "details": details,
            }
        )

    if not policy.enabled:
        add(
            "recovery_policy",
            "Политика восстановления",
            "warning",
            "Контроль RPO/RTO отключён для организации.",
        )
    else:
        add(
            "recovery_policy",
            "Политика восстановления",
            "passed",
            f"RPO {policy.rpo_hours} ч., RTO {policy.rto_minutes} мин.",
        )

    backup_age_hours: float | None = None
    if latest_backup:
        backup_age_hours = max(
            0.0, (moment - _aware(latest_backup.backup_created_at)).total_seconds() / 3600
        )
    rpo_ok = bool(backup_age_hours is not None and backup_age_hours <= policy.rpo_hours)
    add(
        "recovery_rpo",
        "Актуальность резервной копии",
        "passed" if rpo_ok else "blocked" if policy.enabled else "warning",
        (
            f"Последняя подписанная копия создана {backup_age_hours:.1f} ч. назад."
            if backup_age_hours is not None
            else "Подписанный backup receipt не зарегистрирован."
        ),
        rpo_hours=policy.rpo_hours,
        latest_backup_age_hours=round(backup_age_hours, 2)
        if backup_age_hours is not None
        else None,
        latest_backup_id=latest_backup.backup_id if latest_backup else None,
    )

    retained_ok = len(backups) >= policy.minimum_retained_backups
    add(
        "recovery_retention",
        "Количество подтверждённых копий",
        "passed" if retained_ok else "warning",
        f"Зарегистрировано подтверждённых копий: {len(backups)}.",
        required=policy.minimum_retained_backups,
        actual=len(backups),
    )

    encryption_ok = bool(latest_backup and latest_backup.artifact_encrypted)
    add(
        "recovery_encryption",
        "Шифрование последней копии",
        (
            "passed"
            if encryption_ok
            else "blocked"
            if policy.enabled and policy.require_encrypted_backup
            else "warning"
        ),
        (
            "Последняя копия зашифрована."
            if encryption_ok
            else "Последняя копия не подтверждает шифрование."
        ),
        required=policy.require_encrypted_backup,
    )

    signature_ok = bool(
        latest_backup
        and (
            latest_backup.signature_status == ArtifactSignatureStatus.VALID_TRUSTED
            if policy.require_trusted_signature
            else latest_backup.signature_status
            in {
                ArtifactSignatureStatus.VALID_TRUSTED,
                ArtifactSignatureStatus.VALID_UNTRUSTED,
            }
        )
    )
    add(
        "recovery_signature",
        "Подпись последней копии",
        "passed" if signature_ok else "blocked" if policy.enabled else "warning",
        (
            "Подпись backup receipt соответствует политике."
            if signature_ok
            else "Подпись backup receipt не соответствует политике."
        ),
        required_trusted=policy.require_trusted_signature,
        signature_status=(latest_backup.signature_status.value if latest_backup else None),
    )

    drill_age_days: float | None = None
    if latest_drill:
        drill_age_days = max(
            0.0, (moment - _aware(latest_drill.completed_at)).total_seconds() / 86400
        )
    drill_current_rto = bool(
        latest_drill and latest_drill.duration_seconds <= policy.rto_minutes * 60
    )
    drill_isolated_enough = bool(
        latest_drill
        and not (settings.is_production and latest_drill.mode == RecoveryDrillMode.METADATA_ONLY)
    )
    drill_current = bool(
        latest_drill
        and drill_age_days is not None
        and drill_age_days <= policy.restore_drill_max_age_days
        and drill_current_rto
        and drill_isolated_enough
    )
    drill_status = (
        "passed"
        if drill_current
        else "blocked"
        if policy.enabled and policy.require_restore_drill
        else "warning"
    )
    add(
        "recovery_restore_drill",
        "Проверка восстановления",
        drill_status,
        (
            "PostgreSQL metadata-only drill не удовлетворяет production recovery policy."
            if latest_drill
            and settings.is_production
            and latest_drill.mode == RecoveryDrillMode.METADATA_ONLY
            else f"Последний успешный drill выполнен {drill_age_days:.1f} дн. назад."
            if drill_age_days is not None
            else "Успешный restore drill не зарегистрирован."
        ),
        maximum_age_days=policy.restore_drill_max_age_days,
        latest_drill_age_days=round(drill_age_days, 2) if drill_age_days is not None else None,
        rto_met_current_policy=drill_current_rto if latest_drill else None,
        signed_rto_met=latest_drill.rto_met if latest_drill else None,
        duration_seconds=latest_drill.duration_seconds if latest_drill else None,
        mode=latest_drill.mode.value if latest_drill else None,
        isolated_enough=drill_isolated_enough if latest_drill else None,
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
    return policy, RecoveryCompliance(
        status=status,
        checks=checks,
        blockers=blockers,
        warnings=warnings,
        latest_backup=latest_backup,
        latest_drill=latest_drill,
    )


def _sqlite_path(database_url: str) -> Path:
    """Реализовать внутренний этап sqlite path step. Вспомогательная функция сохраняет
    детерминированность и тестируемость процесса.
    """
    url = make_url(database_url)
    if url.get_backend_name() != "sqlite" or not url.database or url.database == ":memory:":
        raise RecoveryError("Для SQLite backup требуется файловая база данных")
    return Path(url.database).expanduser().resolve()


def _sqlite_backup(source: Path, destination: Path) -> tuple[str, str | None]:
    """Создать согласованную копию SQLite и проверить её целостность. Соединения закрываются явно:
    контекстный менеджер ``sqlite3.Connection`` завершает транзакцию, но сам файловый дескриптор
    на Windows не закрывает.
    """
    if not source.exists() or not source.is_file():
        raise RecoveryError(f"SQLite база не найдена: {source}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(source)) as src, closing(sqlite3.connect(destination)) as dst:
        src.backup(dst)
    with closing(sqlite3.connect(destination)) as check:
        integrity = str(check.execute("PRAGMA integrity_check").fetchone()[0])
        try:
            row = check.execute(
                "SELECT version_num FROM alembic_version ORDER BY version_num LIMIT 1"
            ).fetchone()
        except sqlite3.OperationalError:
            row = None
    if integrity.lower() != "ok":
        raise RecoveryError(f"SQLite integrity_check завершился ошибкой: {integrity}")
    return integrity, str(row[0]) if row else None


def _postgres_backup(database_url: str, destination: Path) -> tuple[str, str | None]:
    """Реализовать внутренний этап postgres backup step. Вспомогательная функция сохраняет
    детерминированность и тестируемость процесса.
    """
    executable = shutil.which("pg_dump")
    if not executable:
        raise RecoveryError("pg_dump не найден в PATH")
    destination.parent.mkdir(parents=True, exist_ok=True)
    url = make_url(database_url)
    environment = os.environ.copy()
    if url.password:
        environment["PGPASSWORD"] = url.password
    connection_url = url.set(password=None).render_as_string(hide_password=False)
    result = subprocess.run(
        [executable, "--format=custom", "--no-owner", "--file", str(destination), connection_url],
        capture_output=True,
        text=True,
        timeout=3600,
        check=False,
        env=environment,
    )
    if result.returncode != 0:
        raise RecoveryError(f"pg_dump завершился ошибкой: {result.stderr[-1000:]}")
    return "pg_dump_custom", None


def _copy_local_storage(source: Path, destination: Path, backups_path: Path) -> tuple[int, int]:
    """Реализовать внутренний этап copy local storage step. Вспомогательная функция сохраняет
    детерминированность и тестируемость процесса.
    """
    file_count = 0
    total_bytes = 0
    if not source.exists():
        return 0, 0
    source = source.resolve()
    backups_path = backups_path.resolve()
    for item in sorted(source.rglob("*")):
        resolved = item.resolve()
        if resolved == backups_path or backups_path in resolved.parents:
            continue
        try:
            mode = item.lstat().st_mode
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(mode):
            raise RecoveryError(f"Символические ссылки в storage запрещены: {item}")
        if not item.is_file():
            continue
        relative = item.relative_to(source)
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(item, target)
        size = target.stat().st_size
        file_count += 1
        total_bytes += size
    return file_count, total_bytes


def _manifest_files(root: Path) -> list[ManifestFile]:
    """Реализовать внутренний этап manifest files step. Вспомогательная функция сохраняет
    детерминированность и тестируемость процесса.
    """
    files: list[ManifestFile] = []
    for item in sorted(root.rglob("*")):
        if item.is_symlink():
            raise RecoveryError(f"Символические ссылки в backup запрещены: {item}")
        if not item.is_file():
            continue
        relative = item.relative_to(root).as_posix()
        if relative in {"MANIFEST.json", "SIGNATURE.json"}:
            continue
        files.append(
            ManifestFile(path=relative, sha256=sha256_file(item), size_bytes=item.stat().st_size)
        )
    return files


def _write_zip(source: Path, output: Path) -> None:
    """Реализовать внутренний этап write zip step. Вспомогательная функция сохраняет
    детерминированность и тестируемость процесса.
    """
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for item in sorted(source.rglob("*")):
            if not item.is_file():
                continue
            if item.is_symlink():
                raise RecoveryError(f"Символические ссылки в backup запрещены: {item}")
            archive.write(item, item.relative_to(source).as_posix())


def _encrypt_with_age(source: Path, destination: Path, recipient: str) -> None:
    """Реализовать внутренний этап encrypt with age step. Вспомогательная функция сохраняет
    детерминированность и тестируемость процесса.
    """
    executable = shutil.which("age")
    if not executable:
        raise RecoveryError("age не найден в PATH, но шифрование backup включено")
    result = subprocess.run(
        [executable, "-r", recipient, "-o", str(destination), str(source)],
        capture_output=True,
        text=True,
        timeout=3600,
        check=False,
    )
    if result.returncode != 0:
        raise RecoveryError(f"age завершился ошибкой: {result.stderr[-1000:]}")


def create_recovery_backup(
    db: Session,
    *,
    organization: Organization,
    actor: User,
    settings: Settings,
    cipher: SecretCipher,
    output_dir: Path,
    include_storage: bool = True,
    age_recipient: str | None = None,
) -> CreatedRecoveryBackup:
    """Создать recovery backup. Перед сохранением или возвратом нового значения проверяются
    связанные инварианты.
    """
    policy = get_or_create_recovery_policy(
        db, organization_id=organization.id, actor=actor, settings=settings
    )
    recipient = (age_recipient or settings.recovery_age_recipient or "").strip() or None
    if policy.require_encrypted_backup and not recipient:
        raise RecoveryError("Политика требует шифрование; укажите age recipient")
    key = get_default_signing_key(db, organization_id=organization.id, require_private=True)
    if key is None:
        raise RecoveryError(
            "Для recovery backup требуется основной Ed25519-ключ с приватной частью"
        )

    now = utcnow()
    backup_id = str(uuid.uuid4())
    stamp = now.strftime("%Y%m%dT%H%M%SZ")
    output_dir = output_dir.expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="teleflow-recovery-") as temp_name:
        root = Path(temp_name)
        database_dir = root / "database"
        backend = make_url(settings.database_url).get_backend_name()
        if backend == "sqlite":
            database_file = database_dir / "teleflow.db"
            integrity, revision = _sqlite_backup(_sqlite_path(settings.database_url), database_file)
            database_kind = "sqlite"
        elif backend.startswith("postgresql"):
            database_file = database_dir / "database.dump"
            integrity, revision = _postgres_backup(settings.database_url, database_file)
            database_kind = "postgresql"
        else:
            raise RecoveryError(f"Неподдерживаемая база данных для backup: {backend}")

        storage_count = 0
        storage_bytes = 0
        storage_note: str | None = None
        if include_storage and settings.storage_backend == "local":
            storage_count, storage_bytes = _copy_local_storage(
                settings.storage_path,
                root / "storage",
                settings.backups_path,
            )
            storage_included = True
        elif include_storage and settings.storage_backend == "s3":
            storage_included = False
            storage_note = "S3 objects не включены: используйте versioning/object lock и отдельную репликацию bucket."
        else:
            storage_included = False
            storage_note = "Storage исключён по параметрам backup."

        database_size_bytes = database_file.stat().st_size
        files = _manifest_files(root)
        manifest = RecoveryBackupManifest(
            organization_id=organization.id,
            backup_id=backup_id,
            product_version=settings.version,
            created_at=now,
            database={
                "kind": database_kind,
                "path": database_file.relative_to(root).as_posix(),
                "sha256": sha256_file(database_file),
                "size_bytes": database_file.stat().st_size,
                "integrity_check": integrity,
                "alembic_revision": revision,
            },
            storage={
                "backend": settings.storage_backend,
                "included": storage_included,
                "file_count": storage_count,
                "total_bytes": storage_bytes,
                "note": storage_note,
            },
            files=files,
        )
        manifest_bytes = canonical_json_bytes(manifest.model_dump(mode="json"))
        signature = sign_bytes(
            manifest_bytes,
            key=key,
            cipher=cipher,
            purpose=BACKUP_MANIFEST_PURPOSE,
            created_at=now,
        )
        (root / "MANIFEST.json").write_bytes(manifest_bytes)
        (root / "SIGNATURE.json").write_bytes(canonical_json_bytes(signature))

        plain_name = f"teleflow-recovery-{stamp}-{backup_id[:8]}.zip"
        plain_path = output_dir / plain_name
        _write_zip(root, plain_path)
        if recipient:
            artifact_path = output_dir / f"{plain_name}.age"
            try:
                _encrypt_with_age(plain_path, artifact_path, recipient)
            except Exception:
                artifact_path.unlink(missing_ok=True)
                raise
            finally:
                # A failed encryption must never leave a plaintext database archive
                # in the configured backup directory.
                plain_path.unlink(missing_ok=True)
            encrypted = True
            encryption_algorithm = "age"
        else:
            artifact_path = plain_path
            encrypted = False
            encryption_algorithm = None

    artifact_sha = sha256_file(artifact_path)
    payload = RecoveryBackupReceiptPayload(
        organization_id=organization.id,
        backup_id=backup_id,
        product_version=settings.version,
        created_at=now,
        artifact=RecoveryArtifactClaim(
            filename=artifact_path.name,
            sha256=artifact_sha,
            size_bytes=artifact_path.stat().st_size,
            encrypted=encrypted,
            encryption_algorithm=encryption_algorithm,
        ),
        database=RecoveryDatabaseClaim(
            kind=database_kind,
            alembic_revision=revision,
            integrity_check=integrity,
            size_bytes=database_size_bytes,
        ),
        storage=RecoveryStorageClaim(
            backend=settings.storage_backend,
            included=storage_included,
            file_count=storage_count,
            total_bytes=storage_bytes,
            note=storage_note,
        ),
        manifest_sha256=sha256_bytes(manifest_bytes),
    )
    payload_bytes = canonical_json_bytes(payload.model_dump(mode="json"))
    receipt = RecoveryBackupReceipt(
        payload=payload,
        signature=sign_bytes(
            payload_bytes,
            key=key,
            cipher=cipher,
            purpose=BACKUP_RECEIPT_PURPOSE,
            created_at=now,
        ),
    )
    receipt_bytes = canonical_json_bytes(receipt.model_dump(mode="json", exclude_none=False))
    receipt_path = artifact_path.with_name(f"{artifact_path.name}.receipt.json")
    receipt_path.write_bytes(receipt_bytes)
    evidence = import_backup_receipt(
        db,
        data=receipt_bytes,
        organization_id=organization.id,
        actor=actor,
        settings=settings,
    )
    return CreatedRecoveryBackup(
        artifact_path=artifact_path,
        receipt_path=receipt_path,
        evidence=evidence,
        manifest=manifest,
    )


def _safe_zip_members(archive: zipfile.ZipFile) -> list[zipfile.ZipInfo]:
    """Реализовать внутренний этап safe zip members step. Вспомогательная функция сохраняет
    детерминированность и тестируемость процесса.
    """
    infos = archive.infolist()
    if len(infos) > MAX_ARCHIVE_MEMBERS:
        raise RecoveryError("Backup ZIP содержит слишком много файлов")
    total = 0
    seen: set[str] = set()
    for info in infos:
        try:
            normalized = _safe_relative_posix_path(info.filename, label="путь ZIP")
        except ValueError as exc:
            raise RecoveryError("Backup ZIP содержит небезопасный путь") from exc
        if normalized in seen:
            raise RecoveryError("Backup ZIP содержит повторяющиеся пути")
        seen.add(normalized)
        if info.flag_bits & 0x1:
            raise RecoveryError("Зашифрованные ZIP entries не поддерживаются")
        unix_mode = (info.external_attr >> 16) & 0xFFFF
        if stat.S_ISLNK(unix_mode):
            raise RecoveryError("Символические ссылки в backup ZIP запрещены")
        total += info.file_size
        if total > MAX_ARCHIVE_UNCOMPRESSED_BYTES:
            raise RecoveryError("Распакованный backup превышает допустимый размер")
    return infos


def _decrypt_age(source: Path, destination: Path, identity_file: Path) -> None:
    """Реализовать внутренний этап decrypt age step. Вспомогательная функция сохраняет
    детерминированность и тестируемость процесса.
    """
    executable = shutil.which("age")
    if not executable:
        raise RecoveryError("age не найден в PATH")
    if not identity_file.exists():
        raise RecoveryError("AGE identity file не найден")
    result = subprocess.run(
        [executable, "-d", "-i", str(identity_file), "-o", str(destination), str(source)],
        capture_output=True,
        text=True,
        timeout=3600,
        check=False,
    )
    if result.returncode != 0:
        raise RecoveryError(f"Расшифрование age завершилось ошибкой: {result.stderr[-1000:]}")


def verify_recovery_archive(
    db: Session,
    *,
    artifact_path: Path,
    receipt_data: bytes,
    organization_id: str,
    actor: User,
    settings: Settings,
    age_identity_file: Path | None = None,
) -> tuple[RecoveryBackupReceiptPayload, RecoveryBackupManifest, list[dict[str, Any]]]:
    """Проверить recovery archive. Некорректные данные или состояние отклоняются до побочного
    эффекта.
    """
    receipt = _load_receipt(receipt_data, RecoveryBackupReceipt)
    assert isinstance(receipt, RecoveryBackupReceipt)
    payload = receipt.payload
    # Registering first applies organization, signature, encryption and duplicate checks.
    evidence = import_backup_receipt(
        db,
        data=receipt_data,
        organization_id=organization_id,
        actor=actor,
        settings=settings,
    )
    artifact_path = artifact_path.expanduser().resolve()
    if not artifact_path.exists() or not artifact_path.is_file():
        raise RecoveryError("Backup artifact не найден")
    if artifact_path.name != payload.artifact.filename:
        raise RecoveryError("Имя backup artifact не совпадает с receipt")
    if artifact_path.stat().st_size != payload.artifact.size_bytes:
        raise RecoveryError("Размер backup artifact не совпадает с receipt")
    if sha256_file(artifact_path) != payload.artifact.sha256:
        raise RecoveryError("SHA-256 backup artifact не совпадает с receipt")

    with tempfile.TemporaryDirectory(prefix="teleflow-verify-") as temp_name:
        temp = Path(temp_name)
        if payload.artifact.encrypted:
            if not age_identity_file:
                raise RecoveryError("Для проверки зашифрованного backup нужен AGE identity file")
            zip_path = temp / "backup.zip"
            _decrypt_age(artifact_path, zip_path, age_identity_file)
        else:
            zip_path = artifact_path
        try:
            with zipfile.ZipFile(zip_path) as archive:
                infos = _safe_zip_members(archive)
                archive.extractall(temp / "content")
        except (zipfile.BadZipFile, OSError) as exc:
            raise RecoveryError("Backup artifact не является корректным ZIP") from exc
        root = temp / "content"
        try:
            manifest_raw = (root / "MANIFEST.json").read_bytes()
            signature_raw = json.loads((root / "SIGNATURE.json").read_text("utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RecoveryError(
                "Backup не содержит корректные MANIFEST.json и SIGNATURE.json"
            ) from exc
        if sha256_bytes(manifest_raw) != payload.manifest_sha256:
            raise RecoveryError("SHA-256 manifest не совпадает с receipt")
        try:
            manifest = RecoveryBackupManifest.model_validate(json.loads(manifest_raw))
        except Exception as exc:
            raise RecoveryError(f"Manifest backup недействителен: {exc}") from exc
        if manifest.organization_id != organization_id or manifest.backup_id != payload.backup_id:
            raise RecoveryError("Manifest относится к другой организации или backup ID")
        if manifest.product_version != payload.product_version:
            raise RecoveryError("Версия продукта в manifest не совпадает с receipt")
        if manifest.created_at != payload.created_at:
            raise RecoveryError("Время создания manifest не совпадает с receipt")
        if (
            manifest.database.kind != payload.database.kind
            or manifest.database.size_bytes != payload.database.size_bytes
            or manifest.database.alembic_revision != payload.database.alembic_revision
            or manifest.database.integrity_check != payload.database.integrity_check
        ):
            raise RecoveryError("Database claims в manifest не совпадают с receipt")
        if manifest.storage.model_dump(mode="json") != payload.storage.model_dump(mode="json"):
            raise RecoveryError("Storage claims в manifest не совпадают с receipt")
        policy = get_or_create_recovery_policy(
            db, organization_id=organization_id, actor=actor, settings=settings
        )
        verification = verify_signature(
            db,
            organization_id=organization_id,
            data=manifest_raw,
            envelope=signature_raw,
            expected_purpose=BACKUP_MANIFEST_PURPOSE,
        )
        try:
            enforce_signature_policy(
                verification, policy=_effective_signature_policy(policy, settings)
            )
        except ArtifactSigningError as exc:
            raise RecoveryError(str(exc)) from exc

        declared = {item.path: item for item in manifest.files}
        actual_names = {
            info.filename
            for info in infos
            if not info.is_dir() and info.filename not in {"MANIFEST.json", "SIGNATURE.json"}
        }
        if actual_names != set(declared):
            raise RecoveryError("Состав файлов backup не совпадает с manifest")
        for relative, item in declared.items():
            path = root / PurePosixPath(relative)
            if not path.is_file() or path.is_symlink():
                raise RecoveryError(f"Файл backup отсутствует или небезопасен: {relative}")
            if path.stat().st_size != item.size_bytes or sha256_file(path) != item.sha256:
                raise RecoveryError(f"Контрольная сумма файла backup не совпадает: {relative}")

        storage_items = [
            item
            for relative, item in declared.items()
            if PurePosixPath(relative).parts[:1] == ("storage",)
        ]
        storage_file_count = len(storage_items)
        storage_total_bytes = sum(item.size_bytes for item in storage_items)
        if manifest.storage.included:
            if (
                storage_file_count != manifest.storage.file_count
                or storage_total_bytes != manifest.storage.total_bytes
            ):
                raise RecoveryError("Фактическое содержимое storage не совпадает с manifest")
        elif storage_file_count:
            raise RecoveryError("Manifest запрещает storage, но archive содержит storage-файлы")

        checks: list[dict[str, Any]] = [
            {
                "code": "artifact_integrity",
                "status": "passed",
                "message": "SHA-256 backup artifact совпадает с receipt.",
                "details": {"size_bytes": payload.artifact.size_bytes},
            },
            {
                "code": "manifest_signature",
                "status": "passed",
                "message": "Подпись manifest подтверждена.",
                "details": verification.to_dict(),
            },
            {
                "code": "file_manifest",
                "status": "passed",
                "message": f"Проверено файлов: {len(declared)}.",
                "details": {"file_count": len(declared)},
            },
        ]
        db_info = manifest.database
        db_path = root / db_info.path
        if not db_path.is_file() or db_path.is_symlink():
            raise RecoveryError("Database archive отсутствует или небезопасен")
        if db_path.stat().st_size != db_info.size_bytes or sha256_file(db_path) != db_info.sha256:
            raise RecoveryError("Database archive не соответствует manifest")
        if db_info.kind == "sqlite":
            try:
                # Явное закрытие обязательно на Windows: иначе распакованный
                # файл остаётся занят и временный каталог нельзя очистить.
                with closing(sqlite3.connect(db_path)) as check:
                    integrity = str(check.execute("PRAGMA integrity_check").fetchone()[0])
                    tables = int(
                        check.execute(
                            "SELECT count(*) FROM sqlite_master WHERE type='table'"
                        ).fetchone()[0]
                    )
            except sqlite3.Error as exc:
                raise RecoveryError(f"SQLite backup не открывается: {exc}") from exc
            if integrity.lower() != "ok":
                raise RecoveryError(f"SQLite integrity_check завершился ошибкой: {integrity}")
            checks.append(
                {
                    "code": "database_restore",
                    "status": "passed",
                    "message": "SQLite база открыта в изолированном каталоге и прошла integrity_check.",
                    "details": {"table_count": tables, "integrity_check": integrity},
                }
            )
        elif db_info.kind == "postgresql":
            executable = shutil.which("pg_restore")
            if executable:
                result = subprocess.run(
                    [executable, "--list", str(db_path)],
                    capture_output=True,
                    text=True,
                    timeout=300,
                    check=False,
                )
                if result.returncode != 0:
                    raise RecoveryError(
                        f"pg_restore --list завершился ошибкой: {result.stderr[-1000:]}"
                    )
                checks.append(
                    {
                        "code": "database_archive",
                        "status": "passed",
                        "message": "PostgreSQL custom dump читается pg_restore.",
                        "details": {"toc_lines": len(result.stdout.splitlines())},
                    }
                )
            else:
                checks.append(
                    {
                        "code": "database_archive",
                        "status": "warning",
                        "message": "pg_restore не найден; проверены только подпись и контрольные суммы.",
                        "details": {},
                    }
                )
        else:
            raise RecoveryError("Manifest содержит неподдерживаемый тип базы данных")
    evidence.status = RecoveryBackupStatus.VERIFIED
    evidence.verified_at = utcnow()
    evidence.error_message = None
    db.flush()
    return payload, manifest, checks


def create_restore_drill(
    db: Session,
    *,
    artifact_path: Path,
    receipt_data: bytes,
    organization: Organization,
    actor: User,
    settings: Settings,
    cipher: SecretCipher,
    output_dir: Path,
    age_identity_file: Path | None = None,
) -> CreatedRecoveryDrill:
    """Создать restore drill. Перед сохранением или возвратом нового значения проверяются связанные
    инварианты.
    """
    started = utcnow()
    blockers: list[str] = []
    warnings: list[str] = []
    checks: list[dict[str, Any]] = []
    payload_backup: RecoveryBackupReceiptPayload | None = None
    mode = RecoveryDrillMode.METADATA_ONLY
    try:
        payload_backup, manifest, checks = verify_recovery_archive(
            db,
            artifact_path=artifact_path,
            receipt_data=receipt_data,
            organization_id=organization.id,
            actor=actor,
            settings=settings,
            age_identity_file=age_identity_file,
        )
        if payload_backup.database.kind == "sqlite":
            mode = RecoveryDrillMode.SQLITE_ISOLATED
        elif payload_backup.database.kind == "postgresql":
            mode = RecoveryDrillMode.METADATA_ONLY
            warnings.append(
                "PostgreSQL dump проверен как архив, но не восстановлен в отдельную PostgreSQL-базу."
            )
        status = RecoveryDrillStatus.PASSED
    except RecoveryError as exc:
        status = RecoveryDrillStatus.FAILED
        blockers.append(str(exc))
        checks.append(
            {
                "code": "restore_drill",
                "status": "blocked",
                "message": str(exc),
                "details": {"error_type": type(exc).__name__},
            }
        )
        try:
            parsed = _load_receipt(receipt_data, RecoveryBackupReceipt)
            assert isinstance(parsed, RecoveryBackupReceipt)
            payload_backup = parsed.payload
        except RecoveryError:
            payload_backup = None

    completed = utcnow()
    duration = max(0, int((completed - started).total_seconds()))
    if payload_backup is None:
        raise RecoveryError("Невозможно сформировать drill receipt без корректного backup receipt")
    policy = get_or_create_recovery_policy(
        db, organization_id=organization.id, actor=actor, settings=settings
    )
    key: ArtifactSigningKey | None = get_default_signing_key(
        db, organization_id=organization.id, require_private=True
    )
    if key is None:
        raise RecoveryError("Для drill receipt требуется основной Ed25519-ключ")
    drill_id = str(uuid.uuid4())
    host_hash = hashlib.sha256(socket.gethostname().encode("utf-8")).hexdigest()
    drill_payload = RecoveryDrillReceiptPayload(
        organization_id=organization.id,
        drill_id=drill_id,
        backup_id=payload_backup.backup_id,
        backup_artifact_sha256=payload_backup.artifact.sha256,
        product_version=settings.version,
        mode=mode,
        status=status,
        started_at=started,
        completed_at=completed,
        duration_seconds=duration,
        target_rto_minutes=policy.rto_minutes,
        checks=[RecoveryCheck.model_validate(item) for item in checks],
        blockers=blockers,
        warnings=warnings,
        executed_host_hash=host_hash,
    )
    payload_bytes = canonical_json_bytes(drill_payload.model_dump(mode="json"))
    receipt = RecoveryDrillReceipt(
        payload=drill_payload,
        signature=sign_bytes(
            payload_bytes,
            key=key,
            cipher=cipher,
            purpose=DRILL_RECEIPT_PURPOSE,
            created_at=completed,
        ),
    )
    receipt_bytes = canonical_json_bytes(receipt.model_dump(mode="json", exclude_none=False))
    output_dir = output_dir.expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    receipt_path = (
        output_dir / f"teleflow-drill-{completed.strftime('%Y%m%dT%H%M%SZ')}-{drill_id[:8]}.json"
    )
    receipt_path.write_bytes(receipt_bytes)
    drill = import_drill_receipt(
        db,
        data=receipt_bytes,
        organization_id=organization.id,
        actor=actor,
        settings=settings,
    )
    return CreatedRecoveryDrill(receipt_path=receipt_path, drill=drill, payload=drill_payload)
