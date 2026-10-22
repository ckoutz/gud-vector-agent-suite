"""V1e team calendars: each employee is another calendar under the same
business; the owner chooses whose times Gus offers and who does a visit."""

from datetime import datetime, time, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from gvas.domain.identifiers import BusinessId
from gvas.domain.intake import AvailabilityError, BookingKind, BookingRequest
from gvas.domain.owner import CalendarEvent, VisitEditError
from gvas.infrastructure.models import Business
from gvas.infrastructure.scheduler_switch import ENGINE, EngineAvailability
from gvas.scheduling.core.engine import SchedulingEngine, owner_principal
from gvas.scheduling.core.model import (
    EMPLOYEE,
    Actor,
    BookingPolicy,
    CalendarRole,
    NotFoundError,
    PermissionDeniedError,
    WeeklyWindow,
)
from gvas.scheduling.core.records import SchedulingExternalLinkRecord
from gvas.scheduling.core.sync import push_events
from gvas.scheduling.field_service.booking import FieldServiceScheduling
from scheduling_fixtures import START, TUESDAY_10, Clock, seed_business

pytestmark = pytest.mark.postgres

HOUR = timedelta(hours=1)
TUESDAY_14 = TUESDAY_10 + 4 * HOUR
WEEK = START + timedelta(days=5)


class Team:
    def __init__(self, factory: async_sessionmaker[AsyncSession], clock: Clock) -> None:
        self.factory = factory
        self.engine = SchedulingEngine(factory, clock=clock)
        self.scheduling = FieldServiceScheduling(self.engine, factory)
        self.calendar = EngineAvailability(self.engine, self.scheduling, factory)

    async def setup(self) -> None:
        self.business_id = BusinessId(await seed_business(self.factory, "larkspur"))
        self.owner = owner_principal(self.business_id)
        own = await self.engine.create_calendar(
            self.business_id, name="Owner", time_zone="America/Los_Angeles", owner=self.owner,
            policy=BookingPolicy(min_notice=HOUR),
        )  # fmt: skip
        self.owner_resource = own.resource_id
        await self._hours(own.resource_id, time(8), time(17))
        self.service = await self.scheduling.create_service(
            self.business_id, own.resource_id, name="Estimate", duration=HOUR,
            buffer_after=timedelta(0),
        )  # fmt: skip
        ana = await self._employee("Ana", time(8), time(17), daily_limit=2)
        ben = await self._employee("Ben", time(13), time(17))
        self.ana, self.ana_calendar = ana.resource_id, ana.calendar_id
        self.ben = ben.resource_id
        async with self.factory() as session, session.begin():
            await session.execute(
                update(Business).where(Business.id == self.business_id).values(scheduler=ENGINE)
            )

    async def _hours(self, resource_id: object, start: time, end: time) -> None:
        await self.engine.set_weekly_hours(
            self.business_id, resource_id,  # type: ignore[arg-type]
            [WeeklyWindow(day, start, end) for day in range(5)], by=self.owner,
        )  # fmt: skip

    async def _employee(self, name: str, start: time, end: time, daily_limit: int | None = None):  # type: ignore[no-untyped-def]
        setup = await self.engine.create_calendar(
            self.business_id, name=name, time_zone="America/Los_Angeles", owner=self.owner,
            policy=BookingPolicy(min_notice=HOUR, daily_limit=daily_limit), kind="team",
            resource_kind=EMPLOYEE, color="#2563eb",
        )  # fmt: skip
        await self._hours(setup.resource_id, start, end)
        return setup

    def request(self, start: datetime, reference: str) -> BookingRequest:
        return BookingRequest(
            business_id=self.business_id, slot_start=start, slot_end=start + HOUR,
            invitee_name="Jane Doe", invitee_email="jane@example.com", reference=reference,
        )  # fmt: skip

    async def by_reference(self) -> dict[str | None, CalendarEvent]:
        visits = await self.calendar.upcoming(self.business_id, START, WEEK)
        return {visit.reference: visit for visit in visits}

    async def starts(self) -> set[datetime]:
        slots = await self.calendar.available_slots(self.business_id, START, WEEK)
        return {slot.start for slot in slots}


