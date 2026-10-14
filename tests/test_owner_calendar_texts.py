"""Block and unblock time on the booking calendar by text."""

from datetime import date, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from gvas.application.calendar_blocks import BLOCK_NOT_CONFIGURED_REPLY
from gvas.composition.owner_change_tools import calendar_change_tools
from gvas.domain.calendar_blocks import Interval, format_day
from gvas.domain.identifiers import BusinessId
from gvas.domain.intake import AvailabilityError
from gvas.domain.owner_assistant import OwnerChangeRefusedError, OwnerChangeTool
from gvas.domain.repositories import BusinessRecord
from gvas.infrastructure.repositories import SqlBusinessRepository
from gvas.infrastructure.unit_of_work import SqlUnitOfWorkFactory
from test_calendar_blocks import WORKDAY, ScheduleFake
from test_seed_demo import NOW, _business

DAY = NOW.date() + timedelta(days=2)


class DownSchedule(ScheduleFake):
    """Calendly refuses the write before changing anything."""

    async def set_day_hours(
        self, business_id: BusinessId, day: date, intervals: tuple[Interval, ...] | None
    ) -> None:
        raise AvailabilityError("calendly returned http 502")


class ElsewhereSchedule(ScheduleFake):
    def serves(self, business_id: BusinessId) -> bool:
        return False


async def _tools(
    session_factory: async_sessionmaker[AsyncSession],
    schedule: ScheduleFake,
    clock: list[datetime] | None = None,
) -> tuple[BusinessRecord, OwnerChangeTool, OwnerChangeTool]:
    now = clock if clock is not None else [NOW]
    business_id = await _business(session_factory)
    async with session_factory() as session:
        business = await SqlBusinessRepository(session).get(business_id)
    assert business is not None
    block, unblock = calendar_change_tools(
        SqlUnitOfWorkFactory(session_factory), schedule, now=lambda: now[0]
    )
    return business, block, unblock


@pytest.mark.asyncio
async def test_block_then_unblock_restores_the_usual_hours(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    schedule = ScheduleFake()
    business, block, unblock = await _tools(session_factory, schedule)

    plan = await block.prepare(business, {"day": DAY.isoformat(), "start": "12:00", "end": "14:00"})
    assert plan.summary == (
        f"I will block {format_day(DAY)}, 12:00 PM–2:00 PM on your booking calendar, so no "
        "customer can book it. Visits already booked stay as they are."
    )
    assert schedule.set_calls == []
    reply = await block.apply(business, plan.arguments)
    assert reply.startswith(f"Done: blocked {format_day(DAY)}, 12:00 PM–2:00 PM")
    assert schedule.overrides[DAY] == ((9 * 60, 12 * 60), (14 * 60, 17 * 60))

    plan = await unblock.prepare(business, {"day": DAY.isoformat()})
    assert plan.summary == (
        f"I will unblock {format_day(DAY)} (12:00 PM–2:00 PM), so your usual hours "
        "can be booked again."
    )
    await unblock.apply(business, plan.arguments)
    assert DAY not in schedule.overrides
    with pytest.raises(OwnerChangeRefusedError, match="Nothing is blocked"):
        await unblock.prepare(business, {"day": DAY.isoformat()})


@pytest.mark.asyncio
async def test_unblock_refuses_once_the_day_has_a_block_it_did_not_read_back(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    schedule = ScheduleFake()
    business, block, unblock = await _tools(session_factory, schedule)
    first = await block.prepare(business, {"day": DAY.isoformat(), "start": "9:00", "end": "10:00"})
    await block.apply(business, first.arguments)
    plan = await unblock.prepare(business, {"day": DAY.isoformat()})
    second = await block.prepare(
        business, {"day": DAY.isoformat(), "start": "15:00", "end": "24:00"}
    )
    await block.apply(business, second.arguments)

    with pytest.raises(OwnerChangeRefusedError, match="changed since I asked"):
        await unblock.apply(business, plan.arguments)
    assert schedule.overrides[DAY] == ((10 * 60, 15 * 60),)


@pytest.mark.asyncio
async def test_a_write_that_lands_then_errors_is_kept_so_it_can_be_unblocked(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    schedule = ScheduleFake()
    business, block, unblock = await _tools(session_factory, schedule)
    plan = await block.prepare(business, {"day": DAY.isoformat(), "start": "9:00", "end": "10:00"})
    schedule.fail_after_write = True

    assert (await block.apply(business, plan.arguments)).startswith("Done: blocked")
    undo = await unblock.prepare(business, {"day": DAY.isoformat()})
    await unblock.apply(business, undo.arguments)
    assert DAY not in schedule.overrides


@pytest.mark.asyncio
async def test_a_write_that_never_landed_leaves_nothing_blocked(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    business, block, unblock = await _tools(session_factory, DownSchedule())
    plan = await block.prepare(business, {"day": DAY.isoformat(), "start": "9:00", "end": "10:00"})

    with pytest.raises(OwnerChangeRefusedError, match="couldn't reach the calendar"):
        await block.apply(business, plan.arguments)
    with pytest.raises(OwnerChangeRefusedError, match="Nothing is blocked"):
        await unblock.prepare(business, {"day": DAY.isoformat()})


@pytest.mark.asyncio
async def test_a_yes_after_the_day_has_passed_blocks_nothing(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    schedule = ScheduleFake()
    clock = [NOW]
    business, block, _ = await _tools(session_factory, schedule, clock)
    today = NOW.date().isoformat()
    plan = await block.prepare(business, {"day": today, "start": "15:00", "end": "17:00"})
    clock[0] = NOW + timedelta(days=1)

    with pytest.raises(OwnerChangeRefusedError, match="already passed"):
        await block.apply(business, plan.arguments)
    assert schedule.set_calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("args", "message"),
    [
        (
            {"day": (NOW.date() - timedelta(days=1)).isoformat(), "start": "9:00", "end": "10:00"},
            "already passed",
        ),
        ({"day": "next tuesday", "start": "9:00", "end": "10:00"}, "Which day"),
        ({"day": DAY.isoformat(), "start": "11:00", "end": "10:00"}, "end time must be after"),
        ({"day": DAY.isoformat(), "start": "9am", "end": "10:00"}, "like 8:00 to 12:00"),
        ({"day": DAY.isoformat(), "start": "24:00", "end": "24:00"}, "like 8:00 to 12:00"),
        ({"day": DAY.isoformat(), "start": "18:00", "end": "20:00"}, "aren't open then"),
    ],
)
async def test_block_refuses_what_it_cannot_block(
    session_factory: async_sessionmaker[AsyncSession], args: dict[str, str], message: str
) -> None:
    schedule = ScheduleFake()
    business, block, _ = await _tools(session_factory, schedule)

    with pytest.raises(OwnerChangeRefusedError, match=message):
        await block.prepare(business, args)
    assert schedule.set_calls == []


@pytest.mark.asyncio
async def test_no_tools_without_a_calendar_and_a_refusal_for_another_calendar(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    assert calendar_change_tools(SqlUnitOfWorkFactory(session_factory), None) == ()
    business, block, _ = await _tools(session_factory, ElsewhereSchedule())

    with pytest.raises(OwnerChangeRefusedError) as refused:
        await block.prepare(business, {"day": DAY.isoformat(), "start": "9:00", "end": "10:00"})
    assert str(refused.value) == BLOCK_NOT_CONFIGURED_REPLY
    assert WORKDAY.intervals == ((9 * 60, 17 * 60),)
