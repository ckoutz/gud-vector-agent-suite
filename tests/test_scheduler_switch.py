from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import update
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
from gvas.infrastructure.models import Business
from gvas.infrastructure.scheduler_switch import (
    ENGINE,
    EngineAvailability,
    SchedulerBookedEvents,
    SchedulerRouter,
)
from gvas.interfaces.scheduling_setup import parse_hours
from gvas.scheduling.core.engine import SchedulingEngine, owner_principal
from gvas.scheduling.core.model import BookingPolicy, WeeklyWindow
from gvas.scheduling.field_service.booking import FieldServiceScheduling
from scheduling_fixtures import START, TUESDAY_10, Clock, seed_business

LA = ZoneInfo("America/Los_Angeles")
HOUR = timedelta(hours=1)
TUESDAY = date(2026, 10, 27)


class FakeCalendly:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def serves(self, business_id: BusinessId) -> bool:
        return True

    async def available_slots(
        self, business_id: BusinessId, start: datetime, end: datetime
    ) -> tuple[AvailableSlot, ...]:
        self.calls.append("slots")
        return (AvailableSlot(start=start, end=start + HOUR),)

    async def book(self, request: BookingRequest) -> BookingResult:
        self.calls.append("book")
        return BookingResult(kind=BookingKind.BOOKED, event_type_uri="calendly")

    async def find_booking(self, request: BookingRequest) -> BookingResult | None:
        self.calls.append("find")
        return None

    async def booking_event_type_uri(self, business_id: BusinessId) -> str | None:
        return "calendly"

    async def cancel_booking(self, business_id: BusinessId, event_uri: str) -> None:
        self.calls.append("cancel")

    async def schedule_timezone(self, business_id: BusinessId) -> str | None:
        return "America/Los_Angeles"

    async def day_hours(self, business_id: BusinessId, day: date) -> DayHours:
        self.calls.append("day_hours")
        return DayHours(intervals=(), overridden=False)

    async def set_day_hours(
        self, business_id: BusinessId, day: date, intervals: tuple[Interval, ...] | None
    ) -> None:
        self.calls.append("set_day_hours")


async def engine_business(
    factory: async_sessionmaker[AsyncSession], clock: Clock, *, site_url: str | None = None
) -> tuple[BusinessId, EngineAvailability]:
    business_id = BusinessId(await seed_business(factory, "larkspur"))
    engine = SchedulingEngine(factory, clock=clock)
    owner = owner_principal(business_id)
    setup = await engine.create_calendar(
        business_id,
        name="Visits",
        time_zone="America/Los_Angeles",
        owner=owner,
        policy=BookingPolicy(min_notice=HOUR),
    )
    await engine.set_weekly_hours(
        business_id,
        setup.resource_id,
        [WeeklyWindow(day, time(8), time(17)) for day in range(5)],
        by=owner,
    )
    scheduling = FieldServiceScheduling(engine, factory)
    await scheduling.create_service(
        business_id, setup.resource_id, name="Estimate", duration=HOUR, buffer_after=timedelta(0)
    )
    async with factory() as session, session.begin():
        await session.execute(
            update(Business)
            .where(Business.id == business_id)
            .values(scheduler=ENGINE, site_url=site_url)
        )
    return business_id, EngineAvailability(engine, scheduling, factory)


def request(business_id: BusinessId, reference: str) -> BookingRequest:
    return BookingRequest(
        business_id=business_id,
        slot_start=TUESDAY_10,
        slot_end=TUESDAY_10 + HOUR,
        invitee_name="Jane Doe",
        invitee_email="jane@example.com",
        address="1 Oak St",
        details="Back lawn",
        reference=reference,
    )


