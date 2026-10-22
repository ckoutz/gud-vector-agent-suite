import asyncio
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from gvas.scheduling.core.engine import (
    CalendarSetup,
    SchedulingEngine,
    owner_principal,
)
from gvas.scheduling.core.model import (
    Actor,
    BookingPolicy,
    CalendarRole,
    Change,
    EventStatus,
    HoldExpiredError,
    HoldLimitError,
    NotFoundError,
    PermissionDeniedError,
    SlotTakenError,
    SlotUnavailableError,
    WeeklyWindow,
)
from gvas.scheduling.core.records import (
    SchedulingAuditRecord,
    SchedulingEventRecord,
    SchedulingSlotOfferRecord,
)
from gvas.scheduling.field_service.booking import FieldServiceScheduling, VisitDetails
from gvas.scheduling.field_service.records import FieldServiceBookingRecord
from scheduling_fixtures import START, TUESDAY_10, Clock, seed_business

pytestmark = pytest.mark.postgres

LA = ZoneInfo("America/Los_Angeles")
NY = ZoneInfo("America/New_York")
HOUR = timedelta(hours=1)
POLICY = BookingPolicy(min_notice=HOUR, horizon=timedelta(days=30), step=timedelta(minutes=30))


@dataclass
class Recorder:
    changes: list[Change] = field(default_factory=list)

    async def changed(self, change: Change) -> None:
        self.changes.append(change)


@dataclass
class World:
    factory: async_sessionmaker[AsyncSession]
    clock: Clock
    engine: SchedulingEngine
    business_id: UUID
    setup: CalendarSetup
    recorder: Recorder

    @property
    def owner(self) -> str:
        return owner_principal(self.business_id)

    @property
    def resource(self) -> UUID:
        return self.setup.resource_id

    def engine_for(self) -> SchedulingEngine:
        return SchedulingEngine(self.factory, clock=self.clock)


async def make_world(
    factory: async_sessionmaker[AsyncSession], slug: str = "larkspur", clock: Clock | None = None
) -> World:
    clock = clock or Clock()
    recorder = Recorder()
    engine = SchedulingEngine(factory, clock=clock, listeners=[recorder])
    business_id = await seed_business(factory, slug)
    owner = owner_principal(business_id)
    setup = await engine.create_calendar(
        business_id, name="Visits", time_zone="America/Los_Angeles", owner=owner, policy=POLICY
    )
    await engine.set_weekly_hours(
        business_id,
        setup.resource_id,
        [WeeklyWindow(day, time(8), time(17)) for day in range(5)],
        by=owner,
    )
    return World(factory, clock, engine, business_id, setup, recorder)


@pytest.fixture
async def world(postgres_session_factory: async_sessionmaker[AsyncSession]) -> World:
    return await make_world(postgres_session_factory)


async def offered(world: World, duration: timedelta = HOUR) -> list[datetime]:
    slots = await world.engine.open_slots(
        world.business_id,
        world.resource,
        duration=duration,
        start=START,
        end=START + timedelta(days=3),
        requester=Actor.GUS,
    )
    return [slot.start for slot in slots]


async def hold(world: World, holder: str = "chat-1", ip: str | None = "10.0.0.1") -> UUID:
    held = await world.engine.hold(
        world.business_id,
        world.resource,
        start=TUESDAY_10,
        duration=HOUR,
        title="Estimate",
        holder_ref=holder,
        holder_ip=ip,
        actor=Actor.GUS,
    )
    return held.event_id


async def test_ac3_a_held_slot_is_not_offered(world: World) -> None:
    assert TUESDAY_10 in await offered(world)

    await hold(world)

    assert TUESDAY_10 not in await offered(world)


