from datetime import time, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import delete, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from gvas.scheduling.core.engine import SchedulingEngine, owner_principal
from gvas.scheduling.core.model import Actor, BookingPolicy, WeeklyWindow
from gvas.scheduling.core.records import (
    SchedulingExternalBusyRecord,
    SchedulingExternalLinkRecord,
)
from scheduling_fixtures import START, TUESDAY_10, Clock, seed_business

pytestmark = pytest.mark.postgres
HOUR = timedelta(hours=1)


async def setup(
    factory: async_sessionmaker[AsyncSession], clock: Clock
) -> tuple[SchedulingEngine, UUID, UUID, UUID]:
    business_id = await seed_business(factory, "larkspur")
    engine = SchedulingEngine(factory, clock=clock)
    owner = owner_principal(business_id)
    calendar = await engine.create_calendar(
        business_id, name="Visits", time_zone="America/Los_Angeles", owner=owner,
        policy=BookingPolicy(min_notice=HOUR),
    )  # fmt: skip
    await engine.set_weekly_hours(
        business_id, calendar.resource_id, [WeeklyWindow(1, time(8), time(17))], by=owner
    )
    link_id = uuid4()
    async with factory() as session, session.begin():
        session.add(
            SchedulingExternalLinkRecord(
                id=link_id, business_id=business_id, resource_id=calendar.resource_id,
                provider="google", read_busy=True, write_events=True, last_sync_at=clock(),
                status="active", created_at=clock(),
            )
        )  # fmt: skip
    return engine, business_id, calendar.resource_id, link_id


async def offered(engine: SchedulingEngine, business_id: UUID, resource_id: UUID) -> list[object]:
    slots = await engine.open_slots(
        business_id, resource_id, duration=HOUR, start=START, end=START + timedelta(days=2),
        requester=Actor.GUS,
    )  # fmt: skip
    return [slot.start for slot in slots]


async def test_ac8_ac9_an_outside_busy_block_hides_the_slot_and_deleting_it_brings_it_back(
    postgres_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    clock = Clock()
    engine, business_id, resource_id, link_id = await setup(postgres_session_factory, clock)
    assert TUESDAY_10 in await offered(engine, business_id, resource_id)

    async with postgres_session_factory() as session, session.begin():
        session.add(
            SchedulingExternalBusyRecord(
                id=uuid4(), link_id=link_id, external_id="g-1",
                starts_at=TUESDAY_10, ends_at=TUESDAY_10 + HOUR,
            )
        )  # fmt: skip
    assert TUESDAY_10 not in await offered(engine, business_id, resource_id)

    async with postgres_session_factory() as session, session.begin():
        await session.execute(delete(SchedulingExternalBusyRecord))
    assert TUESDAY_10 in await offered(engine, business_id, resource_id)


async def test_ac11_a_stale_outside_calendar_stops_all_offers(
    postgres_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    clock = Clock()
    engine, business_id, resource_id, link_id = await setup(postgres_session_factory, clock)
    clock.advance(timedelta(minutes=16))

    assert await offered(engine, business_id, resource_id) == []

    async with postgres_session_factory() as session, session.begin():
        await session.execute(
            update(SchedulingExternalLinkRecord)
            .where(SchedulingExternalLinkRecord.id == link_id)
            .values(last_sync_at=clock())
        )
    assert TUESDAY_10 in await offered(engine, business_id, resource_id)
