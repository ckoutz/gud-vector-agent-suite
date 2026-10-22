import base64
from datetime import time, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from gvas.config import GoogleCalendarSettings
from gvas.infrastructure.calendar_credentials import CalendarTokenCipher, StoredRefreshTokens
from gvas.scheduling.core.engine import SchedulingEngine, owner_principal
from gvas.scheduling.core.model import WeeklyWindow
from gvas.scheduling.core.records import SchedulingExternalLinkRecord
from gvas.scheduling.core.sync import BusyBlock, ExternalAccessRevokedError
from gvas.scheduling.core.sync_job import run_sync_round
from scheduling_fixtures import TUESDAY_10, Clock, seed_business

HOUR = timedelta(hours=1)
KEY = base64.b64encode(bytes(range(32))).decode()


async def setup(
    factory: async_sessionmaker[AsyncSession], clock: Clock, links: int = 1
) -> tuple[SchedulingEngine, UUID, UUID, list[UUID]]:
    business_id = await seed_business(factory, "larkspur")
    engine = SchedulingEngine(factory, clock=clock)
    owner = owner_principal(business_id)
    calendar = await engine.create_calendar(
        business_id, name="Visits", time_zone="America/Los_Angeles", owner=owner
    )
    await engine.set_weekly_hours(
        business_id, calendar.resource_id, [WeeklyWindow(1, time(8), time(17))], by=owner
    )
    ids = [uuid4() for _ in range(links)]
    async with factory() as session, session.begin():
        for link_id in ids:
            session.add(
                SchedulingExternalLinkRecord(
                    id=link_id, business_id=business_id, resource_id=calendar.resource_id,
                    provider="google", read_busy=True, write_events=True,
                    credentials_ref=str(link_id), write_calendar_id="visits",
                    last_sync_at=clock(), status="active", created_at=clock(),
                )
            )  # fmt: skip
    return engine, business_id, calendar.resource_id, ids


class Google:
    def __init__(self) -> None:
        self.reads: list[str] = []
        self.writes: list[tuple[str, str]] = []
        self.broken: set[str] = set()
        self.revoked: set[str] = set()

    async def busy(self, credentials_ref: str, start: object, end: object) -> list[BusyBlock]:
        if credentials_ref in self.broken:
            raise RuntimeError("google is down")
        self.reads.append(credentials_ref)
        return [BusyBlock("g-1", TUESDAY_10, TUESDAY_10 + HOUR)]

    async def upsert(self, credentials_ref: str, calendar_id: str, **visit: object) -> None:
        if credentials_ref in self.revoked:
            raise ExternalAccessRevokedError()
        self.writes.append((credentials_ref, str(visit["event_id"])))

    async def delete(self, credentials_ref: str, calendar_id: str, *, event_id: str) -> None:
        self.writes.append((credentials_ref, "delete " + event_id))


@pytest.mark.postgres
async def test_a_round_polls_only_stale_links_and_pushes_visits_once(
    postgres_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    clock = Clock()
    engine, business_id, resource_id, [link] = await setup(postgres_session_factory, clock)
    visit = await engine.add_event(
        business_id, resource_id, start=TUESDAY_10, end=TUESDAY_10 + HOUR,
        title="Estimate", by=owner_principal(business_id),
    )  # fmt: skip
    google = Google()

    first = await run_sync_round(postgres_session_factory, google, google, clock=clock)
    assert google.reads == [] and first.synced == 0, "synced just now, so no poll yet"
    assert first.pushed == 1 and google.writes == [(str(link), str(visit))]

    clock.advance(timedelta(minutes=5))
    second = await run_sync_round(postgres_session_factory, google, google, clock=clock)
    assert google.reads == [str(link)] and second.synced == 1
    assert second.pushed == 0, "an unchanged visit is not sent again"


@pytest.mark.postgres
async def test_one_broken_link_does_not_stop_the_others_and_revoked_means_reconnect(
    postgres_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    clock = Clock()
    engine, business_id, resource_id, [down, revoked, fine] = await setup(
        postgres_session_factory, clock, links=3
    )
    await engine.add_event(
        business_id, resource_id, start=TUESDAY_10, end=TUESDAY_10 + HOUR,
        title="Estimate", by=owner_principal(business_id),
    )  # fmt: skip
    google = Google()
    google.broken.add(str(down))
    google.revoked.add(str(revoked))
    clock.advance(timedelta(minutes=6))

    result = await run_sync_round(postgres_session_factory, google, google, clock=clock)

    assert result.failed == 1 and result.reconnect == 1
    assert str(fine) in google.reads and any(ref == str(fine) for ref, _ in google.writes)
    async with postgres_session_factory() as session:
        link = await session.get(SchedulingExternalLinkRecord, revoked)
    assert link is not None and link.status == "reconnect"


@pytest.mark.postgres
async def test_refresh_tokens_are_stored_sealed_and_a_bad_one_means_reconnect(
    postgres_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    clock = Clock()
    _, _, _, [link_id] = await setup(postgres_session_factory, clock)
    tokens = StoredRefreshTokens(postgres_session_factory, CalendarTokenCipher(KEY))

    await tokens.store(link_id, "1//refresh-token")
    async with postgres_session_factory() as session:
        link = await session.get(SchedulingExternalLinkRecord, link_id)
    assert link is not None and link.refresh_token_sealed
    assert "refresh-token" not in link.refresh_token_sealed
    assert await tokens(str(link_id)) == "1//refresh-token"

    other = StoredRefreshTokens(
        postgres_session_factory, CalendarTokenCipher(base64.b64encode(bytes(32)).decode())
    )
    for resolver, ref in ((other, str(link_id)), (tokens, str(uuid4())), (tokens, "x")):
        with pytest.raises(ExternalAccessRevokedError):
            await resolver(ref)


def test_google_sync_stays_off_until_the_client_and_key_are_all_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in ("GVAS_GOOGLE_OAUTH_CLIENT_ID", "GVAS_GOOGLE_OAUTH_CLIENT_SECRET"):
        monkeypatch.setenv(name, "set")
    assert not GoogleCalendarSettings(_env_file=None).is_configured
    monkeypatch.setenv("GVAS_CALENDAR_TOKEN_KEY", KEY)
    assert GoogleCalendarSettings(_env_file=None).is_configured
