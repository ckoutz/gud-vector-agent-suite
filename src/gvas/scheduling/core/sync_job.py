"""One round of outside-calendar upkeep: re-read busy times for links not
synced within ``every`` (the polling fallback) and push confirmed events to
every write link. One link's failure never stops the others."""

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from gvas.scheduling.core.records import SchedulingExternalLinkRecord
from gvas.scheduling.core.sync import (
    ACTIVE,
    RECONNECT,
    ExternalAccessRevokedError,
    ExternalCalendarPort,
    ExternalEventWriterPort,
    ExternalWatchPort,
    links_due,
    push_events,
    sync_busy,
)

logger = logging.getLogger(__name__)
POLL_EVERY = timedelta(minutes=5)
RENEW_BEFORE = timedelta(days=1)


@dataclass(frozen=True, slots=True)
class SyncRound:
    synced: int = 0
    pushed: int = 0
    reconnect: int = 0
    failed: int = 0
    watched: int = 0


async def run_sync_round(
    session_factory: async_sessionmaker[AsyncSession],
    provider: ExternalCalendarPort,
    writer: ExternalEventWriterPort,
    *,
    every: timedelta = POLL_EVERY,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    watcher: ExternalWatchPort | None = None,
) -> SyncRound:
    synced = pushed = reconnect = failed = watched = 0
    for link_id in await links_due(session_factory, every, clock):
        try:
            if await sync_busy(session_factory, link_id, provider, clock):
                synced += 1
            else:
                reconnect += 1
        except Exception:
            failed += 1
            logger.exception("outside calendar read failed link=%s; retrying next round", link_id)
    async with session_factory() as session:
        writers = list(
            await session.scalars(
                select(SchedulingExternalLinkRecord.id).where(
                    SchedulingExternalLinkRecord.status == ACTIVE,
                    SchedulingExternalLinkRecord.write_events,
                )
            )
        )
    for link_id in writers:
        try:
            pushed += await push_events(session_factory, link_id, writer)
        except ExternalAccessRevokedError:
            reconnect += 1
            await _mark_reconnect(session_factory, link_id)
        except Exception:
            failed += 1
            logger.exception("outside calendar write failed link=%s; retrying next round", link_id)
    if watcher is not None:
        for link_id in await _watches_due(session_factory, clock() + RENEW_BEFORE):
            try:
                await _renew_watch(session_factory, link_id, watcher)
                watched += 1
            except ExternalAccessRevokedError:
                reconnect += 1
                await _mark_reconnect(session_factory, link_id)
            except Exception:
                failed += 1
                logger.exception("outside calendar watch failed link=%s; polling instead", link_id)
    return SyncRound(synced, pushed, reconnect, failed, watched)


async def _mark_reconnect(session_factory: async_sessionmaker[AsyncSession], link_id: UUID) -> None:
    async with session_factory() as session, session.begin():
        link = await session.get(SchedulingExternalLinkRecord, link_id)
        if link is not None:
            link.status = RECONNECT


async def _watches_due(
    session_factory: async_sessionmaker[AsyncSession], before: datetime
) -> list[UUID]:
    async with session_factory() as session:
        rows = await session.scalars(
            select(SchedulingExternalLinkRecord).where(
                SchedulingExternalLinkRecord.status == ACTIVE,
                SchedulingExternalLinkRecord.read_busy,
            )
        )
        return [
            row.id
            for row in rows
            if row.credentials_ref
            and (row.watch_expires_at is None or row.watch_expires_at < before)
        ]


async def _renew_watch(
    session_factory: async_sessionmaker[AsyncSession], link_id: UUID, watcher: ExternalWatchPort
) -> None:
    """Open the new channel first, then close the old one, so no change slips
    between them; a failed close only costs a duplicate notice."""

    async with session_factory() as session:
        link = await session.get(SchedulingExternalLinkRecord, link_id)
    if link is None or link.credentials_ref is None:
        return
    channel_id = uuid4().hex
    channel = await watcher.watch(link.credentials_ref, channel_id=channel_id)
    old = (link.watch_channel_id, link.watch_resource_id)
    async with session_factory() as session, session.begin():
        stored = await session.get(SchedulingExternalLinkRecord, link_id)
        if stored is None:
            return
        stored.watch_channel_id = channel_id
        stored.watch_resource_id = channel.resource_id
        stored.watch_expires_at = channel.expires_at
    if old[0] and old[1]:
        try:
            await watcher.stop(link.credentials_ref, channel_id=old[0], resource_id=old[1])
        except Exception:
            logger.warning("closing the old watch channel failed link=%s", link_id)
