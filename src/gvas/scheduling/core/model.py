"""Pack-neutral scheduling terms: who acts, who may see or change a
calendar, the life of an event, and the rules that make time bookable."""

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from enum import StrEnum
from uuid import UUID


class SchedulingError(Exception):
    """Base for refusals a caller can show as-is."""


class SlotTakenError(SchedulingError):
    """The time overlaps a visit or a held time."""


class SlotUnavailableError(SchedulingError):
    """The time is outside the bookable hours (or too soon, or too far out)."""


class HoldExpiredError(SchedulingError):
    """The hold ran out and its time was taken meanwhile; ``fresh`` holds
    new options of the same length."""

    def __init__(self, message: str, fresh: "tuple[Slot, ...]" = ()) -> None:
        super().__init__(message)
        self.fresh = fresh


class HoldLimitError(SchedulingError):
    """This visitor (or address) already holds as many times as allowed."""


class IllegalTransitionError(SchedulingError):
    pass


class NotFoundError(SchedulingError):
    """Missing, or in another tenant (the two look the same on purpose)."""


class PermissionDeniedError(SchedulingError):
    pass


class Actor(StrEnum):
    OWNER = "owner"
    GUS = "gus"
    PAGE = "page"
    SYSTEM = "system"
    SYNC = "sync"


class CalendarRole(StrEnum):
    OWNER = "owner"
    EDITOR = "editor"
    VIEWER = "viewer"
    FREE_BUSY = "free_busy"


class CalendarAction(StrEnum):
    SEE_BUSY = "see_busy"
    SEE_DETAILS = "see_details"
    CHANGE = "change"
    SHARE = "share"


_ROLE_ACTIONS: dict[CalendarRole, frozenset[CalendarAction]] = {
    CalendarRole.OWNER: frozenset(CalendarAction),
    CalendarRole.EDITOR: frozenset(
        {CalendarAction.SEE_BUSY, CalendarAction.SEE_DETAILS, CalendarAction.CHANGE}
    ),
    CalendarRole.VIEWER: frozenset({CalendarAction.SEE_BUSY, CalendarAction.SEE_DETAILS}),
    CalendarRole.FREE_BUSY: frozenset({CalendarAction.SEE_BUSY}),
}


def role_allows(role: CalendarRole | None, action: CalendarAction) -> bool:
    return role is not None and action in _ROLE_ACTIONS[role]


class EventStatus(StrEnum):
    HELD = "held"
    PENDING_APPROVAL = "pending_approval"
    CONFIRMED = "confirmed"
    CANCELLED = "cancelled"
    COMPLETED = "completed"
    NO_SHOW = "no_show"
    #: A hold that ran out, or an approval nobody gave in time.
    EXPIRED = "expired"


#: Statuses that occupy time; the database refuses two of them overlapping.
OCCUPYING = frozenset({EventStatus.HELD, EventStatus.PENDING_APPROVAL, EventStatus.CONFIRMED})

_TRANSITIONS: dict[EventStatus, frozenset[EventStatus]] = {
    EventStatus.HELD: frozenset(
        {
            EventStatus.PENDING_APPROVAL,
            EventStatus.CONFIRMED,
            EventStatus.CANCELLED,
            EventStatus.EXPIRED,
        }
    ),
    EventStatus.PENDING_APPROVAL: frozenset(
        {EventStatus.CONFIRMED, EventStatus.CANCELLED, EventStatus.EXPIRED}
    ),
    EventStatus.CONFIRMED: frozenset(
        {EventStatus.CANCELLED, EventStatus.COMPLETED, EventStatus.NO_SHOW}
    ),
    EventStatus.CANCELLED: frozenset(),
    EventStatus.COMPLETED: frozenset(),
    EventStatus.NO_SHOW: frozenset(),
    EventStatus.EXPIRED: frozenset(),
}


def require_transition(current: EventStatus, new: EventStatus) -> None:
    if new not in _TRANSITIONS[current]:
        raise IllegalTransitionError(f"a {current.value} event can't become {new.value}")


class BlockKind(StrEnum):
    DAY_OFF = "day_off"
    HOLIDAY = "holiday"
    BLOCK = "block"


class BlockSource(StrEnum):
    MANUAL = "manual"
    TEXT = "text"
    SYNC = "sync"


@dataclass(frozen=True)
class Interval:
    start: datetime
    end: datetime

    def overlaps(self, other: "Interval") -> bool:
        return self.start < other.end and other.start < self.end


@dataclass(frozen=True)
class WeeklyWindow:
    """Bookable wall-clock hours on one weekday (0 = Monday), optionally
    only between two dates."""

    weekday: int
    start: time
    end: time
    effective_from: date | None = None
    effective_to: date | None = None


@dataclass(frozen=True)
class Block:
    """Time off: once, or repeating by an RFC 5545 RRULE expanded in the
    resource's zone from ``start``'s wall-clock time."""

    start: datetime
    end: datetime
    rrule: str | None = None


@dataclass(frozen=True)
class BookingPolicy:
    min_notice: timedelta = timedelta(hours=12)
    horizon: timedelta = timedelta(days=30)
    daily_limit: int | None = None
    step: timedelta = timedelta(minutes=30)


@dataclass(frozen=True)
class Occupied:
    """An occupying event as slot finding sees it: its visit start and the
    range it blocks (visit plus buffers)."""

    start: datetime
    blocked: Interval


@dataclass(frozen=True)
class Slot:
    start: datetime
    end: datetime


@dataclass(frozen=True)
class Change:
    """Published after commit so the rest of GVAS can react by id."""

    business_id: UUID
    event_id: UUID
    status: EventStatus
    actor: Actor
    at: datetime


#: The resource kind of a team member's own calendar under a business.
EMPLOYEE = "employee"
