"""Core scheduling tables. Every row carries ``business_id`` (the tenant)."""

from datetime import date, datetime
from uuid import UUID

from sqlalchemy import (
    DDL,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    event,
    func,
    literal_column,
    text,
)
from sqlalchemy.dialects.postgresql import ExcludeConstraint
from sqlalchemy.orm import Mapped, mapped_column

from gvas.domain.identifiers import JsonValue
from gvas.infrastructure.db import json_type
from gvas.infrastructure.models import Base

#: The double-booking guarantee needs ``=`` on uuid inside a gist index.
event.listen(
    Base.metadata,
    "before_create",
    DDL("CREATE EXTENSION IF NOT EXISTS btree_gist").execute_if(  # type: ignore[no-untyped-call]
        dialect="postgresql"
    ),
)

OCCUPYING_SQL = "status IN ('held', 'pending_approval', 'confirmed')"


class SchedulingCalendarRecord(Base):
    __tablename__ = "scheduling_calendars"

    id: Mapped[UUID] = mapped_column(primary_key=True)
    business_id: Mapped[UUID] = mapped_column(
        ForeignKey("businesses.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    color: Mapped[str | None] = mapped_column(String(16))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class SchedulingCalendarPermissionRecord(Base):
    """Who may do what on a calendar. ``principal`` is an opaque sign-in
    reference (today ``owner:<business_id>``)."""

    __tablename__ = "scheduling_calendar_permissions"

    calendar_id: Mapped[UUID] = mapped_column(
        ForeignKey("scheduling_calendars.id", ondelete="CASCADE"), primary_key=True
    )
    principal: Mapped[str] = mapped_column(String(255), primary_key=True)
    business_id: Mapped[UUID] = mapped_column(
        ForeignKey("businesses.id", ondelete="CASCADE"), nullable=False
    )
    role: Mapped[str] = mapped_column(String(16), nullable=False)


class SchedulingResourceRecord(Base):
    """Something whose time is booked (a person today), with its zone and
    booking policy."""

    __tablename__ = "scheduling_resources"

    id: Mapped[UUID] = mapped_column(primary_key=True)
    business_id: Mapped[UUID] = mapped_column(
        ForeignKey("businesses.id", ondelete="CASCADE"), nullable=False, index=True
    )
    calendar_id: Mapped[UUID] = mapped_column(
        ForeignKey("scheduling_calendars.id", ondelete="CASCADE"), nullable=False
    )
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    time_zone: Mapped[str] = mapped_column(String(64), nullable=False)
    min_notice_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    horizon_days: Mapped[int] = mapped_column(Integer, nullable=False)
    daily_limit: Mapped[int | None] = mapped_column(Integer)
    step_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class SchedulingAvailabilityRuleRecord(Base):
    """Weekly working hours: wall-clock minutes on one weekday (0 = Monday)."""

    __tablename__ = "scheduling_availability_rules"

    id: Mapped[UUID] = mapped_column(primary_key=True)
    business_id: Mapped[UUID] = mapped_column(
        ForeignKey("businesses.id", ondelete="CASCADE"), nullable=False
    )
    resource_id: Mapped[UUID] = mapped_column(
        ForeignKey("scheduling_resources.id", ondelete="CASCADE"), nullable=False, index=True
    )
    weekday: Mapped[int] = mapped_column(Integer, nullable=False)
    start_minute: Mapped[int] = mapped_column(Integer, nullable=False)
    end_minute: Mapped[int] = mapped_column(Integer, nullable=False)
    effective_from: Mapped[date | None] = mapped_column(Date)
    effective_to: Mapped[date | None] = mapped_column(Date)


class SchedulingAvailabilityExceptionRecord(Base):
    """Time off: a day off, a holiday or a block, once or repeating."""

    __tablename__ = "scheduling_availability_exceptions"

    id: Mapped[UUID] = mapped_column(primary_key=True)
    business_id: Mapped[UUID] = mapped_column(
        ForeignKey("businesses.id", ondelete="CASCADE"), nullable=False
    )
    resource_id: Mapped[UUID] = mapped_column(
        ForeignKey("scheduling_resources.id", ondelete="CASCADE"), nullable=False, index=True
    )
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    rrule: Mapped[str | None] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    source: Mapped[str] = mapped_column(String(16), nullable=False)
    note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class SchedulingEventRecord(Base):
    """Anything on a calendar that takes time. ``blocked_start``/``blocked_end``
    include the buffers; the database refuses two occupying events of one
    resource whose blocked ranges overlap."""

    __tablename__ = "scheduling_events"
    __table_args__ = (
        Index("ix_scheduling_events_resource_status", "resource_id", "status"),
        Index("ix_scheduling_events_business_status", "business_id", "status"),
        ExcludeConstraint(
            ("resource_id", "="),
            (
                func.tstzrange(
                    literal_column("blocked_start"),
                    literal_column("blocked_end"),
                    literal_column("'[)'"),
                ),
                "&&",
            ),
            name="ex_scheduling_events_no_overlap",
            using="gist",
            where=text(OCCUPYING_SQL),
        ).ddl_if(dialect="postgresql"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True)
    business_id: Mapped[UUID] = mapped_column(
        ForeignKey("businesses.id", ondelete="CASCADE"), nullable=False
    )
    calendar_id: Mapped[UUID] = mapped_column(
        ForeignKey("scheduling_calendars.id", ondelete="CASCADE"), nullable=False
    )
    resource_id: Mapped[UUID] = mapped_column(
        ForeignKey("scheduling_resources.id", ondelete="CASCADE"), nullable=False
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    time_zone: Mapped[str] = mapped_column(String(64), nullable=False)
    all_day: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    blocked_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    blocked_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    hold_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    approval_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    approval_reminded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    holder_ref: Mapped[str | None] = mapped_column(String(255))
    holder_ip: Mapped[str | None] = mapped_column(String(64))
    created_by: Mapped[str] = mapped_column(String(16), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class SchedulingAuditRecord(Base):
    __tablename__ = "scheduling_audit_log"
    __table_args__ = (Index("ix_scheduling_audit_log_entity", "business_id", "entity_id"),)

    id: Mapped[UUID] = mapped_column(primary_key=True)
    business_id: Mapped[UUID] = mapped_column(
        ForeignKey("businesses.id", ondelete="CASCADE"), nullable=False
    )
    actor: Mapped[str] = mapped_column(String(16), nullable=False)
    entity: Mapped[str] = mapped_column(String(32), nullable=False)
    entity_id: Mapped[UUID] = mapped_column(nullable=False)
    action: Mapped[str] = mapped_column(String(32), nullable=False)
    before: Mapped[dict[str, JsonValue] | None] = mapped_column(json_type)
    after: Mapped[dict[str, JsonValue] | None] = mapped_column(json_type)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class SchedulingSlotOfferRecord(Base):
    """Every set of open times handed out, for debugging what was offered."""

    __tablename__ = "scheduling_slot_offers"
    __table_args__ = (Index("ix_scheduling_slot_offers_business_at", "business_id", "at"),)

    id: Mapped[UUID] = mapped_column(primary_key=True)
    business_id: Mapped[UUID] = mapped_column(
        ForeignKey("businesses.id", ondelete="CASCADE"), nullable=False
    )
    resource_id: Mapped[UUID] = mapped_column(
        ForeignKey("scheduling_resources.id", ondelete="CASCADE"), nullable=False
    )
    requester: Mapped[str] = mapped_column(String(16), nullable=False)
    purpose: Mapped[str | None] = mapped_column(String(255))
    range_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    range_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    slots: Mapped[list[JsonValue]] = mapped_column(json_type, nullable=False)
    #: The newest change to the resource's occupied time when computed.
    snapshot: Mapped[dict[str, JsonValue]] = mapped_column(json_type, nullable=False)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class SchedulingParticipantRecord(Base):
    """A person linked to events: a customer who never signs in, a crew
    member, later a family member. Contact details only; consent lives with
    whoever owns the person (for a customer, GVAS's customer record)."""

    __tablename__ = "scheduling_participants"

    id: Mapped[UUID] = mapped_column(primary_key=True)
    business_id: Mapped[UUID] = mapped_column(
        ForeignKey("businesses.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    email: Mapped[str | None] = mapped_column(String(320))
    phone: Mapped[str | None] = mapped_column(String(32))
    time_zone: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class SchedulingEventParticipantRecord(Base):
    __tablename__ = "scheduling_event_participants"

    event_id: Mapped[UUID] = mapped_column(
        ForeignKey("scheduling_events.id", ondelete="CASCADE"), primary_key=True
    )
    participant_id: Mapped[UUID] = mapped_column(
        ForeignKey("scheduling_participants.id", ondelete="CASCADE"), primary_key=True
    )
    role: Mapped[str] = mapped_column(String(16), nullable=False)


class SchedulingReminderRecord(Base):
    """One message due to one person about one event, on one channel."""

    __tablename__ = "scheduling_reminders"
    __table_args__ = (Index("ix_scheduling_reminders_due", "status", "fire_at"),)

    id: Mapped[UUID] = mapped_column(primary_key=True)
    business_id: Mapped[UUID] = mapped_column(
        ForeignKey("businesses.id", ondelete="CASCADE"), nullable=False
    )
    event_id: Mapped[UUID] = mapped_column(
        ForeignKey("scheduling_events.id", ondelete="CASCADE"), nullable=False, index=True
    )
    participant_id: Mapped[UUID] = mapped_column(
        ForeignKey("scheduling_participants.id", ondelete="CASCADE"), nullable=False
    )
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    channel: Mapped[str] = mapped_column(String(16), nullable=False)
    fire_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    #: The event version the message describes; stale ones are skipped.
    event_version: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    detail: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class SchedulingActionTokenRecord(Base):
    """A no-login link to change or cancel one event; only its hash is kept."""

    __tablename__ = "scheduling_action_tokens"

    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    business_id: Mapped[UUID] = mapped_column(
        ForeignKey("businesses.id", ondelete="CASCADE"), nullable=False
    )
    event_id: Mapped[UUID] = mapped_column(
        ForeignKey("scheduling_events.id", ondelete="CASCADE"), nullable=False, index=True
    )
    actions: Mapped[list[JsonValue]] = mapped_column(json_type, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class SchedulingExternalLinkRecord(Base):
    """A resource's connection to an outside calendar (Google first): read its
    busy times in, write confirmed events out. Credentials live elsewhere;
    ``credentials_ref`` only names them."""

    __tablename__ = "scheduling_external_links"

    id: Mapped[UUID] = mapped_column(primary_key=True)
    business_id: Mapped[UUID] = mapped_column(
        ForeignKey("businesses.id", ondelete="CASCADE"), nullable=False
    )
    resource_id: Mapped[UUID] = mapped_column(
        ForeignKey("scheduling_resources.id", ondelete="CASCADE"), nullable=False, index=True
    )
    provider: Mapped[str] = mapped_column(String(16), nullable=False)
    read_busy: Mapped[bool] = mapped_column(Boolean, nullable=False)
    write_events: Mapped[bool] = mapped_column(Boolean, nullable=False)
    credentials_ref: Mapped[str | None] = mapped_column(String(255))
    #: The dedicated outside calendar confirmed events are written to.
    write_calendar_id: Mapped[str | None] = mapped_column(String(255))
    sync_cursor: Mapped[str | None] = mapped_column(Text)
    #: The provider refresh token, sealed by the infrastructure (never plain).
    refresh_token_sealed: Mapped[str | None] = mapped_column(Text)
    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    #: Set when the provider says something changed; the next round re-reads.
    sync_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    #: The provider's change-notice channel, replaced before it expires.
    watch_channel_id: Mapped[str | None] = mapped_column(String(64), index=True)
    watch_resource_id: Mapped[str | None] = mapped_column(String(255))
    watch_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class SchedulingExternalBusyRecord(Base):
    """A read-only mirror of one outside busy block: times only, never titles."""

    __tablename__ = "scheduling_external_busy"

    id: Mapped[UUID] = mapped_column(primary_key=True)
    link_id: Mapped[UUID] = mapped_column(
        ForeignKey("scheduling_external_links.id", ondelete="CASCADE"), nullable=False, index=True
    )
    external_id: Mapped[str] = mapped_column(String(255), nullable=False)
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class SchedulingExternalPushRecord(Base):
    """Which version of an event an outside calendar last received."""

    __tablename__ = "scheduling_external_pushes"

    link_id: Mapped[UUID] = mapped_column(
        ForeignKey("scheduling_external_links.id", ondelete="CASCADE"), primary_key=True
    )
    event_id: Mapped[UUID] = mapped_column(
        ForeignKey("scheduling_events.id", ondelete="CASCADE"), primary_key=True
    )
    pushed_version: Mapped[int] = mapped_column(Integer, nullable=False)
    deleted: Mapped[bool] = mapped_column(Boolean, nullable=False)
