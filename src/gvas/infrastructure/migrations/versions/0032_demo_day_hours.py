"""demo day hours: a demo calendar's date rules, so owner texts can block time

Revision ID: 0032_demo_day_hours
Revises: 0031_owner_commands
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0032_demo_day_hours"
down_revision = "0031_owner_commands"
branch_labels = None
depends_on = None

json_type = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    op.create_table(
        "demo_day_hours",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "business_id",
            sa.Uuid(),
            sa.ForeignKey("businesses.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("intervals", json_type, nullable=False),
        sa.UniqueConstraint("business_id", "day", name="uq_demo_day_hours_day"),
    )


def downgrade() -> None:
    op.drop_table("demo_day_hours")
