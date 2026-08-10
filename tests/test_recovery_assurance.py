from __future__ import annotations

import io
import json
import zipfile
from datetime import timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.enums import (
    ArtifactSignatureStatus,
    ReadinessStatus,
    RecoveryBackupStatus,
    RecoveryDrillMode,
    RecoveryDrillStatus,
    UserRole,
)
from app.models import Organization, RecoveryBackupEvidence, RecoveryPolicy, User, utcnow
from app.security import hash_password
from app.services.artifact_signing import generate_signing_key, get_default_signing_key, sign_bytes
from app.services.privacy import RetentionService
from app.services.recovery import (
    DRILL_RECEIPT_PURPOSE,
    RecoveryBackupManifest,
    RecoveryBackupReceipt,
    RecoveryCheck,
    RecoveryDrillReceipt,
    RecoveryDrillReceiptPayload,
    RecoveryError,
    _safe_zip_members,
    canonical_json_bytes,
    create_recovery_backup,
    create_restore_drill,
    evaluate_recovery_compliance,
    import_backup_receipt,
    import_drill_receipt,
    verify_recovery_archive,
)
from tests.conftest import csrf_headers


def _actor_and_org(client: TestClient, db):
    """Реализовать внутренний этап actor and org step. Вспомогательная функция сохраняет
    детерминированность и тестируемость процесса.
    """
    actor = db.scalar(select(User).where(User.email == "owner@example.com"))
    assert actor is not None
    organization = db.get(Organization, actor.organization_id)
    assert organization is not None
    return actor, organization


def _ensure_signing_key(client: TestClient, db, actor: User) -> None:
    """Реализовать внутренний этап ensure signing key step. Вспомогательная функция сохраняет
    детерминированность и тестируемость процесса.
    """
    generate_signing_key(
        db,
        organization_id=actor.organization_id,
        created_by=actor,
        cipher=client.app.state.cipher,
        name="Recovery test key",
        make_default=True,
        trusted_for_import=True,
        note="Isolated recovery test key",
    )


def _create_backup(client: TestClient, tmp_path: Path):
    """Реализовать внутренний этап create backup step. Вспомогательная функция сохраняет
    детерминированность и тестируемость процесса.
    """
    settings = client.app.state.settings
    with client.app.state.session_factory() as db:
        actor, organization = _actor_and_org(client, db)
        _ensure_signing_key(client, db, actor)
        policy = RecoveryPolicy(
            organization_id=organization.id,
            enabled=True,
            rpo_hours=24,
            rto_minutes=60,
            restore_drill_max_age_days=30,
            minimum_retained_backups=1,
            require_encrypted_backup=False,
            require_trusted_signature=True,
            require_restore_drill=True,
            created_by_id=actor.id,
            updated_by_id=actor.id,
        )
        db.add(policy)
        db.flush()
        created = create_recovery_backup(
            db,
            organization=organization,
            actor=actor,
            settings=settings,
            cipher=client.app.state.cipher,
            output_dir=tmp_path / "recovery",
            include_storage=True,
        )
        db.commit()
        artifact = created.artifact_path
        receipt = created.receipt_path
        backup_id = created.evidence.backup_id
    return artifact, receipt, backup_id


def _second_tenant(client: TestClient) -> tuple[TestClient, str]:
    """Реализовать внутренний этап second tenant step. Вспомогательная функция сохраняет
    детерминированность и тестируемость процесса.
    """
    email = "recovery-viewer-owner@example.com"
    password = "RecoveryOwner_123!"
    with client.app.state.session_factory() as db:
        organization = Organization(
            name="Recovery Tenant",
            slug="recovery-tenant",
            timezone_name="Europe/Amsterdam",
        )
        db.add(organization)
        db.flush()
        db.add(
            User(
                organization_id=organization.id,
                email=email,
                display_name="Recovery Tenant Owner",
                password_hash=hash_password(password, client.app.state.settings),
                role=UserRole.OWNER,
                is_active=True,
                must_change_password=False,
            )
        )
        organization_id = organization.id
        db.commit()
    other = TestClient(client.app)
    login = other.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert login.status_code == 200, login.text
    return other, organization_id


