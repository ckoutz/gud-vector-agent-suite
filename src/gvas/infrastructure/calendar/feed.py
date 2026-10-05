"""Reads an owner's calendar through its private subscription link.

Google, Apple (iCloud) and Outlook all publish a calendar as an iCal feed, so
one adapter covers every major provider without an OAuth app per provider.
The link is a credential: it is never logged, and failures surface as one
fixed ``OwnerCalendarError`` message.

The server only fetches public https hosts. Every hop — the stored link and
each redirect — is re-validated and its DNS answers must all be globally
routable, so a link cannot be used to reach the server's own network.
"""

import asyncio
import ipaddress
import logging
import socket
from datetime import UTC, date, datetime, time, timedelta
from urllib.parse import urljoin

import httpx
import recurring_ical_events
from icalendar import Calendar

from gvas.domain.owner import (
    CalendarEvent,
    CalendarEventSource,
    OwnerCalendarError,
    normalize_calendar_feed_url,
)

logger = logging.getLogger(__name__)

FEED_MAX_BYTES = 5_000_000
FEED_MAX_REDIRECTS = 3
FEED_TIMEOUT_SECONDS = 15.0
FEED_MAX_EVENTS = 500
UNREADABLE = "Your calendar link couldn't be read. Check it in Settings."


class IcsCalendarFeed:
    """Implements ``CalendarFeedPort``."""

    def __init__(self, client: httpx.AsyncClient, *, resolve_hosts: bool = True) -> None:
        self._client = client
        self._resolve_hosts = resolve_hosts

    async def events(
        self, feed_url: str, start: datetime, end: datetime
    ) -> tuple[CalendarEvent, ...]:
        body = await self._fetch(feed_url)
        try:
            return parse_calendar_feed(body, start, end)
        except Exception as error:  # noqa: BLE001 - any parser failure is one owner-facing error
            logger.warning("calendar feed unparseable: %s", type(error).__name__)
            raise OwnerCalendarError(UNREADABLE) from error

    async def _fetch(self, feed_url: str) -> bytes:
        url = feed_url
        for _ in range(FEED_MAX_REDIRECTS + 1):
            try:
                url = normalize_calendar_feed_url(url)
            except ValueError as error:
                raise OwnerCalendarError(UNREADABLE) from error
            await self._require_public(url)
            try:
                async with self._client.stream(
                    "GET",
                    url,
                    headers={"Accept": "text/calendar"},
                    timeout=FEED_TIMEOUT_SECONDS,
                    follow_redirects=False,
                ) as response:
                    if response.is_redirect:
                        location = response.headers.get("location")
                        if not location:
                            raise OwnerCalendarError(UNREADABLE)
                        url = urljoin(url, location)
                        continue
                    if response.status_code >= 400:
                        logger.warning("calendar feed returned http %s", response.status_code)
                        raise OwnerCalendarError(UNREADABLE)
                    chunks: list[bytes] = []
                    size = 0
                    async for chunk in response.aiter_bytes():
                        size += len(chunk)
                        if size > FEED_MAX_BYTES:
                            logger.warning("calendar feed exceeded %s bytes", FEED_MAX_BYTES)
                            raise OwnerCalendarError(UNREADABLE)
                        chunks.append(chunk)
                    return b"".join(chunks)
            except httpx.HTTPError as error:
                logger.warning("calendar feed request failed: %s", type(error).__name__)
                raise OwnerCalendarError(UNREADABLE) from error
        logger.warning("calendar feed redirected too many times")
        raise OwnerCalendarError(UNREADABLE)

    async def _require_public(self, url: str) -> None:
        if not self._resolve_hosts:
            return
        host = httpx.URL(url).host
        try:
            answers = await asyncio.get_running_loop().getaddrinfo(
                host, 443, type=socket.SOCK_STREAM
            )
        except OSError as error:
            logger.warning("calendar feed host did not resolve")
            raise OwnerCalendarError(UNREADABLE) from error
        for answer in answers:
            address = ipaddress.ip_address(str(answer[4][0]).split("%", 1)[0])
            if not address.is_global:
                logger.warning("calendar feed host resolved to a non-public address")
                raise OwnerCalendarError(UNREADABLE)


def parse_calendar_feed(body: bytes, start: datetime, end: datetime) -> tuple[CalendarEvent, ...]:
    """Expand recurrences and return the events overlapping ``[start, end)``."""

    calendar = Calendar.from_ical(body)
    events: list[CalendarEvent] = []
    for component in recurring_ical_events.of(calendar).between(start, end):
        if str(component.get("STATUS", "")).upper() == "CANCELLED":
            continue
        raw_start = component.decoded("DTSTART")
        all_day = isinstance(raw_start, date) and not isinstance(raw_start, datetime)
        event_start = _as_datetime(raw_start)
        event_end: datetime | None = None
        if "DTEND" in component:
            event_end = _as_datetime(component.decoded("DTEND"))
        elif "DURATION" in component:
            event_end = event_start + component.decoded("DURATION")
        elif all_day:
            event_end = event_start + timedelta(days=1)
        summary = str(component.get("SUMMARY", "")).strip() or "Busy"
        location = str(component.get("LOCATION", "")).strip() or None
        events.append(
            CalendarEvent(
                source=CalendarEventSource.CALENDAR,
                title=summary[:300],
                start=event_start,
                end=event_end,
                all_day=all_day,
                location=location[:500] if location else None,
            )
        )
        if len(events) >= FEED_MAX_EVENTS:
            break
    return tuple(sorted(events, key=lambda event: event.start))


def _as_datetime(value: date | datetime) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else value.replace(tzinfo=UTC)
    # All-day entries carry a date only; they are anchored at UTC midnight and
    # flagged ``all_day`` so the dashboard shows the date, not a time.
    return datetime.combine(value, time.min, tzinfo=UTC)


__all__ = ["IcsCalendarFeed", "parse_calendar_feed"]
