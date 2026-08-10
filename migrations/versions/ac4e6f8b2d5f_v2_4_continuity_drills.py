"""Add continuity policies, drills, runtime evidence and hash-linked events.

Revision ID: ac4e6f8b2d5f
Revises: 9b3d5f7a1c4e
Create Date: 2026-08-08
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "ac4e6f8b2d5f"
down_revision: str | None = "9b3d5f7a1c4e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Создать continuity tables and extend execution sites with runtime evidence."""

    with op.batch_alter_table("execution_sites") as batch:
        batch.add_column(sa.Column("environment", sa.String(length=30), nullable=True))
        batch.add_column(sa.Column("current_revision", sa.String(length=64), nullable=True))
        batch.add_column(sa.Column("expected_revision", sa.String(length=64), nullable=True))
        batch.add_column(sa.Column("schema_current", sa.Boolean(), nullable=True))
        batch.add_column(sa.Column("critical_config_sha256", sa.String(length=64), nullable=True))
        batch.add_column(sa.Column("release_payload_sha256", sa.String(length=64), nullable=True))
        batch.add_column(sa.Column("runtime_fingerprint", sa.String(length=64), nullable=True))
        batch.add_column(sa.Column("runtime_checked_at", sa.DateTime(timezone=True), nullable=True))
        batch.create_index("ix_execution_sites_critical_config_sha256", ["critical_config_sha256"])
        batch.create_index("ix_execution_sites_release_payload_sha256", ["release_payload_sha256"])
        batch.create_index("ix_execution_sites_runtime_fingerprint", ["runtime_fingerprint"])

    op.create_table(
        "continuity_policies",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("organization_id", sa.String(length=36), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("require_live_drill", sa.Boolean(), nullable=False),
        sa.Column("max_rto_seconds", sa.Integer(), nullable=False),
        sa.Column("evidence_valid_days", sa.Integer(), nullable=False),
        sa.Column("require_distinct_signoff", sa.Boolean(), nullable=False),
        sa.Column("created_by_id", sa.String(length=36), nullable=True),
        sa.Column("updated_by_id", sa.String(length=36), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "evidence_valid_days >= 1 AND evidence_valid_days <= 365",
            name="ck_continuity_policy_evidence_days",
        ),
        sa.CheckConstraint(
            "max_rto_seconds >= 30 AND max_rto_seconds <= 86400",
            name="ck_continuity_policy_rto",
        ),
        sa.ForeignKeyConstraint(["created_by_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["updated_by_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("organization_id", name="uq_continuity_policy_organization"),
    )
    op.create_index(
        "ix_continuity_policies_organization_id",
        "continuity_policies",
        ["organization_id"],
        unique=True,
    )

    op.create_table(
        "continuity_drills",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("organization_id", sa.String(length=36), nullable=False),
        sa.Column("mode", sa.String(length=20), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("source_site_key", sa.String(length=80), nullable=False),
        sa.Column("target_site_key", sa.String(length=80), nullable=False),
        sa.Column("source_epoch", sa.BigInteger(), nullable=False),
        sa.Column("target_epoch", sa.BigInteger(), nullable=True),
        sa.Column("return_epoch", sa.BigInteger(), nullable=True),
        sa.Column("failover_request_id", sa.String(length=36), nullable=True),
        sa.Column("failback_request_id", sa.String(length=36), nullable=True),
        sa.Column("policy_snapshot", sa.JSON(), nullable=False),
        sa.Column("policy_sha256", sa.String(length=64), nullable=False),
        sa.Column("runtime_snapshot", sa.JSON(), nullable=False),
        sa.Column("evidence_payload", sa.JSON(), nullable=True),
        sa.Column("evidence_sha256", sa.String(length=64), nullable=True),
        sa.Column("rto_seconds", sa.Integer(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("target_active_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failback_requested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("primary_restored_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by_id", sa.String(length=36), nullable=True),
        sa.Column("signed_off_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("signed_off_by_id", sa.String(length=36), nullable=True),
        sa.Column("signoff_note", sa.Text(), nullable=True),
        sa.Column("failure_reason", sa.Text(), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["failback_request_id"], ["failover_requests.id"]),
        sa.ForeignKeyConstraint(["failover_request_id"], ["failover_requests.id"]),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["signed_off_by_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_continuity_drill_org_status_created",
        "continuity_drills",
        ["organization_id", "status", "created_at"],
    )
    op.create_index(
        "ix_continuity_drill_org_expires", "continuity_drills", ["organization_id", "expires_at"]
    )
    op.create_index(
        "ix_continuity_drills_organization_id", "continuity_drills", ["organization_id"]
    )
    op.create_index("ix_continuity_drills_mode", "continuity_drills", ["mode"])
    op.create_index("ix_continuity_drills_status", "continuity_drills", ["status"])
    op.create_index(
        "ix_continuity_drills_failover_request_id", "continuity_drills", ["failover_request_id"]
    )
    op.create_index(
        "ix_continuity_drills_failback_request_id", "continuity_drills", ["failback_request_id"]
    )
    op.create_index("ix_continuity_drills_policy_sha256", "continuity_drills", ["policy_sha256"])
    op.create_index(
        "ix_continuity_drills_evidence_sha256", "continuity_drills", ["evidence_sha256"]
    )
    op.create_index("ix_continuity_drills_expires_at", "continuity_drills", ["expires_at"])

    op.create_table(
        "continuity_drill_events",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("organization_id", sa.String(length=36), nullable=False),
        sa.Column("drill_id", sa.String(length=36), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(length=40), nullable=False),
        sa.Column("actor_user_id", sa.String(length=36), nullable=True),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("previous_hash", sa.String(length=64), nullable=False),
        sa.Column("event_hash", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["actor_user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["drill_id"], ["continuity_drills.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("drill_id", "sequence", name="uq_continuity_drill_event_sequence"),
    )
    op.create_index(
        "ix_continuity_event_org_created",
        "continuity_drill_events",
        ["organization_id", "created_at"],
    )
    op.create_index(
        "ix_continuity_drill_events_organization_id", "continuity_drill_events", ["organization_id"]
    )
    op.create_index("ix_continuity_drill_events_drill_id", "continuity_drill_events", ["drill_id"])
    op.create_index(
        "ix_continuity_drill_events_event_type", "continuity_drill_events", ["event_type"]
    )
    op.create_index(
        "ix_continuity_drill_events_event_hash", "continuity_drill_events", ["event_hash"]
    )


def downgrade() -> None:
    """Удалить continuity objects and runtime-evidence columns in reverse order."""

    op.drop_table("continuity_drill_events")
    op.drop_table("continuity_drills")
    op.drop_table("continuity_policies")
    with op.batch_alter_table("execution_sites") as batch:
        batch.drop_index("ix_execution_sites_runtime_fingerprint")
        batch.drop_index("ix_execution_sites_release_payload_sha256")
        batch.drop_index("ix_execution_sites_critical_config_sha256")
        batch.drop_column("runtime_checked_at")
        batch.drop_column("runtime_fingerprint")
        batch.drop_column("release_payload_sha256")
        batch.drop_column("critical_config_sha256")
        batch.drop_column("schema_current")
        batch.drop_column("expected_revision")
        batch.drop_column("current_revision")
        batch.drop_column("environment")