async def team(factory: async_sessionmaker[AsyncSession]) -> Team:
    built = Team(factory, Clock())
    await built.setup()
    return built


async def test_offered_times_default_to_the_owner_until_the_owner_picks_others(
    postgres_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    crew = await team(postgres_session_factory)
    members = {member.name: member for member in await crew.calendar.team(crew.business_id)}
    assert {name: member.offers_times for name, member in members.items()} == {
        "Owner": True, "Ana": False, "Ben": False,
    }  # fmt: skip
    await crew.calendar.add_visit(
        crew.business_id, start=TUESDAY_10, end=TUESDAY_10 + HOUR, name="Walt",
        email=None, phone=None, address=None, notes=None,
        employee_id=str(crew.owner_resource),
    )  # fmt: skip
    assert TUESDAY_10 not in await crew.starts()

    await crew.calendar.set_offering(crew.business_id, [str(crew.ana), str(crew.ben)])
    assert TUESDAY_10 in await crew.starts()
    with pytest.raises(VisitEditError):
        await crew.calendar.set_offering(crew.business_id, [str(uuid4())])
    with pytest.raises(VisitEditError):
        await crew.calendar.set_offering(crew.business_id, [])


async def test_two_employees_may_overlap_but_one_person_is_never_booked_twice(
    postgres_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    crew = await team(postgres_session_factory)
    await crew.calendar.set_offering(crew.business_id, [str(crew.ana), str(crew.ben)])
    first = await crew.calendar.book(crew.request(TUESDAY_14, "r1"))
    second = await crew.calendar.book(crew.request(TUESDAY_14, "r2"))
    assert first.kind is second.kind is BookingKind.BOOKED
    with pytest.raises(AvailabilityError):
        await crew.calendar.book(crew.request(TUESDAY_14, "r3"))
    visits = await crew.calendar.upcoming(crew.business_id, START, WEEK)
    assert sorted((visit.employee_name, visit.assigned) for visit in visits) == [
        ("Ana", False), ("Ben", False),
    ]  # fmt: skip
    assert TUESDAY_14 not in await crew.starts()


async def test_the_owner_picks_who_at_approval_or_later_by_moving_the_visit(
    postgres_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    crew = await team(postgres_session_factory)
    await crew.calendar.set_offering(crew.business_id, [str(crew.ana), str(crew.ben)])
    await crew.calendar.assign_on_approval(crew.business_id, "R1", str(crew.ben))
    await crew.calendar.book(crew.request(TUESDAY_14, "r1"))
    await crew.calendar.book(crew.request(TUESDAY_14 + HOUR, "r2"))
    visits = await crew.by_reference()
    assert (visits["r1"].employee_name, visits["r1"].assigned) == ("Ben", True)
    assert (visits["r2"].employee_name, visits["r2"].assigned) == ("Ana", False)

    later = visits["r2"]
    assert later.event_id is not None and later.end is not None
    with pytest.raises(VisitEditError, match="overlaps"):
        await crew.calendar.move_visit(
            crew.business_id, visits["r1"].event_id or "", TUESDAY_14 + HOUR,
            TUESDAY_14 + 2 * HOUR, str(crew.ana),
        )  # fmt: skip
    await crew.calendar.move_visit(
        crew.business_id, later.event_id, later.start, later.end, str(crew.ben)
    )
    moved = await crew.by_reference()
    assert (moved["r2"].employee_name, moved["r2"].assigned, moved["r2"].start) == (
        "Ben", True, later.start,
    )  # fmt: skip
    with pytest.raises(VisitEditError):
        await crew.calendar.assign_on_approval(crew.business_id, "r9", str(uuid4()))


async def test_the_daily_visit_limit_counts_each_employee_alone(
    postgres_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    crew = await team(postgres_session_factory)
    for start in (TUESDAY_10, TUESDAY_14):
        await crew.calendar.add_visit(
            crew.business_id, start=start, end=start + HOUR, name="Visit", email=None,
            phone=None, address=None, notes=None, employee_id=str(crew.ana),
        )  # fmt: skip
    day = TUESDAY_10.replace(hour=0)
    ana = await crew.engine.open_slots(
        crew.business_id, crew.ana, duration=HOUR, start=day, end=day + timedelta(days=1),
        requester=Actor.OWNER,
    )  # fmt: skip
    ben = await crew.engine.open_slots(
        crew.business_id, crew.ben, duration=HOUR, start=day, end=day + timedelta(days=1),
        requester=Actor.OWNER,
    )  # fmt: skip
    assert ana == () and ben != ()


async def test_an_employee_sees_and_changes_only_their_own_calendar(
    postgres_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    crew = await team(postgres_session_factory)
    staff = "staff:ana"
    await crew.engine.grant(
        crew.business_id, crew.ana_calendar, staff, CalendarRole.EDITOR, by=crew.owner
    )
    own = await crew.engine.add_event(
        crew.business_id, crew.ana, start=TUESDAY_10, end=TUESDAY_10 + HOUR, title="Ana's",
        by=staff,
    )  # fmt: skip
    other = await crew.engine.add_event(
        crew.business_id, crew.ben, start=TUESDAY_14, end=TUESDAY_14 + HOUR, title="Ben's",
        by=crew.owner,
    )  # fmt: skip
    assert (await crew.engine.event(crew.business_id, own, by=staff)).title == "Ana's"
    with pytest.raises(NotFoundError):
        await crew.engine.event(crew.business_id, other, by=staff)
    with pytest.raises(PermissionDeniedError):
        await crew.engine.add_event(
            crew.business_id, crew.ben, start=TUESDAY_10, end=TUESDAY_10 + HOUR, title="x",
            by=staff,
        )  # fmt: skip
    with pytest.raises(PermissionDeniedError):
        await crew.engine.move(
            crew.business_id, own, start=TUESDAY_10, end=TUESDAY_10 + HOUR, by=staff,
            resource_id=crew.ben,
        )  # fmt: skip


class Writer:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    async def upsert(
        self, credentials_ref: str, calendar_id: str, *, event_id: str, start: datetime,
        end: datetime, summary: str,
    ) -> None:  # fmt: skip
        self.calls.append(("upsert", event_id))

    async def delete(self, credentials_ref: str, calendar_id: str, *, event_id: str) -> None:
        self.calls.append(("delete", event_id))


async def test_a_visit_given_to_an_employee_leaves_the_owners_google_calendar(
    postgres_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    crew = await team(postgres_session_factory)
    link_id = uuid4()
    async with postgres_session_factory() as session, session.begin():
        session.add(
            SchedulingExternalLinkRecord(
                id=link_id, business_id=crew.business_id, resource_id=crew.owner_resource,
                provider="google", read_busy=False, write_events=True, credentials_ref="c",
                write_calendar_id="visits", status="active", created_at=START,
            )
        )  # fmt: skip
    visit = await crew.calendar.add_visit(
        crew.business_id, start=TUESDAY_10, end=TUESDAY_10 + HOUR, name="Walt", email=None,
        phone=None, address=None, notes=None, employee_id=str(crew.owner_resource),
    )  # fmt: skip
    writer = Writer()
    assert await push_events(postgres_session_factory, link_id, writer) == 1
    await crew.calendar.move_visit(
        crew.business_id, visit, TUESDAY_10, TUESDAY_10 + HOUR, str(crew.ana)
    )
    assert await push_events(postgres_session_factory, link_id, writer) == 1
    assert writer.calls == [("upsert", visit), ("delete", visit)]
