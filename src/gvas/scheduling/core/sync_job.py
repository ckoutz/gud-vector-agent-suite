"""One round of outside-calendar upkeep: re-read busy times for links not
synced within ``every`` (the polling fallback) and push confirmed events to
every write link. One link's failure never stops the others."""

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from gvas.scheduling.core.records import SchedulingExternalLinkRecord
from gvas.scheduling.core.sync import (
    ACTIVE,
    RECONNECT,
    ExternalAccessRevokedError,
    ExternalCalendarPort,
    ExternalEventWriterPort,
    links_due,
    push_events,
    sync_busy,
)

logger = logging.getLogger(__name__)
POLL_EVERY = timedelta(minutes=5)


@dataclass(frozen=True, slots=True)
class SyncRound:
    synced: int = 0
    pushed: int = 0
    reconnect: int = 0
    failed: int = 0


async def run_sync_round(
    session_factory: async_sessionmaker[AsyncSession],
    provider: ExternalCalendarPort,
    writer: ExternalEventWriterPort,
    *,
    every: timedelta = POLL_EVERY,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> SyncRound:
    synced = pushed = reconnect = failed = 0
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
            async with session_factory() as session, session.begin():
                link = await session.get(SchedulingExternalLinkRecord, link_id)
                if link is not None:
                    link.status = RECONNECT
        except Exception:
            failed += 1
            logger.exception("outside calendar write failed link=%s; retrying next round", link_id)
    return SyncRound(synced, pushed, reconnect, failed)
