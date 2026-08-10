"""v2.0 release trust and provenance

Revision ID: 6e0a2c4f8b1d
Revises: 5d9f1b3c7e2a
Create Date: 2026-08-07 05:30:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "6e0a2c4f8b1d"
down_revision: str | Sequence[str] | None = "5d9f1b3c7e2a"
branch_labels = None
depends_on = None

signature_enum = sa.Enum(
    "UNSIGNED",
    "VALID_UNTRUSTED",
    "VALID_TRUSTED",
    "INVALID",
    "REVOKED",
    name="artifactsignaturestatus",
    native_enum=False,
    length=30,
)


def upgrade() -> None:
    """Применить this Alembic revision in dependency-safe schema order."""
    op.create_table(
        "release_attestations",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "organization_id",
            sa.String(36),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("version", sa.String(40), nullable=False),
        sa.Column("source_commit", sa.String(80)),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("payload_sha256", sa.String(64), nullable=False),
        sa.Column("signature_status", signature_enum, nullable=False),
        sa.Column("signature_info", sa.JSON(), nullable=False),
        sa.Column("signer_fingerprint", sa.String(64)),
        sa.Column("verified_at", sa.DateTime(timezone=True)),
        sa.Column("created_by_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "organization_id", "payload_sha256", name="uq_release_attestation_org_payload"
        ),
    )
    op.create_index(
        "ix_release_attestation_org_version_created",
        "release_attestations",
        ["organization_id", "version", "created_at"],
    )
    op.create_index(
        "ix_release_attestation_org_signature",
        "release_attestations",
        ["organization_id", "signature_status"],
    )
    for col in [
        "organization_id",
        "version",
        "source_commit",
        "payload_sha256",
        "signature_status",
        "signer_fingerprint",
    ]:
        op.create_index(f"ix_release_attestations_{col}", "release_attestations", [col])
    with op.batch_alter_table("change_requests") as b:
        b.add_column(sa.Column("release_attestation_id", sa.String(36), nullable=True))
        b.create_foreign_key(
            "fk_change_requests_release_attestation",
            "release_attestations",
            ["release_attestation_id"],
            ["id"],
            ondelete="SET NULL",
        )
        b.create_index("ix_change_requests_release_attestation_id", ["release_attestation_id"])


def downgrade() -> None:
    """Откатить this Alembic revision in dependency-safe schema order."""
    with op.batch_alter_table("change_requests") as b:
        b.drop_index("ix_change_requests_release_attestation_id")
        b.drop_constraint("fk_change_requests_release_attestation", type_="foreignkey")
        b.drop_column("release_attestation_id")
    op.drop_table("release_attestations")