def _viewer_client(client: TestClient) -> TestClient:
    """Реализовать внутренний этап viewer client step. Вспомогательная функция сохраняет
    детерминированность и тестируемость процесса.
    """
    email = "recovery-viewer@example.com"
    password = "RecoveryViewer_123!"
    with client.app.state.session_factory() as db:
        owner = db.scalar(select(User).where(User.email == "owner@example.com"))
        assert owner is not None
        db.add(
            User(
                organization_id=owner.organization_id,
                email=email,
                display_name="Recovery Viewer",
                password_hash=hash_password(password, client.app.state.settings),
                role=UserRole.VIEWER,
                is_active=True,
                must_change_password=False,
            )
        )
        db.commit()
    viewer = TestClient(client.app)
    login = viewer.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert login.status_code == 200, login.text
    return viewer


def test_backup_receipt_is_registered_then_archive_verification_promotes_it(
    auth_client: TestClient, tmp_path: Path
) -> None:
    """Проверить сценарий backup receipt is registered then archive verification promotes it. Тест
    завершается ошибкой при нарушении зафиксированного инварианта.
    """
    artifact, receipt_path, backup_id = _create_backup(auth_client, tmp_path)
    receipt_data = receipt_path.read_bytes()

    with auth_client.app.state.session_factory() as db:
        actor, organization = _actor_and_org(auth_client, db)
        evidence = db.scalar(
            select(RecoveryBackupEvidence).where(
                RecoveryBackupEvidence.organization_id == organization.id,
                RecoveryBackupEvidence.backup_id == backup_id,
            )
        )
        assert evidence is not None
        assert evidence.status == RecoveryBackupStatus.REGISTERED
        assert evidence.verified_at is None

        payload, manifest, checks = verify_recovery_archive(
            db,
            artifact_path=artifact,
            receipt_data=receipt_data,
            organization_id=organization.id,
            actor=actor,
            settings=auth_client.app.state.settings,
        )
        assert payload.backup_id == backup_id
        assert manifest.database.kind == "sqlite"
        assert any(item["code"] == "database_restore" for item in checks)
        assert evidence.status == RecoveryBackupStatus.VERIFIED
        assert evidence.verified_at is not None
        db.commit()


def test_sqlite_restore_drill_and_recovery_compliance_pass(
    auth_client: TestClient, tmp_path: Path
) -> None:
    """Проверить сценарий sqlite restore drill and recovery compliance pass. Тест завершается
    ошибкой при нарушении зафиксированного инварианта.
    """
    artifact, receipt_path, backup_id = _create_backup(auth_client, tmp_path)
    with auth_client.app.state.session_factory() as db:
        actor, organization = _actor_and_org(auth_client, db)
        result = create_restore_drill(
            db,
            artifact_path=artifact,
            receipt_data=receipt_path.read_bytes(),
            organization=organization,
            actor=actor,
            settings=auth_client.app.state.settings,
            cipher=auth_client.app.state.cipher,
            output_dir=tmp_path / "drills",
        )
        assert result.drill.status == RecoveryDrillStatus.PASSED
        assert result.drill.mode == RecoveryDrillMode.SQLITE_ISOLATED
        assert result.drill.backup_id == backup_id
        assert result.drill.rto_met is True

        _, compliance = evaluate_recovery_compliance(
            db,
            organization_id=organization.id,
            actor=actor,
            settings=auth_client.app.state.settings,
        )
        assert compliance.status in {ReadinessStatus.PASSED, ReadinessStatus.WARNING}
        assert not compliance.blockers
        db.commit()
    assert result.receipt_path.exists()


def test_tampered_artifact_does_not_become_verified(
    auth_client: TestClient, tmp_path: Path
) -> None:
    """Проверить сценарий tampered artifact does not become verified. Тест завершается ошибкой при
    нарушении зафиксированного инварианта.
    """
    artifact, receipt_path, backup_id = _create_backup(auth_client, tmp_path)
    artifact.write_bytes(artifact.read_bytes() + b"tamper")

    with auth_client.app.state.session_factory() as db:
        actor, organization = _actor_and_org(auth_client, db)
        with pytest.raises(RecoveryError, match="Размер|SHA-256"):
            verify_recovery_archive(
                db,
                artifact_path=artifact,
                receipt_data=receipt_path.read_bytes(),
                organization_id=organization.id,
                actor=actor,
                settings=auth_client.app.state.settings,
            )
        evidence = db.scalar(
            select(RecoveryBackupEvidence).where(
                RecoveryBackupEvidence.organization_id == organization.id,
                RecoveryBackupEvidence.backup_id == backup_id,
            )
        )
        assert evidence is not None
        assert evidence.status == RecoveryBackupStatus.REGISTERED
        assert evidence.verified_at is None


