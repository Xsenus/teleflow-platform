"""v2.3 execution fencing and failover assurance

Revision ID: 9b3d5f7a1c4e
Revises: 8a2c4e6f0b3d
Create Date: 2026-08-08 00:30:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "9b3d5f7a1c4e"
down_revision: str | Sequence[str] | None = "8a2c4e6f0b3d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Применить this Alembic revision in dependency-safe schema order."""
    op.create_table(
        "execution_sites",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("organization_id", sa.String(length=36), nullable=False),
        sa.Column("site_key", sa.String(length=80), nullable=False),
        sa.Column("display_name", sa.String(length=160), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("last_worker_id", sa.String(length=120), nullable=True),
        sa.Column("hostname", sa.String(length=255), nullable=True),
        sa.Column("version", sa.String(length=40), nullable=True),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("details", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("organization_id", "site_key", name="uq_execution_site_org_key"),
    )
    op.create_index("ix_execution_sites_organization_id", "execution_sites", ["organization_id"])
    op.create_index("ix_execution_sites_site_key", "execution_sites", ["site_key"])
    op.create_index("ix_execution_sites_last_seen_at", "execution_sites", ["last_seen_at"])
    op.create_index(
        "ix_execution_site_org_seen",
        "execution_sites",
        ["organization_id", "last_seen_at"],
    )

    op.create_table(
        "execution_leases",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("organization_id", sa.String(length=36), nullable=False),
        sa.Column("active_site_key", sa.String(length=80), nullable=False),
        sa.Column("holder_worker_id", sa.String(length=120), nullable=True),
        sa.Column("epoch", sa.BigInteger(), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "ACTIVE",
                "DRAINING",
                "FROZEN",
                name="executionleasestatus",
                native_enum=False,
                length=20,
            ),
            nullable=False,
        ),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_renewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("drain_reason", sa.Text(), nullable=True),
        sa.Column("drain_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("organization_id", name="uq_execution_lease_org"),
    )
    op.create_index("ix_execution_leases_organization_id", "execution_leases", ["organization_id"])
    op.create_index("ix_execution_leases_active_site_key", "execution_leases", ["active_site_key"])
    op.create_index(
        "ix_execution_leases_holder_worker_id", "execution_leases", ["holder_worker_id"]
    )
    op.create_index("ix_execution_leases_status", "execution_leases", ["status"])
    op.create_index(
        "ix_execution_leases_lease_expires_at", "execution_leases", ["lease_expires_at"]
    )
    op.create_index(
        "ix_execution_lease_site_status",
        "execution_leases",
        ["active_site_key", "status"],
    )

    op.create_table(
        "failover_requests",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("organization_id", sa.String(length=36), nullable=False),
        sa.Column("source_site_key", sa.String(length=80), nullable=False),
        sa.Column("target_site_key", sa.String(length=80), nullable=False),
        sa.Column("source_epoch", sa.BigInteger(), nullable=False),
        sa.Column("target_epoch", sa.BigInteger(), nullable=True),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "REQUESTED",
                "COMPLETED",
                "CANCELLED",
                "FAILED",
                name="failoverrequeststatus",
                native_enum=False,
                length=20,
            ),
            nullable=False,
        ),
        sa.Column("requested_by_id", sa.String(length=36), nullable=False),
        sa.Column("approved_by_id", sa.String(length=36), nullable=True),
        sa.Column("blockers", sa.JSON(), nullable=False),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["requested_by_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["approved_by_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_failover_requests_organization_id", "failover_requests", ["organization_id"]
    )
    op.create_index("ix_failover_requests_status", "failover_requests", ["status"])
    op.create_index(
        "ix_failover_org_status_created",
        "failover_requests",
        ["organization_id", "status", "created_at"],
    )

    with op.batch_alter_table("delivery_jobs", schema=None) as batch_op:
        batch_op.add_column(sa.Column("execution_site_key", sa.String(length=80), nullable=True))
        batch_op.add_column(sa.Column("execution_epoch", sa.BigInteger(), nullable=True))
        batch_op.create_index("ix_delivery_jobs_execution_site_key", ["execution_site_key"])
        batch_op.create_index("ix_delivery_jobs_execution_epoch", ["execution_epoch"])

    op.create_table(
        "delivery_attempts",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("organization_id", sa.String(length=36), nullable=False),
        sa.Column("job_id", sa.String(length=36), nullable=False),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("worker_id", sa.String(length=120), nullable=False),
        sa.Column("site_key", sa.String(length=80), nullable=False),
        sa.Column("fence_epoch", sa.BigInteger(), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "PREPARED",
                "NETWORK_STARTED",
                "SENT",
                "FAILED",
                "UNCERTAIN",
                "ABANDONED",
                "RECONCILED_NOT_SENT",
                "RECONCILED_SKIPPED",
                name="deliveryattemptstatus",
                native_enum=False,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("prepared_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("network_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("telegram_message_id", sa.String(length=100), nullable=True),
        sa.Column("error_code", sa.String(length=100), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("details", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["job_id"], ["delivery_jobs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("job_id", "attempt_number", name="uq_delivery_attempt_job_number"),
    )
    op.create_index(
        "ix_delivery_attempts_organization_id", "delivery_attempts", ["organization_id"]
    )
    op.create_index("ix_delivery_attempts_job_id", "delivery_attempts", ["job_id"])
    op.create_index("ix_delivery_attempts_site_key", "delivery_attempts", ["site_key"])
    op.create_index("ix_delivery_attempts_fence_epoch", "delivery_attempts", ["fence_epoch"])
    op.create_index("ix_delivery_attempts_status", "delivery_attempts", ["status"])
    op.create_index(
        "ix_delivery_attempt_org_status",
        "delivery_attempts",
        ["organization_id", "status"],
    )
    op.create_index(
        "ix_delivery_attempt_job_created",
        "delivery_attempts",
        ["job_id", "created_at"],
    )


def downgrade() -> None:
    """Откатить this Alembic revision in dependency-safe schema order."""
    op.drop_index("ix_delivery_attempt_job_created", table_name="delivery_attempts")
    op.drop_index("ix_delivery_attempt_org_status", table_name="delivery_attempts")
    op.drop_index("ix_delivery_attempts_status", table_name="delivery_attempts")
    op.drop_index("ix_delivery_attempts_fence_epoch", table_name="delivery_attempts")
    op.drop_index("ix_delivery_attempts_site_key", table_name="delivery_attempts")
    op.drop_index("ix_delivery_attempts_job_id", table_name="delivery_attempts")
    op.drop_index("ix_delivery_attempts_organization_id", table_name="delivery_attempts")
    op.drop_table("delivery_attempts")

    with op.batch_alter_table("delivery_jobs", schema=None) as batch_op:
        batch_op.drop_index("ix_delivery_jobs_execution_epoch")
        batch_op.drop_index("ix_delivery_jobs_execution_site_key")
        batch_op.drop_column("execution_epoch")
        batch_op.drop_column("execution_site_key")

    op.drop_index("ix_failover_org_status_created", table_name="failover_requests")
    op.drop_index("ix_failover_requests_status", table_name="failover_requests")
    op.drop_index("ix_failover_requests_organization_id", table_name="failover_requests")
    op.drop_table("failover_requests")

    op.drop_index("ix_execution_lease_site_status", table_name="execution_leases")
    op.drop_index("ix_execution_leases_lease_expires_at", table_name="execution_leases")
    op.drop_index("ix_execution_leases_status", table_name="execution_leases")
    op.drop_index("ix_execution_leases_holder_worker_id", table_name="execution_leases")
    op.drop_index("ix_execution_leases_active_site_key", table_name="execution_leases")
    op.drop_index("ix_execution_leases_organization_id", table_name="execution_leases")
    op.drop_table("execution_leases")

    op.drop_index("ix_execution_site_org_seen", table_name="execution_sites")
    op.drop_index("ix_execution_sites_last_seen_at", table_name="execution_sites")
    op.drop_index("ix_execution_sites_site_key", table_name="execution_sites")
    op.drop_index("ix_execution_sites_organization_id", table_name="execution_sites")
    op.drop_table("execution_sites")
