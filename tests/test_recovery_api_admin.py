from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from app.models import RecoveryRestoreDrill
from app.services.recovery import MAX_RECEIPT_BYTES, create_restore_drill
from tests.conftest import csrf_headers
from tests.test_recovery_assurance import _actor_and_org, _create_backup


def test_recovery_policy_status_and_lists(auth_client: TestClient) -> None:
    """Проверить формы политики, compliance status и пустые списки evidence/drills."""

    policy = auth_client.get("/api/v1/recovery/policy")
    assert policy.status_code == 200, policy.text
    updated = auth_client.patch(
        "/api/v1/recovery/policy",
        headers=csrf_headers(auth_client),
        json={
            "enabled": True,
            "rpo_hours": 12,
            "rto_minutes": 45,
            "restore_drill_max_age_days": 14,
            "minimum_retained_backups": 2,
            "require_encrypted_backup": False,
            "require_trusted_signature": True,
            "require_restore_drill": True,
        },
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["rpo_hours"] == 12
    assert updated.json()["minimum_retained_backups"] == 2

    status = auth_client.get("/api/v1/recovery/status")
    assert status.status_code == 200
    assert status.json()["policy"]["id"] == policy.json()["id"]
    assert status.json()["status"] == "blocked"
    assert auth_client.get("/api/v1/recovery/backups", params={"limit": 0}).json() == []
    assert auth_client.get("/api/v1/recovery/drills", params={"limit": 999}).json() == []


def test_recovery_receipt_upload_validation(auth_client: TestClient) -> None:
    """Проверить пустые, слишком большие и некорректные backup/drill receipts."""

    headers = csrf_headers(auth_client)
    for endpoint in ("backups", "drills"):
        empty = auth_client.post(
            f"/api/v1/recovery/{endpoint}/import",
            headers=headers,
            files={"file": ("empty.json", b"", "application/json")},
        )
        assert empty.status_code == 400 and "пуст" in empty.text

        invalid = auth_client.post(
            f"/api/v1/recovery/{endpoint}/import",
            headers=headers,
            files={"file": ("invalid.json", b"not-json", "application/json")},
        )
        assert invalid.status_code == 400

        oversized = auth_client.post(
            f"/api/v1/recovery/{endpoint}/import",
            headers=headers,
            files={
                "file": (
                    "oversized.json",
                    b"x" * (MAX_RECEIPT_BYTES + 1),
                    "application/json",
                )
            },
        )
        assert oversized.status_code == 413 and "размер" in oversized.text


def test_recovery_drill_receipt_import_lifecycle(auth_client: TestClient, tmp_path: Path) -> None:
    """Создать реальный подписанный drill receipt и импортировать его через HTTP API."""

    artifact, receipt_path, backup_id = _create_backup(auth_client, tmp_path)
    with auth_client.app.state.session_factory() as db:
        actor, organization = _actor_and_org(auth_client, db)
        created = create_restore_drill(
            db,
            artifact_path=artifact,
            receipt_data=receipt_path.read_bytes(),
            organization=organization,
            actor=actor,
            settings=auth_client.app.state.settings,
            cipher=auth_client.app.state.cipher,
            output_dir=tmp_path / "drill-api",
        )
        receipt = created.receipt_path.read_bytes()
        drill_id = created.drill.drill_id
        db.delete(created.drill)
        db.commit()

    imported = auth_client.post(
        "/api/v1/recovery/drills/import",
        headers=csrf_headers(auth_client),
        files={"file": ("drill.receipt.json", receipt, "application/json")},
    )
    assert imported.status_code == 201, imported.text
    assert imported.json()["drill_id"] == drill_id
    assert imported.json()["backup_id"] == backup_id
    listed = auth_client.get("/api/v1/recovery/drills")
    assert listed.status_code == 200
    assert [item["drill_id"] for item in listed.json()] == [drill_id]
    with auth_client.app.state.session_factory() as db:
        assert db.get(RecoveryRestoreDrill, imported.json()["id"]) is not None
