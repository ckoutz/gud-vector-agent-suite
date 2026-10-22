"""Booking a service: its length, buffers, notice and approval rule drive
the core engine; the visit's details ride in the same transaction."""

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from gvas.domain.identifiers import JsonValue
from gvas.scheduling.core.engine import HeldTime, SchedulingEngine, owner_principal
from gvas.scheduling.core.model import (
    OCCUPYING,
    Actor,
    EventStatus,
    NotFoundError,
    Slot,
    SlotTakenError,
    SlotUnavailableError,
)
from gvas.scheduling.core.notify import add_participant
from gvas.scheduling.core.records import SchedulingEventRecord
from gvas.scheduling.field_service.records import (
    FieldServiceAssignmentRecord,
    FieldServiceBookingRecord,
    FieldServiceOfferingRecord,
    FieldServiceRecord,
)

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
    #: Who hears about the visit (confirmation, reminders, changes).
    name: str | None = None
    email: str | None = None
    phone: str | None = None
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


async def _attach_customer(
    session: AsyncSession, event: SchedulingEventRecord, details: VisitDetails
) -> None:
    if details.email or details.phone:
        await add_participant(
            session,
            event,
            name=details.name or event.title,
            email=details.email,
            phone=details.phone,
            now=event.updated_at,
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
        """Open starts on any calendar whose times Gus offers, merged: a
        start shows once however many people are free then."""

        service = await self.service(business_id, service_id)
        if not service.active:
            return ()
        merged: dict[datetime, Slot] = {}
        for resource_id in await self.offering(business_id, service):
            slots = await self._engine.open_slots(
                business_id,
                resource_id,
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
            for slot in slots:
                merged.setdefault(slot.start, slot)
        return tuple(merged[key] for key in sorted(merged))

    async def offering(self, business_id: UUID, service: Service) -> tuple[UUID, ...]:
        """Whose times Gus offers, in order: the owner's choice of active
        calendars, or the service's own calendar when none is chosen."""

        active = {
            resource.resource_id for resource in await self._engine.resources(business_id)
            if resource.active
        }  # fmt: skip
        async with self._session_factory() as session:
            chosen = [
                row.resource_id
                for row in await session.scalars(
                    select(FieldServiceOfferingRecord)
                    .where(FieldServiceOfferingRecord.business_id == business_id)
                    .order_by(FieldServiceOfferingRecord.position)
                )
                if row.resource_id in active
            ]
        return tuple(chosen) or (service.resource_id,)

    async def set_offering(self, business_id: UUID, resource_ids: Sequence[UUID]) -> None:
        """Replace whose times Gus offers. Each must be this business's."""

        own = {resource.resource_id for resource in await self._engine.resources(business_id)}
        unique = list(dict.fromkeys(resource_ids))
        if not unique or any(resource_id not in own for resource_id in unique):
            raise NotFoundError("no such calendar")
        async with self._session_factory() as session, session.begin():
            await session.execute(
                delete(FieldServiceOfferingRecord).where(
                    FieldServiceOfferingRecord.business_id == business_id
                )
            )
            session.add_all(
                FieldServiceOfferingRecord(
                    business_id=business_id, resource_id=resource_id, position=position
                )
                for position, resource_id in enumerate(unique)
            )

    async def remember_assignment(
        self, business_id: UUID, reference: str, resource_id: UUID, *, by: str
    ) -> None:
        """The owner picked who does a request while approving it; the
        booking made from that approval goes on their calendar."""

        if resource_id not in {
            resource.resource_id for resource in await self._engine.resources(business_id)
        }:
            raise NotFoundError("no such calendar")
        async with self._session_factory() as session, session.begin():
            await session.merge(
                FieldServiceAssignmentRecord(
                    business_id=business_id, reference=reference,
                    resource_id=resource_id, assigned_by=by,
                )
            )  # fmt: skip

    async def assign(
        self, business_id: UUID, event_id: UUID, resource_id: UUID, *, by: str
    ) -> None:
        """Give a booked visit to someone, at the same time. The overlap
        guarantee holds on their calendar."""

        view = await self._engine.event(business_id, event_id, by=by)
        await self._engine.move(
            business_id, event_id, start=view.start, end=view.end, by=by,
            resource_id=resource_id,
        )  # fmt: skip
        await self.mark_assigned(business_id, event_id, by)

    async def mark_assigned(self, business_id: UUID, event_id: UUID, by: str) -> None:
        async with self._session_factory() as session, session.begin():
            booking = await session.scalar(
                select(FieldServiceBookingRecord).where(
                    FieldServiceBookingRecord.business_id == business_id,
                    FieldServiceBookingRecord.event_id == event_id,
                )
            )
            if booking is not None:
                booking.assigned_at = datetime.now(UTC)
                booking.assigned_by = by

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
            await _attach_customer(session, event, details)

        return await self._engine.confirm(
            business_id,
            event_id,
            actor=actor,
            needs_approval=service.needs_owner_approval,
            then=fill,
        )

    async def default_service(self, business_id: UUID) -> Service | None:
        """The business's first active service: what website bookings use."""

        async with self._session_factory() as session:
            record = await session.scalar(
                select(FieldServiceRecord)
                .where(FieldServiceRecord.business_id == business_id, FieldServiceRecord.active)
                .order_by(FieldServiceRecord.created_at, FieldServiceRecord.id)
                .limit(1)
            )
        return None if record is None else _service(record)

    async def book_approved(
        self,
        business_id: UUID,
        service_id: UUID,
        *,
        start: datetime,
        end: datetime,
        title: str,
        details: VisitDetails,
        reference: str | None,
        resource_id: UUID | None = None,
    ) -> UUID:
        """A visit the owner already approved: booked straight away, with the
        service's buffers, under the same no-overlap guarantee.

        It goes on ``resource_id`` when given (the owner chose), else on the
        person the owner picked while approving, else on the first offered
        calendar free at that time, left unassigned for the owner to give
        to someone. Nobody is assigned automatically."""

        service = await self.service(business_id, service_id)
        by = owner_principal(business_id)
        assigned_by: str | None = by if resource_id is not None else None
        candidates: list[UUID] = []
        if resource_id is not None:
            candidates.append(resource_id)
        else:
            picked = None
            if reference is not None:
                async with self._session_factory() as session:
                    picked = await session.get(
                        FieldServiceAssignmentRecord, (business_id, reference)
                    )
            if picked is not None:
                candidates.append(picked.resource_id)
                assigned_by = picked.assigned_by
            else:
                offered = await self.offering(business_id, service)
                if len(offered) == 1:
                    candidates.extend(offered)
                else:
                    free = [
                        candidate
                        for candidate in offered
                        if await self._engine.fits(
                            business_id, candidate, start=start, duration=end - start,
                            buffer_before=service.buffer_before,
                            buffer_after=service.buffer_after,
                        )
                    ]  # fmt: skip
                    candidates.extend(free + [item for item in offered if item not in free])
        stamp = datetime.now(UTC) if assigned_by is not None else None

        async def attach(session: AsyncSession, event: SchedulingEventRecord) -> None:
            session.add(
                FieldServiceBookingRecord(
                    event_id=event.id,
                    business_id=business_id,
                    service_id=service.service_id,
                    reference=reference,
                    customer_id=details.customer_id,
                    address=details.address,
                    job_description=details.job_description,
                    gus_notes=details.gus_notes,
                    answers=dict(details.answers),
                    assigned_at=stamp,
                    assigned_by=assigned_by,
                )
            )
            await _attach_customer(session, event, details)

        taken: SlotTakenError | None = None
        for candidate in candidates:
            try:
                booked = await self._engine.add_event(
                    business_id,
                    candidate,
                    start=start,
                    end=end,
                    title=title,
                    by=by,
                    buffer_before=service.buffer_before,
                    buffer_after=service.buffer_after,
                    then=attach,
                )
            except SlotTakenError as error:
                taken = error
                continue
            if reference is not None:
                async with self._session_factory() as session, session.begin():
                    await session.execute(
                        delete(FieldServiceAssignmentRecord).where(
                            FieldServiceAssignmentRecord.business_id == business_id,
                            FieldServiceAssignmentRecord.reference == reference,
                        )
                    )
            return booked
        raise taken or SlotTakenError("That time overlaps another visit.")

    async def booking_by_reference(self, business_id: UUID, reference: str) -> UUID | None:
        async with self._session_factory() as session:
            found: UUID | None = await session.scalar(
                select(SchedulingEventRecord.id)
                .join(
                    FieldServiceBookingRecord,
                    FieldServiceBookingRecord.event_id == SchedulingEventRecord.id,
                )
                .where(
                    FieldServiceBookingRecord.business_id == business_id,
                    FieldServiceBookingRecord.reference == reference,
                    SchedulingEventRecord.status.in_([status.value for status in OCCUPYING]),
                )
                .limit(1)
            )
        return found

    async def visits_between(
        self, business_id: UUID, start: datetime, end: datetime
    ) -> list[tuple[SchedulingEventRecord, FieldServiceBookingRecord]]:
        """Occupying booked visits overlapping ``start``-``end``, oldest first."""

        async with self._session_factory() as session:
            rows = await session.execute(
                select(SchedulingEventRecord, FieldServiceBookingRecord)
                .join(
                    FieldServiceBookingRecord,
                    FieldServiceBookingRecord.event_id == SchedulingEventRecord.id,
                )
                .where(
                    SchedulingEventRecord.business_id == business_id,
                    SchedulingEventRecord.starts_at < end,
                    SchedulingEventRecord.ends_at > start,
                    SchedulingEventRecord.status.in_(
                        [EventStatus.PENDING_APPROVAL.value, EventStatus.CONFIRMED.value]
                    ),
                )
                .order_by(SchedulingEventRecord.starts_at)
            )
            return [(event, booking) for event, booking in rows.tuples()]

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
