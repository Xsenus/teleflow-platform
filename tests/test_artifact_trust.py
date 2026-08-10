from __future__ import annotations

import hashlib
import io
import json
import subprocess
import sys
import zipfile
from pathlib import Path

from fastapi.testclient import TestClient

from app.enums import ArtifactSignatureStatus, ArtifactSigningKeyStatus, UserRole
from app.models import ArtifactSigningKey, Organization, User
from app.security import hash_password
from app.services.artifact_verifier import inspect_artifact
from app.services.crypto import SecretCipher
from app.services.key_rotation import rotate_master_key
from tests.conftest import csrf_headers
from tests.test_commissioning_portability import _prepare_local_pilot_evidence


def _generate_key(client: TestClient, *, name: str = "Основной ключ") -> dict:
    """Реализовать внутренний этап generate key step. Вспомогательная функция сохраняет
    детерминированность и тестируемость процесса.
    """
    response = client.post(
        "/api/v1/artifact-signing-keys/generate",
        headers=csrf_headers(client),
        json={
            "name": name,
            "make_default": True,
            "trusted_for_import": True,
            "note": "Ключ тестовой организации",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def _second_tenant(client: TestClient) -> TestClient:
    """Реализовать внутренний этап second tenant step. Вспомогательная функция сохраняет
    детерминированность и тестируемость процесса.
    """
    email = "artifact-owner@example.com"
    password = "ArtifactOwner_123!"
    with client.app.state.session_factory() as db:
        organization = Organization(
            name="Artifact Tenant",
            slug="artifact-tenant",
            timezone_name="Europe/Amsterdam",
        )
        db.add(organization)
        db.flush()
        db.add(
            User(
                organization_id=organization.id,
                email=email,
                display_name="Artifact Owner",
                password_hash=hash_password(password, client.app.state.settings),
                role=UserRole.OWNER,
                is_active=True,
                must_change_password=False,
            )
        )
        db.commit()
    other = TestClient(client.app)
    login = other.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert login.status_code == 200, login.text
    return other


def _resignless_repack(archive_bytes: bytes) -> bytes:
    """Изменить payload and manifest while deliberately keeping the old signature."""
    with zipfile.ZipFile(io.BytesIO(archive_bytes), "r") as archive:
        files = {info.filename: archive.read(info) for info in archive.infolist()}
    document = json.loads(files["bundle.json"])
    document["organization_reference"]["name"] = "Подменённая организация"
    files["bundle.json"] = json.dumps(
        document, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    manifest = json.loads(files["manifest.json"])
    manifest["files"]["bundle.json"] = hashlib.sha256(files["bundle.json"]).hexdigest()
    files["manifest.json"] = json.dumps(
        manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, payload in sorted(files.items()):
            archive.writestr(name, payload)
    return output.getvalue()


def _rewrite_signature_metadata(archive_bytes: bytes) -> bytes:
    """Реализовать внутренний этап rewrite signature metadata step. Вспомогательная функция
    сохраняет детерминированность и тестируемость процесса.
    """
    with zipfile.ZipFile(io.BytesIO(archive_bytes), "r") as archive:
        files = {info.filename: archive.read(info) for info in archive.infolist()}
    envelope = json.loads(files["SIGNATURE.json"])
    envelope["created_at"] = "2035-01-01T00:00:00+00:00"
    files["SIGNATURE.json"] = json.dumps(
        envelope, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, payload in sorted(files.items()):
            archive.writestr(name, payload)
    return output.getvalue()


def test_signing_key_lifecycle_hides_private_material(auth_client: TestClient) -> None:
    """Проверить сценарий signing key lifecycle hides private material. Тест завершается ошибкой
    при нарушении зафиксированного инварианта.
    """
    created = _generate_key(auth_client)
    assert created["algorithm"] == "Ed25519"
    assert created["has_private_key"] is True
    assert created["is_default"] is True
    assert created["trusted_for_import"] is True
    assert len(created["fingerprint"]) == 64
    serialized = json.dumps(created).lower()
    assert "private_key_enc" not in serialized
    assert "signature_b64" not in serialized

    listed = auth_client.get("/api/v1/artifact-signing-keys")
    assert listed.status_code == 200, listed.text
    assert listed.json()[0]["id"] == created["id"]
    assert "private_key_enc" not in json.dumps(listed.json()).lower()

    public = auth_client.get(f"/api/v1/artifact-signing-keys/{created['id']}/public")
    assert public.status_code == 200, public.text
    assert "BEGIN PUBLIC KEY" in public.json()["public_key_pem"]
    assert public.json()["fingerprint"] == created["fingerprint"]

    duplicate = auth_client.post(
        "/api/v1/artifact-signing-keys/import",
        headers=csrf_headers(auth_client),
        json={
            "name": "Дубликат",
            "public_key": public.json()["public_key_pem"],
            "trusted_for_import": True,
        },
    )
    assert duplicate.status_code == 409

    revoked = auth_client.post(
        f"/api/v1/artifact-signing-keys/{created['id']}/revoke",
        headers=csrf_headers(auth_client),
        json={"reason": "Плановая проверка отзыва тестового ключа"},
    )
    assert revoked.status_code == 200, revoked.text
    with auth_client.app.state.session_factory() as db:
        row = db.get(ArtifactSigningKey, created["id"])
        assert row is not None
        assert row.status == ArtifactSigningKeyStatus.REVOKED
        assert row.is_default is False
        assert row.trusted_for_import is False
        assert row.private_key_enc is None


def test_configuration_bundle_signature_detects_manifest_rewrite(
    auth_client: TestClient,
) -> None:
    """Проверить сценарий configuration bundle signature detects manifest rewrite. Тест завершается
    ошибкой при нарушении зафиксированного инварианта.
    """
    key = _generate_key(auth_client)
    exported = auth_client.post(
        "/api/v1/configuration-bundles/export",
        headers=csrf_headers(auth_client),
        json={"include_media": False},
    )
    assert exported.status_code == 201, exported.text
    row = exported.json()
    assert row["signature_status"] == ArtifactSignatureStatus.VALID_TRUSTED.value
    assert row["signer_fingerprint"] == key["fingerprint"]

    download = auth_client.get(f"/api/v1/configuration-bundles/{row['id']}/download")
    assert download.status_code == 200, download.text
    assert download.headers["x-artifact-signature-status"] == "valid_trusted"
    with zipfile.ZipFile(io.BytesIO(download.content), "r") as archive:
        assert "SIGNATURE.json" in archive.namelist()
        envelope = json.loads(archive.read("SIGNATURE.json"))
        assert envelope["fingerprint_sha256"] == key["fingerprint"]
        assert envelope["purpose"] == "teleflow.configuration_bundle.manifest.v1"

    preview = auth_client.post(
        "/api/v1/configuration-bundles/preview",
        headers=csrf_headers(auth_client),
        files={"file": ("signed.zip", download.content, "application/zip")},
    )
    assert preview.status_code == 200, preview.text
    assert preview.json()["signature"]["status"] == "valid_trusted"

    inspected = auth_client.post(
        "/api/v1/artifact-signing-keys/verify-artifact",
        headers=csrf_headers(auth_client),
        files={"file": ("signed.zip", download.content, "application/zip")},
    )
    assert inspected.status_code == 200, inspected.text
    assert inspected.json()["artifact_type"] == "configuration_bundle"
    assert inspected.json()["signature"]["trusted"] is True

    metadata_tampered = _rewrite_signature_metadata(download.content)
    metadata_blocked = auth_client.post(
        "/api/v1/configuration-bundles/preview",
        headers=csrf_headers(auth_client),
        files={"file": ("metadata-tampered.zip", metadata_tampered, "application/zip")},
    )
    assert metadata_blocked.status_code == 422

    tampered = _resignless_repack(download.content)
    blocked = auth_client.post(
        "/api/v1/configuration-bundles/preview",
        headers=csrf_headers(auth_client),
        files={"file": ("tampered.zip", tampered, "application/zip")},
    )
    assert blocked.status_code == 422, blocked.text
    assert "подпис" in blocked.text.lower() or "sha-256" in blocked.text.lower()

    revoked = auth_client.post(
        f"/api/v1/artifact-signing-keys/{key['id']}/revoke",
        headers=csrf_headers(auth_client),
        json={"reason": "Компрометация ключа после формирования архива"},
    )
    assert revoked.status_code == 200, revoked.text
    rejected_after_revoke = auth_client.post(
        "/api/v1/configuration-bundles/preview",
        headers=csrf_headers(auth_client),
        files={"file": ("revoked.zip", download.content, "application/zip")},
    )
    assert rejected_after_revoke.status_code == 422
    assert "отозван" in rejected_after_revoke.text.lower()

    inspected_after_revoke = auth_client.post(
        "/api/v1/artifact-signing-keys/verify-artifact",
        headers=csrf_headers(auth_client),
        files={"file": ("revoked.zip", download.content, "application/zip")},
    )
    assert inspected_after_revoke.status_code == 200, inspected_after_revoke.text
    assert inspected_after_revoke.json()["signature"]["status"] == "revoked"
    assert inspected_after_revoke.json()["signature"]["signer_known"] is True
    assert inspected_after_revoke.json()["signature"]["signer_revoked"] is True


def test_cross_tenant_signature_requires_explicit_trust(auth_client: TestClient) -> None:
    """Проверить сценарий cross tenant signature requires explicit trust. Тест завершается ошибкой
    при нарушении зафиксированного инварианта.
    """
    source_key = _generate_key(auth_client)
    exported = auth_client.post(
        "/api/v1/configuration-bundles/export",
        headers=csrf_headers(auth_client),
        json={"include_media": False},
    ).json()
    archive = auth_client.get(f"/api/v1/configuration-bundles/{exported['id']}/download").content
    public = auth_client.get(f"/api/v1/artifact-signing-keys/{source_key['id']}/public").json()

    tenant = _second_tenant(auth_client)
    old_policy = auth_client.app.state.settings.artifact_signature_policy
    auth_client.app.state.settings.artifact_signature_policy = "require_trusted"
    try:
        blocked = tenant.post(
            "/api/v1/configuration-bundles/preview",
            headers=csrf_headers(tenant),
            files={"file": ("source.zip", archive, "application/zip")},
        )
        assert blocked.status_code == 422, blocked.text
        assert "довер" in blocked.text.lower()

        imported_key = tenant.post(
            "/api/v1/artifact-signing-keys/import",
            headers=csrf_headers(tenant),
            json={
                "name": "Доверенный ключ источника",
                "public_key": public["public_key_pem"],
                "trusted_for_import": True,
            },
        )
        assert imported_key.status_code == 201, imported_key.text
        assert imported_key.json()["has_private_key"] is False

        accepted = tenant.post(
            "/api/v1/configuration-bundles/preview",
            headers=csrf_headers(tenant),
            files={"file": ("source.zip", archive, "application/zip")},
        )
        assert accepted.status_code == 200, accepted.text
        assert accepted.json()["signature"]["status"] == "valid_trusted"
    finally:
        auth_client.app.state.settings.artifact_signature_policy = old_policy
        tenant.close()


def test_support_bundle_and_offline_cli_are_signed(auth_client: TestClient, tmp_path: Path) -> None:
    """Проверить сценарий support bundle and offline cli are signed. Тест завершается ошибкой при
    нарушении зафиксированного инварианта.
    """
    key = _generate_key(auth_client)
    created = auth_client.post(
        "/api/v1/pilot/support-bundles",
        headers=csrf_headers(auth_client),
        json={"reason": "Диагностика криптографической подписи"},
    )
    assert created.status_code == 201, created.text
    row = created.json()
    assert row["status"] == "ready"
    assert row["signature_status"] == "valid_trusted"
    assert row["signer_fingerprint"] == key["fingerprint"]
    download = auth_client.get(f"/api/v1/pilot/support-bundles/{row['id']}/download")
    assert download.status_code == 200, download.text

    inspected = inspect_artifact(download.content, trusted_fingerprints=[key["fingerprint"]])
    assert inspected["artifact_type"] == "support_bundle"
    assert inspected["integrity_valid"] is True
    assert inspected["signature"]["trusted"] is True

    artifact_path = tmp_path / "support.zip"
    artifact_path.write_bytes(download.content)
    command = subprocess.run(
        [
            sys.executable,
            "scripts/verify_artifact.py",
            str(artifact_path),
            "--trusted-fingerprint",
            key["fingerprint"],
            "--require-trusted",
            "--json",
        ],
        cwd=Path(__file__).resolve().parents[1],
        check=False,
        capture_output=True,
        text=True,
    )
    assert command.returncode == 0, command.stderr or command.stdout
    assert json.loads(command.stdout)["valid"] is True


def test_pilot_acceptance_report_has_verifiable_signature(
    auth_client: TestClient,
) -> None:
    """Проверить сценарий pilot acceptance report has verifiable signature. Тест завершается
    ошибкой при нарушении зафиксированного инварианта.
    """
    key = _generate_key(auth_client)
    program, _stage, _run_id = _prepare_local_pilot_evidence(auth_client)
    report = auth_client.get(f"/api/v1/pilot/programs/{program['id']}/acceptance-report")
    assert report.status_code == 200, report.text
    wrapper = report.json()
    assert wrapper["signature"]["fingerprint_sha256"] == key["fingerprint"]
    assert report.headers["x-artifact-signature-status"] == "valid_trusted"
    inspected = inspect_artifact(report.content, trusted_fingerprints=[key["fingerprint"]])
    assert inspected["artifact_type"] == "pilot_acceptance_report"
    assert inspected["signature"]["trusted"] is True


def test_master_key_rotation_reencrypts_artifact_private_key(
    auth_client: TestClient,
) -> None:
    """Проверить сценарий master key rotation reencrypts artifact private key. Тест завершается
    ошибкой при нарушении зафиксированного инварианта.
    """
    key = _generate_key(auth_client)
    with auth_client.app.state.session_factory() as db:
        before = db.get(ArtifactSigningKey, key["id"])
        assert before is not None and before.private_key_enc
        old_ciphertext = before.private_key_enc
        new_cipher = SecretCipher("new-artifact-master-key-with-sufficient-entropy-987654321")
        report = rotate_master_key(
            db,
            old_cipher=auth_client.app.state.cipher,
            new_cipher=new_cipher,
            storage=auth_client.app.state.storage,
            dry_run=False,
        )
        db.commit()
        db.expire_all()
        after = db.get(ArtifactSigningKey, key["id"])
        assert after is not None and after.private_key_enc
        assert after.private_key_enc != old_ciphertext
        assert report.fields["artifact_signing_key.private"] == 1
        encoded_private = new_cipher.decrypt(
            after.private_key_enc,
            context=f"artifact-signing-key:{after.id}:private",
        )
        assert encoded_private


def test_signing_key_mutations_require_admin(auth_client: TestClient) -> None:
    """Проверить сценарий signing key mutations require admin. Тест завершается ошибкой при
    нарушении зафиксированного инварианта.
    """
    created = auth_client.post(
        "/api/v1/users",
        headers=csrf_headers(auth_client),
        json={
            "email": "artifact-viewer@example.com",
            "display_name": "Artifact Viewer",
            "password": "ArtifactViewer_123!",
            "role": "viewer",
        },
    )
    assert created.status_code == 201, created.text
    viewer = TestClient(auth_client.app)
    login = viewer.post(
        "/api/v1/auth/login",
        json={
            "email": "artifact-viewer@example.com",
            "password": "ArtifactViewer_123!",
        },
    )
    assert login.status_code == 200, login.text
    try:
        assert viewer.get("/api/v1/artifact-signing-keys").status_code == 200
        denied = viewer.post(
            "/api/v1/artifact-signing-keys/generate",
            headers=csrf_headers(viewer),
            json={"name": "Недоступный ключ"},
        )
        assert denied.status_code == 403
    finally:
        viewer.close()


def test_signing_key_names_are_trimmed_and_blank_values_rejected(
    auth_client: TestClient,
) -> None:
    """Проверить сценарий signing key names are trimmed and blank values rejected. Тест завершается
    ошибкой при нарушении зафиксированного инварианта.
    """
    blank = auth_client.post(
        "/api/v1/artifact-signing-keys/generate",
        headers=csrf_headers(auth_client),
        json={"name": "   ", "make_default": True},
    )
    assert blank.status_code in {409, 422}, blank.text

    created = auth_client.post(
        "/api/v1/artifact-signing-keys/generate",
        headers=csrf_headers(auth_client),
        json={"name": "   Рабочий ключ   ", "make_default": True},
    )
    assert created.status_code == 201, created.text
    assert created.json()["name"] == "Рабочий ключ"

    invalid_patch = auth_client.patch(
        f"/api/v1/artifact-signing-keys/{created.json()['id']}",
        headers=csrf_headers(auth_client),
        json={"name": "  "},
    )
    assert invalid_patch.status_code == 422, invalid_patch.text


def test_signing_key_update_default_filter_and_missing_paths(auth_client: TestClient) -> None:
    """Проверить редактирование, смену основного ключа, фильтрацию и ответы для чужих идентификаторов."""
    first = _generate_key(auth_client, name="Первый ключ")
    second = _generate_key(auth_client, name="Второй ключ")

    updated = auth_client.patch(
        f"/api/v1/artifact-signing-keys/{first['id']}",
        headers=csrf_headers(auth_client),
        json={
            "name": "  Обновлённый ключ  ",
            "trusted_for_import": False,
            "note": "  Проверенная заметка  ",
        },
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["name"] == "Обновлённый ключ"
    assert updated.json()["trusted_for_import"] is False
    assert updated.json()["note"] == "Проверенная заметка"

    selected = auth_client.post(
        f"/api/v1/artifact-signing-keys/{first['id']}/default",
        headers=csrf_headers(auth_client),
    )
    assert selected.status_code == 200, selected.text
    assert selected.json()["is_default"] is True
    listed = auth_client.get("/api/v1/artifact-signing-keys?include_revoked=false")
    assert listed.status_code == 200, listed.text
    defaults = [item["id"] for item in listed.json() if item["is_default"]]
    assert defaults == [first["id"]]
    assert {item["id"] for item in listed.json()} == {first["id"], second["id"]}

    revoked = auth_client.post(
        f"/api/v1/artifact-signing-keys/{second['id']}/revoke",
        headers=csrf_headers(auth_client),
        json={"reason": "Проверка фильтрации отозванного ключа"},
    )
    assert revoked.status_code == 200, revoked.text
    active_only = auth_client.get("/api/v1/artifact-signing-keys?include_revoked=false")
    assert [item["id"] for item in active_only.json()] == [first["id"]]
    assert (
        auth_client.patch(
            f"/api/v1/artifact-signing-keys/{second['id']}",
            headers=csrf_headers(auth_client),
            json={"name": "Нельзя изменить"},
        ).status_code
        == 409
    )
    assert (
        auth_client.post(
            f"/api/v1/artifact-signing-keys/{second['id']}/default",
            headers=csrf_headers(auth_client),
        ).status_code
        == 409
    )

    for method, suffix in (
        ("get", "/public"),
        ("patch", ""),
        ("post", "/default"),
        ("post", "/revoke"),
    ):
        kwargs = {"headers": csrf_headers(auth_client)}
        if method == "patch":
            kwargs["json"] = {"name": "Несуществующий ключ"}
        if suffix == "/revoke":
            kwargs["json"] = {"reason": "Проверка отсутствующего ключа"}
        response = getattr(auth_client, method)(
            f"/api/v1/artifact-signing-keys/missing-key{suffix}", **kwargs
        )
        assert response.status_code == 404


def test_artifact_inspection_rejects_invalid_and_oversized_uploads(
    auth_client: TestClient,
) -> None:
    """Проверить отказ инспектора для повреждённого и превышающего лимит артефакта."""
    invalid = auth_client.post(
        "/api/v1/artifact-signing-keys/verify-artifact",
        headers=csrf_headers(auth_client),
        files={"file": ("broken.bin", b"not-an-artifact", "application/octet-stream")},
    )
    assert invalid.status_code == 422, invalid.text

    old_limit = auth_client.app.state.settings.max_export_bytes
    auth_client.app.state.settings.max_export_bytes = 4
    try:
        oversized = auth_client.post(
            "/api/v1/artifact-signing-keys/verify-artifact",
            headers=csrf_headers(auth_client),
            files={"file": ("large.bin", b"12345", "application/octet-stream")},
        )
        assert oversized.status_code == 413, oversized.text
    finally:
        auth_client.app.state.settings.max_export_bytes = old_limit


def test_acceptance_report_manifest_metadata_is_verified(
    auth_client: TestClient,
) -> None:
    """Проверить сценарий acceptance report manifest metadata is verified. Тест завершается ошибкой
    при нарушении зафиксированного инварианта.
    """
    key = _generate_key(auth_client)
    program, _stage, _run_id = _prepare_local_pilot_evidence(auth_client)
    report = auth_client.get(f"/api/v1/pilot/programs/{program['id']}/acceptance-report")
    assert report.status_code == 200, report.text
    original = report.json()

    wrong_algorithm = json.loads(json.dumps(original))
    wrong_algorithm["manifest"]["algorithm"] = "MD5"
    bad_algorithm = json.dumps(wrong_algorithm, ensure_ascii=False).encode("utf-8")
    try:
        inspect_artifact(bad_algorithm, trusted_fingerprints=[key["fingerprint"]])
    except Exception as exc:
        assert "sha-256" in str(exc).lower()
    else:
        raise AssertionError("Акт с неподдерживаемым алгоритмом должен быть отклонён")

    wrong_time = json.loads(json.dumps(original))
    wrong_time["manifest"]["generated_at"] = "2035-01-01T00:00:00+00:00"
    bad_time = json.dumps(wrong_time, ensure_ascii=False).encode("utf-8")
    try:
        inspect_artifact(bad_time, trusted_fingerprints=[key["fingerprint"]])
    except Exception as exc:
        assert "время" in str(exc).lower()
    else:
        raise AssertionError("Акт с расходящимся временем должен быть отклонён")


def test_require_trusted_policy_rejects_untrusted_default_signing_key(
    auth_client: TestClient,
) -> None:
    """Проверить сценарий require trusted policy rejects untrusted default signing key. Тест
    завершается ошибкой при нарушении зафиксированного инварианта.
    """
    key = auth_client.post(
        "/api/v1/artifact-signing-keys/generate",
        headers=csrf_headers(auth_client),
        json={
            "name": "Локальный недоверенный ключ",
            "make_default": True,
            "trusted_for_import": False,
        },
    )
    assert key.status_code == 201, key.text
    old_policy = auth_client.app.state.settings.artifact_signature_policy
    auth_client.app.state.settings.artifact_signature_policy = "require_trusted"
    try:
        exported = auth_client.post(
            "/api/v1/configuration-bundles/export",
            headers=csrf_headers(auth_client),
            json={"include_media": False},
        )
        assert exported.status_code == 400, exported.text
        assert "довер" in exported.text.lower()
    finally:
        auth_client.app.state.settings.artifact_signature_policy = old_policy
