"""Google Calendar behind the scheduling engine's ``ExternalCalendarPort``:
busy times through the freeBusy API (times only, never titles). Access
tokens come from ``access_token`` (refresh-token handling lives with the
credential store); a 401/403 or ``invalid_grant`` means the owner must
reconnect."""

import hashlib
import hmac
from collections.abc import Awaitable, Callable, Sequence
from datetime import UTC, datetime, timedelta

import httpx
from pydantic import BaseModel, ConfigDict, ValidationError

from gvas.scheduling.core.sync import BusyBlock, ExternalAccessRevokedError, WatchChannel

FREEBUSY_URL = "https://www.googleapis.com/calendar/v3/freeBusy"


class GoogleCalendarError(RuntimeError):
    """Google was unreachable or answered unreadably; retried next round."""


class _Busy(BaseModel):
    model_config = ConfigDict(extra="ignore")
    start: datetime
    end: datetime


class _Calendar(BaseModel):
    model_config = ConfigDict(extra="ignore")
    busy: list[_Busy] = []


class _FreeBusy(BaseModel):
    model_config = ConfigDict(extra="ignore")
    calendars: dict[str, _Calendar] = {}


class GoogleBusyCalendar:
    def __init__(
        self,
        client: httpx.AsyncClient,
        access_token: Callable[[str], Awaitable[str]],
        *,
        calendar_id: str = "primary",
        timeout_seconds: float = 10.0,
    ) -> None:
        self._client = client
        self._access_token = access_token
        self._calendar_id = calendar_id
        self._timeout = timeout_seconds

    async def busy(
        self, credentials_ref: str, start: datetime, end: datetime
    ) -> Sequence[BusyBlock]:
        token = await self._access_token(credentials_ref)
        try:
            response = await self._client.post(
                FREEBUSY_URL,
                json={
                    "timeMin": start.isoformat(),
                    "timeMax": end.isoformat(),
                    "items": [{"id": self._calendar_id}],
                },
                headers={"Authorization": f"Bearer {token}"},
                timeout=self._timeout,
            )
        except httpx.HTTPError as error:
            raise GoogleCalendarError("google calendar was unreachable") from error
        if response.status_code in (401, 403):
            raise ExternalAccessRevokedError()
        if response.status_code >= 400:
            raise GoogleCalendarError(f"google calendar returned http {response.status_code}")
        try:
            parsed = _FreeBusy.model_validate(response.json())
        except (ValueError, ValidationError) as error:
            raise GoogleCalendarError("google calendar returned an unreadable answer") from error
        calendar = parsed.calendars.get(self._calendar_id, _Calendar())
        return [
            BusyBlock(f"{item.start.isoformat()}/{item.end.isoformat()}", item.start, item.end)
            for item in calendar.busy
        ]


EVENTS_URL = "https://www.googleapis.com/calendar/v3/calendars/{calendar}/events"


class GoogleVisitWriter:
    """Writes confirmed visits to the business's dedicated Google calendar
    (FS-22). The event id is derived from our event id, so a retry updates
    rather than duplicates, and cancelling deletes that one entry."""

    def __init__(
        self,
        client: httpx.AsyncClient,
        access_token: Callable[[str], Awaitable[str]],
        *,
        timeout_seconds: float = 10.0,
    ) -> None:
        self._client = client
        self._access_token = access_token
        self._timeout = timeout_seconds

    @staticmethod
    def google_event_id(event_id: str) -> str:
        # Google ids allow lowercase a-v and digits; a UUID's hex fits.
        return "gv" + event_id.replace("-", "").lower()

    async def _call(
        self, method: str, url: str, credentials_ref: str, body: dict[str, object] | None = None
    ) -> httpx.Response:
        token = await self._access_token(credentials_ref)
        try:
            response = await self._client.request(
                method,
                url,
                json=body,
                headers={"Authorization": f"Bearer {token}"},
                timeout=self._timeout,
            )
        except httpx.HTTPError as error:
            raise GoogleCalendarError("google calendar was unreachable") from error
        if response.status_code in (401, 403):
            raise ExternalAccessRevokedError()
        return response

    async def upsert(
        self,
        credentials_ref: str,
        calendar_id: str,
        *,
        event_id: str,
        start: datetime,
        end: datetime,
        summary: str,
    ) -> None:
        google_id = self.google_event_id(event_id)
        base = EVENTS_URL.format(calendar=calendar_id)
        body: dict[str, object] = {
            "id": google_id,
            "summary": summary,
            "start": {"dateTime": start.isoformat()},
            "end": {"dateTime": end.isoformat()},
        }
        response = await self._call("PUT", f"{base}/{google_id}", credentials_ref, body)
        if response.status_code == 404:
            response = await self._call("POST", base, credentials_ref, body)
        if response.status_code >= 400:
            raise GoogleCalendarError(f"google calendar returned http {response.status_code}")

    async def delete(self, credentials_ref: str, calendar_id: str, *, event_id: str) -> None:
        url = f"{EVENTS_URL.format(calendar=calendar_id)}/{self.google_event_id(event_id)}"
        response = await self._call("DELETE", url, credentials_ref)
        if response.status_code >= 400 and response.status_code not in (404, 410):
            raise GoogleCalendarError(f"google calendar returned http {response.status_code}")