def test_failed_drill_is_signed_and_keeps_corrupt_backup_out_of_rpo(
    auth_client: TestClient, tmp_path: Path
) -> None:
    """Проверить сценарий failed drill is signed and keeps corrupt backup out of rpo. Тест
    завершается ошибкой при нарушении зафиксированного инварианта.
    """
    artifact, receipt_path, _ = _create_backup(auth_client, tmp_path)
    artifact.write_bytes(artifact.read_bytes() + b"broken")
    with auth_client.app.state.session_factory() as db:
        actor, organization = _actor_and_org(auth_client, db)
        result = create_restore_drill(
            db,
            artifact_path=artifact,
            receipt_data=receipt_path.read_bytes(),
            organization=organization,
            actor=actor,
            settings=auth_client.app.state.settings,
            cipher=auth_client.app.state.cipher,
            output_dir=tmp_path / "failed-drills",
        )
        assert result.drill.status == RecoveryDrillStatus.FAILED
        assert result.drill.signature_status == ArtifactSignatureStatus.VALID_TRUSTED
        assert result.drill.blockers
        _, compliance = evaluate_recovery_compliance(
            db,
            organization_id=organization.id,
            actor=actor,
            settings=auth_client.app.state.settings,
        )
        assert compliance.status == ReadinessStatus.BLOCKED
        assert any(
            "backup" in item.lower() or "коп" in item.lower() for item in compliance.blockers
        )


def test_receipt_tamper_and_cross_tenant_are_rejected(
    auth_client: TestClient, tmp_path: Path
) -> None:
    """Проверить сценарий receipt tamper and cross tenant are rejected. Тест завершается ошибкой
    при нарушении зафиксированного инварианта.
    """
    _, receipt_path, _ = _create_backup(auth_client, tmp_path)
    raw = json.loads(receipt_path.read_text("utf-8"))
    raw["payload"]["artifact"]["size_bytes"] += 1
    tampered = canonical_json_bytes(raw)

    with auth_client.app.state.session_factory() as db:
        actor, organization = _actor_and_org(auth_client, db)
        with pytest.raises(RecoveryError):
            import_backup_receipt(
                db,
                data=tampered,
                organization_id=organization.id,
                actor=actor,
                settings=auth_client.app.state.settings,
            )

    other, other_org_id = _second_tenant(auth_client)
    try:
        with auth_client.app.state.session_factory() as db:
            other_actor = db.scalar(select(User).where(User.organization_id == other_org_id))
            assert other_actor is not None
            with pytest.raises(RecoveryError, match="другой организации"):
                import_backup_receipt(
                    db,
                    data=receipt_path.read_bytes(),
                    organization_id=other_org_id,
                    actor=other_actor,
                    settings=auth_client.app.state.settings,
                )
    finally:
        other.close()


def test_unsafe_zip_paths_are_rejected() -> None:
    """Проверить сценарий unsafe zip paths are rejected. Тест завершается ошибкой при нарушении
    зафиксированного инварианта.
    """
    payload = io.BytesIO()
    with zipfile.ZipFile(payload, "w") as archive:
        archive.writestr("../escape.txt", b"no")
    with zipfile.ZipFile(io.BytesIO(payload.getvalue()), "r") as archive:
        with pytest.raises(RecoveryError, match="небезопасный путь"):
            _safe_zip_members(archive)

    windows_path = io.BytesIO()
    with zipfile.ZipFile(windows_path, "w") as archive:
        archive.writestr("C:\\escape.txt", b"no")
    with zipfile.ZipFile(io.BytesIO(windows_path.getvalue()), "r") as archive:
        with pytest.raises(RecoveryError, match="небезопасный путь"):
            _safe_zip_members(archive)


def test_recovery_api_metadata_only_and_rbac(auth_client: TestClient, tmp_path: Path) -> None:
    """Проверить сценарий recovery api metadata only and rbac. Тест завершается ошибкой при
    нарушении зафиксированного инварианта.
    """
    artifact, receipt_path, _ = _create_backup(auth_client, tmp_path)
    imported = auth_client.post(
        "/api/v1/recovery/backups/import",
        headers=csrf_headers(auth_client),
        files={"file": ("backup.receipt.json", receipt_path.read_bytes(), "application/json")},
    )
    assert imported.status_code == 201, imported.text
    body = imported.json()
    assert body["status"] == "registered"
    assert "artifact_path" not in body
    assert "private" not in json.dumps(body).lower()

    status = auth_client.get("/api/v1/recovery/status")
    assert status.status_code == 200, status.text
    assert status.json()["status"] == "blocked"

    viewer = _viewer_client(auth_client)
    try:
        denied_policy = viewer.patch(
            "/api/v1/recovery/policy",
            headers=csrf_headers(viewer),
            json={"rpo_hours": 12},
        )
        assert denied_policy.status_code == 403
        denied_import = viewer.post(
            "/api/v1/recovery/backups/import",
            headers=csrf_headers(viewer),
            files={"file": ("receipt.json", receipt_path.read_bytes(), "application/json")},
        )
        assert denied_import.status_code == 403
        read_allowed = viewer.get("/api/v1/recovery/backups")
        assert read_allowed.status_code == 200
    finally:
        viewer.close()