async def test_ac1_two_visitors_taking_one_slot_at_once_leaves_exactly_one_winner(
    world: World,
) -> None:
    async def take(holder: str) -> UUID | Exception:
        try:
            held = await world.engine_for().hold(
                world.business_id,
                world.resource,
                start=TUESDAY_10,
                duration=HOUR,
                title="Estimate",
                holder_ref=holder,
                holder_ip=None,
                actor=Actor.PAGE,
            )
        except SlotTakenError as error:
            return error
        return held.event_id

    results = await asyncio.gather(*(take(f"visitor-{index}") for index in range(4)))

    winners = [result for result in results if isinstance(result, UUID)]
    assert len(winners) == 1
    assert sum(isinstance(result, SlotTakenError) for result in results) == 3

    confirms = await asyncio.gather(
        world.engine_for().confirm(world.business_id, winners[0], actor=Actor.PAGE),
        world.engine_for().confirm(world.business_id, winners[0], actor=Actor.PAGE),
        return_exceptions=True,
    )
    assert sorted(type(result).__name__ for result in confirms) == [
        "EventStatus",
        "HoldExpiredError",
    ]


async def test_ac2_a_lapsed_hold_still_books_when_nobody_took_the_time(world: World) -> None:
    event_id = await hold(world)
    world.clock.advance(timedelta(minutes=11))

    status = await world.engine.confirm(world.business_id, event_id, actor=Actor.GUS)

    assert status is EventStatus.CONFIRMED


async def test_ac2_a_lapsed_hold_whose_time_was_taken_is_refused_with_fresh_options(
    world: World,
) -> None:
    first = await hold(world, holder="chat-1")
    world.clock.advance(timedelta(minutes=11))
    await hold(world, holder="chat-2")

    with pytest.raises(HoldExpiredError) as refused:
        await world.engine.confirm(world.business_id, first, actor=Actor.GUS)

    assert refused.value.fresh
    assert all(slot.start != TUESDAY_10 for slot in refused.value.fresh)
    view = await world.engine.event(world.business_id, first, by=world.owner)
    assert view.status is EventStatus.EXPIRED


async def test_ac4_an_owner_visit_over_a_held_slot_is_refused_with_the_reason(
    world: World,
) -> None:
    await hold(world)

    with pytest.raises(SlotTakenError, match="time someone is booking"):
        await world.engine.add_event(
            world.business_id,
            world.resource,
            start=TUESDAY_10 + timedelta(minutes=30),
            end=TUESDAY_10 + timedelta(minutes=90),
            title="Phone-in",
            by=world.owner,
        )


async def test_ac4_an_owner_visit_after_a_hold_lapsed_takes_the_time(world: World) -> None:
    await hold(world)
    world.clock.advance(timedelta(minutes=11))

    await world.engine.add_event(
        world.business_id,
        world.resource,
        start=TUESDAY_10,
        end=TUESDAY_10 + HOUR,
        title="Phone-in",
        by=world.owner,
    )

    assert TUESDAY_10 not in await offered(world)


async def test_ac6_cancelling_a_confirmed_visit_offers_its_time_again(world: World) -> None:
    event_id = await hold(world)
    await world.engine.confirm(world.business_id, event_id, actor=Actor.GUS)
    assert TUESDAY_10 not in await offered(world)

    await world.engine.set_status(
        world.business_id, event_id, EventStatus.CANCELLED, actor=Actor.OWNER, by=world.owner
    )

    assert TUESDAY_10 in await offered(world)
    assert [change.status for change in world.recorder.changes] == [
        EventStatus.HELD,
        EventStatus.CONFIRMED,
        EventStatus.CANCELLED,
    ]


async def test_ac7_a_direct_overlapping_insert_is_rejected_by_the_database(world: World) -> None:
    await world.engine.add_event(
        world.business_id,
        world.resource,
        start=TUESDAY_10,
        end=TUESDAY_10 + HOUR,
        title="Visit",
        by=world.owner,
    )

    with pytest.raises(IntegrityError, match="ex_scheduling_events_no_overlap"):
        async with world.factory() as session, session.begin():
            session.add(
                SchedulingEventRecord(
                    id=uuid4(),
                    business_id=world.business_id,
                    calendar_id=world.setup.calendar_id,
                    resource_id=world.resource,
                    title="Sneaky",
                    starts_at=TUESDAY_10 + timedelta(minutes=59),
                    ends_at=TUESDAY_10 + 2 * HOUR,
                    time_zone="America/Los_Angeles",
                    all_day=False,
                    status=EventStatus.CONFIRMED.value,
                    blocked_start=TUESDAY_10 + timedelta(minutes=59),
                    blocked_end=TUESDAY_10 + 2 * HOUR,
                    created_by="owner",
                    version=1,
                    created_at=START,
                    updated_at=START,
                )
            )