TOKEN_URL = "https://oauth2.googleapis.com/token"  # noqa: S105 (an address, not a secret)


class GoogleAccessTokens:
    """Turns a stored refresh token into a short-lived access token.
    ``invalid_grant`` (revoked, expired, password changed) means reconnect."""

    def __init__(
        self,
        client: httpx.AsyncClient,
        *,
        client_id: str,
        client_secret: str,
        refresh_token: Callable[[str], Awaitable[str]],
        timeout_seconds: float = 10.0,
    ) -> None:
        self._client = client
        self._client_id = client_id
        self._client_secret = client_secret
        self._refresh_token = refresh_token
        self._timeout = timeout_seconds

    async def __call__(self, credentials_ref: str) -> str:
        refresh = await self._refresh_token(credentials_ref)
        try:
            response = await self._client.post(
                TOKEN_URL,
                data={
                    "grant_type": "refresh_token",
                    "refresh_token": refresh,
                    "client_id": self._client_id,
                    "client_secret": self._client_secret,
                },
                timeout=self._timeout,
            )
        except httpx.HTTPError as error:
            raise GoogleCalendarError("google sign-in was unreachable") from error
        try:
            body = response.json()
        except ValueError:
            body = {}
        if response.status_code in (400, 401) and body.get("error") == "invalid_grant":
            raise ExternalAccessRevokedError()
        token = body.get("access_token") if response.status_code < 400 else None
        if not isinstance(token, str) or not token:
            raise GoogleCalendarError(f"google sign-in returned http {response.status_code}")
        return token


WATCH_URL = "https://www.googleapis.com/calendar/v3/calendars/{calendar}/events/watch"
STOP_URL = "https://www.googleapis.com/calendar/v3/channels/stop"
WATCH_TTL = timedelta(days=7)


def channel_token(secret: str, channel_id: str) -> str:
    """The token Google echoes on every notice; proves the notice is for a
    channel we opened, without storing anything."""

    return hmac.new(secret.encode(), channel_id.encode(), hashlib.sha256).hexdigest()


def channel_token_ok(secret: str, channel_id: str, token: str) -> bool:
    return hmac.compare_digest(channel_token(secret, channel_id), token)


class GoogleChangeWatch:
    """Opens and closes Google change-notice channels on the owner's calendar."""

    def __init__(
        self,
        client: httpx.AsyncClient,
        access_token: Callable[[str], Awaitable[str]],
        *,
        address: str,
        secret: str,
        calendar_id: str = "primary",
        ttl: timedelta = WATCH_TTL,
        timeout_seconds: float = 10.0,
    ) -> None:
        self._client = client
        self._access_token = access_token
        self._address = address
        self._secret = secret
        self._calendar_id = calendar_id
        self._ttl = ttl
        self._timeout = timeout_seconds

    async def _post(
        self, url: str, credentials_ref: str, body: dict[str, object]
    ) -> httpx.Response:
        token = await self._access_token(credentials_ref)
        try:
            response = await self._client.post(
                url, json=body, headers={"Authorization": f"Bearer {token}"}, timeout=self._timeout
            )
        except httpx.HTTPError as error:
            raise GoogleCalendarError("google calendar was unreachable") from error
        if response.status_code in (401, 403):
            raise ExternalAccessRevokedError()
        return response

    async def watch(self, credentials_ref: str, *, channel_id: str) -> WatchChannel:
        response = await self._post(
            WATCH_URL.format(calendar=self._calendar_id),
            credentials_ref,
            {
                "id": channel_id,
                "type": "web_hook",
                "address": self._address,
                "token": channel_token(self._secret, channel_id),
                "params": {"ttl": str(int(self._ttl.total_seconds()))},
            },
        )
        if response.status_code >= 400:
            raise GoogleCalendarError(f"google calendar returned http {response.status_code}")
        try:
            body = response.json()
            expires = datetime.fromtimestamp(int(body["expiration"]) / 1000, UTC)
            return WatchChannel(str(body["resourceId"]), expires)
        except (ValueError, KeyError, TypeError) as error:
            raise GoogleCalendarError("google calendar returned an unreadable answer") from error

    async def stop(self, credentials_ref: str, *, channel_id: str, resource_id: str) -> None:
        response = await self._post(
            STOP_URL, credentials_ref, {"id": channel_id, "resourceId": resource_id}
        )
        if response.status_code >= 400 and response.status_code != 404:
            raise GoogleCalendarError(f"google calendar returned http {response.status_code}")
