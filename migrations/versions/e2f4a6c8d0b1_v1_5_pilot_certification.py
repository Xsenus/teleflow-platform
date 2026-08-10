"""v1.5 pilot certification and redacted diagnostics

Revision ID: e2f4a6c8d0b1
Revises: c9e1f3a5b7d9
Create Date: 2026-08-06 18:30:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e2f4a6c8d0b1"
down_revision: str | Sequence[str] | None = "c9e1f3a5b7d9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


pilot_stage = sa.Enum(
    "LOCAL",
    "SERVICE",
    "FIVE",
    "TWENTY",
    "FIFTY",
    "HUNDRED",
    name="pilotstage",
    native_enum=False,
    length=20,
)
pilot_canary_status = sa.Enum(
    "PENDING",
    "SENT",
    "FAILED",
    "BLOCKED",
    name="pilotcanarystatus",
    native_enum=False,
    length=20,
)
support_bundle_status = sa.Enum(
    "READY",
    "FAILED",
    "EXPIRED",
    "DELETED",
    name="supportbundlestatus",
    native_enum=False,
    length=20,
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
    with op.batch_alter_table("organizations", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "pilot_stage",
                pilot_stage,
                server_default="LOCAL",
                nullable=False,
            )
        )
        batch_op.add_column(
            sa.Column("pilot_stage_updated_at", sa.DateTime(timezone=True), nullable=True)
        )
        batch_op.add_column(
            sa.Column("pilot_stage_updated_by_id", sa.String(length=36), nullable=True)
        )
        batch_op.add_column(sa.Column("pilot_stage_note", sa.Text(), nullable=True))
        batch_op.create_index("ix_organizations_pilot_stage", ["pilot_stage"], unique=False)

    # Existing 1.4 installations already had an effective route capacity of 100.
    # Preserve that behaviour during upgrade, while newly bootstrapped tenants
    # start at LOCAL through the application/model default.
    op.execute(
        sa.text(
            "UPDATE organizations "
            "SET pilot_stage = 'HUNDRED', "
            "pilot_stage_updated_at = CURRENT_TIMESTAMP, "
            "pilot_stage_note = 'Совместимость при обновлении с TeleFlow 1.4'"
        )
    )

    op.create_table(
        "pilot_stage_assessments",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("organization_id", sa.String(length=36), nullable=False),
        sa.Column("current_stage", pilot_stage, nullable=False),
        sa.Column("requested_stage", pilot_stage, nullable=False),
        sa.Column("status", readiness_status, nullable=False),
        sa.Column("checks", sa.JSON(), nullable=False),
        sa.Column("blockers", sa.JSON(), nullable=False),
        sa.Column("warnings", sa.JSON(), nullable=False),
        sa.Column("summary", sa.JSON(), nullable=False),
        sa.Column("fingerprint", sa.String(length=64), nullable=False),
        sa.Column("created_by_id", sa.String(length=36), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["created_by_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_pilot_stage_assessments_organization_id",
        "pilot_stage_assessments",
        ["organization_id"],
        unique=False,
    )
    op.create_index(
        "ix_pilot_stage_assessments_requested_stage",
        "pilot_stage_assessments",
        ["requested_stage"],
        unique=False,
    )
    op.create_index(
        "ix_pilot_stage_assessments_status",
        "pilot_stage_assessments",
        ["status"],
        unique=False,
    )
    op.create_index(
        "ix_pilot_stage_assessments_fingerprint",
        "pilot_stage_assessments",
        ["fingerprint"],
        unique=False,
    )
    op.create_index(
        "ix_pilot_stage_assessments_expires_at",
        "pilot_stage_assessments",
        ["expires_at"],
        unique=False,
    )
    op.create_index(
        "ix_pilot_stage_assessment_org_created",
        "pilot_stage_assessments",
        ["organization_id", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_pilot_stage_assessment_org_status_expires",
        "pilot_stage_assessments",
        ["organization_id", "status", "expires_at"],
        unique=False,
    )

    op.create_table(
        "pilot_canary_attempts",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("organization_id", sa.String(length=36), nullable=False),
        sa.Column("connection_id", sa.String(length=36), nullable=False),
        sa.Column("destination_id", sa.String(length=36), nullable=False),
        sa.Column("status", pilot_canary_status, nullable=False),
        sa.Column("marker", sa.String(length=80), nullable=False),
        sa.Column("body_sha256", sa.String(length=64), nullable=False),
        sa.Column("telegram_message_id", sa.String(length=100), nullable=True),
        sa.Column("is_fake", sa.Boolean(), nullable=False),
        sa.Column("error_code", sa.String(length=100), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("requested_by_id", sa.String(length=36), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["connection_id"], ["telegram_connections.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["destination_id"], ["destinations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["requested_by_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_pilot_canary_attempts_organization_id",
        "pilot_canary_attempts",
        ["organization_id"],
        unique=False,
    )
    op.create_index(
        "ix_pilot_canary_attempts_connection_id",
        "pilot_canary_attempts",
        ["connection_id"],
        unique=False,
    )
    op.create_index(
        "ix_pilot_canary_attempts_destination_id",
        "pilot_canary_attempts",
        ["destination_id"],
        unique=False,
    )
    op.create_index(
        "ix_pilot_canary_attempts_status",
        "pilot_canary_attempts",
        ["status"],
        unique=False,
    )
    op.create_index(
        "ix_pilot_canary_attempts_marker",
        "pilot_canary_attempts",
        ["marker"],
        unique=True,
    )
    op.create_index(
        "ix_pilot_canary_org_destination_created",
        "pilot_canary_attempts",
        ["organization_id", "destination_id", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_pilot_canary_org_status",
        "pilot_canary_attempts",
        ["organization_id", "status"],
        unique=False,
    )

    op.create_table(
        "support_bundles",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("organization_id", sa.String(length=36), nullable=False),
        sa.Column("status", support_bundle_status, nullable=False),
        sa.Column("storage_key", sa.String(length=1000), nullable=True),
        sa.Column("sha256", sa.String(length=64), nullable=True),
        sa.Column("size_bytes", sa.BigInteger(), nullable=True),
        sa.Column("sections", sa.JSON(), nullable=False),
        sa.Column("created_by_id", sa.String(length=36), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["created_by_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_support_bundles_organization_id",
        "support_bundles",
        ["organization_id"],
        unique=False,
    )
    op.create_index(
        "ix_support_bundles_status",
        "support_bundles",
        ["status"],
        unique=False,
    )
    op.create_index(
        "ix_support_bundles_sha256",
        "support_bundles",
        ["sha256"],
        unique=False,
    )
    op.create_index(
        "ix_support_bundles_expires_at",
        "support_bundles",
        ["expires_at"],
        unique=False,
    )
    op.create_index(
        "ix_support_bundle_org_created",
        "support_bundles",
        ["organization_id", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_support_bundle_org_status_expires",
        "support_bundles",
        ["organization_id", "status", "expires_at"],
        unique=False,
    )


def downgrade() -> None:
    """Откатить this Alembic revision in dependency-safe schema order."""
    op.drop_index("ix_support_bundle_org_status_expires", table_name="support_bundles")
    op.drop_index("ix_support_bundle_org_created", table_name="support_bundles")
    op.drop_index("ix_support_bundles_expires_at", table_name="support_bundles")
    op.drop_index("ix_support_bundles_sha256", table_name="support_bundles")
    op.drop_index("ix_support_bundles_status", table_name="support_bundles")
    op.drop_index("ix_support_bundles_organization_id", table_name="support_bundles")
    op.drop_table("support_bundles")

    op.drop_index("ix_pilot_canary_org_status", table_name="pilot_canary_attempts")
    op.drop_index("ix_pilot_canary_org_destination_created", table_name="pilot_canary_attempts")
    op.drop_index("ix_pilot_canary_attempts_marker", table_name="pilot_canary_attempts")
    op.drop_index("ix_pilot_canary_attempts_status", table_name="pilot_canary_attempts")
    op.drop_index("ix_pilot_canary_attempts_destination_id", table_name="pilot_canary_attempts")
    op.drop_index("ix_pilot_canary_attempts_connection_id", table_name="pilot_canary_attempts")
    op.drop_index("ix_pilot_canary_attempts_organization_id", table_name="pilot_canary_attempts")
    op.drop_table("pilot_canary_attempts")

    op.drop_index(
        "ix_pilot_stage_assessment_org_status_expires",
        table_name="pilot_stage_assessments",
    )
    op.drop_index(
        "ix_pilot_stage_assessment_org_created",
        table_name="pilot_stage_assessments",
    )
    op.drop_index("ix_pilot_stage_assessments_expires_at", table_name="pilot_stage_assessments")
    op.drop_index("ix_pilot_stage_assessments_fingerprint", table_name="pilot_stage_assessments")
    op.drop_index("ix_pilot_stage_assessments_status", table_name="pilot_stage_assessments")
    op.drop_index(
        "ix_pilot_stage_assessments_requested_stage", table_name="pilot_stage_assessments"
    )
    op.drop_index(
        "ix_pilot_stage_assessments_organization_id", table_name="pilot_stage_assessments"
    )
    op.drop_table("pilot_stage_assessments")

    with op.batch_alter_table("organizations", schema=None) as batch_op:
        batch_op.drop_index("ix_organizations_pilot_stage")
        batch_op.drop_column("pilot_stage_note")
        batch_op.drop_column("pilot_stage_updated_by_id")
        batch_op.drop_column("pilot_stage_updated_at")
        batch_op.drop_column("pilot_stage")
