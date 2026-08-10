from __future__ import annotations

import sqlite3
import zipfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.enums import (
    ArtifactSignatureStatus,
    ReadinessStatus,
    RecoveryBackupStatus,
    RecoveryDrillMode,
    RecoveryDrillStatus,
)
from app.services import recovery
from app.services.artifact_signing import ArtifactSigningError
from app.services.recovery import RecoveryError

ORG_ID = "00000000-0000-0000-0000-000000000001"
DIGEST = "0" * 64
NOW = datetime(2026, 8, 10, 12, tzinfo=UTC)


def _artifact(**overrides: object) -> dict[str, object]:
    """Создать корректный artifact claim и применить требуемые отклонения."""

    values: dict[str, object] = {
        "filename": "backup.zip",
        "sha256": DIGEST,
        "size_bytes": 1,
        "encrypted": False,
        "encryption_algorithm": None,
    }
    values.update(overrides)
    return values


def _drill_payload(**overrides: object) -> dict[str, object]:
    """Создать минимальный корректный restore-drill receipt payload."""

    values: dict[str, object] = {
        "organization_id": ORG_ID,
        "drill_id": "drill-id",
        "backup_id": "backup-id",
        "backup_artifact_sha256": DIGEST,
        "product_version": "test",
        "mode": RecoveryDrillMode.SQLITE_ISOLATED,
        "status": RecoveryDrillStatus.PASSED,
        "started_at": NOW,
        "completed_at": NOW + timedelta(seconds=10),
        "duration_seconds": 10,
        "target_rto_minutes": 1,
        "checks": [],
        "blockers": [],
        "warnings": [],
    }
    values.update(overrides)
    return values


class RecoveryRows:
    """Имитировать ScalarResult recovery service."""

    def __init__(self, values: list[object]) -> None:
        """Сохранить подготовленные recovery-строки для детерминированного результата."""

        self.values = values

    def all(self) -> list[object]:
        """Вернуть все подготовленные recovery-строки без обращения к базе данных."""

        return self.values


class RecoveryDb:
    """Предоставить очереди scalar/scalars и mutation counters."""

    def __init__(
        self,
        *,
        scalar_values: list[object | None] | None = None,
        scalar_lists: list[list[object]] | None = None,
    ) -> None:
        """Сохранить подготовленные ответы."""

        self.scalar_values = list(scalar_values or [])
        self.scalar_lists = list(scalar_lists or [])
        self.added: list[object] = []
        self.flushes = 0

    def scalar(self, _statement):  # type: ignore[no-untyped-def]
        """Вернуть следующий scalar."""

        return self.scalar_values.pop(0) if self.scalar_values else None

    def scalars(self, _statement) -> RecoveryRows:  # type: ignore[no-untyped-def]
        """Вернуть следующую коллекцию."""

        return RecoveryRows(self.scalar_lists.pop(0) if self.scalar_lists else [])

    def add(self, value: object) -> None:
        """Сохранить ORM-объект."""

        self.added.append(value)

    def flush(self) -> None:
        """Учесть вызов ORM flush без фактической записи в базу данных."""

        self.flushes += 1


def _backup_receipt(**payload_overrides: object) -> recovery.RecoveryBackupReceipt:
    """Создать корректный unsigned backup receipt."""

    payload: dict[str, object] = {
        "organization_id": ORG_ID,
        "backup_id": "backup-id",
        "product_version": "test",
        "created_at": NOW,
        "artifact": _artifact(),
        "database": {"kind": "sqlite", "integrity_check": "ok", "size_bytes": 1},
        "storage": {"backend": "local", "included": False, "file_count": 0, "total_bytes": 0},
        "manifest_sha256": DIGEST,
    }
    payload.update(payload_overrides)
    return recovery.RecoveryBackupReceipt(payload=payload)


def _drill_receipt(**payload_overrides: object) -> recovery.RecoveryDrillReceipt:
    """Создать корректный unsigned restore-drill receipt."""

    return recovery.RecoveryDrillReceipt(payload=_drill_payload(**payload_overrides))


def _verification() -> SimpleNamespace:
    """Создать успешный результат проверки подписи."""

    return SimpleNamespace(
        status=ArtifactSignatureStatus.VALID_TRUSTED,
        fingerprint="fingerprint",
        to_dict=lambda: {"status": "valid_trusted"},
    )


