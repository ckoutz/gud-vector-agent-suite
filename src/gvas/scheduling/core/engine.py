"""The engine's one way in: open slots, holds, confirmation, manual events,
status changes, time off and sweeps. Every call is scoped to one tenant."""

import logging
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from typing import Protocol
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

from sqlalchemy import ColumnElement, func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from gvas.domain.identifiers import JsonValue
from gvas.domain.time_zones import normalize_time_zone
from gvas.scheduling.core.model import (
    OCCUPYING,
    Actor,
    Block,
    BlockKind,
    BlockSource,
    BookingPolicy,
    CalendarAction,
    CalendarRole,
    Change,
    EventStatus,
    HoldExpiredError,
    HoldLimitError,
    Interval,
    NotFoundError,
    Occupied,
    PermissionDeniedError,
    Slot,
    SlotTakenError,
    SlotUnavailableError,
    WeeklyWindow,
    require_transition,
    role_allows,
)
from gvas.scheduling.core.notify import queue_cancelled, queue_confirmed
from gvas.scheduling.core.records import (
    SchedulingAuditRecord,
    SchedulingAvailabilityExceptionRecord,
    SchedulingAvailabilityRuleRecord,
    SchedulingCalendarPermissionRecord,
    SchedulingCalendarRecord,
    SchedulingEventRecord,
    SchedulingExternalBusyRecord,
    SchedulingExternalLinkRecord,
    SchedulingResourceRecord,
    SchedulingSlotOfferRecord,
)
from gvas.scheduling.core.slots import open_slots

logger = logging.getLogger(__name__)

HOLD_FOR = timedelta(minutes=10)
APPROVAL_WINDOW = timedelta(hours=24)
MAX_HOLDS_PER_VISITOR = 2
MAX_HOLDS_PER_IP = 5
#: FS-25: no offers when an outside calendar's last good sync is older.
STALE_AFTER = timedelta(minutes=15)
DEFAULT_POLICY = BookingPolicy()
_OCCUPYING_VALUES = tuple(status.value for status in OCCUPYING)

#: Runs inside the engine's transaction, so a pack's rows commit or roll
#: back together with the event.
Hook = Callable[[AsyncSession, SchedulingEventRecord], Awaitable[None]]


class ChangeListener(Protocol):
    async def changed(self, change: Change) -> None: ...


@dataclass(frozen=True)
class CalendarSetup:
    calendar_id: UUID
    resource_id: UUID


@dataclass(frozen=True)
class HeldTime:
    event_id: UUID
    start: datetime
    end: datetime
    expires_at: datetime


@dataclass(frozen=True)
class EventView:
    event_id: UUID
    start: datetime
    end: datetime
    time_zone: str
    status: EventStatus
    #: ``None`` for someone who may only see busy time.
    title: str | None


@dataclass(frozen=True)
class SweepResult:
    holds_expired: tuple[UUID, ...]
    approval_reminders_due: tuple[UUID, ...]
    approvals_expired: tuple[UUID, ...]


def owner_principal(business_id: UUID) -> str:
    """Today's only sign-in: the business owner's dashboard session."""

    return f"owner:{business_id}"


def _minutes(value: time) -> int:
    return value.hour * 60 + value.minute


