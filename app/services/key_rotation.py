from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit import write_audit
from app.enums import PrivacyRequestStatus, SafetySeverity
from app.models import (
    AIProviderConfig,
    ArtifactSigningKey,
    CandidateProfile,
    ConversationMessage,
    InboundTelegramUpdate,
    IntegrationEndpoint,
    PrivacyRequest,
    TelegramAuthChallenge,
    TelegramConnection,
    User,
)
from app.services.crypto import SecretCipher
from app.services.storage import StorageService


@dataclass(frozen=True)
class SecretFieldSpec:
    model: type[Any]
    attribute: str
    context: Callable[[Any], str]
    label: str


@dataclass
class RotationReport:
    fields: dict[str, int] = field(default_factory=dict)
    privacy_exports: int = 0
    total_values: int = 0
    dry_run: bool = False

    def to_dict(self) -> dict[str, Any]:
        """Преобразовать to dict класса RotationReport without changing the source object."""
        return {
            "fields": dict(sorted(self.fields.items())),
            "privacy_exports": self.privacy_exports,
            "total_values": self.total_values,
            "dry_run": self.dry_run,
        }


FIELD_SPECS = (
    SecretFieldSpec(User, "totp_secret_enc", lambda item: f"user-totp:{item.id}", "user.totp"),
    SecretFieldSpec(
        User,
        "pending_totp_secret_enc",
        lambda item: f"user-totp-pending:{item.id}",
        "user.pending_totp",
    ),
    SecretFieldSpec(
        TelegramConnection,
        "credentials_enc",
        lambda item: f"telegram-connection:{item.id}",
        "telegram_connection.credentials",
    ),
    SecretFieldSpec(
        TelegramAuthChallenge,
        "payload_enc",
        lambda item: f"telegram-auth-challenge:{item.id}",
        "telegram_auth_challenge.payload",
    ),
    SecretFieldSpec(
        InboundTelegramUpdate,
        "payload_enc",
        lambda item: f"inbound-update:{item.id}:payload",
        "inbound_update.payload",
    ),
    SecretFieldSpec(
        ConversationMessage,
        "body_enc",
        lambda item: f"conversation-message:{item.id}:body",
        "conversation_message.body",
    ),
    SecretFieldSpec(
        CandidateProfile,
        "phone_enc",
        lambda item: f"candidate:{item.id}:phone",
        "candidate.phone",
    ),
    SecretFieldSpec(
        CandidateProfile,
        "email_enc",
        lambda item: f"candidate:{item.id}:email",
        "candidate.email",
    ),
    SecretFieldSpec(
        AIProviderConfig,
        "api_key_enc",
        lambda item: f"ai-provider:{item.id}:api-key",
        "ai_provider.api_key",
    ),
    SecretFieldSpec(
        IntegrationEndpoint,
        "config_enc",
        lambda item: f"integration:{item.id}:config",
        "integration.config",
    ),
    SecretFieldSpec(
        ArtifactSigningKey,
        "private_key_enc",
        lambda item: f"artifact-signing-key:{item.id}:private",
        "artifact_signing_key.private",
    ),
)


def rotate_master_key(
    db: Session,
    *,
    old_cipher: SecretCipher,
    new_cipher: SecretCipher,
    storage: StorageService,
    dry_run: bool = False,
) -> RotationReport:
    """Перешифровать every supported application secret with a new master key. Call this only
    during a maintenance window with API and workers stopped. A complete decrypt preflight runs
    before any mutation, so a wrong old key cannot partially rewrite database fields. Privacy
    export objects are also re-encrypted in place and restored on a handled failure.
    """

    report = RotationReport(dry_run=dry_run)
    prepared_fields: list[tuple[Any, str, str, str, str]] = []

    for spec in FIELD_SPECS:
        column = getattr(spec.model, spec.attribute)
        rows = list(db.scalars(select(spec.model).where(column.is_not(None))).all())
        count = 0
        for item in rows:
            encrypted = getattr(item, spec.attribute)
            if not encrypted:
                continue
            context = spec.context(item)
            plaintext = old_cipher.decrypt(encrypted, context=context)
            field_replacement = new_cipher.encrypt(plaintext, context=context)
            prepared_fields.append((item, spec.attribute, field_replacement, spec.label, context))
            count += 1
        if count:
            report.fields[spec.label] = count
            report.total_values += count

    prepared_exports: list[tuple[PrivacyRequest, str, bytes, bytes]] = []
    exports = list(
        db.scalars(
            select(PrivacyRequest).where(
                PrivacyRequest.status == PrivacyRequestStatus.COMPLETED,
                PrivacyRequest.output_relative_path.is_not(None),
            )
        ).all()
    )
    for item in exports:
        assert item.output_relative_path is not None
        key = item.output_relative_path
        original = storage.read_bytes(key)
        plaintext = old_cipher.decrypt(
            original.decode("utf-8"), context=f"privacy-export:{item.id}"
        )
        export_replacement = new_cipher.encrypt(
            plaintext, context=f"privacy-export:{item.id}"
        ).encode("utf-8")
        prepared_exports.append((item, key, original, export_replacement))
    report.privacy_exports = len(prepared_exports)
    report.total_values += len(prepared_exports)

    if dry_run:
        return report

    written_exports: list[tuple[str, bytes]] = []
    try:
        for item, attribute, replacement, _label, _context in prepared_fields:
            setattr(item, attribute, replacement)

        # Storage has no shared transaction with PostgreSQL/S3. During a normal
        # exception path originals are restored before the DB rollback is
        # propagated. The maintenance runbook still requires a backup because
        # no process can make a remote object store and SQL commit crash-atomic.
        for _item, key, original, export_bytes in prepared_exports:
            storage.put_bytes(key, export_bytes, content_type="application/octet-stream")
            written_exports.append((key, original))

        write_audit(
            db,
            action="security.master_key_rotated",
            severity=SafetySeverity.CRITICAL,
            details={
                "field_counts": report.fields,
                "privacy_exports": report.privacy_exports,
                "total_values": report.total_values,
                "maintenance_required": True,
            },
        )
        db.flush()
        return report
    except Exception:
        for key, original in reversed(written_exports):
            try:
                storage.put_bytes(key, original, content_type="application/octet-stream")
            except Exception:
                # Preserve the original exception. The operator must restore
                # from the mandatory pre-rotation backup if this best-effort
                # restoration also fails.
                pass
        raise
