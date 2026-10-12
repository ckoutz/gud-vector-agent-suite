from datetime import UTC, datetime, timedelta

from gvas.application.owner_assistant import _EVENT_STATUS
from gvas.domain.intake import AvailableSlot, slot_confirmed_reply, slot_held_reply
from gvas.domain.owner import CalendarEventSource

START = datetime(2026, 10, 13, 15, tzinfo=UTC)
SLOT = AvailableSlot(start=START, end=START + timedelta(hours=1))


def test_a_demo_confirms_in_the_chat_not_by_email() -> None:
    reply = slot_confirmed_reply(SLOT, UTC, texts=True, in_chat=True)
    assert "We'll confirm here in the chat once the owner approves." in reply
    assert "email" not in reply and "text" not in reply.split("confirm")[1]
    assert slot_held_reply(in_chat=True).endswith("we'll confirm here in the chat.")


def test_a_real_business_still_confirms_by_email() -> None:
    assert "We'll confirm by email once" in slot_confirmed_reply(SLOT, UTC)
    assert slot_held_reply(texts=True).endswith("we'll confirm by email and text.")


def test_every_calendar_event_has_a_plain_status_and_a_request_is_not_booked() -> None:
    assert set(_EVENT_STATUS) == set(CalendarEventSource)
    assert "waiting for the owner" in _EVENT_STATUS[CalendarEventSource.REQUEST]
    assert "booked" not in _EVENT_STATUS[CalendarEventSource.REQUEST]
