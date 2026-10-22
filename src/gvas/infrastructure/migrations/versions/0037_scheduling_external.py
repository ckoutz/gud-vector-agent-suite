"""scheduling external calendars (V1c): links to outside calendars and a
times-only mirror of their busy blocks

Revision ID: 0037_scheduling_external
Revises: 0036_scheduling_messages
"""

import sqlalchemy as sa
from alembic import op

revision = "0037_scheduling_external"
down_revision = "0036_scheduling_messages"
branch_labels = None
depends_on = None

_TZ = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        "scheduling_external_links",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "business_id",
            sa.Uuid(),
            sa.ForeignKey("businesses.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "resource_id",
            sa.Uuid(),
            sa.ForeignKey("scheduling_resources.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("provider", sa.String(16), nullable=False),
        sa.Column("read_busy", sa.Boolean(), nullable=False),
        sa.Column("write_events", sa.Boolean(), nullable=False),
        sa.Column("credentials_ref", sa.String(255)),
        sa.Column("sync_cursor", sa.Text()),
        sa.Column("last_sync_at", _TZ),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("created_at", _TZ, nullable=False),
    )
    op.create_index(
        "ix_scheduling_external_links_resource_id", "scheduling_external_links", ["resource_id"]
    )
    op.create_table(
        "scheduling_external_busy",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "link_id",
            sa.Uuid(),
            sa.ForeignKey("scheduling_external_links.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("external_id", sa.String(255), nullable=False),
        sa.Column("starts_at", _TZ, nullable=False),
        sa.Column("ends_at", _TZ, nullable=False),
    )
    op.create_index("ix_scheduling_external_busy_link_id", "scheduling_external_busy", ["link_id"])


def downgrade() -> None:
    op.drop_table("scheduling_external_busy")
    op.drop_table("scheduling_external_links")
