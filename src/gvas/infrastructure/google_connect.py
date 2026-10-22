"""The owner's "Connect Google Calendar" flow (FS-20, FS-26). The state is
signed and expires, so a sign-in can only finish for the business that
started it. Scopes stay minimal: free/busy reads plus the one calendar we
create for visits."""

import base64
import hashlib
import hmac
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from urllib.parse import urlencode
from uuid import UUID, uuid4

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from gvas.config import GoogleCalendarSettings
from gvas.domain.identifiers import BusinessId
from gvas.domain.owner import CalendarConnectError, CalendarConnection
from gvas.infrastructure.calendar_credentials import CalendarTokenCipher, StoredRefreshTokens
from gvas.infrastructure.google_calendar import TOKEN_URL
from gvas.scheduling.core.records import SchedulingExternalLinkRecord
from gvas.scheduling.core.sync import ACTIVE
from gvas.scheduling.field_service.booking import FieldServiceScheduling

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
CALENDARS_URL = "https://www.googleapis.com/calendar/v3/calendars"
SCOPES = (
    "https://www.googleapis.com/auth/calendar.events.freebusy",
    "https://www.googleapis.com/auth/calendar.app.created",
)
STATE_TTL = timedelta(minutes=10)
VISITS_CALENDAR = "Güd Office visits"
_NOT_SET_UP = "Google Calendar isn't set up yet."
_REFUSED = "Google didn't accept the sign-in. Please try connecting again."


class GoogleCalendarConnect:
    def __init__(
        self,
        settings: GoogleCalendarSettings,
        client: httpx.AsyncClient,
        session_factory: async_sessionmaker[AsyncSession],
        scheduling: FieldServiceScheduling,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        timeout_seconds: float = 10.0,
    ) -> None:
        self._settings = settings
        self._client = client
        self._sessions = session_factory
        self._scheduling = scheduling
        self._clock = clock
        self._timeout = timeout_seconds
        self._tokens = StoredRefreshTokens(session_factory, CalendarTokenCipher(settings.token_key))
        self._state_key = hashlib.sha256(
            b"gvas-google-oauth-state:" + settings.token_key.encode()
        ).digest()

    def _available(self) -> bool:
        return self._settings.is_configured and bool(self._settings.redirect_uri)

    async def _link(
        self, session: AsyncSession, business_id: UUID
    ) -> SchedulingExternalLinkRecord | None:
        link: SchedulingExternalLinkRecord | None = await session.scalar(
            select(SchedulingExternalLinkRecord)
            .where(
                SchedulingExternalLinkRecord.business_id == business_id,
                SchedulingExternalLinkRecord.provider == "google",
            )
            .order_by(SchedulingExternalLinkRecord.created_at.desc())
            .limit(1)
        )
        return link

    async def status(self, business_id: BusinessId) -> CalendarConnection:
        async with self._sessions() as session:
            link = await self._link(session, business_id)
        if link is None:
            return CalendarConnection(available=self._available())
        return CalendarConnection(
            available=self._available(), state=link.status, last_sync_at=link.last_sync_at
        )

    def _sign(self, payload: str) -> str:
        digest = hmac.new(self._state_key, payload.encode(), hashlib.sha256).digest()
        return base64.urlsafe_b64encode(digest).decode().rstrip("=")

    def _state(self, business_id: UUID) -> str:
        payload = f"{business_id}.{int((self._clock() + STATE_TTL).timestamp())}"
        return f"{payload}.{self._sign(payload)}"

    def _check_state(self, business_id: UUID, state: str) -> None:
        parts = state.split(".")
        if len(parts) != 3:
            raise CalendarConnectError(_REFUSED)
        payload = f"{parts[0]}.{parts[1]}"
        if not hmac.compare_digest(self._sign(payload), parts[2]):
            raise CalendarConnectError(_REFUSED)
        if parts[0] != str(business_id) or not parts[1].isdigit():
            raise CalendarConnectError(_REFUSED)
        if int(parts[1]) < self._clock().timestamp():
            raise CalendarConnectError("That sign-in took too long. Please connect again.")

    async def _resource(self, business_id: UUID) -> UUID:
        service = await self._scheduling.default_service(business_id)
        if service is None:
            raise CalendarConnectError(
                "Set up your booking calendar first, then connect Google Calendar."
            )
        return service.resource_id

    async def start_url(self, business_id: BusinessId) -> str:
        if not self._available():
            raise CalendarConnectError(_NOT_SET_UP)
        await self._resource(business_id)
        query = {
            "client_id": self._settings.oauth_client_id,
            "redirect_uri": self._settings.redirect_uri,
            "response_type": "code",
            "scope": " ".join(SCOPES),
            "access_type": "offline",
            "prompt": "consent",
            "include_granted_scopes": "true",
            "state": self._state(business_id),
        }
        return f"{AUTH_URL}?{urlencode(query)}"

    async def _post(self, url: str, **kwargs: object) -> dict[str, object]:
        try:
            response = await self._client.post(url, timeout=self._timeout, **kwargs)  # type: ignore[arg-type]
        except httpx.HTTPError as error:
            raise CalendarConnectError("Google couldn't be reached. Please try again.") from error
        try:
            body = response.json()
        except ValueError:
            body = {}
        if response.status_code >= 400 or not isinstance(body, dict):
            raise CalendarConnectError(_REFUSED)
        return body

    async def finish(self, business_id: BusinessId, *, code: str, state: str) -> None:
        if not self._available():
            raise CalendarConnectError(_NOT_SET_UP)
        self._check_state(business_id, state)
        resource_id = await self._resource(business_id)
        grant = await self._post(
            TOKEN_URL,
            data={
                "grant_type": "authorization_code",
                "code": code,
                "client_id": self._settings.oauth_client_id,
                "client_secret": self._settings.oauth_client_secret,
                "redirect_uri": self._settings.redirect_uri,
            },
        )
        refresh, access = grant.get("refresh_token"), grant.get("access_token")
        if not isinstance(refresh, str) or not isinstance(access, str) or not refresh:
            raise CalendarConnectError(_REFUSED)
        async with self._sessions() as session:
            existing = await self._link(session, business_id)
        calendar_id = existing.write_calendar_id if existing is not None else None
        if not calendar_id:
            created = await self._post(
                CALENDARS_URL,
                json={"summary": VISITS_CALENDAR},
                headers={"Authorization": f"Bearer {access}"},
            )
            if not isinstance(created.get("id"), str):
                raise CalendarConnectError(_REFUSED)
            calendar_id = str(created["id"])
        async with self._sessions() as session, session.begin():
            link = await self._link(session, business_id)
            if link is None:
                link = SchedulingExternalLinkRecord(
                    id=uuid4(), business_id=business_id, resource_id=resource_id,
                    provider="google", created_at=self._clock(),
                )  # fmt: skip
                session.add(link)
            link.resource_id = resource_id
            link.read_busy = True
            link.write_events = True
            link.write_calendar_id = calendar_id
            link.status = ACTIVE
            # Offers pause until the first read lands (within a minute).
            link.last_sync_at = None
            link_id = link.id
        await self._tokens.store(link_id, refresh)
