from datetime import date, datetime
from uuid import UUID

from sqlalchemy import Date, DateTime, ForeignKey, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from gvas.domain.identifiers import JsonValue
from gvas.infrastructure.db import json_type
from gvas.infrastructure.models import Base


class CalendarBlockRecord(Base):
    """One owner-requested block of time on the booking calendar."""

    __tablename__ = "calendar_blocks"
    __table_args__ = (Index("ix_calendar_blocks_business_state", "business_id", "state"),)

    id: Mapped[UUID] = mapped_column(primary_key=True)
    business_id: Mapped[UUID] = mapped_column(
        ForeignKey("businesses.id", ondelete="CASCADE"), nullable=False
    )
    day: Mapped[date] = mapped_column(Date, nullable=False)
    start_minute: Mapped[int] = mapped_column(Integer, nullable=False)
    end_minute: Mapped[int] = mapped_column(Integer, nullable=False)
    reference: Mapped[str | None] = mapped_column(String(32))
    state: Mapped[str] = mapped_column(String(16), nullable=False)
    previous: Mapped[dict[str, JsonValue] | None] = mapped_column(json_type)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class DemoDayHoursRecord(Base):
    """A demo calendar's own rule for one date, as Calendly keeps a date rule:
    the bookable hours that replace the weekly ones (none: closed)."""

    __tablename__ = "demo_day_hours"
    __table_args__ = (UniqueConstraint("business_id", "day", name="uq_demo_day_hours_day"),)

    id: Mapped[UUID] = mapped_column(primary_key=True)
    business_id: Mapped[UUID] = mapped_column(
        ForeignKey("businesses.id", ondelete="CASCADE"), nullable=False
    )
    day: Mapped[date] = mapped_column(Date, nullable=False)
    intervals: Mapped[list[JsonValue]] = mapped_column(json_type, nullable=False)
