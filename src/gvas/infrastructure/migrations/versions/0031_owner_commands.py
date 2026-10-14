"""owner commands: changes the owner asked Gus for by text (the command log)

Revision ID: 0031_owner_commands
Revises: 0030_demo_invoices
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0031_owner_commands"
down_revision = "0030_demo_invoices"
branch_labels = None
depends_on = None

json_type = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    op.create_table(
        "owner_commands",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "business_id",
            sa.Uuid(),
            sa.ForeignKey("businesses.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("conversation_ref", sa.String(255), nullable=False),
        sa.Column("tool", sa.String(64), nullable=False),
        sa.Column("arguments", json_type, nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("sensitive", sa.Boolean(), nullable=False),
        sa.Column("requested_text", sa.Text(), nullable=False),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("outcome", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_owner_commands_business_state", "owner_commands", ["business_id", "state"])
    op.create_index(
        "ix_owner_commands_business_created", "owner_commands", ["business_id", "created_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_owner_commands_business_created", table_name="owner_commands")
    op.drop_index("ix_owner_commands_business_state", table_name="owner_commands")
    op.drop_table("owner_commands")
