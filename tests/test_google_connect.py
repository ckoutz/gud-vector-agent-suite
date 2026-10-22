import base64
import json
from datetime import timedelta
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from gvas.config import GoogleCalendarSettings
from gvas.domain.identifiers import BusinessId
from gvas.domain.owner import CalendarConnectError
from gvas.infrastructure.calendar_credentials import CalendarTokenCipher, StoredRefreshTokens
from gvas.infrastructure.google_calendar import TOKEN_URL
from gvas.infrastructure.google_connect import CALENDARS_URL, SCOPES, GoogleCalendarConnect
from gvas.scheduling.core.engine import SchedulingEngine, owner_principal
from gvas.scheduling.core.records import SchedulingExternalLinkRecord
from gvas.scheduling.field_service.booking import FieldServiceScheduling
from scheduling_fixtures import Clock, seed_business

pytestmark = pytest.mark.postgres
KEY = base64.b64encode(bytes(range(32))).decode()
REDIRECT = "https://gudvector.com/portal/owner/calendar/google"


def google_settings(redirect: str = REDIRECT) -> GoogleCalendarSettings:
    return GoogleCalendarSettings(
        _env_file=None,
        oauth_client_id="client-id",
        oauth_client_secret="client-secret",  # noqa: S106 (a test value)
        redirect_uri=redirect,
    ).model_copy(update={"token_key": KEY})


class Google:
    def __init__(self) -> None:
        self.calendars_made = 0

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if str(request.url) == TOKEN_URL:
            form = parse_qs(request.content.decode())
            assert form["grant_type"] == ["authorization_code"]
            assert form["redirect_uri"] == [REDIRECT]
            if form["code"] != ["good-code"]:
                return httpx.Response(400, json={"error": "invalid_grant"})
            return httpx.Response(200, json={"refresh_token": "1//refresh", "access_token": "at"})
        assert str(request.url) == CALENDARS_URL
        assert request.headers["Authorization"] == "Bearer at"
        assert json.loads(request.content)["summary"] == "Güd Office visits"
        self.calendars_made += 1
        return httpx.Response(200, json={"id": "visits@group.calendar.google.com"})


async def setup(
    factory: async_sessionmaker[AsyncSession],
    clock: Clock,
    *,
    slug: str = "larkspur",
    with_service: bool = True,
    settings: GoogleCalendarSettings | None = None,
) -> tuple[BusinessId, GoogleCalendarConnect, Google]:
    business_id = BusinessId(await seed_business(factory, slug))
    engine = SchedulingEngine(factory, clock=clock)
    scheduling = FieldServiceScheduling(engine, factory)
    if with_service:
        calendar = await engine.create_calendar(
            business_id, name="Visits", time_zone="America/Los_Angeles",
            owner=owner_principal(business_id),
        )  # fmt: skip
        await scheduling.create_service(
            business_id, calendar.resource_id, name="Estimate", duration=timedelta(hours=1),
            buffer_after=timedelta(0),
        )  # fmt: skip
    google = Google()
    connect = GoogleCalendarConnect(
        settings or google_settings(),
        httpx.AsyncClient(transport=httpx.MockTransport(google)),
        factory,
        scheduling,
        clock=clock,
    )
    return business_id, connect, google


def state_of(url: str) -> str:
    query = parse_qs(urlsplit(url).query)
    assert query["client_id"] == ["client-id"] and query["redirect_uri"] == [REDIRECT]
    assert query["scope"] == [" ".join(SCOPES)] and query["access_type"] == ["offline"]
    return query["state"][0]


async def test_connecting_stores_a_sealed_token_and_one_visits_calendar(
    postgres_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    clock = Clock()
    business_id, connect, google = await setup(postgres_session_factory, clock)
    assert (await connect.status(business_id)).state == "none"

    state = state_of(await connect.start_url(business_id))
    await connect.finish(business_id, code="good-code", state=state)

    async with postgres_session_factory() as session:
        [link] = list(await session.scalars(select(SchedulingExternalLinkRecord)))
    assert link.status == "active" and link.read_busy and link.write_events
    assert link.write_calendar_id == "visits@group.calendar.google.com"
    assert link.credentials_ref == str(link.id) and link.last_sync_at is None
    assert link.refresh_token_sealed and "refresh" not in link.refresh_token_sealed
    tokens = StoredRefreshTokens(postgres_session_factory, CalendarTokenCipher(KEY))
    assert await tokens(str(link.id)) == "1//refresh"
    assert (await connect.status(business_id)).state == "active"

    async with postgres_session_factory() as session, session.begin():
        stored = await session.get(SchedulingExternalLinkRecord, link.id)
        assert stored is not None
        stored.status = "reconnect"
    await connect.finish(business_id, code="good-code", state=state)
    async with postgres_session_factory() as session:
        links = list(await session.scalars(select(SchedulingExternalLinkRecord)))
    assert len(links) == 1 and links[0].status == "active"
    assert google.calendars_made == 1, "reconnecting reuses the visits calendar"


async def test_a_sign_in_finishes_only_for_its_business_in_time_with_a_good_code(
    postgres_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    clock = Clock()
    business_id, connect, _ = await setup(postgres_session_factory, clock)
    other_id = BusinessId(await seed_business(postgres_session_factory, "other"))
    state = state_of(await connect.start_url(business_id))

    swapped = state.replace(str(business_id), str(other_id))
    for bad_state in (state[:-2] + "xx", "nonsense", swapped):
        with pytest.raises(CalendarConnectError):
            await connect.finish(business_id, code="good-code", state=bad_state)
    with pytest.raises(CalendarConnectError):
        await connect.finish(other_id, code="good-code", state=state)
    with pytest.raises(CalendarConnectError, match="accept"):
        await connect.finish(business_id, code="stolen-code", state=state)
    clock.advance(timedelta(minutes=11))
    with pytest.raises(CalendarConnectError, match="too long"):
        await connect.finish(business_id, code="good-code", state=state)
    async with postgres_session_factory() as session:
        assert list(await session.scalars(select(SchedulingExternalLinkRecord))) == []


async def test_connect_needs_the_google_setup_and_a_booking_calendar(
    postgres_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    clock = Clock()
    business_id, connect, _ = await setup(postgres_session_factory, clock, with_service=False)
    with pytest.raises(CalendarConnectError, match="booking calendar first"):
        await connect.start_url(business_id)

    unset_id, unset, _ = await setup(
        postgres_session_factory, clock, slug="unset", settings=google_settings(redirect="")
    )
    assert not (await unset.status(unset_id)).available
    with pytest.raises(CalendarConnectError, match="isn't set up"):
        await unset.start_url(unset_id)