async def test_back_to_back_visits_touch_without_overlapping(world: World) -> None:
    for offset in (0, 1):
        await world.engine.add_event(
            world.business_id,
            world.resource,
            start=TUESDAY_10 + offset * HOUR,
            end=TUESDAY_10 + (offset + 1) * HOUR,
            title="Visit",
            by=world.owner,
        )


async def test_ac17_a_new_business_zone_keeps_visits_in_place_and_moves_future_hours(
    world: World,
) -> None:
    event_id = await hold(world)
    await world.engine.confirm(world.business_id, event_id, actor=Actor.GUS)

    await world.engine.set_time_zone(
        world.business_id, world.resource, "America/New_York", by=world.owner
    )

    view = await world.engine.event(world.business_id, event_id, by=world.owner)
    assert view.start == TUESDAY_10
    starts = await offered(world)
    assert min(start for start in starts if start.astimezone(NY).day == 27).astimezone(NY).hour == 8


async def test_hold_limits_per_visitor_and_per_address(world: World) -> None:
    async def hold_at(hours: int, holder: str, ip: str) -> None:
        await world.engine.hold(
            world.business_id,
            world.resource,
            start=TUESDAY_10 + hours * HOUR,
            duration=HOUR,
            title="Estimate",
            holder_ref=holder,
            holder_ip=ip,
            actor=Actor.PAGE,
        )

    await hold_at(-2, "visitor", "10.0.0.9")
    await hold_at(-1, "visitor", "10.0.0.9")
    with pytest.raises(HoldLimitError):
        await hold_at(0, "visitor", "10.0.0.9")
    for index in range(3):
        await hold_at(index, f"other-{index}", "10.0.0.9")
    with pytest.raises(HoldLimitError):
        await hold_at(4, "other-9", "10.0.0.9")

    world.clock.advance(timedelta(minutes=11))
    await hold_at(4, "visitor", "10.0.0.9")


