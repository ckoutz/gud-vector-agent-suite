"""Open slots, computed from working hours, time off and what is already
occupied. Pure: no clock, no database."""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from dateutil.rrule import WEEKLY, rrule, rrulestr

from gvas.scheduling.core.model import (
    Block,
    BookingPolicy,
    Interval,
    Occupied,
    Slot,
    WeeklyWindow,
)


@dataclass(frozen=True)
class _Piece:
    """Free time inside one working window; slots step from the window's
    start so times stay on the hour and half hour."""

    anchor: datetime
    free: Interval


def _utc(wall: datetime, zone: ZoneInfo) -> datetime:
    return wall.replace(tzinfo=zone).astimezone(UTC)


def working_intervals(
    windows: Iterable[WeeklyWindow], first: date, last: date, zone: ZoneInfo
) -> list[Interval]:
    """Each weekly window's occurrences between two local dates, as UTC.

    Expanded in local wall-clock time, so 9:00 stays 9:00 across daylight
    saving changes."""

    intervals: list[Interval] = []
    for window in windows:
        start_day = max(first, window.effective_from) if window.effective_from else first
        end_day = min(last, window.effective_to) if window.effective_to else last
        if start_day > end_day:
            continue
        rule = rrule(
            WEEKLY,
            byweekday=window.weekday,
            dtstart=datetime.combine(start_day, window.start),
            until=datetime.combine(end_day, window.start),
        )
        length = datetime.combine(first, window.end) - datetime.combine(first, window.start)
        for occurrence in rule:
            intervals.append(Interval(_utc(occurrence, zone), _utc(occurrence + length, zone)))
    return sorted(intervals, key=lambda interval: interval.start)


def block_intervals(
    blocks: Iterable[Block], start: datetime, end: datetime, zone: ZoneInfo
) -> list[Interval]:
    """Time off between two instants; repeating blocks keep their local
    wall-clock times."""

    intervals: list[Interval] = []
    for block in blocks:
        if block.rrule is None:
            intervals.append(Interval(block.start, block.end))
            continue
        local_start = block.start.astimezone(zone).replace(tzinfo=None)
        length = block.end.astimezone(zone).replace(tzinfo=None) - local_start
        rule = rrulestr(block.rrule, dtstart=local_start)
        window_start = start.astimezone(zone).replace(tzinfo=None) - length
        window_end = end.astimezone(zone).replace(tzinfo=None)
        for occurrence in rule.between(window_start, window_end, inc=True):
            intervals.append(Interval(_utc(occurrence, zone), _utc(occurrence + length, zone)))
    return [interval for interval in intervals if interval.start < end and interval.end > start]


def _subtract(interval: Interval, cuts: Sequence[Interval]) -> list[Interval]:
    pieces = [interval]
    for cut in cuts:
        next_pieces: list[Interval] = []
        for piece in pieces:
            if not piece.overlaps(cut):
                next_pieces.append(piece)
                continue
            if piece.start < cut.start:
                next_pieces.append(Interval(piece.start, cut.start))
            if cut.end < piece.end:
                next_pieces.append(Interval(cut.end, piece.end))
        pieces = next_pieces
    return pieces


def open_slots(
    *,
    zone: ZoneInfo,
    windows: Sequence[WeeklyWindow],
    blocks: Sequence[Block],
    occupied: Sequence[Occupied],
    policy: BookingPolicy,
    duration: timedelta,
    buffer_before: timedelta,
    buffer_after: timedelta,
    start: datetime,
    end: datetime,
    now: datetime,
) -> tuple[Slot, ...]:
    """Every start in ``[start, end)`` a visit of ``duration`` can take.

    The visit itself must fit working hours outside time off; the visit plus
    its buffers must not touch another occupying event's range (which
    already includes that event's buffers)."""

    earliest = max(start, now + policy.min_notice)
    latest = min(end, now + policy.horizon)
    if earliest >= latest or duration <= timedelta(0):
        return ()
    first = earliest.astimezone(zone).date() - timedelta(days=1)
    last = latest.astimezone(zone).date() + timedelta(days=1)
    working = working_intervals(windows, first, last, zone)
    if not working:
        return ()
    off = block_intervals(blocks, working[0].start, working[-1].end, zone)
    full_days: set[date] = set()
    if policy.daily_limit is not None:
        counts: dict[date, int] = {}
        for event in occupied:
            day = event.start.astimezone(zone).date()
            counts[day] = counts.get(day, 0) + 1
        full_days = {day for day, count in counts.items() if count >= policy.daily_limit}
    pieces = [_Piece(window.start, free) for window in working for free in _subtract(window, off)]
    found: dict[datetime, Slot] = {}
    for piece in pieces:
        lower = max(piece.free.start, earliest)
        steps = -(-(lower - piece.anchor) // policy.step)
        candidate = piece.anchor + steps * policy.step
        while candidate + duration <= piece.free.end and candidate < latest:
            blocked = Interval(candidate - buffer_before, candidate + duration + buffer_after)
            if candidate.astimezone(zone).date() not in full_days and not any(
                blocked.overlaps(event.blocked) for event in occupied
            ):
                found[candidate] = Slot(candidate, candidate + duration)
            candidate += policy.step
    return tuple(found[key] for key in sorted(found))