def _recovery_settings(**overrides: object) -> SimpleNamespace:
    """Создать минимальные настройки import/compliance."""

    values: dict[str, object] = {
        "artifact_signature_policy": "allow_unsigned",
        "recovery_evidence_retention_days": 30,
        "is_production": False,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _synthetic_archive(
    tmp_path: Path,
    case: str,
) -> tuple[Path, recovery.RecoveryBackupReceipt]:
    """Собрать согласованный ZIP/manifest/receipt с одним целевым отклонением."""

    database_path = "database/database.dump"
    database_bytes = b"database payload"
    database_kind = "postgresql" if case.startswith("postgres") else "sqlite"
    database_claim: dict[str, object] = {
        "kind": database_kind,
        "path": database_path,
        "sha256": recovery.sha256_bytes(database_bytes),
        "size_bytes": len(database_bytes),
        "integrity_check": "pg_dump_custom" if database_kind == "postgresql" else "ok",
        "alembic_revision": None,
    }
    receipt_database = {
        key: value for key, value in database_claim.items() if key != "path" and key != "sha256"
    }
    storage_bytes = b"storage"
    include_storage_file = case in {"storage_count", "storage_forbidden"}
    storage_claim: dict[str, object] = {
        "backend": "local",
        "included": include_storage_file,
        "file_count": 1 if include_storage_file else 0,
        "total_bytes": len(storage_bytes) if include_storage_file else 0,
        "note": None,
    }
    receipt_storage = dict(storage_claim)
    files: list[dict[str, object]] = [
        {
            "path": database_path,
            "sha256": recovery.sha256_bytes(database_bytes),
            "size_bytes": len(database_bytes),
        }
    ]
    archive_files = {database_path: database_bytes}
    if include_storage_file:
        files.append(
            {
                "path": "storage/item.bin",
                "sha256": recovery.sha256_bytes(storage_bytes),
                "size_bytes": len(storage_bytes),
            }
        )
        archive_files["storage/item.bin"] = storage_bytes

    manifest: dict[str, object] = {
        "organization_id": ORG_ID,
        "backup_id": "backup-id",
        "product_version": "test",
        "created_at": NOW,
        "database": database_claim,
        "storage": storage_claim,
        "files": files,
    }
    if case == "tenant":
        manifest["organization_id"] = "00000000-0000-0000-0000-000000000002"
    elif case == "version":
        manifest["product_version"] = "other"
    elif case == "time":
        manifest["created_at"] = NOW + timedelta(seconds=1)
    elif case == "database_claim":
        database_claim["integrity_check"] = "different"
    elif case == "storage_claim":
        storage_claim["note"] = "different"
    elif case == "composition":
        files.append({"path": "ghost.bin", "sha256": DIGEST, "size_bytes": 1})
    elif case == "checksum":
        files[0]["sha256"] = DIGEST
    elif case == "storage_count":
        storage_claim["file_count"] = 99
        receipt_storage["file_count"] = 99
    elif case == "storage_forbidden":
        storage_claim.update({"included": False, "file_count": 0, "total_bytes": 0})
        receipt_storage.update({"included": False, "file_count": 0, "total_bytes": 0})
    elif case == "database_missing":
        database_claim["path"] = "database/missing.dump"
    elif case == "database_mismatch":
        database_claim["sha256"] = DIGEST

    validated = recovery.RecoveryBackupManifest.model_validate(manifest)
    manifest_raw = recovery.canonical_json_bytes(validated.model_dump(mode="json"))
    archive_path = tmp_path / f"{case}.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        for name, content in archive_files.items():
            archive.writestr(name, content)
        archive.writestr("MANIFEST.json", manifest_raw)
        archive.writestr("SIGNATURE.json", b"{}")
    receipt = _backup_receipt(
        artifact=_artifact(
            filename=archive_path.name,
            size_bytes=archive_path.stat().st_size,
            sha256=recovery.sha256_file(archive_path),
        ),
        database=receipt_database,
        storage=receipt_storage,
        manifest_sha256=recovery.sha256_bytes(manifest_raw),
    )
    return archive_path, receipt


@pytest.mark.parametrize("value", [".", "/root", "../file", "dir\\file", "C:file", "\x00"])
def test_safe_relative_path_rejects_ambiguous_paths(value: str) -> None:
    """Проверить отклонение пустого, абсолютного, traversal и Windows-пути."""

    with pytest.raises(ValueError, match="Небезопасный"):
        recovery._safe_relative_posix_path(value)


@pytest.mark.parametrize(
    "claim",
    [
        _artifact(filename="dir/backup.zip"),
        _artifact(encrypted=True),
        _artifact(encryption_algorithm="age"),
    ],
)
def test_artifact_claim_rejects_directory_and_inconsistent_encryption(
    claim: dict[str, object],
) -> None:
    """Проверить безопасное имя и согласованность encrypted/algorithm."""

    with pytest.raises(ValidationError):
        recovery.RecoveryArtifactClaim.model_validate(claim)


@pytest.mark.parametrize(
    "overrides",
    [
        {"started_at": NOW.replace(tzinfo=None)},
        {"completed_at": NOW.replace(tzinfo=None)},
        {"completed_at": NOW - timedelta(seconds=1), "duration_seconds": 1},
        {"duration_seconds": 30},
        {"blockers": ["blocked"]},
        {
            "checks": [
                {
                    "code": "restore",
                    "status": "blocked",
                    "message": "blocked",
                }
            ]
        },
    ],
)
def test_drill_receipt_rejects_time_and_success_contradictions(
    overrides: dict[str, object],
) -> None:
    """Проверить timezone, duration и отсутствие blockers у успешного drill."""

    with pytest.raises(ValidationError):
        recovery.RecoveryDrillReceiptPayload.model_validate(_drill_payload(**overrides))


def test_backup_and_manifest_reject_naive_created_at() -> None:
    """Проверить обязательный timezone в backup receipt и manifest."""

    payload = {
        "organization_id": ORG_ID,
        "backup_id": "backup-id",
        "product_version": "test",
        "created_at": NOW.replace(tzinfo=None),
        "artifact": _artifact(),
        "database": {
            "kind": "sqlite",
            "integrity_check": "ok",
            "size_bytes": 1,
        },
        "storage": {"backend": "local", "included": False, "file_count": 0, "total_bytes": 0},
        "manifest_sha256": DIGEST,
    }
    with pytest.raises(ValidationError):
        recovery.RecoveryBackupReceiptPayload.model_validate(payload)

    with pytest.raises(ValidationError):
        recovery.RecoveryBackupManifest.model_validate(
            {
                "organization_id": ORG_ID,
                "backup_id": "backup-id",
                "product_version": "test",
                "created_at": NOW.replace(tzinfo=None),
                "database": {
                    "kind": "sqlite",
                    "path": "database/teleflow.db",
                    "sha256": DIGEST,
                    "size_bytes": 1,
                    "integrity_check": "ok",
                },
                "storage": {
                    "backend": "local",
                    "included": False,
                    "file_count": 0,
                    "total_bytes": 0,
                },
                "files": [],
            }
        )


def test_load_receipt_rejects_size_encoding_json_and_schema() -> None:
    """Проверить bounded UTF-8 JSON parsing и Pydantic contract receipt."""

    for data in (b"", b"\xff", b"{", b"{}"):
        with pytest.raises(RecoveryError):
            recovery._load_receipt(data, recovery.RecoveryBackupReceipt)
    with pytest.raises(RecoveryError, match="превышает"):
        recovery._load_receipt(
            b"x" * (recovery.MAX_RECEIPT_BYTES + 1), recovery.RecoveryBackupReceipt
        )


def test_import_backup_receipt_rejects_encryption_and_duplicate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Проверить policy encryption и immutable duplicate backup ID."""

    receipt = _backup_receipt()
    policy = SimpleNamespace(require_encrypted_backup=True, require_trusted_signature=False)
    monkeypatch.setattr(recovery, "utcnow", lambda: NOW)
    monkeypatch.setattr(recovery, "_load_receipt", lambda *_args: receipt)
    monkeypatch.setattr(recovery, "get_or_create_recovery_policy", lambda *_args, **_kwargs: policy)
    monkeypatch.setattr(recovery, "verify_signature", lambda *_args, **_kwargs: _verification())
    monkeypatch.setattr(recovery, "enforce_signature_policy", lambda *_args, **_kwargs: None)
    with pytest.raises(RecoveryError, match="зашифрованный"):
        recovery.import_backup_receipt(
            RecoveryDb(),  # type: ignore[arg-type]
            data=b"receipt",
            organization_id=ORG_ID,
            actor=SimpleNamespace(id="actor"),  # type: ignore[arg-type]
            settings=_recovery_settings(),  # type: ignore[arg-type]
        )

    policy.require_encrypted_backup = False
    existing = SimpleNamespace(receipt_sha256="different")
    with pytest.raises(RecoveryError, match="другим receipt"):
        recovery.import_backup_receipt(
            RecoveryDb(scalar_values=[existing]),  # type: ignore[arg-type]
            data=b"receipt",
            organization_id=ORG_ID,
            actor=SimpleNamespace(id="actor"),  # type: ignore[arg-type]
            settings=_recovery_settings(),  # type: ignore[arg-type]
        )


@pytest.mark.parametrize(
    "case",
    ["tenant", "future", "signature", "missing", "digest", "unverified", "duplicate"],
)
def test_import_drill_receipt_rejects_invalid_binding(
    monkeypatch: pytest.MonkeyPatch,
    case: str,
) -> None:
    """Проверить tenant/time/signature/backup/digest/status и duplicate drill привязки."""

    receipt = _drill_receipt(
        organization_id=("00000000-0000-0000-0000-000000000002" if case == "tenant" else ORG_ID),
        started_at=NOW + timedelta(days=1) if case == "future" else NOW,
        completed_at=(
            NOW + timedelta(days=1, seconds=10) if case == "future" else NOW + timedelta(seconds=10)
        ),
    )
    policy = SimpleNamespace(require_trusted_signature=False)
    monkeypatch.setattr(recovery, "utcnow", lambda: NOW)
    monkeypatch.setattr(recovery, "_load_receipt", lambda *_args: receipt)
    monkeypatch.setattr(recovery, "get_or_create_recovery_policy", lambda *_args, **_kwargs: policy)
    monkeypatch.setattr(recovery, "verify_signature", lambda *_args, **_kwargs: _verification())
    if case == "signature":
        monkeypatch.setattr(
            recovery,
            "enforce_signature_policy",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(ArtifactSigningError("signature")),
        )
    else:
        monkeypatch.setattr(recovery, "enforce_signature_policy", lambda *_args, **_kwargs: None)
    backup = (
        None
        if case == "missing"
        else SimpleNamespace(
            id="backup-row",
            artifact_sha256="f" * 64 if case == "digest" else DIGEST,
            status=(
                RecoveryBackupStatus.REGISTERED
                if case == "unverified"
                else RecoveryBackupStatus.VERIFIED
            ),
        )
    )
    existing = SimpleNamespace(evidence_sha256="different") if case == "duplicate" else None
    with pytest.raises(RecoveryError):
        recovery.import_drill_receipt(
            RecoveryDb(scalar_values=[backup, existing]),  # type: ignore[arg-type]
            data=b"receipt",
            organization_id=ORG_ID,
            actor=SimpleNamespace(id="actor"),  # type: ignore[arg-type]
            settings=_recovery_settings(),  # type: ignore[arg-type]
        )


def test_import_drill_receipt_returns_identical_existing_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Проверить идемпотентный повтор идентичного drill receipt."""

    receipt = _drill_receipt()
    payload_sha = recovery.sha256_bytes(
        recovery.canonical_json_bytes(receipt.payload.model_dump(mode="json"))
    )
    backup = SimpleNamespace(
        id="backup-row",
        artifact_sha256=DIGEST,
        status=RecoveryBackupStatus.VERIFIED,
    )
    existing = SimpleNamespace(evidence_sha256=payload_sha)
    monkeypatch.setattr(recovery, "utcnow", lambda: NOW)
    monkeypatch.setattr(recovery, "_load_receipt", lambda *_args: receipt)
    monkeypatch.setattr(
        recovery,
        "get_or_create_recovery_policy",
        lambda *_args, **_kwargs: SimpleNamespace(require_trusted_signature=False),
    )
    monkeypatch.setattr(recovery, "verify_signature", lambda *_args, **_kwargs: _verification())
    monkeypatch.setattr(recovery, "enforce_signature_policy", lambda *_args, **_kwargs: None)
    result = recovery.import_drill_receipt(
        RecoveryDb(scalar_values=[backup, existing]),  # type: ignore[arg-type]
        data=b"receipt",
        organization_id=ORG_ID,
        actor=SimpleNamespace(id="actor"),  # type: ignore[arg-type]
        settings=_recovery_settings(),  # type: ignore[arg-type]
    )
    assert result is existing


def test_disabled_recovery_policy_produces_warning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Проверить warning semantics отключённой необязательной recovery policy."""

    policy = SimpleNamespace(
        enabled=False,
        rpo_hours=24,
        rto_minutes=60,
        minimum_retained_backups=1,
        require_encrypted_backup=False,
        require_trusted_signature=False,
        require_restore_drill=False,
        restore_drill_max_age_days=30,
    )
    monkeypatch.setattr(recovery, "get_or_create_recovery_policy", lambda *_args, **_kwargs: policy)
    _, report = recovery.evaluate_recovery_compliance(
        RecoveryDb(scalar_lists=[[]], scalar_values=[None]),  # type: ignore[arg-type]
        organization_id=ORG_ID,
        actor=SimpleNamespace(id="actor"),  # type: ignore[arg-type]
        settings=_recovery_settings(),  # type: ignore[arg-type]
        now=NOW,
    )
    assert report.status == ReadinessStatus.WARNING
    assert report.checks[0]["status"] == "warning"


def test_compliance_serialization_and_signature_policy() -> None:
    """Проверить nullable evidence IDs и приоритет trusted-signature policy."""

    report = recovery.RecoveryCompliance(
        status=ReadinessStatus.BLOCKED,
        checks=[],
        blockers=["blocked"],
        warnings=[],
        latest_backup=None,
        latest_drill=None,
    )
    assert report.to_dict()["latest_backup_id"] is None
    assert (
        recovery._effective_signature_policy(
            SimpleNamespace(require_trusted_signature=True),
            SimpleNamespace(artifact_signature_policy="allow_unsigned"),
        )
        == "require_trusted"
    )
    assert (
        recovery._effective_signature_policy(
            SimpleNamespace(require_trusted_signature=False),
            SimpleNamespace(artifact_signature_policy="allow_unsigned"),
        )
        == "allow_unsigned"
    )


def test_database_backup_helpers_handle_invalid_and_external_commands(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Проверить SQLite guards и все исходы pg_dump без реального PostgreSQL."""

    for url in ("sqlite:///:memory:", "postgresql://localhost/db"):
        with pytest.raises(RecoveryError):
            recovery._sqlite_path(url)
    with pytest.raises(RecoveryError, match="не найдена"):
        recovery._sqlite_backup(tmp_path / "missing.db", tmp_path / "copy.db")
    sqlite_path = tmp_path / "plain.db"
    connection = sqlite3.connect(sqlite_path)
    connection.execute("CREATE TABLE sample (id INTEGER)")
    connection.commit()
    connection.close()
    assert recovery._sqlite_backup(sqlite_path, tmp_path / "plain-copy.db") == ("ok", None)

    monkeypatch.setattr(recovery.shutil, "which", lambda _name: None)
    with pytest.raises(RecoveryError, match="pg_dump"):
        recovery._postgres_backup("postgresql://user:secret@localhost/db", tmp_path / "db.dump")

    monkeypatch.setattr(recovery.shutil, "which", lambda _name: "pg_dump")
    monkeypatch.setattr(
        recovery.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(returncode=1, stderr="failure"),
    )
    with pytest.raises(RecoveryError, match="failure"):
        recovery._postgres_backup("postgresql://user:secret@localhost/db", tmp_path / "db.dump")
    monkeypatch.setattr(
        recovery.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(returncode=0, stderr=""),
    )
    assert recovery._postgres_backup("postgresql://localhost/db", tmp_path / "db.dump") == (
        "pg_dump_custom",
        None,
    )


def test_sqlite_backup_rejects_failed_integrity(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Проверить отказ согласованной SQLite-копии при bad integrity_check."""

    source = tmp_path / "source.db"
    source.write_bytes(b"database")

    class FakeConnection:
        """Имитировать backup и integrity SQLite connection."""

        def __init__(self, *, integrity: bool = False) -> None:
            """Сохранить роль проверочного соединения."""

            self.integrity = integrity

        def backup(self, _destination: object) -> None:
            """Имитировать SQLite online backup."""

        def execute(self, _statement: str) -> SimpleNamespace:
            """Вернуть неуспешный integrity_check."""

            return SimpleNamespace(fetchone=lambda: ("bad",))

        def close(self) -> None:
            """Закрыть фиктивное соединение."""

    connections = [FakeConnection(), FakeConnection(), FakeConnection(integrity=True)]
    monkeypatch.setattr(recovery.sqlite3, "connect", lambda *_args: connections.pop(0))
    with pytest.raises(RecoveryError, match="integrity_check"):
        recovery._sqlite_backup(source, tmp_path / "copy.db")


def test_storage_copy_handles_disappearing_file_and_symlink(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Проверить concurrent deletion и запрещённый symlink storage."""

    source = tmp_path / "source"
    source.mkdir()
    item = source / "item.bin"
    item.write_bytes(b"x")
    original_lstat = Path.lstat
    monkeypatch.setattr(
        Path,
        "lstat",
        lambda self: (
            (_ for _ in ()).throw(FileNotFoundError()) if self == item else original_lstat(self)
        ),
    )
    assert recovery._copy_local_storage(source, tmp_path / "copy", tmp_path / "backups") == (
        0,
        0,
    )
    monkeypatch.setattr(Path, "lstat", original_lstat)
    monkeypatch.setattr(recovery.stat, "S_ISLNK", lambda _mode: True)
    with pytest.raises(RecoveryError, match="Символические ссылки"):
        recovery._copy_local_storage(source, tmp_path / "copy2", tmp_path / "backups")


def test_storage_manifest_and_zip_helpers(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Проверить missing storage, backup exclusion, manifest exclusions и ZIP packaging."""

    assert recovery._copy_local_storage(
        tmp_path / "missing", tmp_path / "target", tmp_path / "backups"
    ) == (0, 0)
    source = tmp_path / "storage"
    source.mkdir()
    (source / "keep.bin").write_bytes(b"abc")
    backups = source / "backups"
    backups.mkdir()
    (backups / "exclude.bin").write_bytes(b"secret")
    assert recovery._copy_local_storage(source, tmp_path / "copy", backups) == (1, 3)

    root = tmp_path / "root"
    root.mkdir()
    (root / "payload.bin").write_bytes(b"data")
    (root / "MANIFEST.json").write_text("{}", encoding="utf-8")
    (root / "SIGNATURE.json").write_text("{}", encoding="utf-8")
    assert [item.path for item in recovery._manifest_files(root)] == ["payload.bin"]
    output = tmp_path / "archive.zip"
    recovery._write_zip(root, output)
    with zipfile.ZipFile(output) as archive:
        assert set(archive.namelist()) == {"payload.bin", "MANIFEST.json", "SIGNATURE.json"}

    original = Path.is_symlink
    monkeypatch.setattr(
        Path,
        "is_symlink",
        lambda self: self.name == "payload.bin" or original(self),
    )
    with pytest.raises(RecoveryError, match="Символические ссылки"):
        recovery._manifest_files(root)
    with pytest.raises(RecoveryError, match="Символические ссылки"):
        recovery._write_zip(root, tmp_path / "unsafe.zip")


def test_age_helpers_cover_missing_identity_failure_and_success(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Проверить fail-closed шифрование/расшифрование и успешный subprocess contract."""

    source = tmp_path / "archive.zip"
    source.write_bytes(b"archive")
    destination = tmp_path / "archive.age"
    identity = tmp_path / "identity.txt"
    monkeypatch.setattr(recovery.shutil, "which", lambda _name: None)
    with pytest.raises(RecoveryError, match="age"):
        recovery._encrypt_with_age(source, destination, "age1recipient")
    with pytest.raises(RecoveryError, match="age"):
        recovery._decrypt_age(source, destination, identity)

    monkeypatch.setattr(recovery.shutil, "which", lambda _name: "age")
    with pytest.raises(RecoveryError, match="identity"):
        recovery._decrypt_age(source, destination, identity)
    identity.write_text("secret", encoding="utf-8")
    monkeypatch.setattr(
        recovery.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(returncode=1, stderr="age failure"),
    )
    with pytest.raises(RecoveryError, match="age failure"):
        recovery._encrypt_with_age(source, destination, "age1recipient")
    with pytest.raises(RecoveryError, match="age failure"):
        recovery._decrypt_age(source, destination, identity)
    monkeypatch.setattr(
        recovery.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(returncode=0, stderr=""),
    )
    recovery._encrypt_with_age(source, destination, "age1recipient")
    recovery._decrypt_age(source, destination, identity)


def test_safe_zip_members_reject_encryption_symlink_and_size(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Проверить защиту ZIP entries от encryption, symlink и zip bomb."""

    class Archive:
        """Вернуть подготовленный список ZipInfo без создания опасного архива."""

        def __init__(self, info: zipfile.ZipInfo) -> None:
            """Сохранить единственную ZIP-запись."""

            self.info = info

        def infolist(self) -> list[zipfile.ZipInfo]:
            """Вернуть подготовленную ZIP-запись."""

            return [self.info]

    encrypted = zipfile.ZipInfo("file.bin")
    encrypted.flag_bits = 1
    with pytest.raises(RecoveryError, match="Зашифрованные"):
        recovery._safe_zip_members(Archive(encrypted))  # type: ignore[arg-type]

    symlink = zipfile.ZipInfo("link")
    symlink.external_attr = 0o120777 << 16
    with pytest.raises(RecoveryError, match="Символические"):
        recovery._safe_zip_members(Archive(symlink))  # type: ignore[arg-type]

    oversized = zipfile.ZipInfo("large.bin")
    oversized.file_size = recovery.MAX_ARCHIVE_UNCOMPRESSED_BYTES + 1
    with pytest.raises(RecoveryError, match="превышает"):
        recovery._safe_zip_members(Archive(oversized))  # type: ignore[arg-type]

    duplicate = zipfile.ZipInfo("same.bin")
    duplicate_archive = SimpleNamespace(infolist=lambda: [duplicate, duplicate])
    with pytest.raises(RecoveryError, match="повторяющиеся"):
        recovery._safe_zip_members(duplicate_archive)  # type: ignore[arg-type]

    too_many = SimpleNamespace(
        infolist=lambda: [
            zipfile.ZipInfo(f"{index}.bin") for index in range(recovery.MAX_ARCHIVE_MEMBERS + 1)
        ]
    )
    with pytest.raises(RecoveryError, match="слишком много"):
        recovery._safe_zip_members(too_many)  # type: ignore[arg-type]


def test_verify_archive_rejects_artifact_and_container_edges(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Проверить missing/name/hash, corrupt ZIP, metadata и manifest parsing guards."""

    evidence = SimpleNamespace(status=None, verified_at=None, error_message=None)
    monkeypatch.setattr(recovery, "import_backup_receipt", lambda *_args, **_kwargs: evidence)

    def verify(path: Path, receipt: recovery.RecoveryBackupReceipt) -> None:
        """Вызвать archive verifier с подготовленным receipt."""

        monkeypatch.setattr(recovery, "_load_receipt", lambda *_args: receipt)
        recovery.verify_recovery_archive(
            RecoveryDb(),  # type: ignore[arg-type]
            artifact_path=path,
            receipt_data=b"receipt",
            organization_id=ORG_ID,
            actor=SimpleNamespace(id="actor"),  # type: ignore[arg-type]
            settings=_recovery_settings(),  # type: ignore[arg-type]
        )

    missing = tmp_path / "missing.zip"
    with pytest.raises(RecoveryError, match="не найден"):
        verify(missing, _backup_receipt(artifact=_artifact(filename="missing.zip")))

    other = tmp_path / "other.zip"
    other.write_bytes(b"x")
    with pytest.raises(RecoveryError, match="Имя"):
        verify(
            other,
            _backup_receipt(
                artifact=_artifact(filename="expected.zip", sha256=recovery.sha256_file(other))
            ),
        )
    with pytest.raises(RecoveryError, match="SHA-256 backup"):
        verify(other, _backup_receipt(artifact=_artifact(filename="other.zip")))

    bad = tmp_path / "bad.zip"
    bad.write_bytes(b"not-a-zip")
    with pytest.raises(RecoveryError, match="корректным ZIP"):
        verify(
            bad,
            _backup_receipt(
                artifact=_artifact(
                    filename="bad.zip",
                    size_bytes=bad.stat().st_size,
                    sha256=recovery.sha256_file(bad),
                )
            ),
        )

    no_metadata = tmp_path / "empty.zip"
    with zipfile.ZipFile(no_metadata, "w"):
        pass
    with pytest.raises(RecoveryError, match="MANIFEST"):
        verify(
            no_metadata,
            _backup_receipt(
                artifact=_artifact(
                    filename="empty.zip",
                    size_bytes=no_metadata.stat().st_size,
                    sha256=recovery.sha256_file(no_metadata),
                )
            ),
        )

    invalid_manifest = tmp_path / "invalid-manifest.zip"
    manifest_raw = b"{}"
    with zipfile.ZipFile(invalid_manifest, "w") as archive:
        archive.writestr("MANIFEST.json", manifest_raw)
        archive.writestr("SIGNATURE.json", b"{}")
    invalid_receipt = _backup_receipt(
        artifact=_artifact(
            filename=invalid_manifest.name,
            size_bytes=invalid_manifest.stat().st_size,
            sha256=recovery.sha256_file(invalid_manifest),
        ),
        manifest_sha256=recovery.sha256_bytes(manifest_raw),
    )
    with pytest.raises(RecoveryError, match="Manifest backup недействителен"):
        verify(invalid_manifest, invalid_receipt)
    invalid_receipt.payload.manifest_sha256 = "f" * 64
    with pytest.raises(RecoveryError, match="SHA-256 manifest"):
        verify(invalid_manifest, invalid_receipt)


@pytest.mark.parametrize(
    ("case", "message"),
    [
        ("tenant", "другой организации"),
        ("version", "Версия продукта"),
        ("time", "Время создания"),
        ("database_claim", "Database claims"),
        ("storage_claim", "Storage claims"),
        ("signature", "signature blocked"),
        ("composition", "Состав файлов"),
        ("checksum", "Контрольная сумма"),
        ("storage_count", "содержимое storage"),
        ("storage_forbidden", "запрещает storage"),
        ("database_missing", "Database archive отсутствует"),
        ("database_mismatch", "Database archive не соответствует"),
        ("sqlite_error", "SQLite backup не открывается"),
        ("sqlite_integrity", "integrity_check"),
        ("postgres_failure", "pg_restore --list"),
    ],
)
def test_verify_archive_rejects_manifest_database_and_storage_mismatches(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    case: str,
    message: str,
) -> None:
    """Проверить взаимную привязку manifest/receipt/files/storage и database inspection."""

    archive_case = "postgres_failure" if case == "postgres_failure" else case
    path, receipt = _synthetic_archive(tmp_path, archive_case)
    evidence = SimpleNamespace(status=None, verified_at=None, error_message=None)
    monkeypatch.setattr(recovery, "_load_receipt", lambda *_args: receipt)
    monkeypatch.setattr(recovery, "import_backup_receipt", lambda *_args, **_kwargs: evidence)
    monkeypatch.setattr(
        recovery,
        "get_or_create_recovery_policy",
        lambda *_args, **_kwargs: SimpleNamespace(require_trusted_signature=False),
    )
    monkeypatch.setattr(recovery, "verify_signature", lambda *_args, **_kwargs: _verification())
    if case == "signature":
        monkeypatch.setattr(
            recovery,
            "enforce_signature_policy",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(
                ArtifactSigningError("signature blocked")
            ),
        )
    else:
        monkeypatch.setattr(recovery, "enforce_signature_policy", lambda *_args, **_kwargs: None)

    if case == "sqlite_error":
        monkeypatch.setattr(
            recovery.sqlite3,
            "connect",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(sqlite3.Error("open failed")),
        )
    elif case == "sqlite_integrity":

        class BadIntegrity:
            """Имитировать SQLite connection с неуспешным integrity_check."""

            def execute(self, statement: str) -> SimpleNamespace:
                """Вернуть bad integrity или нулевое число таблиц."""

                value: object = ("bad",) if "integrity_check" in statement else (0,)
                return SimpleNamespace(fetchone=lambda: value)

            def close(self) -> None:
                """Закрыть фиктивное соединение."""

        monkeypatch.setattr(recovery.sqlite3, "connect", lambda *_args, **_kwargs: BadIntegrity())
    elif case == "postgres_failure":
        monkeypatch.setattr(recovery.shutil, "which", lambda _name: "pg_restore")
        monkeypatch.setattr(
            recovery.subprocess,
            "run",
            lambda *_args, **_kwargs: SimpleNamespace(returncode=1, stderr="restore failed"),
        )

    with pytest.raises(RecoveryError, match=message):
        recovery.verify_recovery_archive(
            RecoveryDb(),  # type: ignore[arg-type]
            artifact_path=path,
            receipt_data=b"receipt",
            organization_id=ORG_ID,
            actor=SimpleNamespace(id="actor"),  # type: ignore[arg-type]
            settings=_recovery_settings(),  # type: ignore[arg-type]
        )


@pytest.mark.parametrize("pg_restore", [None, "pg_restore"])
def test_verify_postgres_archive_warns_or_passes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    pg_restore: str | None,
) -> None:
    """Проверить warning без pg_restore и успешную TOC-проверку при его наличии."""

    path, receipt = _synthetic_archive(tmp_path, "postgres_success")
    evidence = SimpleNamespace(status=None, verified_at=None, error_message="old")
    monkeypatch.setattr(recovery, "_load_receipt", lambda *_args: receipt)
    monkeypatch.setattr(recovery, "import_backup_receipt", lambda *_args, **_kwargs: evidence)
    monkeypatch.setattr(
        recovery,
        "get_or_create_recovery_policy",
        lambda *_args, **_kwargs: SimpleNamespace(require_trusted_signature=False),
    )
    monkeypatch.setattr(recovery, "verify_signature", lambda *_args, **_kwargs: _verification())
    monkeypatch.setattr(recovery, "enforce_signature_policy", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(recovery.shutil, "which", lambda _name: pg_restore)
    monkeypatch.setattr(
        recovery.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(returncode=0, stderr="", stdout="one\ntwo\n"),
    )
    _, _, checks = recovery.verify_recovery_archive(
        RecoveryDb(),  # type: ignore[arg-type]
        artifact_path=path,
        receipt_data=b"receipt",
        organization_id=ORG_ID,
        actor=SimpleNamespace(id="actor"),  # type: ignore[arg-type]
        settings=_recovery_settings(),  # type: ignore[arg-type]
    )
    database_check = checks[-1]
    assert database_check["status"] == ("warning" if pg_restore is None else "passed")
    assert evidence.status == RecoveryBackupStatus.VERIFIED


def test_verify_archive_rejects_unreachable_database_discriminator(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Проверить defensive else при обходе Pydantic Literal через model_construct."""

    path, receipt = _synthetic_archive(tmp_path, "postgres_success")
    receipt.payload.database.kind = "mysql"  # type: ignore[assignment]
    original_validate = recovery.RecoveryBackupManifest.model_validate

    def invalid_manifest(cls: type[object], value: object) -> recovery.RecoveryBackupManifest:
        """Сконструировать невозможный в штатном JSON database discriminator."""

        del cls
        manifest = original_validate(value)
        manifest.database.kind = "mysql"  # type: ignore[assignment]
        return manifest

    monkeypatch.setattr(
        recovery.RecoveryBackupManifest,
        "model_validate",
        classmethod(invalid_manifest),
    )
    monkeypatch.setattr(recovery, "_load_receipt", lambda *_args: receipt)
    monkeypatch.setattr(
        recovery,
        "import_backup_receipt",
        lambda *_args, **_kwargs: SimpleNamespace(status=None),
    )
    monkeypatch.setattr(
        recovery,
        "get_or_create_recovery_policy",
        lambda *_args, **_kwargs: SimpleNamespace(require_trusted_signature=False),
    )
    monkeypatch.setattr(recovery, "verify_signature", lambda *_args, **_kwargs: _verification())
    monkeypatch.setattr(recovery, "enforce_signature_policy", lambda *_args, **_kwargs: None)
    with pytest.raises(RecoveryError, match="неподдерживаемый тип"):
        recovery.verify_recovery_archive(
            RecoveryDb(),  # type: ignore[arg-type]
            artifact_path=path,
            receipt_data=b"receipt",
            organization_id=ORG_ID,
            actor=SimpleNamespace(id="actor"),  # type: ignore[arg-type]
            settings=_recovery_settings(),  # type: ignore[arg-type]
        )


def test_verify_encrypted_archive_requires_identity_and_invokes_decrypt(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Проверить обязательный AGE identity и передачу encrypted artifact в decryptor."""

    encrypted = tmp_path / "backup.zip.age"
    encrypted.write_bytes(b"encrypted")
    receipt = _backup_receipt(
        artifact=_artifact(
            filename=encrypted.name,
            size_bytes=encrypted.stat().st_size,
            sha256=recovery.sha256_file(encrypted),
            encrypted=True,
            encryption_algorithm="age",
        )
    )
    monkeypatch.setattr(recovery, "_load_receipt", lambda *_args: receipt)
    monkeypatch.setattr(
        recovery,
        "import_backup_receipt",
        lambda *_args, **_kwargs: SimpleNamespace(status=None),
    )
    kwargs = {
        "db": RecoveryDb(),
        "artifact_path": encrypted,
        "receipt_data": b"receipt",
        "organization_id": ORG_ID,
        "actor": SimpleNamespace(id="actor"),
        "settings": _recovery_settings(),
    }
    with pytest.raises(RecoveryError, match="AGE identity"):
        recovery.verify_recovery_archive(**kwargs)  # type: ignore[arg-type]
    identity = tmp_path / "identity.txt"
    identity.write_text("identity", encoding="utf-8")
    called: list[Path] = []

    def fail_decrypt(_source: Path, destination: Path, _identity: Path) -> None:
        """Зафиксировать destination и остановить проверку после decrypt call."""

        called.append(destination)
        raise RecoveryError("decrypt stopped")

    monkeypatch.setattr(recovery, "_decrypt_age", fail_decrypt)
    with pytest.raises(RecoveryError, match="decrypt stopped"):
        recovery.verify_recovery_archive(  # type: ignore[arg-type]
            **kwargs,
            age_identity_file=identity,
        )
    assert called and called[0].name == "backup.zip"


def test_verify_archive_rejects_extracted_symlink_projection(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Проверить повторную filesystem-проверку распакованного declared file."""

    path, receipt = _synthetic_archive(tmp_path, "symlink_projection")
    evidence = SimpleNamespace(status=None, verified_at=None, error_message=None)
    monkeypatch.setattr(recovery, "_load_receipt", lambda *_args: receipt)
    monkeypatch.setattr(recovery, "import_backup_receipt", lambda *_args, **_kwargs: evidence)
    monkeypatch.setattr(
        recovery,
        "get_or_create_recovery_policy",
        lambda *_args, **_kwargs: SimpleNamespace(require_trusted_signature=False),
    )
    monkeypatch.setattr(recovery, "verify_signature", lambda *_args, **_kwargs: _verification())
    monkeypatch.setattr(recovery, "enforce_signature_policy", lambda *_args, **_kwargs: None)
    original = Path.is_symlink
    monkeypatch.setattr(
        Path,
        "is_symlink",
        lambda self: self.name == "database.dump" or original(self),
    )
    with pytest.raises(RecoveryError, match="отсутствует или небезопасен"):
        recovery.verify_recovery_archive(
            RecoveryDb(),  # type: ignore[arg-type]
            artifact_path=path,
            receipt_data=b"receipt",
            organization_id=ORG_ID,
            actor=SimpleNamespace(id="actor"),  # type: ignore[arg-type]
            settings=_recovery_settings(),  # type: ignore[arg-type]
        )


def test_backup_and_restore_creation_guards(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Проверить обязательные encryption/signing keys и fallback parsing drill receipt."""

    policy = SimpleNamespace(require_encrypted_backup=True, rto_minutes=60)
    monkeypatch.setattr(recovery, "get_or_create_recovery_policy", lambda *_args, **_kwargs: policy)
    organization = SimpleNamespace(id=ORG_ID)
    actor = SimpleNamespace(id="actor")
    settings = SimpleNamespace(recovery_age_recipient=None)
    with pytest.raises(RecoveryError, match="age recipient"):
        recovery.create_recovery_backup(
            RecoveryDb(),  # type: ignore[arg-type]
            organization=organization,  # type: ignore[arg-type]
            actor=actor,  # type: ignore[arg-type]
            settings=settings,  # type: ignore[arg-type]
            cipher=SimpleNamespace(),  # type: ignore[arg-type]
            output_dir=tmp_path,
        )

    policy.require_encrypted_backup = False
    monkeypatch.setattr(recovery, "get_default_signing_key", lambda *_args, **_kwargs: None)
    with pytest.raises(RecoveryError, match="Ed25519"):
        recovery.create_recovery_backup(
            RecoveryDb(),  # type: ignore[arg-type]
            organization=organization,  # type: ignore[arg-type]
            actor=actor,  # type: ignore[arg-type]
            settings=settings,  # type: ignore[arg-type]
            cipher=SimpleNamespace(),  # type: ignore[arg-type]
            output_dir=tmp_path,
        )

    monkeypatch.setattr(
        recovery,
        "verify_recovery_archive",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RecoveryError("broken archive")),
    )
    monkeypatch.setattr(
        recovery,
        "_load_receipt",
        lambda *_args: (_ for _ in ()).throw(RecoveryError("broken receipt")),
    )
    with pytest.raises(RecoveryError, match="корректного backup receipt"):
        recovery.create_restore_drill(
            RecoveryDb(),  # type: ignore[arg-type]
            artifact_path=tmp_path / "missing",
            receipt_data=b"bad",
            organization=organization,  # type: ignore[arg-type]
            actor=actor,  # type: ignore[arg-type]
            settings=SimpleNamespace(version="test"),  # type: ignore[arg-type]
            cipher=SimpleNamespace(),  # type: ignore[arg-type]
            output_dir=tmp_path,
        )


def test_create_postgres_s3_encrypted_backup(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Проверить PostgreSQL, S3 metadata и успешный AGE encryption lifecycle."""

    policy = SimpleNamespace(require_encrypted_backup=True, require_trusted_signature=False)
    monkeypatch.setattr(recovery, "get_or_create_recovery_policy", lambda *_args, **_kwargs: policy)
    monkeypatch.setattr(recovery, "get_default_signing_key", lambda *_args, **_kwargs: object())

    def postgres_backup(_url: str, destination: Path) -> tuple[str, None]:
        """Создать фиктивный PostgreSQL custom dump."""

        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(b"dump")
        return "pg_dump_custom", None

    monkeypatch.setattr(recovery, "_postgres_backup", postgres_backup)
    monkeypatch.setattr(recovery, "sign_bytes", lambda *_args, **_kwargs: {})

    def write_zip(_source: Path, output: Path) -> None:
        """Создать фиктивный plaintext ZIP artifact."""

        output.write_bytes(b"zip")

    def encrypt(_source: Path, destination: Path, _recipient: str) -> None:
        """Создать фиктивный encrypted artifact."""

        destination.write_bytes(b"encrypted")

    monkeypatch.setattr(recovery, "_write_zip", write_zip)
    monkeypatch.setattr(recovery, "_encrypt_with_age", encrypt)
    monkeypatch.setattr(
        recovery,
        "import_backup_receipt",
        lambda *_args, **_kwargs: SimpleNamespace(backup_id="backup-id"),
    )
    settings = SimpleNamespace(
        recovery_age_recipient="age1recipient",
        database_url="postgresql://localhost/db",
        storage_backend="s3",
        storage_path=tmp_path / "storage",
        backups_path=tmp_path / "backups",
        version="test",
    )
    created = recovery.create_recovery_backup(
        RecoveryDb(),  # type: ignore[arg-type]
        organization=SimpleNamespace(id=ORG_ID),  # type: ignore[arg-type]
        actor=SimpleNamespace(id="actor"),  # type: ignore[arg-type]
        settings=settings,  # type: ignore[arg-type]
        cipher=SimpleNamespace(),  # type: ignore[arg-type]
        output_dir=tmp_path / "output",
    )
    assert created.artifact_path.suffix == ".age"
    assert created.manifest.database.kind == "postgresql"
    assert created.manifest.storage.backend == "s3"


def test_create_backup_rejects_unsupported_database(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Проверить явный отказ backend вне SQLite/PostgreSQL."""

    monkeypatch.setattr(
        recovery,
        "get_or_create_recovery_policy",
        lambda *_args, **_kwargs: SimpleNamespace(require_encrypted_backup=False),
    )
    monkeypatch.setattr(recovery, "get_default_signing_key", lambda *_args, **_kwargs: object())
    settings = SimpleNamespace(
        recovery_age_recipient=None,
        database_url="mysql://localhost/db",
    )
    with pytest.raises(RecoveryError, match="Неподдерживаемая база"):
        recovery.create_recovery_backup(
            RecoveryDb(),  # type: ignore[arg-type]
            organization=SimpleNamespace(id=ORG_ID),  # type: ignore[arg-type]
            actor=SimpleNamespace(id="actor"),  # type: ignore[arg-type]
            settings=settings,  # type: ignore[arg-type]
            cipher=SimpleNamespace(),  # type: ignore[arg-type]
            output_dir=tmp_path,
        )


def test_create_postgres_restore_requires_signing_key(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Проверить metadata-only PostgreSQL mode и обязательный ключ drill receipt."""

    postgres_receipt = _backup_receipt(
        database={"kind": "postgresql", "integrity_check": "pg_dump_custom", "size_bytes": 1}
    )
    monkeypatch.setattr(
        recovery,
        "verify_recovery_archive",
        lambda *_args, **_kwargs: (postgres_receipt.payload, SimpleNamespace(), []),
    )
    monkeypatch.setattr(
        recovery,
        "get_or_create_recovery_policy",
        lambda *_args, **_kwargs: SimpleNamespace(rto_minutes=60),
    )
    monkeypatch.setattr(recovery, "get_default_signing_key", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(recovery, "utcnow", lambda: NOW)
    with pytest.raises(RecoveryError, match="drill receipt требуется"):
        recovery.create_restore_drill(
            RecoveryDb(),  # type: ignore[arg-type]
            artifact_path=tmp_path / "unused",
            receipt_data=b"receipt",
            organization=SimpleNamespace(id=ORG_ID),  # type: ignore[arg-type]
            actor=SimpleNamespace(id="actor"),  # type: ignore[arg-type]
            settings=SimpleNamespace(version="test"),  # type: ignore[arg-type]
            cipher=SimpleNamespace(),  # type: ignore[arg-type]
            output_dir=tmp_path,
        )
