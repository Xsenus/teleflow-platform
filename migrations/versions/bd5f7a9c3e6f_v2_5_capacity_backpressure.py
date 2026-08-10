"""Add tenant capacity policies and immutable queue-pressure assessments.

Revision ID: bd5f7a9c3e6f
Revises: ac4e6f8b2d5f
Create Date: 2026-08-09
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "bd5f7a9c3e6f"
down_revision: str | None = "ac4e6f8b2d5f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Создать capacity policy and assessment tables with database-level invariants."""

    op.create_table(
        "capacity_policies",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("organization_id", sa.String(length=36), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("max_active_jobs", sa.Integer(), nullable=False),
        sa.Column("max_ready_jobs", sa.Integer(), nullable=False),
        sa.Column("max_processing_jobs", sa.Integer(), nullable=False),
        sa.Column("max_active_runs", sa.Integer(), nullable=False),
        sa.Column("max_jobs_per_run", sa.Integer(), nullable=False),
        sa.Column("max_network_starts_per_minute", sa.Integer(), nullable=False),
        sa.Column("max_network_starts_per_hour", sa.Integer(), nullable=False),
        sa.Column("max_estimated_drain_seconds", sa.Integer(), nullable=False),
        sa.Column("warning_utilization_percent", sa.Integer(), nullable=False),
        sa.Column(
            "admission_block_utilization_percent",
            sa.Integer(),
            nullable=False,
        ),
        sa.Column("assessment_ttl_minutes", sa.Integer(), nullable=False),
        sa.Column("gate_admission", sa.Boolean(), nullable=False),
        sa.Column("gate_dispatch", sa.Boolean(), nullable=False),
        sa.Column("created_by_id", sa.String(length=36), nullable=True),
        sa.Column("updated_by_id", sa.String(length=36), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "max_active_jobs >= 1 AND max_ready_jobs >= 1 AND max_processing_jobs >= 1",
            name="ck_capacity_policy_job_limits_positive",
        ),
        sa.CheckConstraint(
            "max_ready_jobs <= max_active_jobs AND max_processing_jobs <= max_ready_jobs",
            name="ck_capacity_policy_job_limit_order",
        ),
        sa.CheckConstraint(
            "max_active_runs >= 1 AND max_jobs_per_run >= 1 "
            "AND max_jobs_per_run <= max_active_jobs",
            name="ck_capacity_policy_run_limits",
        ),
        sa.CheckConstraint(
            "max_network_starts_per_minute >= 1 "
            "AND max_network_starts_per_hour >= max_network_starts_per_minute",
            name="ck_capacity_policy_network_limits",
        ),
        sa.CheckConstraint(
            "max_estimated_drain_seconds >= 60",
            name="ck_capacity_policy_drain_limit",
        ),
        sa.CheckConstraint(
            "warning_utilization_percent >= 1 "
            "AND warning_utilization_percent "
            "< admission_block_utilization_percent "
            "AND admission_block_utilization_percent <= 100",
            name="ck_capacity_policy_utilization_thresholds",
        ),
        sa.CheckConstraint(
            "assessment_ttl_minutes >= 1 AND assessment_ttl_minutes <= 1440",
            name="ck_capacity_policy_assessment_ttl",
        ),
        sa.ForeignKeyConstraint(["created_by_id"], ["users.id"]),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(["updated_by_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "organization_id",
            name="uq_capacity_policy_organization",
        ),
    )
    op.create_index(
        "ix_capacity_policies_organization_id",
        "capacity_policies",
        ["organization_id"],
        unique=True,
    )

    op.create_table(
        "capacity_assessments",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("organization_id", sa.String(length=36), nullable=False),
        sa.Column("source", sa.String(length=20), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("policy_snapshot", sa.JSON(), nullable=False),
        sa.Column("policy_sha256", sa.String(length=64), nullable=False),
        sa.Column("metrics", sa.JSON(), nullable=False),
        sa.Column("checks", sa.JSON(), nullable=False),
        sa.Column("blockers", sa.JSON(), nullable=False),
        sa.Column("warnings", sa.JSON(), nullable=False),
        sa.Column("active_jobs", sa.Integer(), nullable=False),
        sa.Column("ready_jobs", sa.Integer(), nullable=False),
        sa.Column("held_jobs", sa.Integer(), nullable=False),
        sa.Column("processing_jobs", sa.Integer(), nullable=False),
        sa.Column("waiting_review_jobs", sa.Integer(), nullable=False),
        sa.Column("active_runs", sa.Integer(), nullable=False),
        sa.Column("network_starts_last_minute", sa.Integer(), nullable=False),
        sa.Column("network_starts_last_hour", sa.Integer(), nullable=False),
        sa.Column("estimated_drain_seconds", sa.Integer(), nullable=False),
        sa.Column("queue_utilization_percent", sa.Integer(), nullable=False),
        sa.Column("projected_jobs", sa.Integer(), nullable=False),
        sa.Column("projected_ready_jobs", sa.Integer(), nullable=False),
        sa.Column("projected_runs", sa.Integer(), nullable=False),
        sa.Column("fingerprint", sa.String(length=64), nullable=False),
        sa.Column("created_by_id", sa.String(length=36), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["created_by_id"], ["users.id"]),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_capacity_assessment_org_created",
        "capacity_assessments",
        ["organization_id", "created_at"],
    )
    op.create_index(
        "ix_capacity_assessment_org_status_expiry",
        "capacity_assessments",
        ["organization_id", "status", "expires_at"],
    )
    op.create_index(
        "ix_capacity_assessments_organization_id",
        "capacity_assessments",
        ["organization_id"],
    )
    op.create_index(
        "ix_capacity_assessments_source",
        "capacity_assessments",
        ["source"],
    )
    op.create_index(
        "ix_capacity_assessments_status",
        "capacity_assessments",
        ["status"],
    )
    op.create_index(
        "ix_capacity_assessments_policy_sha256",
        "capacity_assessments",
        ["policy_sha256"],
    )
    op.create_index(
        "ix_capacity_assessments_fingerprint",
        "capacity_assessments",
        ["fingerprint"],
    )
    op.create_index(
        "ix_capacity_assessments_expires_at",
        "capacity_assessments",
        ["expires_at"],
    )


def downgrade() -> None:
    """Удалить capacity evidence first and then the tenant policy table."""

    op.drop_table("capacity_assessments")
    op.drop_table("capacity_policies")
