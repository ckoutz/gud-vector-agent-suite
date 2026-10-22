"""scheduling engine (V1a): calendars, resources, hours, time off, events
with a database-level no-overlap guarantee, audit and slot-offer logs, and
the field-service services and booking details

Revision ID: 0035_scheduling_engine
Revises: 0034_customers_email_optional
"""

import sqlalchemy as sa
from alembic import op

revision = "0035_scheduling_engine"
down_revision = "0034_customers_email_optional"
branch_labels = None
depends_on = None

_TZ = sa.DateTime(timezone=True)


def _business() -> sa.Column:
    return sa.Column(
        "business_id",
        sa.Uuid(),
        sa.ForeignKey("businesses.id", ondelete="CASCADE"),
        nullable=False,
    )


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS btree_gist")
    op.create_table(
        "scheduling_calendars",
        sa.Column("id", sa.Uuid(), primary_key=True),
        _business(),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("color", sa.String(16)),
        sa.Column("created_at", _TZ, nullable=False),
    )
    op.create_index("ix_scheduling_calendars_business_id", "scheduling_calendars", ["business_id"])
    op.create_table(
        "scheduling_calendar_permissions",
        sa.Column(
            "calendar_id",
            sa.Uuid(),
            sa.ForeignKey("scheduling_calendars.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("principal", sa.String(255), primary_key=True),
        _business(),
        sa.Column("role", sa.String(16), nullable=False),
    )
    op.create_table(
        "scheduling_resources",
        sa.Column("id", sa.Uuid(), primary_key=True),
        _business(),
        sa.Column(
            "calendar_id",
            sa.Uuid(),
            sa.ForeignKey("scheduling_calendars.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("time_zone", sa.String(64), nullable=False),
        sa.Column("min_notice_minutes", sa.Integer(), nullable=False),
        sa.Column("horizon_days", sa.Integer(), nullable=False),
        sa.Column("daily_limit", sa.Integer()),
        sa.Column("step_minutes", sa.Integer(), nullable=False),
        sa.Column("created_at", _TZ, nullable=False),
        sa.Column("updated_at", _TZ, nullable=False),
    )
    op.create_index("ix_scheduling_resources_business_id", "scheduling_resources", ["business_id"])
    resource = sa.ForeignKey("scheduling_resources.id", ondelete="CASCADE")
    op.create_table(
        "scheduling_availability_rules",
        sa.Column("id", sa.Uuid(), primary_key=True),
        _business(),
        sa.Column("resource_id", sa.Uuid(), resource, nullable=False),
        sa.Column("weekday", sa.Integer(), nullable=False),
        sa.Column("start_minute", sa.Integer(), nullable=False),
        sa.Column("end_minute", sa.Integer(), nullable=False),
        sa.Column("effective_from", sa.Date()),
        sa.Column("effective_to", sa.Date()),
    )
    op.create_index(
        "ix_scheduling_availability_rules_resource_id",
        "scheduling_availability_rules",
        ["resource_id"],
    )
    op.create_table(
        "scheduling_availability_exceptions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        _business(),
        sa.Column(
            "resource_id",
            sa.Uuid(),
            sa.ForeignKey("scheduling_resources.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("starts_at", _TZ, nullable=False),
        sa.Column("ends_at", _TZ, nullable=False),
        sa.Column("rrule", sa.Text()),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("source", sa.String(16), nullable=False),
        sa.Column("note", sa.Text()),
        sa.Column("created_at", _TZ, nullable=False),
    )
    op.create_index(
        "ix_scheduling_availability_exceptions_resource_id",
        "scheduling_availability_exceptions",
        ["resource_id"],
    )
    op.create_table(
        "scheduling_events",
        sa.Column("id", sa.Uuid(), primary_key=True),
        _business(),
        sa.Column(
            "calendar_id",
            sa.Uuid(),
            sa.ForeignKey("scheduling_calendars.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "resource_id",
            sa.Uuid(),
            sa.ForeignKey("scheduling_resources.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("starts_at", _TZ, nullable=False),
        sa.Column("ends_at", _TZ, nullable=False),
        sa.Column("time_zone", sa.String(64), nullable=False),
        sa.Column("all_day", sa.Boolean(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("blocked_start", _TZ, nullable=False),
        sa.Column("blocked_end", _TZ, nullable=False),
        sa.Column("hold_expires_at", _TZ),
        sa.Column("approval_expires_at", _TZ),
        sa.Column("approval_reminded_at", _TZ),
        sa.Column("holder_ref", sa.String(255)),
        sa.Column("holder_ip", sa.String(64)),
        sa.Column("created_by", sa.String(16), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_at", _TZ, nullable=False),
        sa.Column("updated_at", _TZ, nullable=False),
    )
    op.create_index(
        "ix_scheduling_events_resource_status", "scheduling_events", ["resource_id", "status"]
    )
    op.create_index(
        "ix_scheduling_events_business_status", "scheduling_events", ["business_id", "status"]
    )
    op.execute(
        "ALTER TABLE scheduling_events ADD CONSTRAINT ex_scheduling_events_no_overlap "
        "EXCLUDE USING gist (resource_id WITH =, "
        "tstzrange(blocked_start, blocked_end, '[)') WITH &&) "
        "WHERE (status IN ('held', 'pending_approval', 'confirmed'))"
    )
    op.create_table(
        "scheduling_audit_log",
        sa.Column("id", sa.Uuid(), primary_key=True),
        _business(),
        sa.Column("actor", sa.String(16), nullable=False),
        sa.Column("entity", sa.String(32), nullable=False),
        sa.Column("entity_id", sa.Uuid(), nullable=False),
        sa.Column("action", sa.String(32), nullable=False),
        sa.Column("before", sa.JSON().with_variant(sa.dialects.postgresql.JSONB(), "postgresql")),
        sa.Column("after", sa.JSON().with_variant(sa.dialects.postgresql.JSONB(), "postgresql")),
        sa.Column("at", _TZ, nullable=False),
    )
    op.create_index(
        "ix_scheduling_audit_log_entity", "scheduling_audit_log", ["business_id", "entity_id"]
    )
    op.create_table(
        "scheduling_slot_offers",
        sa.Column("id", sa.Uuid(), primary_key=True),
        _business(),
        sa.Column(
            "resource_id",
            sa.Uuid(),
            sa.ForeignKey("scheduling_resources.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("requester", sa.String(16), nullable=False),
        sa.Column("purpose", sa.String(255)),
        sa.Column("range_start", _TZ, nullable=False),
        sa.Column("range_end", _TZ, nullable=False),
        sa.Column(
            "slots",
            sa.JSON().with_variant(sa.dialects.postgresql.JSONB(), "postgresql"),
            nullable=False,
        ),
        sa.Column(
            "snapshot",
            sa.JSON().with_variant(sa.dialects.postgresql.JSONB(), "postgresql"),
            nullable=False,
        ),
        sa.Column("at", _TZ, nullable=False),
    )
    op.create_index(
        "ix_scheduling_slot_offers_business_at", "scheduling_slot_offers", ["business_id", "at"]
    )
    op.create_table(
        "scheduling_fs_services",
        sa.Column("id", sa.Uuid(), primary_key=True),
        _business(),
        sa.Column(
            "resource_id",
            sa.Uuid(),
            sa.ForeignKey("scheduling_resources.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("duration_minutes", sa.Integer(), nullable=False),
        sa.Column("buffer_before_minutes", sa.Integer(), nullable=False),
        sa.Column("buffer_after_minutes", sa.Integer(), nullable=False),
        sa.Column("customer_notes", sa.Text()),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("needs_owner_approval", sa.Boolean(), nullable=False),
        sa.Column("min_notice_minutes", sa.Integer()),
        sa.Column("horizon_days", sa.Integer()),
        sa.Column(
            "questions",
            sa.JSON().with_variant(sa.dialects.postgresql.JSONB(), "postgresql"),
            nullable=False,
        ),
        sa.Column("created_at", _TZ, nullable=False),
        sa.Column("updated_at", _TZ, nullable=False),
    )
    op.create_index(
        "ix_scheduling_fs_services_business_id", "scheduling_fs_services", ["business_id"]
    )
    op.create_table(
        "scheduling_fs_bookings",
        sa.Column(
            "event_id",
            sa.Uuid(),
            sa.ForeignKey("scheduling_events.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        _business(),
        sa.Column(
            "service_id",
            sa.Uuid(),
            sa.ForeignKey("scheduling_fs_services.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("customer_id", sa.Uuid()),
        sa.Column("address", sa.Text()),
        sa.Column("job_description", sa.Text()),
        sa.Column("gus_notes", sa.Text()),
        sa.Column(
            "answers",
            sa.JSON().with_variant(sa.dialects.postgresql.JSONB(), "postgresql"),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_scheduling_fs_bookings_business_id", "scheduling_fs_bookings", ["business_id"]
    )


def downgrade() -> None:
    for table in (
        "scheduling_fs_bookings",
        "scheduling_fs_services",
        "scheduling_slot_offers",
        "scheduling_audit_log",
        "scheduling_events",
        "scheduling_availability_exceptions",
        "scheduling_availability_rules",
        "scheduling_resources",
        "scheduling_calendar_permissions",
        "scheduling_calendars",
    ):
        op.drop_table(table)
