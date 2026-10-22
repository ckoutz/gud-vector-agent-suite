"""scheduling team calendars (V1e): employee colour and active flag, whose
times Gus offers, and who each visit is assigned to

Revision ID: 0040_scheduling_team
Revises: 0039_scheduling_link_watch
"""

import sqlalchemy as sa
from alembic import op

revision = "0040_scheduling_team"
down_revision = "0039_scheduling_link_watch"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("scheduling_resources", sa.Column("color", sa.String(16)))
    op.add_column(
        "scheduling_resources",
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    op.add_column("scheduling_fs_bookings", sa.Column("assigned_at", sa.DateTime(timezone=True)))
    op.add_column("scheduling_fs_bookings", sa.Column("assigned_by", sa.String(255)))
    op.create_table(
        "scheduling_fs_offering",
        sa.Column(
            "business_id",
            sa.Uuid(),
            sa.ForeignKey("businesses.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "resource_id",
            sa.Uuid(),
            sa.ForeignKey("scheduling_resources.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("position", sa.Integer(), nullable=False),
    )
    op.create_table(
        "scheduling_fs_assignments",
        sa.Column(
            "business_id",
            sa.Uuid(),
            sa.ForeignKey("businesses.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("reference", sa.String(64), primary_key=True),
        sa.Column(
            "resource_id",
            sa.Uuid(),
            sa.ForeignKey("scheduling_resources.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("assigned_by", sa.String(255), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("scheduling_fs_assignments")
    op.drop_table("scheduling_fs_offering")
    op.drop_column("scheduling_fs_bookings", "assigned_by")
    op.drop_column("scheduling_fs_bookings", "assigned_at")
    op.drop_column("scheduling_resources", "active")
    op.drop_column("scheduling_resources", "color")
