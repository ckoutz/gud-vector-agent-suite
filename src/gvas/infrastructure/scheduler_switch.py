"""The per-business scheduler switch: each business books through Calendly
(the default) or through our own scheduling engine, never both.

``SchedulerRouter`` stands where the Calendly (or demo) adapter stood, so
Gus, owner approval and blocks by text keep one interface whichever
calendar a business is on."""

from datetime import date, datetime, time, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from gvas.domain.calendar_blocks import DayHours, Interval
from gvas.domain.identifiers import BusinessId
from gvas.domain.intake import (
    AvailabilityError,
    AvailableSlot,
    BookingKind,
    BookingRequest,
    BookingResult,
)
from gvas.domain.owner import CalendarEvent, CalendarEventSource
from gvas.domain.ports import AvailabilityPort, BookedEventsPort, ScheduleBlockPort
from gvas.infrastructure.models import Business
from gvas.scheduling.core.engine import SchedulingEngine, owner_principal
from gvas.scheduling.core.model import (
    Actor,
    BlockSource,
    EventStatus,
    IllegalTransitionError,
    NotFoundError,
    SlotTakenError,
)
from gvas.scheduling.field_service.booking import FieldServiceScheduling, Service, VisitDetails

CALENDLY = "calendly"
ENGINE = "engine"
SCHEDULERS = frozenset({CALENDLY, ENGINE})
EVENT_URI_PREFIX = "engine:event:"
SERVICE_URI_PREFIX = "engine:service:"
_DAY_NOTE = "day:"


def _minutes(value: datetime) -> int:
    return value.hour * 60 + value.minute


