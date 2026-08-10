"""v1.4 pilot readiness and policy calendar

Revision ID: c9e1f3a5b7d9
Revises: b7c9d2e4f6a8
Create Date: 2026-08-06 16:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c9e1f3a5b7d9"
down_revision: str | Sequence[str] | None = "b7c9d2e4f6a8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


validation_status = sa.Enum(
    "PASSED",
    "FAILED",
    "WRITE_FORBIDDEN",
    name="destinationvalidationstatus",
    native_enum=False,
    length=30,
)
blackout_scope = sa.Enum(
    "ORGANIZATION",
    "CONNECTION",
    "DESTINATION",
    name="blackoutscope",
    native_enum=False,
    length=24,
)
blackout_kind = sa.Enum(
    "ONE_TIME",
    "WEEKLY",
    name="blackoutkind",
    native_enum=False,
    length=24,
)
readiness_status = sa.Enum(
    "PASSED",
    "WARNING",
    "BLOCKED",
    name="readinessstatus",
    native_enum=False,
    length=20,
)


def upgrade() -> None:
    """Применить this Alembic revision in dependency-safe schema order."""
    with op.batch_alter_table("destinations", schema=None) as batch_op:
        batch_op.add_column(sa.Column("validated_at", sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(
            sa.Column("validation_expires_at", sa.DateTime(timezone=True), nullable=True)
        )
        batch_op.create_index("ix_destinations_validated_at", ["validated_at"], unique=False)
        batch_op.create_index(
            "ix_destinations_validation_expires_at", ["validation_expires_at"], unique=False
        )

    # Preserve the meaning of legacy ``validated=true`` rows. The application
    # derives the effective expiry from this timestamp when no explicit expiry
    # is stored.
    op.execute(
        sa.text(
            "UPDATE destinations SET validated_at = updated_at "
            "WHERE validated = :validated AND validated_at IS NULL"
        ).bindparams(validated=True)
    )

    op.create_table(
        "destination_validation_records",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("organization_id", sa.String(length=36), nullable=False),
        sa.Column("destination_id", sa.String(length=36), nullable=False),
        sa.Column("connection_id", sa.String(length=36), nullable=False),
        sa.Column("status", validation_status, nullable=False),
        sa.Column("capabilities", sa.JSON(), nullable=False),
        sa.Column("error_code", sa.String(length=100), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("source", sa.String(length=40), nullable=False),
        sa.Column("checked_by_id", sa.String(length=36), nullable=True),
        sa.Column("checked_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["checked_by_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["connection_id"], ["telegram_connections.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["destination_id"], ["destinations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_destination_validation_records_organization_id",
        "destination_validation_records",
        ["organization_id"],
        unique=False,
    )
    op.create_index(
        "ix_destination_validation_records_destination_id",
        "destination_validation_records",
        ["destination_id"],
        unique=False,
    )
    op.create_index(
        "ix_destination_validation_records_connection_id",
        "destination_validation_records",
        ["connection_id"],
        unique=False,
    )
    op.create_index(
        "ix_destination_validation_records_status",
        "destination_validation_records",
        ["status"],
        unique=False,
    )
    op.create_index(
        "ix_destination_validation_records_checked_at",
        "destination_validation_records",
        ["checked_at"],
        unique=False,
    )
    op.create_index(
        "ix_destination_validation_org_destination_checked",
        "destination_validation_records",
        ["organization_id", "destination_id", "checked_at"],
        unique=False,
    )

    op.create_table(
        "publishing_blackouts",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("organization_id", sa.String(length=36), nullable=False),
        sa.Column("scope", blackout_scope, nullable=False),
        sa.Column("kind", blackout_kind, nullable=False),
        sa.Column("connection_id", sa.String(length=36), nullable=True),
        sa.Column("destination_id", sa.String(length=36), nullable=True),
        sa.Column("title", sa.String(length=180), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("timezone_name", sa.String(length=80), nullable=True),
        sa.Column("weekdays", sa.JSON(), nullable=False),
        sa.Column("start_time", sa.Time(), nullable=True),
        sa.Column("end_time", sa.Time(), nullable=True),
        sa.Column("created_by_id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["connection_id"], ["telegram_connections.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["destination_id"], ["destinations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_publishing_blackouts_organization_id",
        "publishing_blackouts",
        ["organization_id"],
        unique=False,
    )
    op.create_index(
        "ix_publishing_blackouts_scope", "publishing_blackouts", ["scope"], unique=False
    )
    op.create_index(
        "ix_publishing_blackouts_connection_id",
        "publishing_blackouts",
        ["connection_id"],
        unique=False,
    )
    op.create_index(
        "ix_publishing_blackouts_destination_id",
        "publishing_blackouts",
        ["destination_id"],
        unique=False,
    )
    op.create_index(
        "ix_publishing_blackouts_enabled", "publishing_blackouts", ["enabled"], unique=False
    )
    op.create_index(
        "ix_blackout_org_enabled_scope",
        "publishing_blackouts",
        ["organization_id", "enabled", "scope"],
        unique=False,
    )
    op.create_index(
        "ix_blackout_org_starts_ends",
        "publishing_blackouts",
        ["organization_id", "starts_at", "ends_at"],
        unique=False,
    )

    op.create_table(
        "pilot_readiness_reports",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("organization_id", sa.String(length=36), nullable=False),
        sa.Column("campaign_id", sa.String(length=36), nullable=False),
        sa.Column("connection_id", sa.String(length=36), nullable=False),
        sa.Column("fingerprint", sa.String(length=64), nullable=False),
        sa.Column("status", readiness_status, nullable=False),
        sa.Column("checks", sa.JSON(), nullable=False),
        sa.Column("blockers", sa.JSON(), nullable=False),
        sa.Column("warnings", sa.JSON(), nullable=False),
        sa.Column("summary", sa.JSON(), nullable=False),
        sa.Column("preflight_report_id", sa.String(length=36), nullable=True),
        sa.Column("created_by_id", sa.String(length=36), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["campaign_id"], ["campaigns.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["connection_id"], ["telegram_connections.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["preflight_report_id"], ["campaign_preflight_reports.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_pilot_readiness_reports_organization_id",
        "pilot_readiness_reports",
        ["organization_id"],
        unique=False,
    )
    op.create_index(
        "ix_pilot_readiness_reports_campaign_id",
        "pilot_readiness_reports",
        ["campaign_id"],
        unique=False,
    )
    op.create_index(
        "ix_pilot_readiness_reports_connection_id",
        "pilot_readiness_reports",
        ["connection_id"],
        unique=False,
    )
    op.create_index(
        "ix_pilot_readiness_reports_fingerprint",
        "pilot_readiness_reports",
        ["fingerprint"],
        unique=False,
    )
    op.create_index(
        "ix_pilot_readiness_reports_status",
        "pilot_readiness_reports",
        ["status"],
        unique=False,
    )
    op.create_index(
        "ix_pilot_readiness_reports_preflight_report_id",
        "pilot_readiness_reports",
        ["preflight_report_id"],
        unique=False,
    )
    op.create_index(
        "ix_pilot_readiness_reports_expires_at",
        "pilot_readiness_reports",
        ["expires_at"],
        unique=False,
    )
    op.create_index(
        "ix_readiness_org_campaign_created",
        "pilot_readiness_reports",
        ["organization_id", "campaign_id", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_readiness_org_status_expires",
        "pilot_readiness_reports",
        ["organization_id", "status", "expires_at"],
        unique=False,
    )


def downgrade() -> None:
    """Откатить this Alembic revision in dependency-safe schema order."""
    op.drop_index("ix_readiness_org_status_expires", table_name="pilot_readiness_reports")
    op.drop_index("ix_readiness_org_campaign_created", table_name="pilot_readiness_reports")
    op.drop_index("ix_pilot_readiness_reports_expires_at", table_name="pilot_readiness_reports")
    op.drop_index(
        "ix_pilot_readiness_reports_preflight_report_id", table_name="pilot_readiness_reports"
    )
    op.drop_index("ix_pilot_readiness_reports_status", table_name="pilot_readiness_reports")
    op.drop_index("ix_pilot_readiness_reports_fingerprint", table_name="pilot_readiness_reports")
    op.drop_index("ix_pilot_readiness_reports_connection_id", table_name="pilot_readiness_reports")
    op.drop_index("ix_pilot_readiness_reports_campaign_id", table_name="pilot_readiness_reports")
    op.drop_index(
        "ix_pilot_readiness_reports_organization_id", table_name="pilot_readiness_reports"
    )
    op.drop_table("pilot_readiness_reports")

    op.drop_index("ix_blackout_org_starts_ends", table_name="publishing_blackouts")
    op.drop_index("ix_blackout_org_enabled_scope", table_name="publishing_blackouts")
    op.drop_index("ix_publishing_blackouts_enabled", table_name="publishing_blackouts")
    op.drop_index("ix_publishing_blackouts_destination_id", table_name="publishing_blackouts")
    op.drop_index("ix_publishing_blackouts_connection_id", table_name="publishing_blackouts")
    op.drop_index("ix_publishing_blackouts_scope", table_name="publishing_blackouts")
    op.drop_index("ix_publishing_blackouts_organization_id", table_name="publishing_blackouts")
    op.drop_table("publishing_blackouts")

    op.drop_index(
        "ix_destination_validation_org_destination_checked",
        table_name="destination_validation_records",
    )
    op.drop_index(
        "ix_destination_validation_records_checked_at",
        table_name="destination_validation_records",
    )
    op.drop_index(
        "ix_destination_validation_records_status",
        table_name="destination_validation_records",
    )
    op.drop_index(
        "ix_destination_validation_records_connection_id",
        table_name="destination_validation_records",
    )
    op.drop_index(
        "ix_destination_validation_records_destination_id",
        table_name="destination_validation_records",
    )
    op.drop_index(
        "ix_destination_validation_records_organization_id",
        table_name="destination_validation_records",
    )
    op.drop_table("destination_validation_records")

    with op.batch_alter_table("destinations", schema=None) as batch_op:
        batch_op.drop_index("ix_destinations_validation_expires_at")
        batch_op.drop_index("ix_destinations_validated_at")
        batch_op.drop_column("validation_expires_at")
        batch_op.drop_column("validated_at")
