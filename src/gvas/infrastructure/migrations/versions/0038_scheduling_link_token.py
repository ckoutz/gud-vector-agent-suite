"""scheduling external links (V1c): the outside calendar's refresh token,
sealed with GVAS_CALENDAR_TOKEN_KEY

Revision ID: 0038_scheduling_link_token
Revises: 0037_scheduling_external
"""

import sqlalchemy as sa
from alembic import op

revision = "0038_scheduling_link_token"
down_revision = "0037_scheduling_external"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("scheduling_external_links", sa.Column("refresh_token_sealed", sa.Text()))


def downgrade() -> None:
    op.drop_column("scheduling_external_links", "refresh_token_sealed")
