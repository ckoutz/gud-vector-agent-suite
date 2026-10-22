"""When and how a visit's messages go out: reminder times, quiet hours for
texts, calendar invites that update in place, and one-visit action links.
Pure: no clock, no database."""

import hashlib
import secrets
from datetime import UTC, datetime, time, timedelta
from enum import StrEnum
from zoneinfo import ZoneInfo

from icalendar import Calendar, Event, vCalAddress, vText

#: No texts before 8:00 or after 20:00 in the recipient's zone; queued instead.
TEXT_WINDOW = (time(8), time(20))
REMINDER_OFFSETS = (("reminder_24h", timedelta(hours=24)), ("reminder_2h", timedelta(hours=2)))


class MessageKind(StrEnum):
    CONFIRMATION = "confirmation"
    REMINDER_24H = "reminder_24h"
    REMINDER_2H = "reminder_2h"
    RESCHEDULED = "rescheduled"
    CANCELLED = "cancelled"


class Channel(StrEnum):
    EMAIL = "email"
    SMS = "sms"


def reminder_times(start: datetime, now: datetime) -> tuple[tuple[MessageKind, datetime], ...]:
    """The 24-hour and 2-hour reminders still ahead of ``now``."""

    return tuple(
        (MessageKind(kind), start - offset)
        for kind, offset in REMINDER_OFFSETS
        if start - offset > now
    )


def text_send_time(at: datetime, zone: ZoneInfo) -> datetime:
    """``at`` if it falls inside the text window in ``zone``, else the
    window's next opening (a late-evening text waits for 8:00)."""

    local = at.astimezone(zone)
    opens, closes = TEXT_WINDOW
    if opens <= local.time() < closes:
        return at
    day = local.date() if local.time() < opens else local.date() + timedelta(days=1)
    return datetime.combine(day, opens, tzinfo=zone).astimezone(UTC)


def new_action_token() -> str:
    return secrets.token_urlsafe(32)


def hash_action_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def visit_invite(
    *,
    uid: str,
    sequence: int,
    start: datetime,
    end: datetime,
    time_zone: str,
    summary: str,
    stamp: datetime,
    organizer_email: str,
    attendee_email: str | None = None,
    location: str | None = None,
    description: str | None = None,
    cancelled: bool = False,
) -> bytes:
    """An iCalendar invite. The same ``uid`` with a higher ``sequence``
    updates the entry already in the customer's calendar; ``cancelled``
    removes it."""

    zone = ZoneInfo(time_zone)
    calendar = Calendar()
    calendar.add("prodid", "-//Gud Vector//Scheduling//EN")
    calendar.add("version", "2.0")
    calendar.add("method", "CANCEL" if cancelled else "REQUEST")
    event = Event()
    event.add("uid", uid)
    event.add("sequence", sequence)
    event.add("dtstamp", stamp.astimezone(UTC))
    event.add("dtstart", start.astimezone(zone))
    event.add("dtend", end.astimezone(zone))
    event.add("summary", summary)
    event.add("status", "CANCELLED" if cancelled else "CONFIRMED")
    if location:
        event.add("location", location)
    if description:
        event.add("description", description)
    organizer = vCalAddress(f"mailto:{organizer_email}")
    event["organizer"] = organizer
    if attendee_email:
        attendee = vCalAddress(f"mailto:{attendee_email}")
        attendee.params["role"] = vText("REQ-PARTICIPANT")
        event.add("attendee", attendee, encode=False)
    calendar.add_component(event)
    calendar.add_missing_timezones()
    invite: bytes = calendar.to_ical()
    return invite
