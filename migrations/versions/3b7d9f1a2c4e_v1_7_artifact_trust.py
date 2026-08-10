"""v1.7 artifact trust and signed evidence

Revision ID: 3b7d9f1a2c4e
Revises: 2a6f0104062a
Create Date: 2026-08-06 14:30:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "3b7d9f1a2c4e"
down_revision: str | Sequence[str] | None = "2a6f0104062a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


signature_status_enum = sa.Enum(
    "UNSIGNED",
    "VALID_TRUSTED",
    "VALID_UNTRUSTED",
    "INVALID",
    "REVOKED",
    name="artifactsignaturestatus",
    native_enum=False,
    length=30,
)
key_status_enum = sa.Enum(
    "ACTIVE",
    "REVOKED",
    name="artifactsigningkeystatus",
    native_enum=False,
    length=20,
)


def upgrade() -> None:
    """Применить this Alembic revision in dependency-safe schema order."""
    op.create_table(
        "artifact_signing_keys",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("organization_id", sa.String(length=36), nullable=False),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("key_id", sa.String(length=80), nullable=False),
        sa.Column("algorithm", sa.String(length=20), nullable=False),
        sa.Column("fingerprint", sa.String(length=64), nullable=False),
        sa.Column("public_key_b64", sa.Text(), nullable=False),
        sa.Column("private_key_enc", sa.Text(), nullable=True),
        sa.Column("status", key_status_enum, nullable=False),
        sa.Column("trusted_for_import", sa.Boolean(), nullable=False),
        sa.Column("is_default", sa.Boolean(), nullable=False),
        sa.Column("created_by_id", sa.String(length=36), nullable=False),
        sa.Column("revoked_by_id", sa.String(length=36), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["created_by_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["revoked_by_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "organization_id", "fingerprint", name="uq_artifact_signing_key_org_fingerprint"
        ),
        sa.UniqueConstraint("organization_id", "key_id", name="uq_artifact_signing_key_org_key_id"),
    )
    with op.batch_alter_table("artifact_signing_keys", schema=None) as batch_op:
        batch_op.create_index(
            "ix_artifact_signing_key_org_default",
            ["organization_id", "is_default"],
            unique=False,
        )
        batch_op.create_index(
            "ix_artifact_signing_key_org_status",
            ["organization_id", "status"],
            unique=False,
        )
        batch_op.create_index(
            batch_op.f("ix_artifact_signing_keys_fingerprint"), ["fingerprint"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_artifact_signing_keys_organization_id"),
            ["organization_id"],
            unique=False,
        )
        batch_op.create_index(
            batch_op.f("ix_artifact_signing_keys_status"), ["status"], unique=False
        )

    with op.batch_alter_table("configuration_bundles", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "signature_status",
                signature_status_enum,
                server_default="UNSIGNED",
                nullable=False,
            )
        )
        batch_op.add_column(
            sa.Column("signature_info", sa.JSON(), server_default=sa.text("'{}'"), nullable=False)
        )
        batch_op.add_column(sa.Column("signer_fingerprint", sa.String(length=64), nullable=True))
        batch_op.create_index(
            batch_op.f("ix_configuration_bundles_signature_status"),
            ["signature_status"],
            unique=False,
        )
        batch_op.create_index(
            batch_op.f("ix_configuration_bundles_signer_fingerprint"),
            ["signer_fingerprint"],
            unique=False,
        )

    with op.batch_alter_table("support_bundles", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "signature_status",
                signature_status_enum,
                server_default="UNSIGNED",
                nullable=False,
            )
        )
        batch_op.add_column(
            sa.Column("signature_info", sa.JSON(), server_default=sa.text("'{}'"), nullable=False)
        )
        batch_op.add_column(sa.Column("signer_fingerprint", sa.String(length=64), nullable=True))
        batch_op.create_index(
            batch_op.f("ix_support_bundles_signature_status"),
            ["signature_status"],
            unique=False,
        )
        batch_op.create_index(
            batch_op.f("ix_support_bundles_signer_fingerprint"),
            ["signer_fingerprint"],
            unique=False,
        )


def downgrade() -> None:
    """Откатить this Alembic revision in dependency-safe schema order."""
    with op.batch_alter_table("support_bundles", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_support_bundles_signer_fingerprint"))
        batch_op.drop_index(batch_op.f("ix_support_bundles_signature_status"))
        batch_op.drop_column("signer_fingerprint")
        batch_op.drop_column("signature_info")
        batch_op.drop_column("signature_status")

    with op.batch_alter_table("configuration_bundles", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_configuration_bundles_signer_fingerprint"))
        batch_op.drop_index(batch_op.f("ix_configuration_bundles_signature_status"))
        batch_op.drop_column("signer_fingerprint")
        batch_op.drop_column("signature_info")
        batch_op.drop_column("signature_status")

    with op.batch_alter_table("artifact_signing_keys", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_artifact_signing_keys_status"))
        batch_op.drop_index(batch_op.f("ix_artifact_signing_keys_organization_id"))
        batch_op.drop_index(batch_op.f("ix_artifact_signing_keys_fingerprint"))
        batch_op.drop_index("ix_artifact_signing_key_org_status")
        batch_op.drop_index("ix_artifact_signing_key_org_default")
    op.drop_table("artifact_signing_keys")
