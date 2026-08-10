"""v1.2 governance and resilience

Revision ID: f4a6b8c12d34
Revises: d8b4a210f6c3
Create Date: 2026-08-06 08:30:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f4a6b8c12d34"
down_revision: str | Sequence[str] | None = "d8b4a210f6c3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Применить this Alembic revision in dependency-safe schema order."""
    with op.batch_alter_table("organizations", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "publishing_paused",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
        )
        batch_op.add_column(sa.Column("publishing_pause_reason", sa.Text(), nullable=True))
        batch_op.add_column(
            sa.Column("publishing_paused_at", sa.DateTime(timezone=True), nullable=True)
        )
        batch_op.add_column(
            sa.Column("publishing_paused_by_id", sa.String(length=36), nullable=True)
        )
        batch_op.add_column(
            sa.Column(
                "require_distinct_campaign_approver",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
        )
        batch_op.add_column(
            sa.Column(
                "high_risk_destination_threshold",
                sa.Integer(),
                nullable=False,
                server_default=sa.text("20"),
            )
        )
        batch_op.add_column(
            sa.Column(
                "high_risk_required_approvals",
                sa.Integer(),
                nullable=False,
                server_default=sa.text("2"),
            )
        )
        batch_op.add_column(
            sa.Column(
                "approval_request_ttl_hours",
                sa.Integer(),
                nullable=False,
                server_default=sa.text("24"),
            )
        )

    with op.batch_alter_table("campaigns", schema=None) as batch_op:
        batch_op.add_column(sa.Column("approved_fingerprint", sa.String(length=64), nullable=True))
        batch_op.add_column(
            sa.Column("active_approval_request_id", sa.String(length=36), nullable=True)
        )
        batch_op.create_index(
            "ix_campaigns_approved_fingerprint", ["approved_fingerprint"], unique=False
        )
        batch_op.create_index(
            "ix_campaigns_active_approval_request_id",
            ["active_approval_request_id"],
            unique=False,
        )

    with op.batch_alter_table("audit_logs", schema=None) as batch_op:
        batch_op.add_column(sa.Column("sequence", sa.BigInteger(), nullable=True))
        batch_op.add_column(sa.Column("prev_hash", sa.String(length=64), nullable=True))
        batch_op.add_column(sa.Column("entry_hash", sa.String(length=64), nullable=True))
        batch_op.add_column(sa.Column("chain_version", sa.Integer(), nullable=True))
        batch_op.create_index("ix_audit_logs_entry_hash", ["entry_hash"], unique=False)
        batch_op.create_index(
            "ix_audit_org_sequence", ["organization_id", "sequence"], unique=False
        )

    op.create_table(
        "audit_chain_states",
        sa.Column("chain_key", sa.String(length=80), nullable=False),
        sa.Column("organization_id", sa.String(length=36), nullable=True),
        sa.Column("last_sequence", sa.BigInteger(), nullable=False, server_default=sa.text("0")),
        sa.Column(
            "last_hash",
            sa.String(length=64),
            nullable=False,
            server_default=sa.text("'" + ("0" * 64) + "'"),
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("chain_key"),
    )
    op.create_index(
        "ix_audit_chain_states_organization_id",
        "audit_chain_states",
        ["organization_id"],
        unique=False,
    )

    # Pre-create one chain head per existing organization. This avoids a race
    # between the first two concurrent audit writes after an upgrade. Existing
    # legacy audit rows are intentionally not rewritten inside Alembic; the
    # documented offline backfill command performs that operation explicitly.
    bind = op.get_bind()
    organizations = sa.table("organizations", sa.column("id", sa.String(length=36)))
    chain_states = sa.table(
        "audit_chain_states",
        sa.column("chain_key", sa.String(length=80)),
        sa.column("organization_id", sa.String(length=36)),
        sa.column("last_sequence", sa.BigInteger()),
        sa.column("last_hash", sa.String(length=64)),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )
    now = sa.func.now()
    for organization_id in bind.execute(sa.select(organizations.c.id)).scalars():
        bind.execute(
            chain_states.insert().values(
                chain_key=organization_id,
                organization_id=organization_id,
                last_sequence=0,
                last_hash="0" * 64,
                updated_at=now,
            )
        )
    bind.execute(
        chain_states.insert().values(
            chain_key="__system__",
            organization_id=None,
            last_sequence=0,
            last_hash="0" * 64,
            updated_at=now,
        )
    )

    op.create_table(
        "campaign_approval_requests",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("organization_id", sa.String(length=36), nullable=False),
        sa.Column("campaign_id", sa.String(length=36), nullable=False),
        sa.Column("fingerprint", sa.String(length=64), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "PENDING",
                "APPROVED",
                "REJECTED",
                "CANCELLED",
                "EXPIRED",
                name="campaignapprovalstatus",
                native_enum=False,
                length=20,
            ),
            nullable=False,
        ),
        sa.Column("required_approvals", sa.Integer(), nullable=False),
        sa.Column("require_distinct_requester", sa.Boolean(), nullable=False),
        sa.Column("requested_by_id", sa.String(length=36), nullable=False),
        sa.Column("request_note", sa.Text(), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["campaign_id"], ["campaigns.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["requested_by_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_campaign_approval_requests_organization_id",
        "campaign_approval_requests",
        ["organization_id"],
        unique=False,
    )
    op.create_index(
        "ix_campaign_approval_requests_campaign_id",
        "campaign_approval_requests",
        ["campaign_id"],
        unique=False,
    )
    op.create_index(
        "ix_campaign_approval_requests_status",
        "campaign_approval_requests",
        ["status"],
        unique=False,
    )
    op.create_index(
        "ix_campaign_approval_org_status_created",
        "campaign_approval_requests",
        ["organization_id", "status", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_campaign_approval_campaign_status",
        "campaign_approval_requests",
        ["campaign_id", "status"],
        unique=False,
    )

    op.create_table(
        "campaign_approval_decisions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("organization_id", sa.String(length=36), nullable=False),
        sa.Column("request_id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column(
            "decision",
            sa.Enum(
                "APPROVE",
                "REJECT",
                name="approvaldecision",
                native_enum=False,
                length=16,
            ),
            nullable=False,
        ),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["request_id"], ["campaign_approval_requests.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("request_id", "user_id", name="uq_campaign_approval_user"),
    )
    op.create_index(
        "ix_campaign_approval_decisions_organization_id",
        "campaign_approval_decisions",
        ["organization_id"],
        unique=False,
    )
    op.create_index(
        "ix_campaign_approval_decisions_request_id",
        "campaign_approval_decisions",
        ["request_id"],
        unique=False,
    )
    op.create_index(
        "ix_campaign_approval_decisions_user_id",
        "campaign_approval_decisions",
        ["user_id"],
        unique=False,
    )
    op.create_index(
        "ix_campaign_approval_decision_org_created",
        "campaign_approval_decisions",
        ["organization_id", "created_at"],
        unique=False,
    )

    op.create_table(
        "notifications",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("organization_id", sa.String(length=36), nullable=False),
        sa.Column("event_type", sa.String(length=120), nullable=False),
        sa.Column(
            "severity",
            sa.Enum(
                "INFO",
                "WARNING",
                "CRITICAL",
                name="safetyseverity",
                native_enum=False,
                length=20,
            ),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.Enum(
                "UNREAD",
                "READ",
                "ACKNOWLEDGED",
                name="notificationstatus",
                native_enum=False,
                length=20,
            ),
            nullable=False,
        ),
        sa.Column("title", sa.String(length=220), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("entity_type", sa.String(length=80), nullable=True),
        sa.Column("entity_id", sa.String(length=80), nullable=True),
        sa.Column("dedup_key", sa.String(length=240), nullable=True),
        sa.Column("details", sa.JSON(), nullable=False),
        sa.Column("occurrence_count", sa.Integer(), nullable=False),
        sa.Column("last_occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("read_by_id", sa.String(length=36), nullable=True),
        sa.Column("acknowledged_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("acknowledged_by_id", sa.String(length=36), nullable=True),
        sa.ForeignKeyConstraint(["acknowledged_by_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["read_by_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_notifications_organization_id",
        "notifications",
        ["organization_id"],
        unique=False,
    )
    op.create_index("ix_notifications_event_type", "notifications", ["event_type"], unique=False)
    op.create_index("ix_notifications_status", "notifications", ["status"], unique=False)
    op.create_index("ix_notifications_entity_id", "notifications", ["entity_id"], unique=False)
    op.create_index(
        "ix_notification_org_status_created",
        "notifications",
        ["organization_id", "status", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_notification_org_dedup",
        "notifications",
        ["organization_id", "dedup_key"],
        unique=False,
    )


def downgrade() -> None:
    """Откатить this Alembic revision in dependency-safe schema order."""
    op.drop_index("ix_notification_org_dedup", table_name="notifications")
    op.drop_index("ix_notification_org_status_created", table_name="notifications")
    op.drop_index("ix_notifications_entity_id", table_name="notifications")
    op.drop_index("ix_notifications_status", table_name="notifications")
    op.drop_index("ix_notifications_event_type", table_name="notifications")
    op.drop_index("ix_notifications_organization_id", table_name="notifications")
    op.drop_table("notifications")

    op.drop_index(
        "ix_campaign_approval_decision_org_created",
        table_name="campaign_approval_decisions",
    )
    op.drop_index(
        "ix_campaign_approval_decisions_user_id", table_name="campaign_approval_decisions"
    )
    op.drop_index(
        "ix_campaign_approval_decisions_request_id", table_name="campaign_approval_decisions"
    )
    op.drop_index(
        "ix_campaign_approval_decisions_organization_id",
        table_name="campaign_approval_decisions",
    )
    op.drop_table("campaign_approval_decisions")

    op.drop_index("ix_campaign_approval_campaign_status", table_name="campaign_approval_requests")
    op.drop_index(
        "ix_campaign_approval_org_status_created", table_name="campaign_approval_requests"
    )
    op.drop_index("ix_campaign_approval_requests_status", table_name="campaign_approval_requests")
    op.drop_index(
        "ix_campaign_approval_requests_campaign_id", table_name="campaign_approval_requests"
    )
    op.drop_index(
        "ix_campaign_approval_requests_organization_id",
        table_name="campaign_approval_requests",
    )
    op.drop_table("campaign_approval_requests")

    op.drop_index("ix_audit_chain_states_organization_id", table_name="audit_chain_states")
    op.drop_table("audit_chain_states")

    with op.batch_alter_table("audit_logs", schema=None) as batch_op:
        batch_op.drop_index("ix_audit_org_sequence")
        batch_op.drop_index("ix_audit_logs_entry_hash")
        batch_op.drop_column("chain_version")
        batch_op.drop_column("entry_hash")
        batch_op.drop_column("prev_hash")
        batch_op.drop_column("sequence")

    with op.batch_alter_table("campaigns", schema=None) as batch_op:
        batch_op.drop_index("ix_campaigns_active_approval_request_id")
        batch_op.drop_index("ix_campaigns_approved_fingerprint")
        batch_op.drop_column("active_approval_request_id")
        batch_op.drop_column("approved_fingerprint")

    with op.batch_alter_table("organizations", schema=None) as batch_op:
        batch_op.drop_column("approval_request_ttl_hours")
        batch_op.drop_column("high_risk_required_approvals")
        batch_op.drop_column("high_risk_destination_threshold")
        batch_op.drop_column("require_distinct_campaign_approver")
        batch_op.drop_column("publishing_paused_by_id")
        batch_op.drop_column("publishing_paused_at")
        batch_op.drop_column("publishing_pause_reason")
        batch_op.drop_column("publishing_paused")
