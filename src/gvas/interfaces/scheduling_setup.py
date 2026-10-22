"""Set a business up on the scheduling engine, or flip its switch.

    gvas-scheduling-setup --business-id <uuid> --time-zone America/Los_Angeles \
        --hours "mon-fri=08:00-12:00,13:00-17:00" --hours "sat=09:00-12:00" \
        --service "Estimate walk-through" --minutes 30 [--needs-approval]

    gvas-scheduling-setup --business-id <uuid> --switch engine   # or calendly

    gvas-scheduling-setup --business-id <uuid> --add-employee "Ana Ruiz" \
        --hours "tue-sat=08:00-16:00" [--color "#2563eb"] [--offers-times]

Setting up creates the business's calendar, hours and first service; it does
not switch the business over. Switching back to calendly is always safe:
nothing is deleted.
"""

import argparse
import asyncio
import re
from collections.abc import Sequence
from datetime import time, timedelta
from uuid import UUID

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from gvas.config import Settings
from gvas.infrastructure.db import create_engine, create_session_factory
from gvas.infrastructure.models import Business
from gvas.infrastructure.scheduler_switch import SCHEDULERS
from gvas.scheduling.core.engine import SchedulingEngine, owner_principal
from gvas.scheduling.core.model import EMPLOYEE, BookingPolicy, WeeklyWindow
from gvas.scheduling.field_service.booking import FieldServiceScheduling

DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
_SPEC = re.compile(r"^(?P<days>[a-z]{3}(?:-[a-z]{3})?)=(?P<windows>.+)$")
_WINDOW = re.compile(r"^(\d{1,2}):(\d{2})-(\d{1,2}):(\d{2})$")


def parse_hours(specs: Sequence[str]) -> list[WeeklyWindow]:
    """``mon-fri=08:00-12:00,13:00-17:00`` into weekly windows."""

    windows: list[WeeklyWindow] = []
    for spec in specs:
        match = _SPEC.match(spec.strip().lower())
        if match is None:
            raise ValueError(f"hours {spec!r}: use e.g. mon-fri=08:00-12:00,13:00-17:00")
        first, _, last = match["days"].partition("-")
        if first not in DAYS or (last and last not in DAYS):
            raise ValueError(f"hours {spec!r}: days are {', '.join(DAYS)}")
        start_day = DAYS.index(first)
        end_day = DAYS.index(last) if last else start_day
        if end_day < start_day:
            raise ValueError(f"hours {spec!r}: the day range runs backwards")
        for window in match["windows"].split(","):
            parts = _WINDOW.match(window.strip())
            if parts is None:
                raise ValueError(f"hours {spec!r}: times look like 08:00-12:00")
            start = time(int(parts[1]), int(parts[2]))
            end = time(int(parts[3]), int(parts[4]))
            if start >= end:
                raise ValueError(f"hours {spec!r}: {window} ends before it starts")
            windows.extend(WeeklyWindow(day, start, end) for day in range(start_day, end_day + 1))
    return windows


async def _add_employee(
    session_factory: async_sessionmaker[AsyncSession],
    business_id: UUID,
    arguments: argparse.Namespace,
) -> None:
    """An employee's own calendar under the business, with their hours.
    Their times are offered only with --offers-times (the owner's choice)."""

    windows = parse_hours(arguments.hours or [])
    if not windows:
        raise SystemExit("an employee needs --hours")
    engine = SchedulingEngine(session_factory)
    scheduling = FieldServiceScheduling(engine, session_factory)
    service = await scheduling.default_service(business_id)
    if service is None:
        raise SystemExit("set the business up on the engine first")
    owner = owner_principal(business_id)
    setup = await engine.create_calendar(
        business_id,
        name=arguments.add_employee,
        time_zone=arguments.time_zone,
        owner=owner,
        policy=BookingPolicy(
            min_notice=timedelta(hours=arguments.notice_hours),
            horizon=timedelta(days=arguments.horizon_days),
            daily_limit=arguments.daily_limit,
        ),
        kind="team",
        resource_kind=EMPLOYEE,
        color=arguments.color,
    )
    await engine.set_weekly_hours(business_id, setup.resource_id, windows, by=owner)
    if arguments.offers_times:
        offered = await scheduling.offering(business_id, service)
        await scheduling.set_offering(business_id, [*offered, setup.resource_id])
    print(f"employee calendar {setup.calendar_id}, resource {setup.resource_id}")


async def _run(arguments: argparse.Namespace) -> None:
    engine = create_engine(Settings().database_url)
    try:
        session_factory = create_session_factory(engine)
        business_id = UUID(arguments.business_id)
        if arguments.switch is not None:
            async with session_factory() as session, session.begin():
                result = await session.execute(
                    update(Business)
                    .where(Business.id == business_id)
                    .values(scheduler=arguments.switch)
                )
            if result.rowcount != 1:  # type: ignore[attr-defined]
                raise SystemExit("no such business")
            print(f"scheduler: {arguments.switch}")
            return
        if arguments.add_employee:
            await _add_employee(session_factory, business_id, arguments)
            return
        windows = parse_hours(arguments.hours or [])
        if not windows or not arguments.service or not arguments.minutes:
            raise SystemExit("setting up needs --hours, --service and --minutes")
        scheduling = SchedulingEngine(session_factory)
        owner = owner_principal(business_id)
        setup = await scheduling.create_calendar(
            business_id,
            name=arguments.service,
            time_zone=arguments.time_zone,
            owner=owner,
            policy=BookingPolicy(
                min_notice=timedelta(hours=arguments.notice_hours),
                horizon=timedelta(days=arguments.horizon_days),
                daily_limit=arguments.daily_limit,
            ),
        )
        await scheduling.set_weekly_hours(business_id, setup.resource_id, windows, by=owner)
        service = await FieldServiceScheduling(scheduling, session_factory).create_service(
            business_id,
            setup.resource_id,
            name=arguments.service,
            duration=timedelta(minutes=arguments.minutes),
            buffer_after=timedelta(minutes=arguments.buffer_after),
            needs_owner_approval=arguments.needs_approval,
        )
        print(f"calendar {setup.calendar_id}, service {service.service_id}; still on calendly")
    finally:
        await engine.dispose()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--business-id", required=True)
    parser.add_argument("--switch", choices=sorted(SCHEDULERS))
    parser.add_argument("--time-zone", default="America/Los_Angeles")
    parser.add_argument("--hours", action="append")
    parser.add_argument("--service")
    parser.add_argument("--minutes", type=int)
    parser.add_argument("--buffer-after", type=int, default=15)
    parser.add_argument("--needs-approval", action="store_true")
    parser.add_argument("--notice-hours", type=int, default=12)
    parser.add_argument("--horizon-days", type=int, default=30)
    parser.add_argument("--daily-limit", type=int)
    parser.add_argument("--add-employee", metavar="NAME")
    parser.add_argument("--color")
    parser.add_argument("--offers-times", action="store_true")
    asyncio.run(_run(parser.parse_args(argv)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