def test_expired_backup_and_drill_break_compliance(auth_client: TestClient, tmp_path: Path) -> None:
    """Проверить сценарий expired backup and drill break compliance. Тест завершается ошибкой при
    нарушении зафиксированного инварианта.
    """
    artifact, receipt_path, _ = _create_backup(auth_client, tmp_path)
    with auth_client.app.state.session_factory() as db:
        actor, organization = _actor_and_org(auth_client, db)
        result = create_restore_drill(
            db,
            artifact_path=artifact,
            receipt_data=receipt_path.read_bytes(),
            organization=organization,
            actor=actor,
            settings=auth_client.app.state.settings,
            cipher=auth_client.app.state.cipher,
            output_dir=tmp_path / "drills-old",
        )
        evidence = result.drill
        backup = db.get(RecoveryBackupEvidence, evidence.backup_evidence_id)
        assert backup is not None
        backup.backup_created_at = utcnow() - timedelta(hours=72)
        evidence.completed_at = utcnow() - timedelta(days=60)
        _, compliance = evaluate_recovery_compliance(
            db,
            organization_id=organization.id,
            actor=actor,
            settings=auth_client.app.state.settings,
        )
        assert compliance.status == ReadinessStatus.BLOCKED
        codes = {item["code"] for item in compliance.checks if item["status"] == "blocked"}
        assert "recovery_rpo" in codes
        assert "recovery_restore_drill" in codes


def test_future_receipt_is_rejected_even_when_structurally_valid(
    auth_client: TestClient, tmp_path: Path
) -> None:
    """Проверить сценарий future receipt is rejected even when structurally valid. Тест завершается
    ошибкой при нарушении зафиксированного инварианта.
    """
    _, receipt_path, _ = _create_backup(auth_client, tmp_path)
    parsed = RecoveryBackupReceipt.model_validate_json(receipt_path.read_bytes())
    raw = parsed.model_dump(mode="json", exclude_none=False)
    raw["payload"]["created_at"] = (utcnow() + timedelta(hours=1)).isoformat()
    # Signature is now invalid too, but the time boundary is checked first to avoid future RPO evidence.
    with auth_client.app.state.session_factory() as db:
        actor, organization = _actor_and_org(auth_client, db)
        with pytest.raises(RecoveryError, match="будущем"):
            import_backup_receipt(
                db,
                data=canonical_json_bytes(raw),
                organization_id=organization.id,
                actor=actor,
                settings=auth_client.app.state.settings,
            )


def test_passed_drill_receipt_requires_verified_backup(
    auth_client: TestClient, tmp_path: Path
) -> None:
    """Проверить сценарий passed drill receipt requires verified backup. Тест завершается ошибкой
    при нарушении зафиксированного инварианта.
    """
    _, receipt_path, backup_id = _create_backup(auth_client, tmp_path)
    backup_receipt = RecoveryBackupReceipt.model_validate_json(receipt_path.read_bytes())
    now = utcnow()
    with auth_client.app.state.session_factory() as db:
        actor, organization = _actor_and_org(auth_client, db)
        key = get_default_signing_key(db, organization_id=organization.id, require_private=True)
        assert key is not None
        payload = RecoveryDrillReceiptPayload(
            organization_id=organization.id,
            drill_id="verified-required-drill",
            backup_id=backup_id,
            backup_artifact_sha256=backup_receipt.payload.artifact.sha256,
            product_version=auth_client.app.state.settings.version,
            mode=RecoveryDrillMode.SQLITE_ISOLATED,
            status=RecoveryDrillStatus.PASSED,
            started_at=now,
            completed_at=now,
            duration_seconds=0,
            target_rto_minutes=60,
            checks=[
                RecoveryCheck(
                    code="synthetic",
                    status="passed",
                    message="Synthetic signed drill",
                )
            ],
        )
        payload_bytes = canonical_json_bytes(payload.model_dump(mode="json"))
        receipt = RecoveryDrillReceipt(
            payload=payload,
            signature=sign_bytes(
                payload_bytes,
                key=key,
                cipher=auth_client.app.state.cipher,
                purpose=DRILL_RECEIPT_PURPOSE,
                created_at=now,
            ),
        )
        with pytest.raises(RecoveryError, match="физически проверенного"):
            import_drill_receipt(
                db,
                data=canonical_json_bytes(receipt.model_dump(mode="json", exclude_none=False)),
                organization_id=organization.id,
                actor=actor,
                settings=auth_client.app.state.settings,
            )


