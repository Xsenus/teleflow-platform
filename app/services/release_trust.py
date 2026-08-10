from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.enums import ArtifactSignatureStatus, ChangeRequestType
from app.models import ChangeRequest, ReleaseAttestation, User, utcnow
from app.services.artifact_signing import (
    ArtifactSigningError,
    get_default_signing_key,
    sign_bytes,
    verify_signature,
)
from app.services.crypto import SecretCipher

RELEASE_ATTESTATION_PURPOSE = "teleflow-release-attestation-v1"
RELEASE_ATTESTATION_SCHEMA = 1


class ReleaseTrustError(RuntimeError):
    pass


def canonical_release_payload(payload: dict[str, Any]) -> bytes:
    """Преобразовать data for canonical release payload using the project's canonical
    representation.
    """
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )


def _sha256_file(path: Path) -> str | None:
    """Вычислить sha256 file. Канонический ввод обеспечивает детерминированное сравнение
    целостности между процессами.
    """
    if not path.exists() or not path.is_file():
        return None
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _requirements(path: Path) -> list[str]:
    """Реализовать внутренний этап requirements step. Вспомогательная функция сохраняет
    детерминированность и тестируемость процесса.
    """
    if not path.exists():
        return []
    result: list[str] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        item = raw.strip()
        if not item or item.startswith("#") or item.startswith("-"):
            continue
        result.append(item)
    return sorted(dict.fromkeys(result), key=str.lower)


def build_local_release_payload(settings: Settings, *, root: Path | None = None) -> dict[str, Any]:
    """Создать local release payload. Перед сохранением или возвратом нового значения проверяются
    связанные инварианты.
    """
    root = root or Path(__file__).resolve().parents[2]
    build_path = root / "BUILD_INFO.json"
    try:
        build_info = (
            json.loads(build_path.read_text(encoding="utf-8")) if build_path.exists() else {}
        )
    except (json.JSONDecodeError, UnicodeDecodeError):
        build_info = {}
    manifest_path = root / "MANIFEST.sha256"
    requirements_path = root / "requirements.txt"
    requirements_dev_path = root / "requirements-dev.txt"
    dependencies = _requirements(requirements_path)
    dev_dependencies = _requirements(requirements_dev_path)
    source_commit = (
        build_info.get("final_release_commit")
        or build_info.get("release_commit")
        or build_info.get("source_commit")
        or build_info.get("implementation_commit")
    )
    return {
        "schema_version": RELEASE_ATTESTATION_SCHEMA,
        "product": settings.app_name,
        "version": settings.version,
        "source_commit": source_commit,
        "release_date": build_info.get("release_date"),
        "build_info_sha256": _sha256_file(build_path),
        "manifest_sha256": _sha256_file(manifest_path),
        "requirements_sha256": _sha256_file(requirements_path),
        "requirements_dev_sha256": _sha256_file(requirements_dev_path),
        "dependencies": dependencies,
        "development_dependencies": dev_dependencies,
        "dependency_count": len(dependencies),
        "generated_from": "installed-release-tree",
    }


def create_local_release_attestation(
    db: Session,
    *,
    organization_id: str,
    user: User,
    settings: Settings,
    cipher: SecretCipher,
) -> ReleaseAttestation:
    """Создать local release attestation. Перед сохранением или возвратом нового значения
    проверяются связанные инварианты.
    """
    payload = build_local_release_payload(settings)
    data = canonical_release_payload(payload)
    digest = hashlib.sha256(data).hexdigest()
    existing = db.scalar(
        select(ReleaseAttestation).where(
            ReleaseAttestation.organization_id == organization_id,
            ReleaseAttestation.payload_sha256 == digest,
        )
    )
    if existing is not None:
        return existing
    key = get_default_signing_key(db, organization_id=organization_id, require_private=True)
    if key is None:
        raise ReleaseTrustError("Сначала создайте основной Ed25519-ключ подписи")
    try:
        envelope = sign_bytes(data, key=key, cipher=cipher, purpose=RELEASE_ATTESTATION_PURPOSE)
        verification = verify_signature(
            db,
            organization_id=organization_id,
            data=data,
            envelope=envelope,
            expected_purpose=RELEASE_ATTESTATION_PURPOSE,
        )
    except ArtifactSigningError as exc:
        raise ReleaseTrustError(str(exc)) from exc
    item = ReleaseAttestation(
        organization_id=organization_id,
        version=str(payload["version"]),
        source_commit=(
            str(payload.get("source_commit"))[:80] if payload.get("source_commit") else None
        ),
        payload=payload,
        payload_sha256=digest,
        signature_status=verification.status,
        signature_info=envelope,
        signer_fingerprint=verification.fingerprint,
        verified_at=utcnow() if verification.cryptographically_valid else None,
        created_by_id=user.id,
    )
    db.add(item)
    db.flush()
    return item


