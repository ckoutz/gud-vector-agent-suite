"""scheduling messages (V1b): participants, per-person reminders and
one-visit action links

Revision ID: 0036_scheduling_messages
Revises: 0035_scheduling_engine
"""

import sqlalchemy as sa
from alembic import op

revision = "0036_scheduling_messages"
down_revision = "0035_scheduling_engine"
branch_labels = None
depends_on = None

_TZ = sa.DateTime(timezone=True)
_JSON = sa.JSON().with_variant(sa.dialects.postgresql.JSONB(), "postgresql")


def _business() -> sa.Column:
    return sa.Column(
        "business_id", sa.Uuid(), sa.ForeignKey("businesses.id", ondelete="CASCADE"), nullable=False
    )


def _event(**kwargs: object) -> sa.Column:
    return sa.Column(
        "event_id",
        sa.Uuid(),
        sa.ForeignKey("scheduling_events.id", ondelete="CASCADE"),
        **kwargs,  # type: ignore[arg-type]
    )


def upgrade() -> None:
    op.create_table(
        "scheduling_participants",
        sa.Column("id", sa.Uuid(), primary_key=True),
        _business(),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("email", sa.String(320)),
        sa.Column("phone", sa.String(32)),
        sa.Column("time_zone", sa.String(64)),
        sa.Column("created_at", _TZ, nullable=False),
    )
    op.create_index(
        "ix_scheduling_participants_business_id", "scheduling_participants", ["business_id"]
    )
    op.create_table(
        "scheduling_event_participants",
        _event(primary_key=True),
        sa.Column(
            "participant_id",
            sa.Uuid(),
            sa.ForeignKey("scheduling_participants.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("role", sa.String(16), nullable=False),
    )
    op.create_table(
        "scheduling_reminders",
        sa.Column("id", sa.Uuid(), primary_key=True),
        _business(),
        _event(nullable=False),
        sa.Column(
            "participant_id",
            sa.Uuid(),
            sa.ForeignKey("scheduling_participants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("channel", sa.String(16), nullable=False),
        sa.Column("fire_at", _TZ, nullable=False),
        sa.Column("event_version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("detail", sa.String(255)),
        sa.Column("created_at", _TZ, nullable=False),
        sa.Column("sent_at", _TZ),
    )
    op.create_index("ix_scheduling_reminders_due", "scheduling_reminders", ["status", "fire_at"])
    op.create_index("ix_scheduling_reminders_event_id", "scheduling_reminders", ["event_id"])
    op.create_table(
        "scheduling_action_tokens",
        sa.Column("token_hash", sa.String(64), primary_key=True),
        _business(),
        _event(nullable=False),
        sa.Column("actions", _JSON, nullable=False),
        sa.Column("expires_at", _TZ, nullable=False),
        sa.Column("used_at", _TZ),
        sa.Column("created_at", _TZ, nullable=False),
    )
    op.create_index(
        "ix_scheduling_action_tokens_event_id", "scheduling_action_tokens", ["event_id"]
    )


def downgrade() -> None:
    for table in (
        "scheduling_action_tokens",
        "scheduling_reminders",
        "scheduling_event_participants",
        "scheduling_participants",
    ):
        op.drop_table(table)
