from __future__ import annotations

import base64
import hashlib
import hmac
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.enums import ArtifactSignatureStatus, ArtifactSigningKeyStatus
from app.models import ArtifactSigningKey, Organization, User, new_id, utcnow
from app.services.crypto import SecretCipher

SIGNATURE_SCHEMA_VERSION = 1
SIGNATURE_ALGORITHM = "Ed25519"
SIGNATURE_FILENAME = "SIGNATURE.json"
_SIGNATURE_DOMAIN = b"TeleFlow-Artifact-Signature-v1\n"
_SIGNATURE_FIELDS = {
    "schema_version",
    "algorithm",
    "purpose",
    "key_id",
    "fingerprint_sha256",
    "public_key_b64",
    "signed_sha256",
    "created_at",
}


class ArtifactSigningError(RuntimeError):
    pass


@dataclass(frozen=True)
class SignatureVerification:
    status: ArtifactSignatureStatus
    signed: bool
    cryptographically_valid: bool
    trusted: bool
    fingerprint: str | None
    key_id: str | None
    purpose: str | None
    signer_known: bool
    signer_revoked: bool
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Преобразовать to dict класса SignatureVerification without changing the source object."""
        return {
            "status": self.status.value,
            "signed": self.signed,
            "cryptographically_valid": self.cryptographically_valid,
            "trusted": self.trusted,
            "fingerprint": self.fingerprint,
            "key_id": self.key_id,
            "purpose": self.purpose,
            "signer_known": self.signer_known,
            "signer_revoked": self.signer_revoked,
            "error": self.error,
        }


def _b64encode(value: bytes) -> str:
    """Реализовать внутренний этап b64encode step. Вспомогательная функция сохраняет
    детерминированность и тестируемость процесса.
    """
    return base64.b64encode(value).decode("ascii")


def _b64decode(value: str, *, field: str) -> bytes:
    """Реализовать внутренний этап b64decode step. Вспомогательная функция сохраняет
    детерминированность и тестируемость процесса.
    """
    try:
        return base64.b64decode(value.encode("ascii"), validate=True)
    except (ValueError, UnicodeEncodeError) as exc:
        raise ArtifactSigningError(f"Некорректное поле подписи: {field}") from exc


def _public_raw(public_key: Ed25519PublicKey) -> bytes:
    """Реализовать внутренний этап public raw step. Вспомогательная функция сохраняет
    детерминированность и тестируемость процесса.
    """
    return public_key.public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )


def _private_raw(private_key: Ed25519PrivateKey) -> bytes:
    """Реализовать внутренний этап private raw step. Вспомогательная функция сохраняет
    детерминированность и тестируемость процесса.
    """
    return private_key.private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption(),
    )


def public_fingerprint(public_raw: bytes) -> str:
    """Вычислить public fingerprint. Канонический ввод обеспечивает детерминированное сравнение
    целостности между процессами.
    """
    if len(public_raw) != 32:
        raise ArtifactSigningError("Публичный ключ Ed25519 должен содержать 32 байта")
    return hashlib.sha256(public_raw).hexdigest()


def public_key_pem(public_key_b64: str) -> str:
    """Выполнить операцию public key pem. Аргументы интерпретируются в контексте модуля, результат
    возвращается вызывающему коду.
    """
    raw = _b64decode(public_key_b64, field="public_key_b64")
    if len(raw) != 32:
        raise ArtifactSigningError("Публичный ключ Ed25519 должен содержать 32 байта")
    key = Ed25519PublicKey.from_public_bytes(raw)
    return key.public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode("ascii")


def parse_public_key(value: str) -> bytes:
    """Выполнить операцию parse public key. Аргументы интерпретируются в контексте модуля,
    результат возвращается вызывающему коду.
    """
    text = value.strip()
    if not text:
        raise ArtifactSigningError("Публичный ключ не заполнен")
    if "BEGIN PUBLIC KEY" in text:
        try:
            loaded = serialization.load_pem_public_key(text.encode("ascii"))
        except (ValueError, TypeError, UnicodeEncodeError) as exc:
            raise ArtifactSigningError("Некорректный PEM публичного ключа") from exc
        if not isinstance(loaded, Ed25519PublicKey):
            raise ArtifactSigningError("Поддерживаются только публичные ключи Ed25519")
        raw = _public_raw(loaded)
    else:
        raw = _b64decode(text, field="public_key")
    if len(raw) != 32:
        raise ArtifactSigningError("Публичный ключ Ed25519 должен содержать 32 байта")
    return raw


def _lock_organization(db: Session, organization_id: str) -> None:
    """Реализовать внутренний этап lock organization step. Вспомогательная функция сохраняет
    детерминированность и тестируемость процесса.
    """
    organization = db.scalar(
        select(Organization).where(Organization.id == organization_id).with_for_update()
    )
    if organization is None:
        raise ArtifactSigningError("Организация для ключа подписи не найдена")


def get_default_signing_key(
    db: Session,
    *,
    organization_id: str,
    require_private: bool = True,
) -> ArtifactSigningKey | None:
    """Прочитать default signing key. Значение возвращается без несвязанных изменений состояния."""
    stmt = select(ArtifactSigningKey).where(
        ArtifactSigningKey.organization_id == organization_id,
        ArtifactSigningKey.status == ArtifactSigningKeyStatus.ACTIVE,
        ArtifactSigningKey.is_default.is_(True),
    )
    if require_private:
        stmt = stmt.where(ArtifactSigningKey.private_key_enc.is_not(None))
    return db.scalar(stmt.order_by(ArtifactSigningKey.created_at.desc()).limit(1))


def generate_signing_key(
    db: Session,
    *,
    organization_id: str,
    created_by: User,
    cipher: SecretCipher,
    name: str,
    make_default: bool = True,
    trusted_for_import: bool = True,
    note: str | None = None,
) -> ArtifactSigningKey:
    """Выполнить операцию generate signing key. Аргументы интерпретируются в контексте модуля,
    результат возвращается вызывающему коду.
    """
    normalized_name = name.strip()
    if len(normalized_name) < 3:
        raise ArtifactSigningError("Название ключа должно содержать не менее 3 символов")
    private_key = Ed25519PrivateKey.generate()
    private_raw = _private_raw(private_key)
    public_raw = _public_raw(private_key.public_key())
    fingerprint = public_fingerprint(public_raw)
    item_id = new_id()
    key_id = f"ed25519-{fingerprint[:20]}"
    if make_default:
        _lock_organization(db, organization_id)
        db.execute(
            update(ArtifactSigningKey)
            .where(
                ArtifactSigningKey.organization_id == organization_id,
                ArtifactSigningKey.is_default.is_(True),
            )
            .values(is_default=False)
        )
    item = ArtifactSigningKey(
        id=item_id,
        organization_id=organization_id,
        name=normalized_name[:160],
        key_id=key_id,
        algorithm=SIGNATURE_ALGORITHM,
        fingerprint=fingerprint,
        public_key_b64=_b64encode(public_raw),
        private_key_enc=cipher.encrypt(
            _b64encode(private_raw), context=f"artifact-signing-key:{item_id}:private"
        ),
        status=ArtifactSigningKeyStatus.ACTIVE,
        trusted_for_import=trusted_for_import,
        is_default=make_default,
        created_by_id=created_by.id,
        note=(note or "").strip()[:2000] or None,
    )
    db.add(item)
    db.flush()
    return item


def import_trusted_public_key(
    db: Session,
    *,
    organization_id: str,
    created_by: User,
    name: str,
    public_key: str,
    trusted_for_import: bool = True,
    note: str | None = None,
) -> ArtifactSigningKey:
    """Создать trusted public key. Перед сохранением или возвратом нового значения проверяются
    связанные инварианты.
    """
    normalized_name = name.strip()
    if len(normalized_name) < 3:
        raise ArtifactSigningError("Название ключа должно содержать не менее 3 символов")
    raw = parse_public_key(public_key)
    fingerprint = public_fingerprint(raw)
    existing = db.scalar(
        select(ArtifactSigningKey).where(
            ArtifactSigningKey.organization_id == organization_id,
            ArtifactSigningKey.fingerprint == fingerprint,
        )
    )
    if existing is not None:
        raise ArtifactSigningError("Такой публичный ключ уже добавлен")
    item = ArtifactSigningKey(
        organization_id=organization_id,
        name=normalized_name[:160],
        key_id=f"ed25519-{fingerprint[:20]}",
        algorithm=SIGNATURE_ALGORITHM,
        fingerprint=fingerprint,
        public_key_b64=_b64encode(raw),
        private_key_enc=None,
        status=ArtifactSigningKeyStatus.ACTIVE,
        trusted_for_import=trusted_for_import,
        is_default=False,
        created_by_id=created_by.id,
        note=(note or "").strip()[:2000] or None,
    )
    db.add(item)
    db.flush()
    return item


def set_default_signing_key(
    db: Session,
    *,
    key: ArtifactSigningKey,
) -> None:
    """Обновить default signing key. Переход применяется только после проверки его предусловий."""
    if key.status != ArtifactSigningKeyStatus.ACTIVE:
        raise ArtifactSigningError("Отозванный ключ нельзя назначить основным")
    _lock_organization(db, key.organization_id)
    if not key.private_key_enc:
        raise ArtifactSigningError("Основным может быть только ключ с локальной приватной частью")
    db.execute(
        update(ArtifactSigningKey)
        .where(
            ArtifactSigningKey.organization_id == key.organization_id,
            ArtifactSigningKey.is_default.is_(True),
        )
        .values(is_default=False)
    )
    key.is_default = True
    db.flush()


def revoke_signing_key(
    db: Session,
    *,
    key: ArtifactSigningKey,
    revoked_by: User,
    note: str | None = None,
) -> None:
    """Безопасно выполнить revoke signing key. Зависимое состояние и видимые в аудите последствия
    обрабатываются согласованно.
    """
    if key.status == ArtifactSigningKeyStatus.REVOKED:
        return
    key.status = ArtifactSigningKeyStatus.REVOKED
    key.is_default = False
    key.trusted_for_import = False
    # Public metadata remains for historical verification; the encrypted
    # private signing material is destroyed on revoke and cannot be restored.
    key.private_key_enc = None
    key.revoked_by_id = revoked_by.id
    key.revoked_at = utcnow()
    if note:
        key.note = note.strip()[:2000]
    db.flush()


def _signature_input(metadata: dict[str, Any]) -> bytes:
    """Реализовать внутренний этап signature input step. Вспомогательная функция сохраняет
    детерминированность и тестируемость процесса.
    """
    canonical = json.dumps(
        metadata,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return _SIGNATURE_DOMAIN + canonical


def _signature_metadata(envelope: dict[str, Any]) -> dict[str, Any]:
    """Реализовать внутренний этап signature metadata step. Вспомогательная функция сохраняет
    детерминированность и тестируемость процесса.
    """
    unexpected = set(envelope) - (_SIGNATURE_FIELDS | {"signature_b64"})
    missing = _SIGNATURE_FIELDS - set(envelope)
    if unexpected:
        raise ArtifactSigningError(
            "Контейнер подписи содержит неподдерживаемые поля: " + ", ".join(sorted(unexpected))
        )
    if missing:
        raise ArtifactSigningError(
            "Контейнер подписи не содержит обязательные поля: " + ", ".join(sorted(missing))
        )
    return {field: envelope[field] for field in sorted(_SIGNATURE_FIELDS)}


def sign_bytes(
    data: bytes,
    *,
    key: ArtifactSigningKey,
    cipher: SecretCipher,
    purpose: str,
    created_at: datetime | None = None,
) -> dict[str, Any]:
    """Выполнить операцию sign bytes. Аргументы интерпретируются в контексте модуля, результат
    возвращается вызывающему коду.
    """
    if key.status != ArtifactSigningKeyStatus.ACTIVE:
        raise ArtifactSigningError("Ключ подписи отозван")
    if key.algorithm != SIGNATURE_ALGORITHM:
        raise ArtifactSigningError("Неподдерживаемый алгоритм ключа подписи")
    if not key.private_key_enc:
        raise ArtifactSigningError("У ключа отсутствует локальная приватная часть")
    encoded_private = cipher.decrypt(
        key.private_key_enc, context=f"artifact-signing-key:{key.id}:private"
    )
    private_raw = _b64decode(encoded_private, field="private_key")
    if len(private_raw) != 32:
        raise ArtifactSigningError("Приватный ключ Ed25519 повреждён")
    private_key = Ed25519PrivateKey.from_private_bytes(private_raw)
    actual_public = _public_raw(private_key.public_key())
    expected_public = _b64decode(key.public_key_b64, field="public_key_b64")
    if not hmac.compare_digest(actual_public, expected_public):
        raise ArtifactSigningError("Приватная и публичная части ключа не совпадают")
    timestamp = created_at or utcnow()
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=UTC)
    metadata = {
        "schema_version": SIGNATURE_SCHEMA_VERSION,
        "algorithm": SIGNATURE_ALGORITHM,
        "purpose": purpose,
        "key_id": key.key_id,
        "fingerprint_sha256": key.fingerprint,
        "public_key_b64": key.public_key_b64,
        "signed_sha256": hashlib.sha256(data).hexdigest(),
        "created_at": timestamp.astimezone(UTC).isoformat(),
    }
    signature = private_key.sign(_signature_input(metadata))
    return {**metadata, "signature_b64": _b64encode(signature)}


def verify_embedded_signature(
    *,
    data: bytes,
    envelope: dict[str, Any] | None,
    expected_purpose: str,
) -> SignatureVerification:
    """Проверить an embedded Ed25519 envelope without applying local trust. The public key is
    carried by the envelope, so this proves integrity and possession of the corresponding
    private key. Local trust is deliberately resolved separately by :func:`verify_signature`.
    """
    if not envelope:
        return SignatureVerification(
            status=ArtifactSignatureStatus.UNSIGNED,
            signed=False,
            cryptographically_valid=False,
            trusted=False,
            fingerprint=None,
            key_id=None,
            purpose=None,
            signer_known=False,
            signer_revoked=False,
        )
    try:
        metadata = _signature_metadata(envelope)
        if int(str(metadata.get("schema_version"))) != SIGNATURE_SCHEMA_VERSION:
            raise ArtifactSigningError("Неподдерживаемая версия контейнера подписи")
        if metadata.get("algorithm") != SIGNATURE_ALGORITHM:
            raise ArtifactSigningError("Поддерживается только Ed25519")
        purpose = str(metadata.get("purpose") or "")
        if purpose != expected_purpose:
            raise ArtifactSigningError("Назначение подписи не совпадает")
        public_raw = _b64decode(str(metadata.get("public_key_b64") or ""), field="public_key_b64")
        if len(public_raw) != 32:
            raise ArtifactSigningError("Публичный ключ Ed25519 повреждён")
        fingerprint = public_fingerprint(public_raw)
        claimed_fingerprint = str(metadata.get("fingerprint_sha256") or "")
        if not hmac.compare_digest(fingerprint, claimed_fingerprint):
            raise ArtifactSigningError("Fingerprint публичного ключа не совпадает")
        claimed_digest = str(metadata.get("signed_sha256") or "")
        actual_digest = hashlib.sha256(data).hexdigest()
        if not hmac.compare_digest(actual_digest, claimed_digest):
            raise ArtifactSigningError("SHA-256 подписанного содержимого не совпадает")
        key_id = str(metadata.get("key_id") or "")
        if not key_id or len(key_id) > 80:
            raise ArtifactSigningError("Некорректный key_id контейнера подписи")
        try:
            signed_at = datetime.fromisoformat(str(metadata.get("created_at") or ""))
        except ValueError as exc:
            raise ArtifactSigningError("Некорректное время создания подписи") from exc
        if signed_at.tzinfo is None:
            raise ArtifactSigningError("Время подписи должно содержать часовой пояс")
        signature = _b64decode(str(envelope.get("signature_b64") or ""), field="signature_b64")
        Ed25519PublicKey.from_public_bytes(public_raw).verify(signature, _signature_input(metadata))
    except (ArtifactSigningError, InvalidSignature, ValueError, TypeError) as exc:
        return SignatureVerification(
            status=ArtifactSignatureStatus.INVALID,
            signed=True,
            cryptographically_valid=False,
            trusted=False,
            fingerprint=str(envelope.get("fingerprint_sha256") or "") or None,
            key_id=str(envelope.get("key_id") or "") or None,
            purpose=str(envelope.get("purpose") or "") or None,
            signer_known=False,
            signer_revoked=False,
            error=str(exc) or type(exc).__name__,
        )

    return SignatureVerification(
        status=ArtifactSignatureStatus.VALID_UNTRUSTED,
        signed=True,
        cryptographically_valid=True,
        trusted=False,
        fingerprint=fingerprint,
        key_id=str(envelope.get("key_id") or "") or None,
        purpose=str(envelope.get("purpose") or "") or None,
        signer_known=False,
        signer_revoked=False,
    )


def verify_signature(
    db: Session,
    *,
    organization_id: str,
    data: bytes,
    envelope: dict[str, Any] | None,
    expected_purpose: str,
) -> SignatureVerification:
    """Проверить signature. Некорректные данные или состояние отклоняются до побочного эффекта."""
    embedded = verify_embedded_signature(
        data=data, envelope=envelope, expected_purpose=expected_purpose
    )
    if not embedded.cryptographically_valid:
        return embedded
    assert embedded.fingerprint is not None
    known = db.scalar(
        select(ArtifactSigningKey).where(
            ArtifactSigningKey.organization_id == organization_id,
            ArtifactSigningKey.fingerprint == embedded.fingerprint,
        )
    )
    revoked = bool(known and known.status == ArtifactSigningKeyStatus.REVOKED)
    trusted = bool(
        known and known.status == ArtifactSigningKeyStatus.ACTIVE and known.trusted_for_import
    )
    status = (
        ArtifactSignatureStatus.REVOKED
        if revoked
        else ArtifactSignatureStatus.VALID_TRUSTED
        if trusted
        else ArtifactSignatureStatus.VALID_UNTRUSTED
    )
    return SignatureVerification(
        status=status,
        signed=True,
        cryptographically_valid=True,
        trusted=trusted,
        fingerprint=embedded.fingerprint,
        key_id=embedded.key_id,
        purpose=embedded.purpose,
        signer_known=known is not None,
        signer_revoked=revoked,
    )


def enforce_signature_policy(
    verification: SignatureVerification,
    *,
    policy: str,
) -> None:
    """Выполнить операцию enforce signature policy. Аргументы интерпретируются в контексте модуля,
    результат возвращается вызывающему коду.
    """
    if policy == "optional":
        if verification.status == ArtifactSignatureStatus.INVALID:
            raise ArtifactSigningError("Криптографическая подпись архива недействительна")
        if verification.status == ArtifactSignatureStatus.REVOKED:
            raise ArtifactSigningError("Архив подписан отозванным ключом")
        return
    if policy == "require_valid":
        if not verification.cryptographically_valid or verification.signer_revoked:
            raise ArtifactSigningError("Требуется действительная Ed25519-подпись архива")
        return
    if policy == "require_trusted":
        if not verification.cryptographically_valid or not verification.trusted:
            raise ArtifactSigningError("Архив должен быть подписан доверенным активным ключом")
        return
    raise ArtifactSigningError("Неизвестная политика проверки подписей")
