from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest

from gvas.scheduling.core.model import (
    Block,
    BookingPolicy,
    CalendarAction,
    CalendarRole,
    EventStatus,
    IllegalTransitionError,
    Interval,
    Occupied,
    Slot,
    WeeklyWindow,
    require_transition,
    role_allows,
)
from gvas.scheduling.core.offers import spread_offers
from gvas.scheduling.core.slots import open_slots

LA = ZoneInfo("America/Los_Angeles")
NY = ZoneInfo("America/New_York")
NO_NOTICE = BookingPolicy(min_notice=timedelta(0), horizon=timedelta(days=400))
MONDAY = date(2026, 10, 26)


def local(day: date, hour: int, minute: int = 0, zone: ZoneInfo = LA) -> datetime:
    return datetime.combine(day, time(hour, minute), tzinfo=zone)


def slots(
    windows: list[WeeklyWindow],
    day: date,
    *,
    days: int = 1,
    duration: int = 60,
    after: int = 0,
    occupied: tuple[Occupied, ...] = (),
    blocks: tuple[Block, ...] = (),
    policy: BookingPolicy = NO_NOTICE,
    now: datetime | None = None,
    zone: ZoneInfo = LA,
) -> tuple[Slot, ...]:
    return open_slots(
        zone=zone,
        windows=windows,
        blocks=blocks,
        occupied=occupied,
        policy=policy,
        duration=timedelta(minutes=duration),
        buffer_before=timedelta(0),
        buffer_after=timedelta(minutes=after),
        start=local(day, 0, zone=zone),
        end=local(day + timedelta(days=days), 0, zone=zone),
        now=now or datetime(2026, 1, 1, tzinfo=UTC),
    )


def wall(found: tuple[Slot, ...], zone: ZoneInfo = LA) -> list[str]:
    return [slot.start.astimezone(zone).strftime("%H:%M") for slot in found]


def test_several_windows_in_one_day() -> None:
    windows = [WeeklyWindow(0, time(8), time(12)), WeeklyWindow(0, time(13), time(17))]

    assert wall(slots(windows, MONDAY)) == [
        "08:00", "08:30", "09:00", "09:30", "10:00", "10:30", "11:00",
        "13:00", "13:30", "14:00", "14:30", "15:00", "15:30", "16:00",
    ]  # fmt: skip


def test_ac5_a_visit_ending_at_three_with_its_buffer_pushes_the_next_start_past_three_fifteen() -> (
    None
):
    windows = [WeeklyWindow(0, time(13), time(17))]
    visit = Occupied(local(MONDAY, 14), Interval(local(MONDAY, 14), local(MONDAY, 15, 15)))

    found = slots(windows, MONDAY, after=15, occupied=(visit,))

    assert wall(found) == ["15:30", "16:00"]
    assert all(slot.start >= local(MONDAY, 15, 15) for slot in found)


def test_notice_and_horizon_trim_the_range() -> None:
    windows = [WeeklyWindow(weekday, time(9), time(11)) for weekday in range(7)]
    now = local(MONDAY, 8)
    policy = BookingPolicy(min_notice=timedelta(hours=2), horizon=timedelta(days=1, hours=2))

    found = slots(windows, MONDAY, days=3, policy=policy, now=now)

    assert [slot.start.astimezone(LA).strftime("%a %H:%M") for slot in found] == [
        "Mon 10:00",
        "Tue 09:00",
        "Tue 09:30",
    ]


def test_a_full_day_offers_nothing_under_a_daily_limit() -> None:
    windows = [WeeklyWindow(0, time(8), time(17)), WeeklyWindow(1, time(8), time(17))]
    visit = Occupied(local(MONDAY, 8), Interval(local(MONDAY, 8), local(MONDAY, 9)))
    policy = BookingPolicy(min_notice=timedelta(0), horizon=timedelta(days=400), daily_limit=1)

    found = slots(windows, MONDAY, days=2, occupied=(visit,), policy=policy)

    assert {slot.start.astimezone(LA).date() for slot in found} == {MONDAY + timedelta(days=1)}


def test_one_time_and_repeating_time_off() -> None:
    windows = [WeeklyWindow(weekday, time(9), time(13)) for weekday in range(7)]
    lunch = Block(local(MONDAY, 11), local(MONDAY, 13), rrule="FREQ=DAILY")
    day_off = Block(local(MONDAY + timedelta(days=1), 0), local(MONDAY + timedelta(days=2), 0))

    found = slots(windows, MONDAY, days=3, blocks=(lunch, day_off))

    assert [slot.start.astimezone(LA).strftime("%a %H:%M") for slot in found] == [
        "Mon 09:00", "Mon 09:30", "Mon 10:00", "Wed 09:00", "Wed 09:30", "Wed 10:00",
    ]  # fmt: skip


