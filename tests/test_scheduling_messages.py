from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from icalendar import Calendar

from gvas.scheduling.core.messages import (
    MessageKind,
    hash_action_token,
    new_action_token,
    reminder_times,
    text_send_time,
    visit_invite,
)

LA = ZoneInfo("America/Los_Angeles")
VISIT = datetime(2026, 10, 27, 10, tzinfo=LA)


def test_reminders_24_and_2_hours_ahead_only_while_still_ahead() -> None:
    assert reminder_times(VISIT, VISIT - timedelta(days=3)) == (
        (MessageKind.REMINDER_24H, VISIT - timedelta(hours=24)),
        (MessageKind.REMINDER_2H, VISIT - timedelta(hours=2)),
    )
    assert reminder_times(VISIT, VISIT - timedelta(hours=5)) == (
        (MessageKind.REMINDER_2H, VISIT - timedelta(hours=2)),
    )
    assert reminder_times(VISIT, VISIT - timedelta(hours=1)) == ()


def test_texts_wait_out_quiet_hours_in_the_recipients_zone() -> None:
    noon = datetime(2026, 10, 26, 12, tzinfo=LA)
    late = datetime(2026, 10, 26, 21, 30, tzinfo=LA)
    early = datetime(2026, 10, 27, 6, tzinfo=LA)
    eight_pm = datetime(2026, 10, 26, 20, tzinfo=LA)

    assert text_send_time(noon, LA) == noon
    assert text_send_time(late, LA) == datetime(2026, 10, 27, 8, tzinfo=LA)
    assert text_send_time(early, LA) == datetime(2026, 10, 27, 8, tzinfo=LA)
    assert text_send_time(eight_pm, LA) == datetime(2026, 10, 27, 8, tzinfo=LA)
    # 4:00 UTC is 21:00 the evening before in Los Angeles.
    assert text_send_time(datetime(2026, 10, 27, 4, tzinfo=UTC), LA) == datetime(
        2026, 10, 27, 8, tzinfo=LA
    )


def test_action_tokens_are_random_and_only_their_hash_is_kept() -> None:
    first, second = new_action_token(), new_action_token()

    assert first != second and len(first) >= 40
    assert hash_action_token(first) == hash_action_token(first) != hash_action_token(second)
    assert first not in hash_action_token(first)


def _event(ics: bytes) -> Calendar:
    calendar = Calendar.from_ical(ics)
    return calendar


def test_an_invite_update_keeps_its_uid_with_a_higher_sequence_and_a_cancel_removes_it() -> None:
    common = {
        "uid": "visit-1@gudvector.com",
        "start": VISIT,
        "end": VISIT + timedelta(hours=1),
        "time_zone": "America/Los_Angeles",
        "summary": "Estimate with Larkspur",
        "stamp": datetime(2026, 10, 20, tzinfo=UTC),
        "organizer_email": "info@gudvector.com",
        "attendee_email": "jane@example.com",
        "location": "1 Oak St",
    }
    first = _event(visit_invite(sequence=1, **common))  # type: ignore[arg-type]
    moved = _event(visit_invite(sequence=2, **common))  # type: ignore[arg-type]
    gone = _event(visit_invite(sequence=3, cancelled=True, **common))  # type: ignore[arg-type]

    events = [next(iter(c.walk("VEVENT"))) for c in (first, moved, gone)]
    assert {str(event["uid"]) for event in events} == {"visit-1@gudvector.com"}
    assert [int(event["sequence"]) for event in events] == [1, 2, 3]
    assert [str(c["method"]) for c in (first, moved, gone)] == ["REQUEST", "REQUEST", "CANCEL"]
    assert str(events[2]["status"]) == "CANCELLED"
    start = events[0].decoded("dtstart")
    assert start == VISIT and str(start.tzinfo) == "America/Los_Angeles"
    assert list(first.walk("VTIMEZONE"))