def test_expired_evidence_is_not_counted_as_retained_or_current(
    auth_client: TestClient, tmp_path: Path
) -> None:
    """Проверить сценарий expired evidence is not counted as retained or current. Тест завершается
    ошибкой при нарушении зафиксированного инварианта.
    """
    artifact, receipt_path, _ = _create_backup(auth_client, tmp_path)
    with auth_client.app.state.session_factory() as db:
        actor, organization = _actor_and_org(auth_client, db)
        verify_recovery_archive(
            db,
            artifact_path=artifact,
            receipt_data=receipt_path.read_bytes(),
            organization_id=organization.id,
            actor=actor,
            settings=auth_client.app.state.settings,
        )
        backup = db.scalar(
            select(RecoveryBackupEvidence).where(
                RecoveryBackupEvidence.organization_id == organization.id
            )
        )
        assert backup is not None
        backup.expires_at = utcnow() - timedelta(seconds=1)
        _, compliance = evaluate_recovery_compliance(
            db,
            organization_id=organization.id,
            actor=actor,
            settings=auth_client.app.state.settings,
        )
        assert compliance.latest_backup is None
        checks = {item["code"]: item for item in compliance.checks}
        assert checks["recovery_rpo"]["status"] == "blocked"
        assert checks["recovery_retention"]["details"]["actual"] == 0


def test_current_rto_policy_is_re_evaluated(auth_client: TestClient, tmp_path: Path) -> None:
    """Проверить сценарий current rto policy is re evaluated. Тест завершается ошибкой при
    нарушении зафиксированного инварианта.
    """
    artifact, receipt_path, _ = _create_backup(auth_client, tmp_path)
    with auth_client.app.state.session_factory() as db:
        actor, organization = _actor_and_org(auth_client, db)
        result = create_restore_drill(
            db,
            artifact_path=artifact,
            receipt_data=receipt_path.read_bytes(),
            organization=organization,
            actor=actor,
            settings=auth_client.app.state.settings,
            cipher=auth_client.app.state.cipher,
            output_dir=tmp_path / "rto-drill",
        )
        result.drill.duration_seconds = 120
        result.drill.rto_met = True
        policy = db.scalar(
            select(RecoveryPolicy).where(RecoveryPolicy.organization_id == organization.id)
        )
        assert policy is not None
        policy.rto_minutes = 1
        _, compliance = evaluate_recovery_compliance(
            db,
            organization_id=organization.id,
            actor=actor,
            settings=auth_client.app.state.settings,
        )
        drill_check = next(
            item for item in compliance.checks if item["code"] == "recovery_restore_drill"
        )
        assert drill_check["status"] == "blocked"
        assert drill_check["details"]["rto_met_current_policy"] is False
        assert drill_check["details"]["signed_rto_met"] is True


def test_production_metadata_only_drill_does_not_satisfy_recovery_policy(
    auth_client: TestClient, tmp_path: Path
) -> None:
    """Проверить сценарий production metadata only drill does not satisfy recovery policy. Тест
    завершается ошибкой при нарушении зафиксированного инварианта.
    """
    artifact, receipt_path, _ = _create_backup(auth_client, tmp_path)
    with auth_client.app.state.session_factory() as db:
        actor, organization = _actor_and_org(auth_client, db)
        result = create_restore_drill(
            db,
            artifact_path=artifact,
            receipt_data=receipt_path.read_bytes(),
            organization=organization,
            actor=actor,
            settings=auth_client.app.state.settings,
            cipher=auth_client.app.state.cipher,
            output_dir=tmp_path / "metadata-drill",
        )
        result.drill.mode = RecoveryDrillMode.METADATA_ONLY
        production_settings = auth_client.app.state.settings.model_copy(
            update={"environment": "production"}
        )
        _, compliance = evaluate_recovery_compliance(
            db,
            organization_id=organization.id,
            actor=actor,
            settings=production_settings,
        )
        drill_check = next(
            item for item in compliance.checks if item["code"] == "recovery_restore_drill"
        )
        assert drill_check["status"] == "blocked"
        assert drill_check["details"]["isolated_enough"] is False
        assert "metadata-only" in drill_check["message"]


