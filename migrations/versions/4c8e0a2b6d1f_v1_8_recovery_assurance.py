"""v1.8 recovery assurance and continuity evidence

Revision ID: 4c8e0a2b6d1f
Revises: 3b7d9f1a2c4e
Create Date: 2026-08-07 00:30:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "4c8e0a2b6d1f"
down_revision: str | Sequence[str] | None = "3b7d9f1a2c4e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


backup_status_enum = sa.Enum(
    "REGISTERED",
    "VERIFIED",
    "INVALID",
    "EXPIRED",
    "DELETED",
    name="recoverybackupstatus",
    native_enum=False,
    length=20,
)
drill_status_enum = sa.Enum(
    "PASSED",
    "FAILED",
    "BLOCKED",
    name="recoverydrillstatus",
    native_enum=False,
    length=20,
)
drill_mode_enum = sa.Enum(
    "SQLITE_ISOLATED",
    "POSTGRES_ISOLATED",
    "METADATA_ONLY",
    name="recoverydrillmode",
    native_enum=False,
    length=24,
)
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


def upgrade() -> None:
    """Применить this Alembic revision in dependency-safe schema order."""
    op.create_table(
        "recovery_policies",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("organization_id", sa.String(length=36), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("rpo_hours", sa.Integer(), nullable=False),
        sa.Column("rto_minutes", sa.Integer(), nullable=False),
        sa.Column("restore_drill_max_age_days", sa.Integer(), nullable=False),
        sa.Column("minimum_retained_backups", sa.Integer(), nullable=False),
        sa.Column("require_encrypted_backup", sa.Boolean(), nullable=False),
        sa.Column("require_trusted_signature", sa.Boolean(), nullable=False),
        sa.Column("require_restore_drill", sa.Boolean(), nullable=False),
        sa.Column("created_by_id", sa.String(length=36), nullable=False),
        sa.Column("updated_by_id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["created_by_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["updated_by_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("organization_id", name="uq_recovery_policy_organization"),
    )
    with op.batch_alter_table("recovery_policies", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_recovery_policies_organization_id"), ["organization_id"], unique=False
        )

    op.create_table(
        "recovery_backup_evidence",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("organization_id", sa.String(length=36), nullable=False),
        sa.Column("backup_id", sa.String(length=80), nullable=False),
        sa.Column("status", backup_status_enum, nullable=False),
        sa.Column("product_version", sa.String(length=40), nullable=False),
        sa.Column("database_kind", sa.String(length=24), nullable=False),
        sa.Column("storage_backend", sa.String(length=24), nullable=False),
        sa.Column("artifact_filename", sa.String(length=255), nullable=False),
        sa.Column("artifact_sha256", sa.String(length=64), nullable=False),
        sa.Column("artifact_size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("artifact_encrypted", sa.Boolean(), nullable=False),
        sa.Column("encryption_algorithm", sa.String(length=40), nullable=True),
        sa.Column("backup_created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("receipt_sha256", sa.String(length=64), nullable=False),
        sa.Column("manifest_sha256", sa.String(length=64), nullable=False),
        sa.Column("manifest_summary", sa.JSON(), nullable=False),
        sa.Column("signature_status", signature_status_enum, nullable=False),
        sa.Column("signature_info", sa.JSON(), nullable=False),
        sa.Column("signer_fingerprint", sa.String(length=64), nullable=True),
        sa.Column("imported_by_id", sa.String(length=36), nullable=False),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["imported_by_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("organization_id", "backup_id", name="uq_recovery_backup_org_backup"),
    )
    with op.batch_alter_table("recovery_backup_evidence", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_recovery_backup_evidence_artifact_sha256"),
            ["artifact_sha256"],
            unique=False,
        )
        batch_op.create_index(
            batch_op.f("ix_recovery_backup_evidence_backup_created_at"),
            ["backup_created_at"],
            unique=False,
        )
        batch_op.create_index(
            batch_op.f("ix_recovery_backup_evidence_backup_id"), ["backup_id"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_recovery_backup_evidence_expires_at"), ["expires_at"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_recovery_backup_evidence_organization_id"),
            ["organization_id"],
            unique=False,
        )
        batch_op.create_index(
            batch_op.f("ix_recovery_backup_evidence_receipt_sha256"),
            ["receipt_sha256"],
            unique=False,
        )
        batch_op.create_index(
            batch_op.f("ix_recovery_backup_evidence_signature_status"),
            ["signature_status"],
            unique=False,
        )
        batch_op.create_index(
            batch_op.f("ix_recovery_backup_evidence_signer_fingerprint"),
            ["signer_fingerprint"],
            unique=False,
        )
        batch_op.create_index(
            batch_op.f("ix_recovery_backup_evidence_status"), ["status"], unique=False
        )
        batch_op.create_index(
            "ix_recovery_backup_org_signature",
            ["organization_id", "signature_status"],
            unique=False,
        )
        batch_op.create_index(
            "ix_recovery_backup_org_status_created",
            ["organization_id", "status", "backup_created_at"],
            unique=False,
        )

    op.create_table(
        "recovery_restore_drills",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("organization_id", sa.String(length=36), nullable=False),
        sa.Column("backup_evidence_id", sa.String(length=36), nullable=True),
        sa.Column("drill_id", sa.String(length=80), nullable=False),
        sa.Column("backup_id", sa.String(length=80), nullable=False),
        sa.Column("backup_artifact_sha256", sa.String(length=64), nullable=False),
        sa.Column("product_version", sa.String(length=40), nullable=False),
        sa.Column("mode", drill_mode_enum, nullable=False),
        sa.Column("status", drill_status_enum, nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("duration_seconds", sa.Integer(), nullable=False),
        sa.Column("target_rto_minutes", sa.Integer(), nullable=False),
        sa.Column("rto_met", sa.Boolean(), nullable=False),
        sa.Column("checks", sa.JSON(), nullable=False),
        sa.Column("blockers", sa.JSON(), nullable=False),
        sa.Column("warnings", sa.JSON(), nullable=False),
        sa.Column("evidence_sha256", sa.String(length=64), nullable=False),
        sa.Column("signature_status", signature_status_enum, nullable=False),
        sa.Column("signature_info", sa.JSON(), nullable=False),
        sa.Column("signer_fingerprint", sa.String(length=64), nullable=True),
        sa.Column("executed_host_hash", sa.String(length=64), nullable=True),
        sa.Column("imported_by_id", sa.String(length=36), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["backup_evidence_id"], ["recovery_backup_evidence.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(["imported_by_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("organization_id", "drill_id", name="uq_recovery_drill_org_drill"),
    )
    with op.batch_alter_table("recovery_restore_drills", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_recovery_restore_drills_backup_evidence_id"),
            ["backup_evidence_id"],
            unique=False,
        )
        batch_op.create_index(
            batch_op.f("ix_recovery_restore_drills_backup_id"), ["backup_id"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_recovery_restore_drills_completed_at"), ["completed_at"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_recovery_restore_drills_drill_id"), ["drill_id"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_recovery_restore_drills_evidence_sha256"),
            ["evidence_sha256"],
            unique=False,
        )
        batch_op.create_index(
            batch_op.f("ix_recovery_restore_drills_organization_id"),
            ["organization_id"],
            unique=False,
        )
        batch_op.create_index(
            batch_op.f("ix_recovery_restore_drills_signature_status"),
            ["signature_status"],
            unique=False,
        )
        batch_op.create_index(
            batch_op.f("ix_recovery_restore_drills_signer_fingerprint"),
            ["signer_fingerprint"],
            unique=False,
        )
        batch_op.create_index(
            batch_op.f("ix_recovery_restore_drills_status"), ["status"], unique=False
        )
        batch_op.create_index(
            "ix_recovery_drill_org_backup", ["organization_id", "backup_id"], unique=False
        )
        batch_op.create_index(
            "ix_recovery_drill_org_status_completed",
            ["organization_id", "status", "completed_at"],
            unique=False,
        )


def downgrade() -> None:
    """Откатить this Alembic revision in dependency-safe schema order."""
    with op.batch_alter_table("recovery_restore_drills", schema=None) as batch_op:
        batch_op.drop_index("ix_recovery_drill_org_status_completed")
        batch_op.drop_index("ix_recovery_drill_org_backup")
        batch_op.drop_index(batch_op.f("ix_recovery_restore_drills_status"))
        batch_op.drop_index(batch_op.f("ix_recovery_restore_drills_signer_fingerprint"))
        batch_op.drop_index(batch_op.f("ix_recovery_restore_drills_signature_status"))
        batch_op.drop_index(batch_op.f("ix_recovery_restore_drills_organization_id"))
        batch_op.drop_index(batch_op.f("ix_recovery_restore_drills_evidence_sha256"))
        batch_op.drop_index(batch_op.f("ix_recovery_restore_drills_drill_id"))
        batch_op.drop_index(batch_op.f("ix_recovery_restore_drills_completed_at"))
        batch_op.drop_index(batch_op.f("ix_recovery_restore_drills_backup_id"))
        batch_op.drop_index(batch_op.f("ix_recovery_restore_drills_backup_evidence_id"))
    op.drop_table("recovery_restore_drills")

    with op.batch_alter_table("recovery_backup_evidence", schema=None) as batch_op:
        batch_op.drop_index("ix_recovery_backup_org_status_created")
        batch_op.drop_index("ix_recovery_backup_org_signature")
        batch_op.drop_index(batch_op.f("ix_recovery_backup_evidence_status"))
        batch_op.drop_index(batch_op.f("ix_recovery_backup_evidence_signer_fingerprint"))
        batch_op.drop_index(batch_op.f("ix_recovery_backup_evidence_signature_status"))
        batch_op.drop_index(batch_op.f("ix_recovery_backup_evidence_receipt_sha256"))
        batch_op.drop_index(batch_op.f("ix_recovery_backup_evidence_organization_id"))
        batch_op.drop_index(batch_op.f("ix_recovery_backup_evidence_expires_at"))
        batch_op.drop_index(batch_op.f("ix_recovery_backup_evidence_backup_id"))
        batch_op.drop_index(batch_op.f("ix_recovery_backup_evidence_backup_created_at"))
        batch_op.drop_index(batch_op.f("ix_recovery_backup_evidence_artifact_sha256"))
    op.drop_table("recovery_backup_evidence")

    with op.batch_alter_table("recovery_policies", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_recovery_policies_organization_id"))
    op.drop_table("recovery_policies")
