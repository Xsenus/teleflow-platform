"""v1.1 automation flows

Revision ID: 7e31b486a5c9
Revises: 0a418fe81167
Create Date: 2026-08-06 02:30:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "7e31b486a5c9"
down_revision: str | Sequence[str] | None = "0a418fe81167"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Применить this Alembic revision in dependency-safe schema order."""
    op.create_table(
        "automation_flows",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("organization_id", sa.String(length=36), nullable=False),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("definition", sa.JSON(), nullable=False),
        sa.Column("created_by_id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["created_by_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("organization_id", "name", name="uq_flow_org_name"),
    )
    with op.batch_alter_table("automation_flows", schema=None) as batch_op:
        batch_op.create_index("ix_flow_org_active", ["organization_id", "is_active"], unique=False)
        batch_op.create_index(
            batch_op.f("ix_automation_flows_organization_id"),
            ["organization_id"],
            unique=False,
        )

    with op.batch_alter_table("automation_policies", schema=None) as batch_op:
        batch_op.add_column(sa.Column("automation_flow_id", sa.String(length=36), nullable=True))
        batch_op.create_index(
            batch_op.f("ix_automation_policies_automation_flow_id"),
            ["automation_flow_id"],
            unique=False,
        )
        batch_op.create_foreign_key(
            "fk_automation_policies_flow",
            "automation_flows",
            ["automation_flow_id"],
            ["id"],
            ondelete="SET NULL",
        )

    with op.batch_alter_table("conversations", schema=None) as batch_op:
        batch_op.add_column(sa.Column("flow_node_id", sa.String(length=64), nullable=True))
        batch_op.add_column(
            sa.Column(
                "flow_state_json",
                sa.JSON(),
                nullable=False,
                server_default=sa.text("'{}'"),
            )
        )
        batch_op.add_column(
            sa.Column("flow_completed_at", sa.DateTime(timezone=True), nullable=True)
        )


def downgrade() -> None:
    """Откатить this Alembic revision in dependency-safe schema order."""
    with op.batch_alter_table("conversations", schema=None) as batch_op:
        batch_op.drop_column("flow_completed_at")
        batch_op.drop_column("flow_state_json")
        batch_op.drop_column("flow_node_id")

    with op.batch_alter_table("automation_policies", schema=None) as batch_op:
        batch_op.drop_constraint("fk_automation_policies_flow", type_="foreignkey")
        batch_op.drop_index(batch_op.f("ix_automation_policies_automation_flow_id"))
        batch_op.drop_column("automation_flow_id")

    with op.batch_alter_table("automation_flows", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_automation_flows_organization_id"))
        batch_op.drop_index("ix_flow_org_active")
    op.drop_table("automation_flows")
