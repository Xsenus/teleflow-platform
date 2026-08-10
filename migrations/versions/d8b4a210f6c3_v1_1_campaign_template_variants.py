"""v1.1 campaign template variants

Revision ID: d8b4a210f6c3
Revises: c3f5a8d91b27
Create Date: 2026-08-06 05:30:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d8b4a210f6c3"
down_revision: str | Sequence[str] | None = "c3f5a8d91b27"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Применить this Alembic revision in dependency-safe schema order."""
    with op.batch_alter_table("campaigns", schema=None) as batch_op:
        batch_op.add_column(sa.Column("secondary_template_id", sa.String(length=36), nullable=True))
        batch_op.add_column(
            sa.Column(
                "secondary_template_weight",
                sa.Integer(),
                nullable=False,
                server_default=sa.text("0"),
            )
        )
        batch_op.create_index(
            "ix_campaigns_secondary_template_id", ["secondary_template_id"], unique=False
        )
        batch_op.create_foreign_key(
            "fk_campaign_secondary_template",
            "message_templates",
            ["secondary_template_id"],
            ["id"],
        )


def downgrade() -> None:
    """Откатить this Alembic revision in dependency-safe schema order."""
    with op.batch_alter_table("campaigns", schema=None) as batch_op:
        batch_op.drop_constraint("fk_campaign_secondary_template", type_="foreignkey")
        batch_op.drop_index("ix_campaigns_secondary_template_id")
        batch_op.drop_column("secondary_template_weight")
        batch_op.drop_column("secondary_template_id")