def test_a_repeating_block_keeps_its_wall_clock_time_across_daylight_saving() -> None:
    windows = [WeeklyWindow(1, time(9), time(13))]
    before = date(2026, 10, 27)
    after = date(2026, 11, 3)
    block = Block(local(before, 12), local(before, 13), rrule="FREQ=WEEKLY;BYDAY=TU")

    found = slots(windows, before, days=8, duration=60, blocks=(block,))

    by_day: dict[date, list[str]] = {}
    for slot in found:
        by_day.setdefault(slot.start.astimezone(LA).date(), []).append(
            slot.start.astimezone(LA).strftime("%H:%M")
        )
    assert by_day[before] == by_day[after] == ["09:00", "09:30", "10:00", "10:30", "11:00"]


def test_ac13_spring_forward_skips_the_missing_hour_and_keeps_utc_right() -> None:
    day = date(2026, 3, 8)
    found = slots([WeeklyWindow(6, time(0), time(4))], day, duration=30)

    assert wall(found) == ["00:00", "00:30", "01:00", "01:30", "03:00", "03:30"]
    assert [slot.start.astimezone(UTC).strftime("%H:%M") for slot in found] == [
        "08:00", "08:30", "09:00", "09:30", "10:00", "10:30",
    ]  # fmt: skip


def test_ac14_fall_back_offers_the_repeated_hour_once_each_with_no_duplicate_or_gap() -> None:
    day = date(2026, 11, 1)
    found = slots([WeeklyWindow(6, time(0), time(3))], day, duration=30)

    starts = [slot.start for slot in found]
    assert len(starts) == len(set(starts)) == 8
    assert all(b - a == timedelta(minutes=30) for a, b in zip(starts, starts[1:], strict=False))
    assert wall(found) == ["00:00", "00:30", "01:00", "01:30", "01:00", "01:30", "02:00", "02:30"]


def test_ac16_a_weekly_nine_oclock_stays_nine_across_daylight_saving() -> None:
    windows = [WeeklyWindow(1, time(9), time(10))]
    found = slots(windows, date(2026, 10, 27), days=8)

    assert wall(found) == ["09:00", "09:00"]
    assert [slot.start.astimezone(UTC).hour for slot in found] == [16, 17]


def test_ac15_times_convert_for_a_viewer_in_another_zone() -> None:
    found = slots([WeeklyWindow(0, time(9), time(10))], MONDAY)

    assert wall(found, NY) == ["12:00"]
    assert found[0].start.utcoffset() == timedelta(0)


def test_dated_windows_apply_only_between_their_dates() -> None:
    windows = [WeeklyWindow(0, time(9), time(10), effective_from=MONDAY + timedelta(days=7))]

    assert slots(windows, MONDAY) == ()
    assert wall(slots(windows, MONDAY + timedelta(days=7))) == ["09:00"]


def test_offers_spread_over_days_and_stop_at_three() -> None:
    monday = [Slot(local(MONDAY, hour), local(MONDAY, hour + 1)) for hour in (8, 9, 10)]
    tuesday = [Slot(local(MONDAY + timedelta(days=1), 8), local(MONDAY + timedelta(days=1), 9))]

    offered = spread_offers([*monday, *tuesday], LA)

    assert [slot.start.astimezone(LA).strftime("%a %H") for slot in offered] == [
        "Mon 08",
        "Mon 09",
        "Tue 08",
    ]
    assert len(spread_offers(monday, LA)) == 3
    assert spread_offers([], LA) == ()


def test_only_legal_status_changes() -> None:
    require_transition(EventStatus.HELD, EventStatus.CONFIRMED)
    require_transition(EventStatus.PENDING_APPROVAL, EventStatus.CONFIRMED)
    require_transition(EventStatus.CONFIRMED, EventStatus.NO_SHOW)
    for current, new in (
        (EventStatus.CANCELLED, EventStatus.CONFIRMED),
        (EventStatus.COMPLETED, EventStatus.CANCELLED),
        (EventStatus.CONFIRMED, EventStatus.HELD),
        (EventStatus.HELD, EventStatus.COMPLETED),
        (EventStatus.EXPIRED, EventStatus.CONFIRMED),
    ):
        with pytest.raises(IllegalTransitionError):
            require_transition(current, new)


def test_calendar_roles() -> None:
    assert role_allows(CalendarRole.OWNER, CalendarAction.SHARE)
    assert role_allows(CalendarRole.EDITOR, CalendarAction.CHANGE)
    assert not role_allows(CalendarRole.EDITOR, CalendarAction.SHARE)
    assert not role_allows(CalendarRole.VIEWER, CalendarAction.CHANGE)
    assert not role_allows(CalendarRole.FREE_BUSY, CalendarAction.SEE_DETAILS)
    assert not role_allows(None, CalendarAction.SEE_BUSY)