def _time(minutes: int) -> time:
    return time(minutes // 60, minutes % 60)


def _event_state(record: SchedulingEventRecord) -> dict[str, JsonValue]:
    return {
        "status": record.status,
        "start": record.starts_at.isoformat(),
        "end": record.ends_at.isoformat(),
    }


class SchedulingEngine:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        listeners: Sequence[ChangeListener] = (),
    ) -> None:
        self._session_factory = session_factory
        self._clock = clock
        self._listeners = tuple(listeners)

    # --- setup -------------------------------------------------------------

    async def create_calendar(
        self,
        business_id: UUID,
        *,
        name: str,
        time_zone: str,
        owner: str,
        policy: BookingPolicy = DEFAULT_POLICY,
        kind: str = "business",
        resource_kind: str = "person",
    ) -> CalendarSetup:
        now = self._clock()
        zone = normalize_time_zone(time_zone)
        calendar_id, resource_id = uuid4(), uuid4()
        async with self._session_factory() as session, session.begin():
            session.add(
                SchedulingCalendarRecord(
                    id=calendar_id, business_id=business_id, name=name, kind=kind, created_at=now
                )
            )
            await session.flush()
            session.add(
                SchedulingCalendarPermissionRecord(
                    calendar_id=calendar_id,
                    principal=owner,
                    business_id=business_id,
                    role=CalendarRole.OWNER.value,
                )
            )
            session.add(
                SchedulingResourceRecord(
                    id=resource_id,
                    business_id=business_id,
                    calendar_id=calendar_id,
                    kind=resource_kind,
                    name=name,
                    time_zone=zone,
                    min_notice_minutes=int(policy.min_notice / timedelta(minutes=1)),
                    horizon_days=policy.horizon.days,
                    daily_limit=policy.daily_limit,
                    step_minutes=int(policy.step / timedelta(minutes=1)),
                    created_at=now,
                    updated_at=now,
                )
            )
        return CalendarSetup(calendar_id, resource_id)

    async def grant(
        self, business_id: UUID, calendar_id: UUID, principal: str, role: CalendarRole, *, by: str
    ) -> None:
        async with self._session_factory() as session, session.begin():
            await self._require(session, business_id, calendar_id, by, CalendarAction.SHARE)
            existing = await session.get(
                SchedulingCalendarPermissionRecord, (calendar_id, principal)
            )
            if existing is None:
                session.add(
                    SchedulingCalendarPermissionRecord(
                        calendar_id=calendar_id,
                        principal=principal,
                        business_id=business_id,
                        role=role.value,
                    )
                )
            else:
                existing.role = role.value

    async def set_weekly_hours(
        self, business_id: UUID, resource_id: UUID, windows: Sequence[WeeklyWindow], *, by: str
    ) -> None:
        for window in windows:
            if not 0 <= window.weekday <= 6 or window.start >= window.end:
                raise ValueError("each window needs a weekday 0-6 and a start before its end")
        async with self._session_factory() as session, session.begin():
            resource = await self._resource(session, business_id, resource_id, lock=True)
            await self._require(
                session, business_id, resource.calendar_id, by, CalendarAction.CHANGE
            )
            existing = await session.scalars(
                select(SchedulingAvailabilityRuleRecord).where(
                    SchedulingAvailabilityRuleRecord.resource_id == resource_id
                )
            )
            for record in existing:
                await session.delete(record)
            for window in windows:
                session.add(
                    SchedulingAvailabilityRuleRecord(
                        id=uuid4(),
                        business_id=business_id,
                        resource_id=resource_id,
                        weekday=window.weekday,
                        start_minute=_minutes(window.start),
                        end_minute=_minutes(window.end),
                        effective_from=window.effective_from,
                        effective_to=window.effective_to,
                    )
                )
            self._audit(
                session,
                business_id,
                Actor.OWNER,
                "resource",
                resource_id,
                "hours",
                None,
                {
                    "windows": [
                        [window.weekday, window.start.isoformat(), window.end.isoformat()]
                        for window in windows
                    ]
                },
            )

    async def set_time_zone(
        self, business_id: UUID, resource_id: UUID, time_zone: str, *, by: str
    ) -> None:
        """Existing events keep their instant; future hours read in the new zone."""

        zone = normalize_time_zone(time_zone)
        async with self._session_factory() as session, session.begin():
            resource = await self._resource(session, business_id, resource_id, lock=True)
            await self._require(
                session, business_id, resource.calendar_id, by, CalendarAction.CHANGE
            )
            before = resource.time_zone
            resource.time_zone = zone
            resource.updated_at = self._clock()
            self._audit(
                session,
                business_id,
                Actor.OWNER,
                "resource",
                resource_id,
                "time_zone",
                {"time_zone": before},
                {"time_zone": zone},
            )

    async def add_block(
        self,
        business_id: UUID,
        resource_id: UUID,
        *,
        start: datetime,
        end: datetime,
        by: str,
        kind: BlockKind = BlockKind.BLOCK,
        source: BlockSource = BlockSource.MANUAL,
        rrule: str | None = None,
        note: str | None = None,
    ) -> UUID:
        if start >= end:
            raise ValueError("a block needs a start before its end")
        block_id = uuid4()
        async with self._session_factory() as session, session.begin():
            resource = await self._resource(session, business_id, resource_id, lock=True)
            await self._require(
                session, business_id, resource.calendar_id, by, CalendarAction.CHANGE
            )
            session.add(
                SchedulingAvailabilityExceptionRecord(
                    id=block_id,
                    business_id=business_id,
                    resource_id=resource_id,
                    starts_at=start,
                    ends_at=end,
                    rrule=rrule,
                    kind=kind.value,
                    source=source.value,
                    note=note,
                    created_at=self._clock(),
                )
            )
            self._audit(
                session,
                business_id,
                Actor.OWNER,
                "block",
                block_id,
                "added",
                None,
                {"start": start.isoformat(), "end": end.isoformat(), "rrule": rrule},
            )
        return block_id

    async def remove_block(self, business_id: UUID, block_id: UUID, *, by: str) -> None:
        async with self._session_factory() as session, session.begin():
            block = await session.scalar(
                select(SchedulingAvailabilityExceptionRecord).where(
                    SchedulingAvailabilityExceptionRecord.business_id == business_id,
                    SchedulingAvailabilityExceptionRecord.id == block_id,
                )
            )
            if block is None:
                raise NotFoundError("no such block")
            resource = await self._resource(session, business_id, block.resource_id, lock=True)
            await self._require(
                session, business_id, resource.calendar_id, by, CalendarAction.CHANGE
            )
            await session.delete(block)
            self._audit(session, business_id, Actor.OWNER, "block", block_id, "removed", None, None)

    async def resource_time_zone(self, business_id: UUID, resource_id: UUID) -> str:
        async with self._session_factory() as session:
            return (await self._resource(session, business_id, resource_id)).time_zone

    async def weekly_hours(self, business_id: UUID, resource_id: UUID) -> tuple[WeeklyWindow, ...]:
        async with self._session_factory() as session:
            await self._resource(session, business_id, resource_id)
            return tuple(await self._windows(session, resource_id))

    async def blocks_between(
        self, business_id: UUID, resource_id: UUID, start: datetime, end: datetime
    ) -> tuple[tuple[UUID, Block, BlockSource, str | None], ...]:
        """One-time blocks overlapping ``start``-``end``, with their source and note."""

        async with self._session_factory() as session:
            await self._resource(session, business_id, resource_id)
            rows = await session.scalars(
                select(SchedulingAvailabilityExceptionRecord).where(
                    SchedulingAvailabilityExceptionRecord.resource_id == resource_id,
                    SchedulingAvailabilityExceptionRecord.rrule.is_(None),
                    SchedulingAvailabilityExceptionRecord.starts_at < end,
                    SchedulingAvailabilityExceptionRecord.ends_at > start,
                )
            )
            return tuple(
                (row.id, Block(row.starts_at, row.ends_at), BlockSource(row.source), row.note)
                for row in rows
            )

    # --- finding and booking time -----------------------------------------

    async def open_slots(
        self,
        business_id: UUID,
        resource_id: UUID,
        *,
        duration: timedelta,
        start: datetime,
        end: datetime,
        requester: Actor,
        buffer_before: timedelta = timedelta(0),
        buffer_after: timedelta = timedelta(0),
        min_notice: timedelta | None = None,
        horizon: timedelta | None = None,
        purpose: str | None = None,
    ) -> tuple[Slot, ...]:
        """Bookable starts, logged as an offer. Read-only otherwise."""

        now = self._clock()
        async with self._session_factory() as session, session.begin():
            resource = await self._resource(session, business_id, resource_id)
            slots, snapshot = await self._compute(
                session,
                resource,
                now,
                start,
                end,
                duration,
                buffer_before,
                buffer_after,
                min_notice,
                horizon,
            )
            session.add(
                SchedulingSlotOfferRecord(
                    id=uuid4(),
                    business_id=business_id,
                    resource_id=resource_id,
                    requester=requester.value,
                    purpose=purpose,
                    range_start=start,
                    range_end=end,
                    slots=[[slot.start.isoformat(), slot.end.isoformat()] for slot in slots],
                    snapshot=snapshot,
                    at=now,
                )
            )
        return slots

    async def hold(
        self,
        business_id: UUID,
        resource_id: UUID,
        *,
        start: datetime,
        duration: timedelta,
        title: str,
        holder_ref: str,
        holder_ip: str | None,
        actor: Actor,
        buffer_before: timedelta = timedelta(0),
        buffer_after: timedelta = timedelta(0),
        min_notice: timedelta | None = None,
        horizon: timedelta | None = None,
        then: Hook | None = None,
    ) -> HeldTime:
        """Reserve one open slot for ``HOLD_FOR`` while the visitor decides."""

        now = self._clock()
        event_id = uuid4()
        try:
            async with self._session_factory() as session, session.begin():
                resource = await self._resource(session, business_id, resource_id, lock=True)
                await self._expire_lapsed_holds(session, resource_id, now)
                await self._check_hold_limits(session, business_id, holder_ref, holder_ip, now)
                slots, _ = await self._compute(
                    session,
                    resource,
                    now,
                    start,
                    start + timedelta(microseconds=1),
                    duration,
                    buffer_before,
                    buffer_after,
                    min_notice,
                    horizon,
                )
                if not any(slot.start == start for slot in slots):
                    if await self._overlaps(
                        session,
                        resource_id,
                        start - buffer_before,
                        start + duration + buffer_after,
                        now,
                    ):
                        raise SlotTakenError("That time was just taken.")
                    raise SlotUnavailableError("That time isn't open for booking.")
                record = SchedulingEventRecord(
                    id=event_id,
                    business_id=business_id,
                    calendar_id=resource.calendar_id,
                    resource_id=resource_id,
                    title=title,
                    starts_at=start,
                    ends_at=start + duration,
                    time_zone=resource.time_zone,
                    all_day=False,
                    status=EventStatus.HELD.value,
                    blocked_start=start - buffer_before,
                    blocked_end=start + duration + buffer_after,
                    hold_expires_at=now + HOLD_FOR,
                    holder_ref=holder_ref,
                    holder_ip=holder_ip,
                    created_by=actor.value,
                    version=1,
                    created_at=now,
                    updated_at=now,
                )
                session.add(record)
                await session.flush()
                if then is not None:
                    await then(session, record)
                self._audit(
                    session,
                    business_id,
                    actor,
                    "event",
                    event_id,
                    "held",
                    None,
                    _event_state(record),
                )
        except IntegrityError as error:
            raise SlotTakenError("That time was just taken.") from error
        await self._publish(business_id, event_id, EventStatus.HELD, actor, now)
        return HeldTime(event_id, start, start + duration, now + HOLD_FOR)

    async def confirm(
        self,
        business_id: UUID,
        event_id: UUID,
        *,
        actor: Actor,
        needs_approval: bool = False,
        then: Hook | None = None,
    ) -> EventStatus:
        """Turn a hold into a visit in one transaction.

        A lapsed hold still books when nothing took its time; otherwise
        ``HoldExpiredError`` carries fresh options for the same length."""

        now = self._clock()
        new_status = EventStatus.PENDING_APPROVAL if needs_approval else EventStatus.CONFIRMED
        fresh: tuple[Slot, ...] = ()
        refused = False
        try:
            async with self._session_factory() as session, session.begin():
                record = await self._event(session, business_id, event_id)
                resource = await self._resource(session, business_id, record.resource_id, lock=True)
                await session.refresh(record)
                current = EventStatus(record.status)
                if current is EventStatus.EXPIRED:
                    fresh = await self._fresh(session, resource, record, now)
                    raise HoldExpiredError("That hold ran out and the time was taken.", fresh)
                if current is not EventStatus.HELD:
                    raise HoldExpiredError("That hold is no longer active.")
                lapsed = record.hold_expires_at is not None and record.hold_expires_at <= now
                if lapsed:
                    await self._expire_lapsed_holds(
                        session, record.resource_id, now, keep=record.id
                    )
                    refused = await self._overlaps(
                        session,
                        record.resource_id,
                        record.blocked_start,
                        record.blocked_end,
                        now,
                        exclude=record.id,
                    )
                if refused:
                    # Committed: the lapsed hold leaves the calendar either way.
                    record.status = EventStatus.EXPIRED.value
                    record.updated_at = now
                    self._audit(
                        session,
                        business_id,
                        actor,
                        "event",
                        event_id,
                        "expired",
                        None,
                        _event_state(record),
                    )
                    fresh = await self._fresh(session, resource, record, now)
                else:
                    before = _event_state(record)
                    require_transition(EventStatus.HELD, new_status)
                    record.status = new_status.value
                    record.hold_expires_at = None
                    record.approval_expires_at = now + APPROVAL_WINDOW if needs_approval else None
                    record.version += 1
                    record.updated_at = now
                    await session.flush()
                    if then is not None:
                        await then(session, record)
                    if new_status is EventStatus.CONFIRMED:
                        await queue_confirmed(session, record, now)
                    self._audit(
                        session,
                        business_id,
                        actor,
                        "event",
                        event_id,
                        new_status.value,
                        before,
                        _event_state(record),
                    )
        except IntegrityError as error:
            raise SlotTakenError("That time was just taken.") from error
        if refused:
            await self._publish(business_id, event_id, EventStatus.EXPIRED, actor, now)
            raise HoldExpiredError("That hold ran out and the time was taken.", fresh)
        await self._publish(business_id, event_id, new_status, actor, now)
        return new_status

    async def add_event(
        self,
        business_id: UUID,
        resource_id: UUID,
        *,
        start: datetime,
        end: datetime,
        title: str,
        by: str,
        buffer_before: timedelta = timedelta(0),
        buffer_after: timedelta = timedelta(0),
        then: Hook | None = None,
    ) -> UUID:
        """An owner-made event (a phone-in visit): any hours the owner
        chooses, but never over another occupying event or hold."""

        if start >= end:
            raise ValueError("an event needs a start before its end")
        now = self._clock()
        event_id = uuid4()
        try:
            async with self._session_factory() as session, session.begin():
                resource = await self._resource(session, business_id, resource_id, lock=True)
                await self._require(
                    session, business_id, resource.calendar_id, by, CalendarAction.CHANGE
                )
                await self._expire_lapsed_holds(session, resource_id, now)
                record = SchedulingEventRecord(
                    id=event_id,
                    business_id=business_id,
                    calendar_id=resource.calendar_id,
                    resource_id=resource_id,
                    title=title,
                    starts_at=start,
                    ends_at=end,
                    time_zone=resource.time_zone,
                    all_day=False,
                    status=EventStatus.CONFIRMED.value,
                    blocked_start=start - buffer_before,
                    blocked_end=end + buffer_after,
                    created_by=Actor.OWNER.value,
                    version=1,
                    created_at=now,
                    updated_at=now,
                )
                session.add(record)
                await session.flush()
                if then is not None:
                    await then(session, record)
                await queue_confirmed(session, record, now)
                self._audit(
                    session,
                    business_id,
                    Actor.OWNER,
                    "event",
                    event_id,
                    "added",
                    None,
                    _event_state(record),
                )
        except IntegrityError as error:
            raise SlotTakenError(
                "That time overlaps another visit or a time someone is booking."
            ) from error
        await self._publish(business_id, event_id, EventStatus.CONFIRMED, Actor.OWNER, now)
        return event_id

    async def set_status(
        self,
        business_id: UUID,
        event_id: UUID,
        status: EventStatus,
        *,
        actor: Actor,
        by: str | None = None,
    ) -> None:
        """Approve, cancel, complete or mark a no-show. Leaving an occupying
        status frees the time at once. The owner's changes need ``by``."""

        now = self._clock()
        async with self._session_factory() as session, session.begin():
            record = await self._event(session, business_id, event_id)
            await self._resource(session, business_id, record.resource_id, lock=True)
            await session.refresh(record)
            if actor is Actor.OWNER:
                if by is None:
                    raise PermissionDeniedError("the owner's change needs a sign-in")
                await self._require(
                    session, business_id, record.calendar_id, by, CalendarAction.CHANGE
                )
            before = _event_state(record)
            require_transition(EventStatus(record.status), status)
            record.status = status.value
            record.hold_expires_at = None
            record.approval_expires_at = None
            record.version += 1
            record.updated_at = now
            if status is EventStatus.CONFIRMED:
                await queue_confirmed(session, record, now)
            elif status is EventStatus.CANCELLED:
                await queue_cancelled(session, record, now)
            self._audit(
                session,
                business_id,
                actor,
                "event",
                event_id,
                status.value,
                before,
                _event_state(record),
            )
        await self._publish(business_id, event_id, status, actor, now)

    async def event(self, business_id: UUID, event_id: UUID, *, by: str) -> EventView:
        async with self._session_factory() as session:
            record = await self._event(session, business_id, event_id)
            role = await self._role(session, business_id, record.calendar_id, by)
            if not role_allows(role, CalendarAction.SEE_BUSY):
                raise NotFoundError("no such event")
            details = role_allows(role, CalendarAction.SEE_DETAILS)
            return EventView(
                event_id=record.id,
                start=record.starts_at,
                end=record.ends_at,
                time_zone=record.time_zone,
                status=EventStatus(record.status),
                title=record.title if details else None,
            )

    async def sweep(self) -> SweepResult:
        """Lapse old holds, flag approvals at their halfway point and expire
        approvals nobody gave. Callers send the messages (V1b)."""

        now = self._clock()
        async with self._session_factory() as session, session.begin():
            lapsed = list(
                await session.scalars(
                    select(SchedulingEventRecord)
                    .where(
                        SchedulingEventRecord.status == EventStatus.HELD.value,
                        SchedulingEventRecord.hold_expires_at <= now,
                    )
                    .with_for_update(skip_locked=True)
                )
            )
            pending = list(
                await session.scalars(
                    select(SchedulingEventRecord)
                    .where(
                        SchedulingEventRecord.status == EventStatus.PENDING_APPROVAL.value,
                        SchedulingEventRecord.approval_expires_at.is_not(None),
                    )
                    .with_for_update(skip_locked=True)
                )
            )
            reminders: list[UUID] = []
            expired: list[UUID] = []
            for record in lapsed:
                record.status = EventStatus.EXPIRED.value
                record.updated_at = now
                self._audit(
                    session,
                    record.business_id,
                    Actor.SYSTEM,
                    "event",
                    record.id,
                    "expired",
                    None,
                    _event_state(record),
                )
            for record in pending:
                assert record.approval_expires_at is not None
                if record.approval_expires_at <= now:
                    record.status = EventStatus.EXPIRED.value
                    record.approval_expires_at = None
                    record.updated_at = now
                    expired.append(record.id)
                    self._audit(
                        session,
                        record.business_id,
                        Actor.SYSTEM,
                        "event",
                        record.id,
                        "expired",
                        None,
                        _event_state(record),
                    )
                elif record.approval_reminded_at is None and (
                    now >= record.approval_expires_at - APPROVAL_WINDOW / 2
                ):
                    record.approval_reminded_at = now
                    reminders.append(record.id)
        for record in lapsed:
            await self._publish(
                record.business_id, record.id, EventStatus.EXPIRED, Actor.SYSTEM, now
            )
        for record in pending:
            if record.id in expired:
                await self._publish(
                    record.business_id, record.id, EventStatus.EXPIRED, Actor.SYSTEM, now
                )
        return SweepResult(tuple(record.id for record in lapsed), tuple(reminders), tuple(expired))

    # --- internals ----------------------------------------------------------

    async def _resource(
        self, session: AsyncSession, business_id: UUID, resource_id: UUID, *, lock: bool = False
    ) -> SchedulingResourceRecord:
        query = select(SchedulingResourceRecord).where(
            SchedulingResourceRecord.business_id == business_id,
            SchedulingResourceRecord.id == resource_id,
        )
        if lock:
            query = query.with_for_update()
        resource = await session.scalar(query)
        if resource is None:
            raise NotFoundError("no such calendar")
        return resource

    async def _event(
        self, session: AsyncSession, business_id: UUID, event_id: UUID
    ) -> SchedulingEventRecord:
        record = await session.scalar(
            select(SchedulingEventRecord).where(
                SchedulingEventRecord.business_id == business_id,
                SchedulingEventRecord.id == event_id,
            )
        )
        if record is None:
            raise NotFoundError("no such event")
        return record

    async def _role(
        self, session: AsyncSession, business_id: UUID, calendar_id: UUID, principal: str
    ) -> CalendarRole | None:
        role = await session.scalar(
            select(SchedulingCalendarPermissionRecord.role).where(
                SchedulingCalendarPermissionRecord.business_id == business_id,
                SchedulingCalendarPermissionRecord.calendar_id == calendar_id,
                SchedulingCalendarPermissionRecord.principal == principal,
            )
        )
        return CalendarRole(role) if role is not None else None

    async def _require(
        self,
        session: AsyncSession,
        business_id: UUID,
        calendar_id: UUID,
        principal: str,
        action: CalendarAction,
    ) -> None:
        if not role_allows(await self._role(session, business_id, calendar_id, principal), action):
            raise PermissionDeniedError(f"not allowed to {action.value} this calendar")

    async def _expire_lapsed_holds(
        self, session: AsyncSession, resource_id: UUID, now: datetime, keep: UUID | None = None
    ) -> None:
        """A constraint can't read the clock, so lapsed holds leave the
        occupied set here, inside the booking transaction."""

        statement = (
            update(SchedulingEventRecord)
            .where(
                SchedulingEventRecord.resource_id == resource_id,
                SchedulingEventRecord.status == EventStatus.HELD.value,
                SchedulingEventRecord.hold_expires_at <= now,
            )
            .values(status=EventStatus.EXPIRED.value, updated_at=now)
        )
        if keep is not None:
            statement = statement.where(SchedulingEventRecord.id != keep)
        await session.execute(statement)

    async def _check_hold_limits(
        self,
        session: AsyncSession,
        business_id: UUID,
        holder_ref: str,
        holder_ip: str | None,
        now: datetime,
    ) -> None:
        active = (
            SchedulingEventRecord.business_id == business_id,
            SchedulingEventRecord.status == EventStatus.HELD.value,
            SchedulingEventRecord.hold_expires_at > now,
        )
        by_visitor = await session.scalar(
            select(func.count()).where(*active, SchedulingEventRecord.holder_ref == holder_ref)
        )
        if (by_visitor or 0) >= MAX_HOLDS_PER_VISITOR:
            raise HoldLimitError("You're already holding as many times as allowed.")
        if holder_ip is not None:
            by_ip = await session.scalar(
                select(func.count()).where(*active, SchedulingEventRecord.holder_ip == holder_ip)
            )
            if (by_ip or 0) >= MAX_HOLDS_PER_IP:
                raise HoldLimitError("Too many times are being held from this address.")

    def _occupying(self, now: datetime) -> tuple[ColumnElement[bool], ...]:
        return (
            SchedulingEventRecord.status.in_(_OCCUPYING_VALUES),
            or_(
                SchedulingEventRecord.status != EventStatus.HELD.value,
                SchedulingEventRecord.hold_expires_at > now,
            ),
        )

    async def _overlaps(
        self,
        session: AsyncSession,
        resource_id: UUID,
        start: datetime,
        end: datetime,
        now: datetime,
        exclude: UUID | None = None,
    ) -> bool:
        query = select(SchedulingEventRecord.id).where(
            SchedulingEventRecord.resource_id == resource_id,
            SchedulingEventRecord.blocked_start < end,
            SchedulingEventRecord.blocked_end > start,
            *self._occupying(now),
        )
        if exclude is not None:
            query = query.where(SchedulingEventRecord.id != exclude)
        return (await session.scalar(query.limit(1))) is not None

    async def _fresh(
        self,
        session: AsyncSession,
        resource: SchedulingResourceRecord,
        record: SchedulingEventRecord,
        now: datetime,
    ) -> tuple[Slot, ...]:
        """New options of the same length and buffers for the next two weeks."""

        slots, _ = await self._compute(
            session,
            resource,
            now,
            now,
            now + timedelta(days=14),
            record.ends_at - record.starts_at,
            record.starts_at - record.blocked_start,
            record.blocked_end - record.ends_at,
            None,
            None,
        )
        return slots

    async def _windows(self, session: AsyncSession, resource_id: UUID) -> list[WeeklyWindow]:
        rules = await session.scalars(
            select(SchedulingAvailabilityRuleRecord).where(
                SchedulingAvailabilityRuleRecord.resource_id == resource_id
            )
        )
        return [
            WeeklyWindow(
                rule.weekday,
                _time(rule.start_minute),
                _time(rule.end_minute),
                rule.effective_from,
                rule.effective_to,
            )
            for rule in rules
        ]

    async def _compute(
        self,
        session: AsyncSession,
        resource: SchedulingResourceRecord,
        now: datetime,
        start: datetime,
        end: datetime,
        duration: timedelta,
        buffer_before: timedelta,
        buffer_after: timedelta,
        min_notice: timedelta | None,
        horizon: timedelta | None,
    ) -> tuple[tuple[Slot, ...], dict[str, JsonValue]]:
        zone = ZoneInfo(resource.time_zone)
        windows = await self._windows(session, resource.id)
        exceptions = await session.scalars(
            select(SchedulingAvailabilityExceptionRecord).where(
                SchedulingAvailabilityExceptionRecord.resource_id == resource.id
            )
        )
        blocks = [Block(item.starts_at, item.ends_at, item.rrule) for item in exceptions]
        # A day's whole occupancy counts toward its visit limit.
        reach = timedelta(days=2)
        events = list(
            await session.scalars(
                select(SchedulingEventRecord).where(
                    SchedulingEventRecord.resource_id == resource.id,
                    SchedulingEventRecord.blocked_start < end + reach,
                    SchedulingEventRecord.blocked_end > start - reach,
                    *self._occupying(now),
                )
            )
        )
        occupied = [
            Occupied(event.starts_at, Interval(event.blocked_start, event.blocked_end))
            for event in events
        ]
        # Outside busy times block like time off. A read link that hasn't
        # synced within STALE_AFTER stops all offers rather than risk a time
        # that is really busy.
        links = list(
            await session.scalars(
                select(SchedulingExternalLinkRecord).where(
                    SchedulingExternalLinkRecord.resource_id == resource.id,
                    SchedulingExternalLinkRecord.read_busy,
                    SchedulingExternalLinkRecord.status == "active",
                )
            )
        )
        if any(
            link.last_sync_at is None or now - link.last_sync_at > STALE_AFTER for link in links
        ):
            return (), {"stale": True, "time_zone": resource.time_zone}
        if links:
            busy = await session.scalars(
                select(SchedulingExternalBusyRecord).where(
                    SchedulingExternalBusyRecord.link_id.in_([link.id for link in links]),
                    SchedulingExternalBusyRecord.starts_at < end + reach,
                    SchedulingExternalBusyRecord.ends_at > start - reach,
                )
            )
            blocks.extend(Block(item.starts_at, item.ends_at) for item in busy)
        policy = BookingPolicy(
            min_notice=min_notice
            if min_notice is not None
            else timedelta(minutes=resource.min_notice_minutes),
            horizon=horizon if horizon is not None else timedelta(days=resource.horizon_days),
            daily_limit=resource.daily_limit,
            step=timedelta(minutes=resource.step_minutes),
        )
        slots = open_slots(
            zone=zone,
            windows=windows,
            blocks=blocks,
            occupied=occupied,
            policy=policy,
            duration=duration,
            buffer_before=buffer_before,
            buffer_after=buffer_after,
            start=start,
            end=end,
            now=now,
        )
        latest = max((event.updated_at for event in events), default=None)
        snapshot: dict[str, JsonValue] = {
            "occupied": len(events),
            "latest_change": latest.isoformat() if latest else None,
            "time_zone": resource.time_zone,
        }
        return slots, snapshot

    def _audit(
        self,
        session: AsyncSession,
        business_id: UUID,
        actor: Actor,
        entity: str,
        entity_id: UUID,
        action: str,
        before: dict[str, JsonValue] | None,
        after: dict[str, JsonValue] | None,
    ) -> None:
        session.add(
            SchedulingAuditRecord(
                id=uuid4(),
                business_id=business_id,
                actor=actor.value,
                entity=entity,
                entity_id=entity_id,
                action=action,
                before=before,
                after=after,
                at=self._clock(),
            )
        )

    async def _publish(
        self, business_id: UUID, event_id: UUID, status: EventStatus, actor: Actor, at: datetime
    ) -> None:
        change = Change(business_id, event_id, status, actor, at)
        for listener in self._listeners:
            try:
                await listener.changed(change)
            except Exception:
                logger.exception("scheduling listener failed", extra={"event_id": str(event_id)})