def test_failed_age_encryption_removes_plaintext_archive(
    auth_client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Проверить сценарий failed age encryption removes plaintext archive. Тест завершается ошибкой
    при нарушении зафиксированного инварианта.
    """
    output = tmp_path / "encrypted-output"

    def fail_encrypt(*_args, **_kwargs):
        """Выполнить операцию fail encrypt. Аргументы интерпретируются в контексте модуля,
        результат возвращается вызывающему коду.
        """
        raise RecoveryError("age failed")

    monkeypatch.setattr("app.services.recovery._encrypt_with_age", fail_encrypt)
    with auth_client.app.state.session_factory() as db:
        actor, organization = _actor_and_org(auth_client, db)
        _ensure_signing_key(auth_client, db, actor)
        db.add(
            RecoveryPolicy(
                organization_id=organization.id,
                enabled=True,
                rpo_hours=24,
                rto_minutes=60,
                restore_drill_max_age_days=30,
                minimum_retained_backups=1,
                require_encrypted_backup=False,
                require_trusted_signature=True,
                require_restore_drill=True,
                created_by_id=actor.id,
                updated_by_id=actor.id,
            )
        )
        db.flush()
        with pytest.raises(RecoveryError, match="age failed"):
            create_recovery_backup(
                db,
                organization=organization,
                actor=actor,
                settings=auth_client.app.state.settings,
                cipher=auth_client.app.state.cipher,
                output_dir=output,
                include_storage=False,
                age_recipient="age1-test-recipient",
            )
    assert not list(output.glob("*.zip"))
    assert not list(output.glob("*.age"))


def test_recovery_manifest_rejects_duplicate_paths() -> None:
    """Проверить сценарий recovery manifest rejects duplicate paths. Тест завершается ошибкой при
    нарушении зафиксированного инварианта.
    """
    now = utcnow()
    file_claim = {
        "path": "database/teleflow.db",
        "sha256": "0" * 64,
        "size_bytes": 1,
    }
    with pytest.raises(ValueError, match="повторяющиеся пути"):
        RecoveryBackupManifest.model_validate(
            {
                "organization_id": "0" * 36,
                "backup_id": "duplicate-paths",
                "product_version": "1.8.0",
                "created_at": now,
                "database": {
                    "kind": "sqlite",
                    "path": "database/teleflow.db",
                    "sha256": "0" * 64,
                    "size_bytes": 1,
                    "integrity_check": "ok",
                },
                "storage": {
                    "backend": "local",
                    "included": False,
                    "file_count": 0,
                    "total_bytes": 0,
                },
                "files": [file_claim, file_claim],
            }
        )


def test_retention_marks_expired_recovery_evidence(auth_client: TestClient, tmp_path: Path) -> None:
    """Проверить сценарий retention marks expired recovery evidence. Тест завершается ошибкой при
    нарушении зафиксированного инварианта.
    """
    artifact, receipt_path, _ = _create_backup(auth_client, tmp_path)
    with auth_client.app.state.session_factory() as db:
        actor, organization = _actor_and_org(auth_client, db)
        verify_recovery_archive(
            db,
            artifact_path=artifact,
            receipt_data=receipt_path.read_bytes(),
            organization_id=organization.id,
            actor=actor,
            settings=auth_client.app.state.settings,
        )
        backup = db.scalar(
            select(RecoveryBackupEvidence).where(
                RecoveryBackupEvidence.organization_id == organization.id
            )
        )
        assert backup is not None
        backup.expires_at = utcnow() - timedelta(seconds=1)
        db.commit()
        backup_id = backup.id

    retention = RetentionService(
        auth_client.app.state.session_factory,
        auth_client.app.state.settings,
        auth_client.app.state.storage,
    )
    stats = retention.run()
    assert stats["recovery_evidence_expired"] == 1
    with auth_client.app.state.session_factory() as db:
        backup = db.get(RecoveryBackupEvidence, backup_id)
        assert backup is not None
        assert backup.status == RecoveryBackupStatus.EXPIRED
