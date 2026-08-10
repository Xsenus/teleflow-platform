"""v1.3 controlled operations

Revision ID: b7c9d2e4f6a8
Revises: f4a6b8c12d34
Create Date: 2026-08-06 13:30:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b7c9d2e4f6a8"
down_revision: str | Sequence[str] | None = "f4a6b8c12d34"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


rollout_mode = sa.Enum(
    "STANDARD",
    "STAGED",
    name="rolloutmode",
    native_enum=False,
    length=20,
)
preflight_status = sa.Enum(
    "PASSED",
    "WARNING",
    "BLOCKED",
    "EXPIRED",
    name="preflightstatus",
    native_enum=False,
    length=20,
)
delivery_review_resolution = sa.Enum(
    "CONFIRMED_SENT",
    "CONFIRMED_NOT_SENT",
    "SKIPPED",
    name="deliveryreviewresolution",
    native_enum=False,
    length=30,
)


def upgrade() -> None:
    """Применить this Alembic revision in dependency-safe schema order."""
    with op.batch_alter_table("destinations", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("permission_reviewed_at", sa.DateTime(timezone=True), nullable=True)
        )
        batch_op.add_column(
            sa.Column("permission_expires_at", sa.DateTime(timezone=True), nullable=True)
        )
        batch_op.create_index(
            "ix_destinations_permission_expires_at",
            ["permission_expires_at"],
            unique=False,
        )

    with op.batch_alter_table("campaigns", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "rollout_mode",
                rollout_mode,
                nullable=False,
                server_default="STANDARD",
            )
        )
        batch_op.add_column(
            sa.Column(
                "rollout_batch_size",
                sa.Integer(),
                nullable=False,
                server_default=sa.text("5"),
            )
        )
        batch_op.add_column(
            sa.Column(
                "rollout_pause_seconds",
                sa.Integer(),
                nullable=False,
                server_default=sa.text("300"),
            )
        )
        batch_op.add_column(
            sa.Column(
                "rollout_require_checkpoint",
                sa.Boolean(),
                nullable=False,
                server_default=sa.true(),
            )
        )
        batch_op.add_column(
            sa.Column(
                "rollout_failure_threshold_percent",
                sa.Integer(),
                nullable=False,
                server_default=sa.text("20"),
            )
        )
        batch_op.add_column(
            sa.Column(
                "duplicate_guard_minutes",
                sa.Integer(),
                nullable=False,
                server_default=sa.text("1380"),
            )
        )

    with op.batch_alter_table("campaign_runs", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "rollout_mode",
                rollout_mode,
                nullable=False,
                server_default="STANDARD",
            )
        )
        batch_op.add_column(
            sa.Column("batch_size", sa.Integer(), nullable=False, server_default=sa.text("0"))
        )
        batch_op.add_column(
            sa.Column("total_batches", sa.Integer(), nullable=False, server_default=sa.text("1"))
        )
        batch_op.add_column(
            sa.Column("active_batch", sa.Integer(), nullable=False, server_default=sa.text("1"))
        )
        batch_op.add_column(
            sa.Column(
                "rollout_pause_seconds",
                sa.Integer(),
                nullable=False,
                server_default=sa.text("0"),
            )
        )
        batch_op.add_column(
            sa.Column(
                "failure_threshold_percent",
                sa.Integer(),
                nullable=False,
                server_default=sa.text("100"),
            )
        )
        batch_op.add_column(
            sa.Column(
                "checkpoint_required",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
        )
        batch_op.add_column(sa.Column("checkpoint_reason", sa.Text(), nullable=True))
        batch_op.add_column(
            sa.Column("checkpoint_requested_at", sa.DateTime(timezone=True), nullable=True)
        )
        batch_op.add_column(
            sa.Column("checkpoint_approved_at", sa.DateTime(timezone=True), nullable=True)
        )
        batch_op.add_column(
            sa.Column("checkpoint_approved_by_id", sa.String(length=36), nullable=True)
        )
        batch_op.add_column(sa.Column("checkpoint_note", sa.Text(), nullable=True))
        batch_op.create_foreign_key(
            "fk_campaign_runs_checkpoint_approved_by_id_users",
            "users",
            ["checkpoint_approved_by_id"],
            ["id"],
        )

    with op.batch_alter_table("delivery_jobs", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("batch_number", sa.Integer(), nullable=False, server_default=sa.text("1"))
        )
        batch_op.add_column(sa.Column("content_fingerprint", sa.String(length=64), nullable=True))
        batch_op.add_column(
            sa.Column("review_resolution", delivery_review_resolution, nullable=True)
        )
        batch_op.add_column(sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column("reviewed_by_id", sa.String(length=36), nullable=True))
        batch_op.add_column(sa.Column("review_note", sa.Text(), nullable=True))
        batch_op.create_index("ix_delivery_jobs_batch_number", ["batch_number"], unique=False)
        batch_op.create_index(
            "ix_delivery_jobs_content_fingerprint", ["content_fingerprint"], unique=False
        )
        batch_op.create_foreign_key(
            "fk_delivery_jobs_reviewed_by_id_users",
            "users",
            ["reviewed_by_id"],
            ["id"],
        )

    op.create_table(
        "campaign_preflight_reports",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("organization_id", sa.String(length=36), nullable=False),
        sa.Column("campaign_id", sa.String(length=36), nullable=False),
        sa.Column("fingerprint", sa.String(length=64), nullable=False),
        sa.Column("status", preflight_status, nullable=False),
        sa.Column("blockers", sa.JSON(), nullable=False),
        sa.Column("warnings", sa.JSON(), nullable=False),
        sa.Column("items", sa.JSON(), nullable=False),
        sa.Column("summary", sa.JSON(), nullable=False),
        sa.Column("created_by_id", sa.String(length=36), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["campaign_id"], ["campaigns.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_campaign_preflight_reports_organization_id",
        "campaign_preflight_reports",
        ["organization_id"],
        unique=False,
    )
    op.create_index(
        "ix_campaign_preflight_reports_campaign_id",
        "campaign_preflight_reports",
        ["campaign_id"],
        unique=False,
    )
    op.create_index(
        "ix_campaign_preflight_reports_fingerprint",
        "campaign_preflight_reports",
        ["fingerprint"],
        unique=False,
    )
    op.create_index(
        "ix_campaign_preflight_reports_status",
        "campaign_preflight_reports",
        ["status"],
        unique=False,
    )
    op.create_index(
        "ix_campaign_preflight_reports_expires_at",
        "campaign_preflight_reports",
        ["expires_at"],
        unique=False,
    )
    op.create_index(
        "ix_preflight_org_campaign_created",
        "campaign_preflight_reports",
        ["organization_id", "campaign_id", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_preflight_org_status_expires",
        "campaign_preflight_reports",
        ["organization_id", "status", "expires_at"],
        unique=False,
    )


def downgrade() -> None:
    """Откатить this Alembic revision in dependency-safe schema order."""
    op.drop_index("ix_preflight_org_status_expires", table_name="campaign_preflight_reports")
    op.drop_index("ix_preflight_org_campaign_created", table_name="campaign_preflight_reports")
    op.drop_index(
        "ix_campaign_preflight_reports_expires_at",
        table_name="campaign_preflight_reports",
    )
    op.drop_index("ix_campaign_preflight_reports_status", table_name="campaign_preflight_reports")
    op.drop_index(
        "ix_campaign_preflight_reports_fingerprint", table_name="campaign_preflight_reports"
    )
    op.drop_index(
        "ix_campaign_preflight_reports_campaign_id", table_name="campaign_preflight_reports"
    )
    op.drop_index(
        "ix_campaign_preflight_reports_organization_id",
        table_name="campaign_preflight_reports",
    )
    op.drop_table("campaign_preflight_reports")

    with op.batch_alter_table("delivery_jobs", schema=None) as batch_op:
        batch_op.drop_constraint("fk_delivery_jobs_reviewed_by_id_users", type_="foreignkey")
        batch_op.drop_index("ix_delivery_jobs_content_fingerprint")
        batch_op.drop_index("ix_delivery_jobs_batch_number")
        batch_op.drop_column("review_note")
        batch_op.drop_column("reviewed_by_id")
        batch_op.drop_column("reviewed_at")
        batch_op.drop_column("review_resolution")
        batch_op.drop_column("content_fingerprint")
        batch_op.drop_column("batch_number")

    with op.batch_alter_table("campaign_runs", schema=None) as batch_op:
        batch_op.drop_constraint(
            "fk_campaign_runs_checkpoint_approved_by_id_users", type_="foreignkey"
        )
        batch_op.drop_column("checkpoint_note")
        batch_op.drop_column("checkpoint_approved_by_id")
        batch_op.drop_column("checkpoint_approved_at")
        batch_op.drop_column("checkpoint_requested_at")
        batch_op.drop_column("checkpoint_reason")
        batch_op.drop_column("checkpoint_required")
        batch_op.drop_column("failure_threshold_percent")
        batch_op.drop_column("rollout_pause_seconds")
        batch_op.drop_column("active_batch")
        batch_op.drop_column("total_batches")
        batch_op.drop_column("batch_size")
        batch_op.drop_column("rollout_mode")

    with op.batch_alter_table("campaigns", schema=None) as batch_op:
        batch_op.drop_column("duplicate_guard_minutes")
        batch_op.drop_column("rollout_failure_threshold_percent")
        batch_op.drop_column("rollout_require_checkpoint")
        batch_op.drop_column("rollout_pause_seconds")
        batch_op.drop_column("rollout_batch_size")
        batch_op.drop_column("rollout_mode")

    with op.batch_alter_table("destinations", schema=None) as batch_op:
        batch_op.drop_index("ix_destinations_permission_expires_at")
        batch_op.drop_column("permission_expires_at")
        batch_op.drop_column("permission_reviewed_at")
