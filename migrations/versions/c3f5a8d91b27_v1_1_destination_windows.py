"""v1.1 destination publication windows

Revision ID: c3f5a8d91b27
Revises: 7e31b486a5c9
Create Date: 2026-08-06 04:10:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c3f5a8d91b27"
down_revision: str | Sequence[str] | None = "7e31b486a5c9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Применить this Alembic revision in dependency-safe schema order."""
    with op.batch_alter_table("destinations", schema=None) as batch_op:
        batch_op.add_column(sa.Column("timezone_name", sa.String(length=80), nullable=True))
        batch_op.add_column(
            sa.Column(
                "allowed_weekdays",
                sa.JSON(),
                nullable=False,
                server_default=sa.text("'[]'"),
            )
        )
        batch_op.add_column(sa.Column("allowed_start_time", sa.Time(), nullable=True))
        batch_op.add_column(sa.Column("allowed_end_time", sa.Time(), nullable=True))
        batch_op.add_column(sa.Column("cooldown_minutes_override", sa.Integer(), nullable=True))


def downgrade() -> None:
    """Откатить this Alembic revision in dependency-safe schema order."""
    with op.batch_alter_table("destinations", schema=None) as batch_op:
        batch_op.drop_column("cooldown_minutes_override")
        batch_op.drop_column("allowed_end_time")
        batch_op.drop_column("allowed_start_time")
        batch_op.drop_column("allowed_weekdays")
        batch_op.drop_column("timezone_name")
