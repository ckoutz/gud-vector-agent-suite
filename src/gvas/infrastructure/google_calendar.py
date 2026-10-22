"""Google Calendar behind the scheduling engine's ``ExternalCalendarPort``:
busy times through the freeBusy API (times only, never titles). Access
tokens come from ``access_token`` (refresh-token handling lives with the
credential store); a 401/403 or ``invalid_grant`` means the owner must
reconnect."""

from collections.abc import Awaitable, Callable, Sequence
from datetime import datetime

import httpx
from pydantic import BaseModel, ConfigDict, ValidationError

from gvas.scheduling.core.sync import BusyBlock, ExternalAccessRevokedError

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