def import_release_attestation(
    db: Session,
    *,
    organization_id: str,
    user: User,
    payload: dict[str, Any],
    signature: dict[str, Any],
) -> ReleaseAttestation:
    """Создать release attestation. Перед сохранением или возвратом нового значения проверяются
    связанные инварианты.
    """
    if int(payload.get("schema_version") or 0) != RELEASE_ATTESTATION_SCHEMA:
        raise ReleaseTrustError("Неподдерживаемая версия release attestation")
    version = str(payload.get("version") or "").strip()
    product = str(payload.get("product") or "").strip()
    if not version or len(version) > 40:
        raise ReleaseTrustError("Некорректная версия release attestation")
    if product != "TeleFlow Platform":
        raise ReleaseTrustError("Release attestation относится к другому продукту")
    data = canonical_release_payload(payload)
    digest = hashlib.sha256(data).hexdigest()
    existing = db.scalar(
        select(ReleaseAttestation).where(
            ReleaseAttestation.organization_id == organization_id,
            ReleaseAttestation.payload_sha256 == digest,
        )
    )
    if existing is not None:
        return existing
    verification = verify_signature(
        db,
        organization_id=organization_id,
        data=data,
        envelope=signature,
        expected_purpose=RELEASE_ATTESTATION_PURPOSE,
    )
    if not verification.cryptographically_valid or verification.signer_revoked:
        raise ReleaseTrustError(
            verification.error or "Недействительная подпись release attestation"
        )
    item = ReleaseAttestation(
        organization_id=organization_id,
        version=version,
        source_commit=(
            str(payload.get("source_commit"))[:80] if payload.get("source_commit") else None
        ),
        payload=payload,
        payload_sha256=digest,
        signature_status=verification.status,
        signature_info=signature,
        signer_fingerprint=verification.fingerprint,
        verified_at=utcnow(),
        created_by_id=user.id,
    )
    db.add(item)
    db.flush()
    return item


def verify_release_attestation(db: Session, item: ReleaseAttestation) -> bool:
    """Проверить release attestation. Некорректные данные или состояние отклоняются до побочного
    эффекта.
    """
    data = canonical_release_payload(item.payload)
    digest = hashlib.sha256(data).hexdigest()
    verification = verify_signature(
        db,
        organization_id=item.organization_id,
        data=data,
        envelope=item.signature_info,
        expected_purpose=RELEASE_ATTESTATION_PURPOSE,
    )
    item.payload_sha256 = digest
    item.signature_status = verification.status
    item.signer_fingerprint = verification.fingerprint
    item.verified_at = utcnow() if verification.cryptographically_valid else None
    return (
        verification.cryptographically_valid
        and verification.trusted
        and not verification.signer_revoked
    )


def validate_change_release_attestation(
    db: Session,
    *,
    change: ChangeRequest,
    settings: Settings,
) -> tuple[bool, str]:
    """Проверить change release attestation. Некорректные данные или состояние отклоняются до
    побочного эффекта.
    """
    if change.change_type != ChangeRequestType.UPGRADE:
        return True, "Для этого типа изменения release attestation не требуется."
    if not settings.require_trusted_release_attestation:
        return True, "Проверка доверенного release attestation отключена для этой среды."
    if not change.release_attestation_id:
        return False, "Для production-обновления требуется доверенный release attestation."
    item = db.scalar(
        select(ReleaseAttestation).where(
            ReleaseAttestation.id == change.release_attestation_id,
            ReleaseAttestation.organization_id == change.organization_id,
        )
    )
    if item is None:
        return False, "Release attestation не найден в текущей организации."
    if item.version != change.target_version:
        return (
            False,
            f"Release attestation относится к версии {item.version}, а ожидается {change.target_version}.",
        )
    trusted = verify_release_attestation(db, item)
    if not trusted or item.signature_status != ArtifactSignatureStatus.VALID_TRUSTED:
        return False, "Release attestation не подтверждён доверенным активным Ed25519-ключом."
    return True, "Release attestation доверен и соответствует целевой версии."
