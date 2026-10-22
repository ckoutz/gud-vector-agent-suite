"""Keeping an outside calendar's busy times mirrored (times only, never
titles). The provider is behind ``ExternalCalendarPort``; a revoked or
expired grant marks the link ``reconnect`` so the owner is asked to
reconnect, and offers stop until then (the stale rule in the engine)."""

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol
from uuid import UUID, uuid4

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from gvas.scheduling.core.model import EventStatus
from gvas.scheduling.core.records import (
    SchedulingEventRecord,
    SchedulingExternalBusyRecord,
    SchedulingExternalLinkRecord,
    SchedulingExternalPushRecord,
)

SYNC_AHEAD = timedelta(days=60)
ACTIVE = "active"
RECONNECT = "reconnect"


class ExternalAccessRevokedError(Exception):
    """The owner withdrew access, or the grant expired."""


@dataclass(frozen=True)
class BusyBlock:
    external_id: str
    start: datetime
    end: datetime


class ExternalCalendarPort(Protocol):
    async def busy(
        self, credentials_ref: str, start: datetime, end: datetime
    ) -> Sequence[BusyBlock]: ...


class ExternalEventWriterPort(Protocol):
    async def upsert(
        self,
        credentials_ref: str,
        calendar_id: str,
        *,
        event_id: str,
        start: datetime,
        end: datetime,
        summary: str,
    ) -> None: ...

    async def delete(self, credentials_ref: str, calendar_id: str, *, event_id: str) -> None: ...


async def push_events(
    session_factory: async_sessionmaker[AsyncSession],
    link_id: UUID,
    writer: ExternalEventWriterPort,
) -> int:
    """Send the outside calendar every confirmed event it hasn't seen at its
    current version, and remove ones since cancelled. Returns how many went.
    A provider error stops the round; what already went is recorded."""

    async with session_factory() as session:
        link = await session.get(SchedulingExternalLinkRecord, link_id)
        if (
            link is None
            or link.status != ACTIVE
            or not link.write_events
            or not link.credentials_ref
            or not link.write_calendar_id
        ):
            return 0
        credentials, calendar_id = link.credentials_ref, link.write_calendar_id
        pushed = {
            row.event_id: row
            for row in await session.scalars(
                select(SchedulingExternalPushRecord).where(
                    SchedulingExternalPushRecord.link_id == link_id
                )
            )
        }
        events = list(
            await session.scalars(
                select(SchedulingEventRecord).where(
                    SchedulingEventRecord.resource_id == link.resource_id
                )
            )
        )
    sent = 0
    for event in events:
        before = pushed.get(event.id)
        if before is not None and before.pushed_version == event.version:
            continue
        confirmed = event.status == EventStatus.CONFIRMED.value
        if confirmed:
            await writer.upsert(
                credentials, calendar_id, event_id=str(event.id),
                start=event.starts_at, end=event.ends_at, summary=event.title,
            )  # fmt: skip
        elif before is not None and not before.deleted:
            await writer.delete(credentials, calendar_id, event_id=str(event.id))
        else:
            continue
        async with session_factory() as session, session.begin():
            await session.merge(
                SchedulingExternalPushRecord(
                    link_id=link_id, event_id=event.id,
                    pushed_version=event.version, deleted=not confirmed,
                )
            )  # fmt: skip
        sent += 1
    return sent


async def sync_busy(
    session_factory: async_sessionmaker[AsyncSession],
    link_id: UUID,
    provider: ExternalCalendarPort,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> bool:
    """Replace the link's mirror with the provider's current busy times.
    False when the link needs the owner to reconnect (or is gone)."""

    now = clock()
    async with session_factory() as session:
        link = await session.get(SchedulingExternalLinkRecord, link_id)
        if link is None or link.status != ACTIVE or not link.credentials_ref:
            return False
        credentials = link.credentials_ref
    try:
        blocks = await provider.busy(credentials, now, now + SYNC_AHEAD)
    except ExternalAccessRevokedError:
        async with session_factory() as session, session.begin():
            link = await session.get(SchedulingExternalLinkRecord, link_id)
            if link is not None:
                link.status = RECONNECT
        return False
    async with session_factory() as session, session.begin():
        link = await session.get(SchedulingExternalLinkRecord, link_id, with_for_update=True)
        if link is None:
            return False
        await session.execute(
            delete(SchedulingExternalBusyRecord).where(
                SchedulingExternalBusyRecord.link_id == link_id
            )
        )
        for block in blocks:
            if block.start < block.end:
                session.add(
                    SchedulingExternalBusyRecord(
                        id=uuid4(),
                        link_id=link_id,
                        external_id=block.external_id,
                        starts_at=block.start,
                        ends_at=block.end,
                    )
                )
        link.last_sync_at = now
    return True


async def links_due(
    session_factory: async_sessionmaker[AsyncSession],
    every: timedelta,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> list[UUID]:
    """Active read links whose last good sync is older than ``every`` (the
    polling fallback when push notices don't arrive)."""

    now = clock()
    async with session_factory() as session:
        rows = await session.scalars(
            select(SchedulingExternalLinkRecord).where(
                SchedulingExternalLinkRecord.status == ACTIVE,
                SchedulingExternalLinkRecord.read_busy,
            )
        )
        return [
            row.id for row in rows if row.last_sync_at is None or now - row.last_sync_at >= every
        ]