class EngineAvailability:
    """Our engine behind the existing calendar interfaces, for one business
    at a time: its first active service is what website bookings use."""

    def __init__(
        self,
        engine: SchedulingEngine,
        scheduling: FieldServiceScheduling,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        self._engine = engine
        self._scheduling = scheduling
        self._session_factory = session_factory

    async def _service(self, business_id: BusinessId) -> Service:
        service = await self._scheduling.default_service(business_id)
        if service is None:
            raise AvailabilityError("no bookable service is set up on the calendar")
        return service

    def serves(self, business_id: BusinessId) -> bool:
        return True

    async def available_slots(
        self, business_id: BusinessId, start: datetime, end: datetime
    ) -> tuple[AvailableSlot, ...]:
        service = await self._service(business_id)
        slots = await self._scheduling.open_slots(
            business_id, service.service_id, start=start, end=end, requester=Actor.GUS
        )
        return tuple(AvailableSlot(start=slot.start, end=slot.end) for slot in slots)

    async def book(self, request: BookingRequest) -> BookingResult:
        service = await self._service(request.business_id)
        uri = f"{SERVICE_URI_PREFIX}{service.service_id}"
        try:
            await self._scheduling.book_approved(
                request.business_id,
                service.service_id,
                start=request.slot_start,
                end=request.slot_end,
                title=request.invitee_name,
                details=VisitDetails(address=request.address, job_description=request.details),
                reference=request.reference,
            )
        except SlotTakenError:
            async with self._session_factory() as session:
                site_url = await session.scalar(
                    select(Business.site_url).where(Business.id == request.business_id)
                )
            if not site_url:
                raise AvailabilityError("that time is already booked") from None
            return BookingResult(kind=BookingKind.LINK, link=site_url, event_type_uri=uri)
        return BookingResult(kind=BookingKind.BOOKED, event_type_uri=uri)

    async def find_booking(self, request: BookingRequest) -> BookingResult | None:
        if request.reference is None:
            return None
        found = await self._scheduling.booking_by_reference(request.business_id, request.reference)
        if found is None:
            return None
        return BookingResult(kind=BookingKind.BOOKED, event_type_uri=request.event_type_uri)

    async def booking_event_type_uri(self, business_id: BusinessId) -> str | None:
        service = await self._scheduling.default_service(business_id)
        return None if service is None else f"{SERVICE_URI_PREFIX}{service.service_id}"

    async def cancel_booking(self, business_id: BusinessId, event_uri: str) -> None:
        if not event_uri.startswith(EVENT_URI_PREFIX):
            return
        try:
            event_id = UUID(event_uri.removeprefix(EVENT_URI_PREFIX))
            await self._engine.set_status(
                business_id, event_id, EventStatus.CANCELLED, actor=Actor.SYSTEM
            )
        except (ValueError, NotFoundError, IllegalTransitionError):
            return

    async def upcoming(
        self, business_id: BusinessId, start: datetime, end: datetime
    ) -> tuple[CalendarEvent, ...]:
        visits = await self._scheduling.visits_between(business_id, start, end)
        return tuple(
            CalendarEvent(
                source=CalendarEventSource.BOOKING,
                title=event.title,
                start=event.starts_at,
                end=event.ends_at,
                location=booking.address,
                invitee_name=event.title,
                reference=booking.reference,
            )
            for event, booking in visits
        )

    # --- blocks by text: a date's own hours, kept as text-sourced blocks ---

    async def schedule_timezone(self, business_id: BusinessId) -> str | None:
        service = await self._service(business_id)
        return await self._engine.resource_time_zone(business_id, service.resource_id)

    async def _day(self, business_id: BusinessId, day: date) -> tuple[Service, ZoneInfo]:
        service = await self._service(business_id)
        zone = ZoneInfo(await self._engine.resource_time_zone(business_id, service.resource_id))
        return service, zone

    async def day_hours(self, business_id: BusinessId, day: date) -> DayHours:
        service, zone = await self._day(business_id, day)
        windows = await self._engine.weekly_hours(business_id, service.resource_id)
        hours: list[Interval] = sorted(
            (window.start.hour * 60 + window.start.minute, window.end.hour * 60 + window.end.minute)
            for window in windows
            if window.weekday == day.weekday()
            and (window.effective_from is None or window.effective_from <= day)
            and (window.effective_to is None or day <= window.effective_to)
        )
        start = datetime.combine(day, time(0), tzinfo=zone)
        blocks = [
            block
            for _, block, source, note in await self._engine.blocks_between(
                business_id, service.resource_id, start, start + timedelta(days=1)
            )
            if source is BlockSource.TEXT and note == f"{_DAY_NOTE}{day.isoformat()}"
        ]
        if not blocks:
            return DayHours(intervals=tuple(hours), overridden=False)
        cuts = [
            (
                _minutes(block.start.astimezone(zone)),
                24 * 60
                if block.end.astimezone(zone).date() > day
                else _minutes(block.end.astimezone(zone)),
            )
            for block in blocks
        ]
        free: list[Interval] = []
        cursor = 0
        for low, high in sorted(cuts):
            if low > cursor:
                free.append((cursor, low))
            cursor = max(cursor, high)
        if cursor < 24 * 60:
            free.append((cursor, 24 * 60))
        return DayHours(intervals=tuple(free), overridden=True)

    async def set_day_hours(
        self, business_id: BusinessId, day: date, intervals: tuple[Interval, ...] | None
    ) -> None:
        service, zone = await self._day(business_id, day)
        owner = owner_principal(business_id)
        start = datetime.combine(day, time(0), tzinfo=zone)
        for block_id, _, source, note in await self._engine.blocks_between(
            business_id, service.resource_id, start, start + timedelta(days=1)
        ):
            if source is BlockSource.TEXT and note == f"{_DAY_NOTE}{day.isoformat()}":
                await self._engine.remove_block(business_id, block_id, by=owner)
        if intervals is None:
            return
        cursor = 0
        gaps: list[Interval] = []
        for low, high in sorted(intervals):
            if low > cursor:
                gaps.append((cursor, low))
            cursor = max(cursor, high)
        if cursor < 24 * 60:
            gaps.append((cursor, 24 * 60))
        for low, high in gaps:
            await self._engine.add_block(
                business_id,
                service.resource_id,
                start=start + timedelta(minutes=low),
                end=start + timedelta(minutes=high)
                if high < 24 * 60
                else datetime.combine(day + timedelta(days=1), time(0), tzinfo=zone),
                by=owner,
                source=BlockSource.TEXT,
                note=f"{_DAY_NOTE}{day.isoformat()}",
            )


class SchedulerRouter:
    """Routes each call by the business's ``scheduler`` setting."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        engine: EngineAvailability,
        fallback: AvailabilityPort,
        fallback_blocks: ScheduleBlockPort | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._engine = engine
        self._fallback = fallback
        self._fallback_blocks = fallback_blocks

    async def _on_engine(self, business_id: BusinessId) -> bool:
        async with self._session_factory() as session:
            scheduler = await session.scalar(
                select(Business.scheduler).where(Business.id == business_id)
            )
        return scheduler == ENGINE

    async def _calendar(self, business_id: BusinessId) -> AvailabilityPort:
        return self._engine if await self._on_engine(business_id) else self._fallback

    async def _blocks(self, business_id: BusinessId) -> ScheduleBlockPort:
        if await self._on_engine(business_id):
            return self._engine
        if self._fallback_blocks is None:
            raise AvailabilityError("blocks are not available for this calendar")
        return self._fallback_blocks

    async def available_slots(
        self, business_id: BusinessId, start: datetime, end: datetime
    ) -> tuple[AvailableSlot, ...]:
        return await (await self._calendar(business_id)).available_slots(business_id, start, end)

    async def book(self, request: BookingRequest) -> BookingResult:
        return await (await self._calendar(request.business_id)).book(request)

    async def find_booking(self, request: BookingRequest) -> BookingResult | None:
        return await (await self._calendar(request.business_id)).find_booking(request)

    async def booking_event_type_uri(self, business_id: BusinessId) -> str | None:
        return await (await self._calendar(business_id)).booking_event_type_uri(business_id)

    async def cancel_booking(self, business_id: BusinessId, event_uri: str) -> None:
        await (await self._calendar(business_id)).cancel_booking(business_id, event_uri)

    def serves(self, business_id: BusinessId) -> bool:
        # Synchronous by contract, so it can't read the switch; the async
        # calls below route by it.
        return self._fallback_blocks is not None and self._fallback_blocks.serves(business_id)

    async def schedule_timezone(self, business_id: BusinessId) -> str | None:
        return await (await self._blocks(business_id)).schedule_timezone(business_id)

    async def day_hours(self, business_id: BusinessId, day: date) -> DayHours:
        return await (await self._blocks(business_id)).day_hours(business_id, day)

    async def set_day_hours(
        self, business_id: BusinessId, day: date, intervals: tuple[Interval, ...] | None
    ) -> None:
        await (await self._blocks(business_id)).set_day_hours(business_id, day, intervals)


class SchedulerBookedEvents:
    """The dashboard's bookings, from whichever calendar the business is on."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        engine: EngineAvailability,
        fallback: BookedEventsPort | None,
    ) -> None:
        self._router_session = session_factory
        self._engine = engine
        self._fallback = fallback

    def serves(self, business_id: BusinessId) -> bool:
        return True

    async def upcoming(
        self, business_id: BusinessId, start: datetime, end: datetime
    ) -> tuple[CalendarEvent, ...]:
        async with self._router_session() as session:
            scheduler = await session.scalar(
                select(Business.scheduler).where(Business.id == business_id)
            )
        if scheduler == ENGINE:
            return await self._engine.upcoming(business_id, start, end)
        if self._fallback is None or not self._fallback.serves(business_id):
            return ()
        return await self._fallback.upcoming(business_id, start, end)


def build_engine_availability(
    session_factory: async_sessionmaker[AsyncSession],
) -> EngineAvailability:
    engine = SchedulingEngine(session_factory)
    return EngineAvailability(
        engine, FieldServiceScheduling(engine, session_factory), session_factory
    )
