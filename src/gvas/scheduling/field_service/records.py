"""Field-service tables. Customers are linked by id only: the scheduling
package never imports the customer, quote or invoice code."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from gvas.domain.identifiers import JsonValue
from gvas.infrastructure.db import json_type
from gvas.infrastructure.models import Base


class FieldServiceRecord(Base):
    """A bookable kind of visit, e.g. "Estimate walk-through, 30 minutes"."""

    __tablename__ = "scheduling_fs_services"

    id: Mapped[UUID] = mapped_column(primary_key=True)
    business_id: Mapped[UUID] = mapped_column(
        ForeignKey("businesses.id", ondelete="CASCADE"), nullable=False, index=True
    )
    resource_id: Mapped[UUID] = mapped_column(
        ForeignKey("scheduling_resources.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    duration_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    buffer_before_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    buffer_after_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    customer_notes: Mapped[str | None] = mapped_column(Text)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False)
    needs_owner_approval: Mapped[bool] = mapped_column(Boolean, nullable=False)
    min_notice_minutes: Mapped[int | None] = mapped_column(Integer)
    horizon_days: Mapped[int | None] = mapped_column(Integer)
    questions: Mapped[list[JsonValue]] = mapped_column(json_type, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class FieldServiceBookingRecord(Base):
    """The visit details a booking carries, one-to-one with its event."""

    __tablename__ = "scheduling_fs_bookings"

    event_id: Mapped[UUID] = mapped_column(
        ForeignKey("scheduling_events.id", ondelete="CASCADE"), primary_key=True
    )
    business_id: Mapped[UUID] = mapped_column(
        ForeignKey("businesses.id", ondelete="CASCADE"), nullable=False, index=True
    )
    service_id: Mapped[UUID] = mapped_column(
        ForeignKey("scheduling_fs_services.id", ondelete="RESTRICT"), nullable=False
    )
    customer_id: Mapped[UUID | None] = mapped_column()
    address: Mapped[str | None] = mapped_column(Text)
    job_description: Mapped[str | None] = mapped_column(Text)
    gus_notes: Mapped[str | None] = mapped_column(Text)
    answers: Mapped[dict[str, JsonValue]] = mapped_column(json_type, nullable=False)