async def test_demo_holds_never_block_another_business(
    postgres_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    clock = Clock()
    demo = await make_world(postgres_session_factory, "demo-sandbox", clock)
    real = await make_world(postgres_session_factory, "real", clock)
    for hours in (0, 1):
        await demo.engine.hold(
            demo.business_id,
            demo.resource,
            start=TUESDAY_10 + hours * HOUR,
            duration=HOUR,
            title="Estimate",
            holder_ref="shared-visitor",
            holder_ip="10.0.0.1",
            actor=Actor.PAGE,
        )

    assert TUESDAY_10 in await offered(real)
    await hold(real, holder="shared-visitor", ip="10.0.0.1")


async def test_another_business_cannot_see_or_change_an_event(
    postgres_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    clock = Clock()
    mine = await make_world(postgres_session_factory, "mine", clock)
    theirs = await make_world(postgres_session_factory, "theirs", clock)
    event_id = await hold(mine)

    with pytest.raises(NotFoundError):
        await theirs.engine.confirm(theirs.business_id, event_id, actor=Actor.GUS)
    with pytest.raises(NotFoundError):
        await theirs.engine.event(theirs.business_id, event_id, by=theirs.owner)
    with pytest.raises(NotFoundError):
        await theirs.engine.open_slots(
            theirs.business_id,
            mine.resource,
            duration=HOUR,
            start=START,
            end=START + HOUR,
            requester=Actor.PAGE,
        )


async def test_calendar_permissions_decide_who_sees_and_changes(world: World) -> None:
    event_id = await hold(world)
    await world.engine.grant(
        world.business_id, world.setup.calendar_id, "crew:1", CalendarRole.FREE_BUSY, by=world.owner
    )
    await world.engine.grant(
        world.business_id, world.setup.calendar_id, "viewer:1", CalendarRole.VIEWER, by=world.owner
    )

    assert (await world.engine.event(world.business_id, event_id, by="crew:1")).title is None
    assert (
        await world.engine.event(world.business_id, event_id, by="viewer:1")
    ).title == "Estimate"
    with pytest.raises(NotFoundError):
        await world.engine.event(world.business_id, event_id, by="stranger")
    with pytest.raises(PermissionDeniedError):
        await world.engine.add_event(
            world.business_id,
            world.resource,
            start=TUESDAY_10 + 3 * HOUR,
            end=TUESDAY_10 + 4 * HOUR,
            title="Visit",
            by="viewer:1",
        )
    with pytest.raises(PermissionDeniedError):
        await world.engine.grant(
            world.business_id, world.setup.calendar_id, "x", CalendarRole.OWNER, by="viewer:1"
        )
    with pytest.raises(PermissionDeniedError):
        await world.engine.set_status(
            world.business_id, event_id, EventStatus.CANCELLED, actor=Actor.OWNER, by="viewer:1"
        )


async def test_a_blocked_hour_is_not_offered_and_unblocking_returns_it(world: World) -> None:
    block_id = await world.engine.add_block(
        world.business_id,
        world.resource,
        start=TUESDAY_10,
        end=TUESDAY_10 + HOUR,
        by=world.owner,
    )
    assert TUESDAY_10 not in await offered(world)
    with pytest.raises(SlotUnavailableError):
        await hold(world)

    await world.engine.remove_block(world.business_id, block_id, by=world.owner)

    assert TUESDAY_10 in await offered(world)


async def test_every_offer_and_change_is_logged(world: World) -> None:
    await offered(world)
    event_id = await hold(world)
    await world.engine.confirm(world.business_id, event_id, actor=Actor.GUS)

    async with world.factory() as session:
        offers = await session.scalar(
            select(func.count()).where(SchedulingSlotOfferRecord.business_id == world.business_id)
        )
        actions = list(
            await session.scalars(
                select(SchedulingAuditRecord.action)
                .where(SchedulingAuditRecord.entity_id == event_id)
                .order_by(SchedulingAuditRecord.at, SchedulingAuditRecord.action.desc())
            )
        )
    assert offers == 1
    assert sorted(actions) == ["confirmed", "held"]


def field_service(world: World) -> FieldServiceScheduling:
    return FieldServiceScheduling(world.engine, world.factory)


async def test_a_service_books_with_its_buffer_and_keeps_the_visit_details(world: World) -> None:
    scheduling = field_service(world)
    service = await scheduling.create_service(
        world.business_id,
        world.resource,
        name="Estimate walk-through",
        duration=timedelta(minutes=30),
        customer_notes="About 30 minutes on site.",
        questions=("Is the gate locked?",),
    )
    held = await scheduling.hold(
        world.business_id,
        service.service_id,
        start=TUESDAY_10,
        holder_ref="chat-1",
        holder_ip=None,
        actor=Actor.GUS,
    )
    customer_id = uuid4()
    status = await scheduling.confirm(
        world.business_id,
        held.event_id,
        VisitDetails(
            customer_id=customer_id,
            address="1 Oak St",
            job_description="Back lawn",
            answers={"Is the gate locked?": "No"},
        ),
        actor=Actor.GUS,
    )

    assert status is EventStatus.CONFIRMED
    slots = await scheduling.open_slots(
        world.business_id,
        service.service_id,
        start=TUESDAY_10,
        end=TUESDAY_10 + 2 * HOUR,
        requester=Actor.PAGE,
    )
    # 10:00-10:30 plus the 15-minute buffer: the next start is 11:00.
    assert [slot.start for slot in slots][0] == TUESDAY_10 + HOUR
    async with world.factory() as session:
        booking = await session.get(FieldServiceBookingRecord, held.event_id)
    assert booking is not None
    assert booking.customer_id == customer_id
    assert booking.answers == {"Is the gate locked?": "No"}


async def test_a_switched_off_service_offers_nothing(world: World) -> None:
    scheduling = field_service(world)
    service = await scheduling.create_service(
        world.business_id, world.resource, name="Estimate", duration=HOUR
    )
    await scheduling.set_active(world.business_id, service.service_id, False)

    assert (
        await scheduling.open_slots(
            world.business_id,
            service.service_id,
            start=START,
            end=START + timedelta(days=3),
            requester=Actor.GUS,
        )
        == ()
    )
    with pytest.raises(SlotUnavailableError):
        await scheduling.hold(
            world.business_id,
            service.service_id,
            start=TUESDAY_10,
            holder_ref="chat",
            holder_ip=None,
            actor=Actor.GUS,
        )


async def test_an_approval_waits_is_reminded_halfway_and_expires_freeing_the_time(
    world: World,
) -> None:
    scheduling = field_service(world)
    service = await scheduling.create_service(
        world.business_id,
        world.resource,
        name="Big job",
        duration=HOUR,
        needs_owner_approval=True,
    )
    held = await scheduling.hold(
        world.business_id,
        service.service_id,
        start=TUESDAY_10,
        holder_ref="chat",
        holder_ip=None,
        actor=Actor.GUS,
    )
    status = await scheduling.confirm(
        world.business_id, held.event_id, VisitDetails(), actor=Actor.GUS
    )
    assert status is EventStatus.PENDING_APPROVAL
    assert TUESDAY_10 not in await offered(world)

    world.clock.advance(timedelta(hours=12))
    first = await world.engine.sweep()
    second = await world.engine.sweep()
    assert first.approval_reminders_due == (held.event_id,)
    assert second.approval_reminders_due == ()

    world.clock.advance(timedelta(hours=12))
    final = await world.engine.sweep()
    assert final.approvals_expired == (held.event_id,)
    assert TUESDAY_10 in await offered(world)


async def test_the_owner_approves_a_pending_visit(world: World) -> None:
    scheduling = field_service(world)
    service = await scheduling.create_service(
        world.business_id, world.resource, name="Big job", duration=HOUR, needs_owner_approval=True
    )
    held = await scheduling.hold(
        world.business_id,
        service.service_id,
        start=TUESDAY_10,
        holder_ref="chat",
        holder_ip=None,
        actor=Actor.GUS,
    )
    await scheduling.confirm(world.business_id, held.event_id, VisitDetails(), actor=Actor.GUS)

    await world.engine.set_status(
        world.business_id, held.event_id, EventStatus.CONFIRMED, actor=Actor.OWNER, by=world.owner
    )
    world.clock.advance(timedelta(days=2))

    assert (await world.engine.sweep()).approvals_expired == ()
    view = await world.engine.event(world.business_id, held.event_id, by=world.owner)
    assert view.status is EventStatus.CONFIRMED


async def test_sweep_lapses_old_holds(world: World) -> None:
    event_id = await hold(world)
    world.clock.advance(timedelta(minutes=11))

    assert (await world.engine.sweep()).holds_expired == (event_id,)


async def test_confirming_queues_a_confirmation_and_reminders_and_cancelling_replaces_them(
    world: World,
) -> None:
    from gvas.scheduling.core.records import SchedulingReminderRecord

    scheduling = field_service(world)
    service = await scheduling.create_service(
        world.business_id, world.resource, name="Estimate", duration=HOUR
    )
    held = await scheduling.hold(
        world.business_id,
        service.service_id,
        start=TUESDAY_10,
        holder_ref="chat",
        holder_ip=None,
        actor=Actor.GUS,
    )
    await scheduling.confirm(
        world.business_id,
        held.event_id,
        VisitDetails(name="Jane", email="jane@example.com", phone="+15550100009"),
        actor=Actor.GUS,
    )

    async def queued() -> list[tuple[str, str, str]]:
        async with world.factory() as session:
            rows = await session.scalars(
                select(SchedulingReminderRecord)
                .where(SchedulingReminderRecord.event_id == held.event_id)
                .order_by(SchedulingReminderRecord.fire_at, SchedulingReminderRecord.channel)
            )
            return [(row.kind, row.channel, row.status) for row in rows]

    assert await queued() == [
        ("confirmation", "email", "pending"),
        ("confirmation", "sms", "pending"),
        ("reminder_24h", "email", "pending"),
        ("reminder_24h", "sms", "pending"),
        ("reminder_2h", "email", "pending"),
        ("reminder_2h", "sms", "pending"),
    ]

    await world.engine.set_status(
        world.business_id, held.event_id, EventStatus.CANCELLED, actor=Actor.OWNER, by=world.owner
    )

    after = await queued()
    assert sorted(status for kind, _, status in after if kind != "cancelled") == ["skipped"] * 6
    assert [(kind, channel) for kind, channel, status in after if status == "pending"] == [
        ("cancelled", "email"),
        ("cancelled", "sms"),
    ]
