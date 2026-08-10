"""v1.9 change management and upgrade assurance

Revision ID: 5d9f1b3c7e2a
Revises: 4c8e0a2b6d1f
Create Date: 2026-08-07 04:45:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "5d9f1b3c7e2a"
down_revision: str | Sequence[str] | None = "4c8e0a2b6d1f"
branch_labels = None
depends_on = None

change_type_enum = sa.Enum(
    "UPGRADE",
    "CONFIGURATION",
    "DATABASE_MIGRATION",
    "INFRASTRUCTURE",
    name="changerequesttype",
    native_enum=False,
    length=32,
)
change_status_enum = sa.Enum(
    "DRAFT",
    "APPROVED",
    "IN_PROGRESS",
    "COMPLETED",
    "FAILED",
    "CANCELLED",
    name="changerequeststatus",
    native_enum=False,
    length=24,
)
phase_enum = sa.Enum(
    "PRE_CHANGE", "POST_CHANGE", name="deploymentverificationphase", native_enum=False, length=20
)
readiness_enum = sa.Enum(
    "PASSED", "WARNING", "BLOCKED", name="readinessstatus", native_enum=False, length=20
)


def upgrade() -> None:
    """Применить this Alembic revision in dependency-safe schema order."""
    with op.batch_alter_table("organizations") as b:
        b.add_column(
            sa.Column("maintenance_mode", sa.Boolean(), nullable=False, server_default=sa.false())
        )
        b.add_column(sa.Column("maintenance_reason", sa.Text(), nullable=True))
        b.add_column(sa.Column("maintenance_started_at", sa.DateTime(timezone=True), nullable=True))
        b.add_column(sa.Column("maintenance_started_by_id", sa.String(length=36), nullable=True))
    op.create_table(
        "change_requests",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "organization_id",
            sa.String(36),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("title", sa.String(180), nullable=False),
        sa.Column("change_type", change_type_enum, nullable=False),
        sa.Column("status", change_status_enum, nullable=False),
        sa.Column("current_version", sa.String(40)),
        sa.Column("target_version", sa.String(40)),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("risk_summary", sa.Text(), nullable=False),
        sa.Column("rollback_plan", sa.Text(), nullable=False),
        sa.Column("planned_start_at", sa.DateTime(timezone=True)),
        sa.Column("planned_end_at", sa.DateTime(timezone=True)),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("created_by_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("approved_by_id", sa.String(36), sa.ForeignKey("users.id")),
        sa.Column("approved_at", sa.DateTime(timezone=True)),
        sa.Column("started_by_id", sa.String(36), sa.ForeignKey("users.id")),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("completed_by_id", sa.String(36), sa.ForeignKey("users.id")),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("result_summary", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_change_request_org_status_created",
        "change_requests",
        ["organization_id", "status", "created_at"],
    )
    op.create_index(
        "ix_change_request_org_type", "change_requests", ["organization_id", "change_type"]
    )
    op.create_index("ix_change_requests_organization_id", "change_requests", ["organization_id"])
    op.create_index("ix_change_requests_change_type", "change_requests", ["change_type"])
    op.create_index("ix_change_requests_status", "change_requests", ["status"])
    op.create_index("ix_change_requests_fingerprint", "change_requests", ["fingerprint"])
    op.create_table(
        "deployment_verification_reports",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "organization_id",
            sa.String(36),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "change_request_id",
            sa.String(36),
            sa.ForeignKey("change_requests.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("phase", phase_enum, nullable=False),
        sa.Column("status", readiness_enum, nullable=False),
        sa.Column("expected_version", sa.String(40)),
        sa.Column("observed_version", sa.String(40), nullable=False),
        sa.Column("checks", sa.JSON(), nullable=False),
        sa.Column("blockers", sa.JSON(), nullable=False),
        sa.Column("warnings", sa.JSON(), nullable=False),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("created_by_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_deploy_verify_org_phase_created",
        "deployment_verification_reports",
        ["organization_id", "phase", "created_at"],
    )
    op.create_index(
        "ix_deploy_verify_change_created",
        "deployment_verification_reports",
        ["change_request_id", "created_at"],
    )
    for c in [
        "organization_id",
        "change_request_id",
        "phase",
        "status",
        "fingerprint",
        "expires_at",
    ]:
        op.create_index(
            f"ix_deployment_verification_reports_{c}", "deployment_verification_reports", [c]
        )


def downgrade() -> None:
    """Откатить this Alembic revision in dependency-safe schema order."""
    op.drop_table("deployment_verification_reports")
    op.drop_table("change_requests")
    with op.batch_alter_table("organizations") as b:
        b.drop_column("maintenance_started_by_id")
        b.drop_column("maintenance_started_at")
        b.drop_column("maintenance_reason")
        b.drop_column("maintenance_mode")
