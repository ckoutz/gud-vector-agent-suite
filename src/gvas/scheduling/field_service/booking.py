"""Booking a service: its length, buffers, notice and approval rule drive
the core engine; the visit's details ride in the same transaction."""

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from gvas.domain.identifiers import JsonValue
from gvas.scheduling.core.engine import HeldTime, SchedulingEngine
from gvas.scheduling.core.model import (
    Actor,
    EventStatus,
    NotFoundError,
    Slot,
    SlotUnavailableError,
)
from gvas.scheduling.core.records import SchedulingEventRecord
from gvas.scheduling.field_service.records import FieldServiceBookingRecord, FieldServiceRecord

DEFAULT_BUFFER_AFTER = timedelta(minutes=15)


@dataclass(frozen=True)
class Service:
    service_id: UUID
    resource_id: UUID
    name: str
    duration: timedelta
    buffer_before: timedelta
    buffer_after: timedelta
    customer_notes: str | None
    active: bool
    needs_owner_approval: bool
    min_notice: timedelta | None
    horizon: timedelta | None
    questions: tuple[str, ...]


@dataclass(frozen=True)
class VisitDetails:
    customer_id: UUID | None = None
    address: str | None = None
    job_description: str | None = None
    gus_notes: str | None = None
    answers: dict[str, str] = field(default_factory=dict)


def _service(record: FieldServiceRecord) -> Service:
    return Service(
        service_id=record.id,
        resource_id=record.resource_id,
        name=record.name,
        duration=timedelta(minutes=record.duration_minutes),
        buffer_before=timedelta(minutes=record.buffer_before_minutes),
        buffer_after=timedelta(minutes=record.buffer_after_minutes),
        customer_notes=record.customer_notes,
        active=record.active,
        needs_owner_approval=record.needs_owner_approval,
        min_notice=None
        if record.min_notice_minutes is None
        else timedelta(minutes=record.min_notice_minutes),
        horizon=None if record.horizon_days is None else timedelta(days=record.horizon_days),
        questions=tuple(str(question) for question in record.questions),
    )


class FieldServiceScheduling:
    def __init__(
        self, engine: SchedulingEngine, session_factory: async_sessionmaker[AsyncSession]
    ) -> None:
        self._engine = engine
        self._session_factory = session_factory

    async def create_service(
        self,
        business_id: UUID,
        resource_id: UUID,
        *,
        name: str,
        duration: timedelta,
        buffer_after: timedelta = DEFAULT_BUFFER_AFTER,
        buffer_before: timedelta = timedelta(0),
        customer_notes: str | None = None,
        needs_owner_approval: bool = False,
        min_notice: timedelta | None = None,
        horizon: timedelta | None = None,
        questions: Sequence[str] = (),
    ) -> Service:
        if duration <= timedelta(0):
            raise ValueError("a service needs a length")
        now = datetime.now(UTC)
        record = FieldServiceRecord(
            id=uuid4(),
            business_id=business_id,
            resource_id=resource_id,
            name=name,
            duration_minutes=int(duration / timedelta(minutes=1)),
            buffer_before_minutes=int(buffer_before / timedelta(minutes=1)),
            buffer_after_minutes=int(buffer_after / timedelta(minutes=1)),
            customer_notes=customer_notes,
            active=True,
            needs_owner_approval=needs_owner_approval,
            min_notice_minutes=None
            if min_notice is None
            else int(min_notice / timedelta(minutes=1)),
            horizon_days=None if horizon is None else horizon.days,
            questions=list(questions),
            created_at=now,
            updated_at=now,
        )
        async with self._session_factory() as session, session.begin():
            session.add(record)
        return _service(record)

    async def set_active(self, business_id: UUID, service_id: UUID, active: bool) -> None:
        async with self._session_factory() as session, session.begin():
            record = await self._record(session, business_id, service_id)
            record.active = active
            record.updated_at = datetime.now(UTC)

    async def service(self, business_id: UUID, service_id: UUID) -> Service:
        async with self._session_factory() as session:
            return _service(await self._record(session, business_id, service_id))

    async def open_slots(
        self,
        business_id: UUID,
        service_id: UUID,
        *,
        start: datetime,
        end: datetime,
        requester: Actor,
    ) -> tuple[Slot, ...]:
        service = await self.service(business_id, service_id)
        if not service.active:
            return ()
        return await self._engine.open_slots(
            business_id,
            service.resource_id,
            duration=service.duration,
            buffer_before=service.buffer_before,
            buffer_after=service.buffer_after,
            min_notice=service.min_notice,
            horizon=service.horizon,
            start=start,
            end=end,
            requester=requester,
            purpose=f"service:{service.service_id}",
        )

    async def hold(
        self,
        business_id: UUID,
        service_id: UUID,
        *,
        start: datetime,
        holder_ref: str,
        holder_ip: str | None,
        actor: Actor,
    ) -> HeldTime:
        service = await self.service(business_id, service_id)
        if not service.active:
            raise SlotUnavailableError("That service isn't being booked right now.")

        async def attach(session: AsyncSession, event: SchedulingEventRecord) -> None:
            session.add(
                FieldServiceBookingRecord(
                    event_id=event.id,
                    business_id=business_id,
                    service_id=service.service_id,
                    answers={},
                )
            )

        return await self._engine.hold(
            business_id,
            service.resource_id,
            start=start,
            duration=service.duration,
            buffer_before=service.buffer_before,
            buffer_after=service.buffer_after,
            min_notice=service.min_notice,
            horizon=service.horizon,
            title=service.name,
            holder_ref=holder_ref,
            holder_ip=holder_ip,
            actor=actor,
            then=attach,
        )

    async def confirm(
        self, business_id: UUID, event_id: UUID, details: VisitDetails, *, actor: Actor
    ) -> EventStatus:
        """Confirmed, or pending the owner's approval when the service asks."""

        async with self._session_factory() as session:
            booking = await session.scalar(
                select(FieldServiceBookingRecord).where(
                    FieldServiceBookingRecord.business_id == business_id,
                    FieldServiceBookingRecord.event_id == event_id,
                )
            )
            if booking is None:
                raise NotFoundError("no such booking")
            service = _service(await self._record(session, business_id, booking.service_id))

        async def fill(session: AsyncSession, event: SchedulingEventRecord) -> None:
            record = await session.get(FieldServiceBookingRecord, event.id)
            assert record is not None
            record.customer_id = details.customer_id
            record.address = details.address
            record.job_description = details.job_description
            record.gus_notes = details.gus_notes
            answers: dict[str, JsonValue] = dict(details.answers)
            record.answers = answers

        return await self._engine.confirm(
            business_id,
            event_id,
            actor=actor,
            needs_approval=service.needs_owner_approval,
            then=fill,
        )

    async def _record(
        self, session: AsyncSession, business_id: UUID, service_id: UUID
    ) -> FieldServiceRecord:
        record = await session.scalar(
            select(FieldServiceRecord).where(
                FieldServiceRecord.business_id == business_id,
                FieldServiceRecord.id == service_id,
            )
        )
        if record is None:
            raise NotFoundError("no such service")
        return record
