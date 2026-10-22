"""scheduling external links (V1c): change-notice channels and a re-read
request flag

Revision ID: 0039_scheduling_link_watch
Revises: 0038_scheduling_link_token
"""

import sqlalchemy as sa
from alembic import op

revision = "0039_scheduling_link_watch"
down_revision = "0038_scheduling_link_token"
branch_labels = None
depends_on = None

TABLE = "scheduling_external_links"


def upgrade() -> None:
    op.add_column(TABLE, sa.Column("sync_requested_at", sa.DateTime(timezone=True)))
    op.add_column(TABLE, sa.Column("watch_channel_id", sa.String(64)))
    op.add_column(TABLE, sa.Column("watch_resource_id", sa.String(255)))
    op.add_column(TABLE, sa.Column("watch_expires_at", sa.DateTime(timezone=True)))
    op.create_index("ix_scheduling_external_links_watch_channel_id", TABLE, ["watch_channel_id"])


def downgrade() -> None:
    op.drop_index("ix_scheduling_external_links_watch_channel_id", table_name=TABLE)
    for column in ("watch_expires_at", "watch_resource_id", "watch_channel_id"):
        op.drop_column(TABLE, column)
    op.drop_column(TABLE, "sync_requested_at")