@pytest.mark.postgres
async def test_a_calendly_business_is_untouched_by_the_switch(
    postgres_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    clock = Clock()
    _, calendar = await engine_business(postgres_session_factory, clock)
    calendly_business = BusinessId(await seed_business(postgres_session_factory, "plumber"))
    calendly = FakeCalendly()
    router = SchedulerRouter(postgres_session_factory, calendar, calendly, calendly)

    await router.available_slots(calendly_business, START, START + HOUR)
    await router.book(request(calendly_business, "R-1"))
    await router.find_booking(request(calendly_business, "R-1"))
    await router.cancel_booking(calendly_business, "https://api.calendly.com/x")
    await router.set_day_hours(calendly_business, TUESDAY, None)

    assert calendly.calls == ["slots", "book", "find", "cancel", "set_day_hours"]
    assert router.serves(calendly_business)


@pytest.mark.postgres
async def test_an_engine_business_books_through_the_engine_once(
    postgres_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    clock = Clock()
    business_id, calendar = await engine_business(postgres_session_factory, clock)
    calendly = FakeCalendly()
    router = SchedulerRouter(postgres_session_factory, calendar, calendly, calendly)

    slots = await router.available_slots(business_id, START, START + timedelta(days=2))
    assert TUESDAY_10 in [slot.start for slot in slots]

    booked = await router.book(request(business_id, "R-1"))
    assert booked.kind is BookingKind.BOOKED
    assert booked.sends_own_messages
    assert await router.find_booking(request(business_id, "R-1")) is not None
    assert await router.find_booking(request(business_id, "R-2")) is None
    slots = await router.available_slots(business_id, START, START + timedelta(days=2))
    assert TUESDAY_10 not in [slot.start for slot in slots]
    with pytest.raises(AvailabilityError):
        await router.book(request(business_id, "R-2"))
    assert calendly.calls == []

    events = await SchedulerBookedEvents(postgres_session_factory, calendar, None).upcoming(
        business_id, START, START + timedelta(days=2)
    )
    assert [(event.start, event.location, event.reference) for event in events] == [
        (TUESDAY_10, "1 Oak St", "R-1")
    ]


@pytest.mark.postgres
async def test_a_taken_time_sends_the_customer_back_to_pick_again(
    postgres_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    business_id, calendar = await engine_business(
        postgres_session_factory, Clock(), site_url="https://larkspur.example"
    )
    await calendar.book(request(business_id, "R-1"))

    again = await calendar.book(request(business_id, "R-2"))

    assert again.kind is BookingKind.LINK
    assert again.link == "https://larkspur.example"


@pytest.mark.postgres
async def test_blocking_a_day_by_text_closes_it_on_the_engine_and_unblocking_reopens_it(
    postgres_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    clock = Clock()
    business_id, calendar = await engine_business(postgres_session_factory, clock)
    calendly = FakeCalendly()
    router = SchedulerRouter(postgres_session_factory, calendar, calendly, calendly)
    day_start = datetime.combine(TUESDAY, time(0), tzinfo=LA)

    async def tuesday() -> list[str]:
        slots = await router.available_slots(business_id, day_start, day_start + timedelta(days=1))
        return [slot.start.astimezone(LA).strftime("%H:%M") for slot in slots]

    assert await router.day_hours(business_id, TUESDAY) == DayHours(
        intervals=((480, 1020),), overridden=False
    )
    await router.set_day_hours(business_id, TUESDAY, ((12 * 60, 17 * 60),))

    assert (await tuesday())[0] == "12:00"
    assert await router.day_hours(business_id, TUESDAY) == DayHours(
        intervals=((720, 1020),), overridden=True
    )

    await router.set_day_hours(business_id, TUESDAY, ())
    assert await tuesday() == []

    await router.set_day_hours(business_id, TUESDAY, None)
    assert (await tuesday())[0] == "08:00"
    assert calendly.calls == []


def test_setup_hours_read_like_a_week() -> None:
    windows = parse_hours(["mon-fri=08:00-12:00,13:00-17:00", "sat=09:00-12:00"])

    assert len(windows) == 11
    assert windows[-1] == WeeklyWindow(5, time(9), time(12))
    for bad in ("fri-mon=08:00-12:00", "mon=12:00-08:00", "mon 8-12", "xyz=08:00-09:00"):
        with pytest.raises(ValueError):
            parse_hours([bad])


@pytest.mark.postgres
async def test_the_owner_adds_moves_and_cancels_engine_visits_but_not_calendly_ones(
    postgres_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    from gvas.domain.owner import VisitEditError

    clock = Clock()
    business_id, calendar = await engine_business(postgres_session_factory, clock)
    calendly_business = BusinessId(await seed_business(postgres_session_factory, "plumber"))
    calendly = FakeCalendly()
    router = SchedulerRouter(postgres_session_factory, calendar, calendly, calendly)
    booked = SchedulerBookedEvents(postgres_session_factory, calendar, None)

    phone_in = await router.add_visit(
        business_id, start=TUESDAY_10, end=TUESDAY_10 + HOUR, name="Walt Sims",
        email=None, phone="+15555550100", address="9 Elm St", notes="Called in",
    )  # fmt: skip
    with pytest.raises(VisitEditError, match="overlaps"):
        await router.add_visit(
            business_id, start=TUESDAY_10, end=TUESDAY_10 + HOUR, name="Dup",
            email=None, phone=None, address=None, notes=None,
        )  # fmt: skip
    [event] = await booked.upcoming(business_id, START, START + timedelta(days=2))
    assert (event.event_id, event.status, event.location) == (phone_in, "confirmed", "9 Elm St")

    await router.move_visit(business_id, phone_in, TUESDAY_10 + 2 * HOUR, TUESDAY_10 + 3 * HOUR)
    [event] = await booked.upcoming(business_id, START, START + timedelta(days=2))
    assert event.start == TUESDAY_10 + 2 * HOUR

    await router.cancel_visit(business_id, phone_in)
    assert await booked.upcoming(business_id, START, START + timedelta(days=2)) == ()
    with pytest.raises(VisitEditError):
        await router.cancel_visit(business_id, phone_in)
    with pytest.raises(VisitEditError):
        await router.move_visit(business_id, "not-an-id", TUESDAY_10, TUESDAY_10 + HOUR)
    with pytest.raises(VisitEditError, match="Calendly"):
        await router.cancel_visit(calendly_business, phone_in)
