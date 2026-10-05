"""Booked-mode intake: after a booking request the chat stays open.

Covers the reschedule/cancel flows — the original booking stays in force
until the owner approves the new time (``superseded_booking`` custody) —
and the one-click approve/decline links in the owner notification e-mail.
"""

import re
from datetime import UTC, datetime

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from composition_fakes import OwnerReplyFake
from gvas.application.intake import RESCHEDULE_OFFER_REPLY
from gvas.composition import Application
from gvas.config import IntakeSettings
from gvas.domain.identifiers import BusinessId
from gvas.domain.intake import (
    INTAKE_BOOKING_CANCEL_COMMAND_TYPE,
    INTAKE_CUSTOMER_EMAIL_COMMAND_TYPE,
    OWNER_NOTICE_EMAIL_COMMAND_TYPE,
    BookingEventKind,
    BookingKind,
    BookingResult,
    IntakeBookingEvent,
    IntakeTurn,
)
from gvas.domain.ports import OwnerEmailPort
from gvas.infrastructure.intake_models import IntakeConversation as IntakeRow
from gvas.interfaces.http.app import create_app
from gvas.interfaces.http.public import create_public_router
from test_composition import inbound
from test_intake_booking import (
    EMAIL,
    PUBLIC_KEY,
    SITE_URL,
    AvailabilityFake,
    IntakeAgentFake,
    collected_turn,
    commands_of,
    conversation_row,
    intake_app,
    intake_business,
    seed_owner_thread,
    slot,
)
from test_pilot_runtime import immediate_worker, texts_of

LINK_SIGNING_KEY = "link-signing-key"
DECISION_ORIGIN = "https://gvas.example.test"
OLD_EVENT = "https://api.calendly.com/scheduled_events/OLD"


def http_client(application: Application) -> httpx.AsyncClient:
    async def origins() -> frozenset[str]:
        return frozenset({SITE_URL})

    app = create_app(
        routers=(
            create_public_router(
                application.public_quotes,
                intake=application.intake,
                decision_links=application.intake_decision_links,
            ),
        ),
        cors_origins=origins,
    )
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


async def drive(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    availability: AvailabilityFake,
    agent: IntakeAgentFake | None = None,
    intake_settings: IntakeSettings | None = None,
    notification_email: str | None = None,
    email: str = EMAIL,
    owner_email: OwnerEmailPort | None = None,
) -> tuple[Application, OwnerReplyFake, BusinessId, str, str, str]:
    """Like ``reach_awaiting_owner`` but returns the token too, and honours
    custom intake settings (decision links)."""

    business_id = await intake_business(session_factory, notification_email=notification_email)
    offered = slot(datetime.now(UTC))
    availability.slots = (offered,)
    agent = agent or IntakeAgentFake(
        [
            collected_turn(name="Jane Doe", email=email, phone="+15555550100", problem="leak"),
            collected_turn(address="1 Main St"),
            IntakeTurn(reply="Great, here are openings.", ready_for_slots=True),
        ]
    )
    application, owner = intake_app(
        session_factory,
        agent=agent,
        availability=availability,
        intake_settings=intake_settings,
        owner_email=owner_email,
    )
    await seed_owner_thread(application, business_id)
    await immediate_worker(application).drain()

    async with http_client(application) as client:
        created = await client.post(f"/v1/businesses/{PUBLIC_KEY}/intake/conversations")
        assert created.status_code == 201
        body = created.json()
        token = body["conversationToken"]
        conversation_id = body["conversationId"]
        headers = {"Authorization": f"Bearer {token}"}
        await client.post(
            f"/v1/intake/conversations/{conversation_id}/messages",
            json={"message": "leak"},
            headers=headers,
        )
        await client.post(
            f"/v1/intake/conversations/{conversation_id}/messages",
            json={"message": "1 Main St"},
            headers=headers,
        )
        proposed = await client.post(
            f"/v1/intake/conversations/{conversation_id}/messages",
            json={"message": "times?"},
            headers=headers,
        )
        assert proposed.json()["state"] == "proposing_slots"
        picked = await client.post(
            f"/v1/intake/conversations/{conversation_id}/messages",
            json={"message": f"slot:{offered.start.isoformat()}"},
            headers=headers,
        )
        assert picked.json()["state"] == "awaiting_owner"
    for _ in range(4):
        await immediate_worker(application).drain()
    row = await conversation_row(session_factory, business_id)
    return application, owner, business_id, row.reference, conversation_id, token


async def post(
    client: httpx.AsyncClient, conversation_id: str, token: str, message: str
) -> httpx.Response:
    return await client.post(
        f"/v1/intake/conversations/{conversation_id}/messages",
        json={"message": message},
        headers={"Authorization": f"Bearer {token}"},
    )


async def owner_approve(
    application: Application, business_id: BusinessId, reference: str, key: str
) -> None:
    await application.ingest_service.ingest(
        inbound(business_id, f"approve booking {reference}", message_key=key)
    )
    for _ in range(8):
        await immediate_worker(application).drain()


async def record_event(application: Application, business_id: BusinessId, row: IntakeRow) -> None:
    assert row.requested_slot_start is not None
    # Stored sqlite datetimes come back naive — the provider sends UTC.
    start = row.requested_slot_start.replace(tzinfo=UTC)
    end = row.requested_slot_end.replace(tzinfo=UTC) if row.requested_slot_end is not None else None
    await application.intake_booking_events.handle(
        IntakeBookingEvent(
            kind=BookingEventKind.CREATED,
            business_id=business_id,
            invitee_email=EMAIL,
            event_uri=OLD_EVENT,
            start=start,
            end=end,
        )
    )


async def row_by_id(
    session_factory: async_sessionmaker[AsyncSession],
    business_id: BusinessId,
    conversation_id: str,
) -> IntakeRow:
    async with session_factory() as session:
        rows = (
            await session.scalars(select(IntakeRow).where(IntakeRow.business_id == business_id))
        ).all()
    by_id = {str(r.id): r for r in rows}
    return by_id[conversation_id]


def decision_paths(email_body: str) -> tuple[str, str]:
    approve = re.search(r"Approve: \S*(/v1/intake/booking-decisions/\S+)", email_body)
    decline = re.search(r"Decline: \S*(/v1/intake/booking-decisions/\S+)", email_body)
    assert approve and decline, f"decision links missing from e-mail body: {email_body}"
    return approve.group(1), decline.group(1)


@pytest.mark.asyncio
async def test_booked_chat_keeps_answering_after_the_request(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    agent = IntakeAgentFake(
        [
            collected_turn(name="Jane Doe", email=EMAIL, phone="+15555550100", problem="leak"),
            collected_turn(address="1 Main St"),
            IntakeTurn(reply="Great, here are openings.", ready_for_slots=True),
            IntakeTurn(reply="Weekdays, 8 to 5."),
        ]
    )
    application, _o, _b, _r, conversation_id, token = await drive(
        session_factory, availability=AvailabilityFake(), agent=agent
    )
    async with http_client(application) as client:
        reply = await post(client, conversation_id, token, "do you work weekends?")
        assert reply.status_code == 200
        body = reply.json()
        assert body["state"] == "awaiting_owner"
        assert body["reply"] == "Weekdays, 8 to 5."
        booking = body["booking"]
        assert booking and booking["status"] == "requested" and booking["start"]
    request = agent.requests[-1]
    assert request.existing_booking is not None
    assert request.existing_booking.status.value == "requested"


@pytest.mark.asyncio
async def test_cancel_request_notifies_the_owner_and_closes(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    agent = IntakeAgentFake(
        [
            collected_turn(name="Jane Doe", email=EMAIL, phone="+15555550100", problem="leak"),
            collected_turn(address="1 Main St"),
            IntakeTurn(reply="Great, here are openings.", ready_for_slots=True),
            IntakeTurn(reply="cancelling", wants_cancel=True),
        ]
    )
    application, owner, business_id, reference, conversation_id, token = await drive(
        session_factory, availability=AvailabilityFake(), agent=agent
    )
    async with http_client(application) as client:
        reply = await post(client, conversation_id, token, "actually, cancel my call")
        assert reply.status_code == 200
        assert reply.json()["state"] == "closed"
        again = await post(client, conversation_id, token, "hello?")
        assert again.status_code == 409
    for _ in range(4):
        await immediate_worker(application).drain()
    row = await conversation_row(session_factory, business_id)
    assert row.state == "closed"
    notices = [t for t in texts_of(owner, "Booking") if "canceled" in t and reference in t]
    assert notices, "the owner hears the customer canceled"


@pytest.mark.asyncio
async def test_reschedule_reoffers_slots_and_the_pick_rerequests(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    availability = AvailabilityFake()
    agent = IntakeAgentFake(
        [
            collected_turn(name="Jane Doe", email=EMAIL, phone="+15555550100", problem="leak"),
            collected_turn(address="1 Main St"),
            IntakeTurn(reply="Great, here are openings.", ready_for_slots=True),
            IntakeTurn(reply="Here are other times.", wants_reschedule=True),
        ]
    )
    application, owner, business_id, reference, conversation_id, token = await drive(
        session_factory, availability=availability, agent=agent
    )
    later = slot(datetime.now(UTC), days=3)
    availability.slots = (later,)
    async with http_client(application) as client:
        offered = await post(client, conversation_id, token, "can I move it?")
        assert offered.status_code == 200
        body = offered.json()
        assert body["state"] == "awaiting_owner"
        assert body["reply"] == RESCHEDULE_OFFER_REPLY
        assert [s["start"] for s in body["slots"]] == [later.start.isoformat()]
        picked = await post(client, conversation_id, token, f"slot:{later.start.isoformat()}")
        assert picked.status_code == 200
        assert picked.json()["state"] == "awaiting_owner"
        assert picked.json()["slots"] is None
    for _ in range(4):
        await immediate_worker(application).drain()
    row = await conversation_row(session_factory, business_id)
    assert row.state == "awaiting_owner"
    assert row.requested_slot_start == later.start.replace(tzinfo=None)
    notices = [t for t in texts_of(owner, "Updated booking request")]
    assert notices and reference in notices[0]
    assert "Replaces the earlier request" in notices[0]


@pytest.mark.asyncio
async def test_approved_booking_moves_only_once_the_new_time_is_approved(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    availability = AvailabilityFake(result=BookingResult(kind=BookingKind.BOOKED))
    agent = IntakeAgentFake(
        [
            collected_turn(name="Jane Doe", email=EMAIL, phone="+15555550100", problem="leak"),
            collected_turn(address="1 Main St"),
            IntakeTurn(reply="Great, here are openings.", ready_for_slots=True),
            IntakeTurn(reply="Here are other times.", wants_reschedule=True),
        ]
    )
    application, _o, business_id, reference, conversation_id, token = await drive(
        session_factory, availability=availability, agent=agent
    )
    await owner_approve(application, business_id, reference, "approve-one")
    row = await conversation_row(session_factory, business_id)
    assert row.state == "approved"
    await record_event(application, business_id, row)
    row = await conversation_row(session_factory, business_id)
    assert row.booked_event_uri == OLD_EVENT
    original_start = row.requested_slot_start

    later = slot(datetime.now(UTC), days=3)
    availability.slots = (later,)
    async with http_client(application) as client:
        offered = await post(client, conversation_id, token, "need to move it")
        assert offered.status_code == 200
        assert offered.json()["state"] == "approved"
        assert offered.json()["slots"]
        picked = await post(client, conversation_id, token, f"slot:{later.start.isoformat()}")
        assert picked.json()["state"] == "awaiting_owner"
    row = await conversation_row(session_factory, business_id)
    assert row.state == "awaiting_owner"
    assert row.requested_slot_start == later.start.replace(tzinfo=None)
    # The original booking is in custody, not cancelled yet.
    assert row.superseded_booking is not None
    assert row.superseded_booking["event_uri"] == OLD_EVENT
    assert row.booked_event_uri is None
    assert availability.cancel_calls == []

    await owner_approve(application, business_id, reference, "approve-two")
    row = await conversation_row(session_factory, business_id)
    assert row.state == "approved"
    assert row.requested_slot_start == later.start.replace(tzinfo=None)
    assert row.superseded_booking is None
    cancels = await commands_of(session_factory, business_id, INTAKE_BOOKING_CANCEL_COMMAND_TYPE)
    assert cancels and cancels[0].payload["event_uri"] == OLD_EVENT
    assert availability.cancel_calls == [(business_id, OLD_EVENT)]
    assert original_start is not None


@pytest.mark.asyncio
async def test_repeated_reschedules_keep_the_original_booking_in_custody(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    availability = AvailabilityFake(result=BookingResult(kind=BookingKind.BOOKED))
    agent = IntakeAgentFake(
        [
            collected_turn(name="Jane Doe", email=EMAIL, phone="+15555550100", problem="leak"),
            collected_turn(address="1 Main St"),
            IntakeTurn(reply="Great, here are openings.", ready_for_slots=True),
            IntakeTurn(reply="Here are other times.", wants_reschedule=True),
            IntakeTurn(reply="Here are even more times.", wants_reschedule=True),
        ]
    )
    application, _o, business_id, reference, conversation_id, token = await drive(
        session_factory, availability=availability, agent=agent
    )
    await owner_approve(application, business_id, reference, "approve-one")
    row = await conversation_row(session_factory, business_id)
    await record_event(application, business_id, row)
    row = await conversation_row(session_factory, business_id)
    assert row.booked_event_uri == OLD_EVENT

    first = slot(datetime.now(UTC), days=3)
    second = slot(datetime.now(UTC), days=4)
    availability.slots = (first,)
    async with http_client(application) as client:
        await post(client, conversation_id, token, "need to move it")
        await post(client, conversation_id, token, f"slot:{first.start.isoformat()}")
        # Change their mind again before the owner decides: the first event
        # must stay in custody or approving would leave two bookings live.
        availability.slots = (second,)
        offered = await post(client, conversation_id, token, "actually move it again")
        assert offered.json()["slots"]
        picked = await post(client, conversation_id, token, f"slot:{second.start.isoformat()}")
        assert picked.json()["state"] == "awaiting_owner"

    row = await conversation_row(session_factory, business_id)
    assert row.requested_slot_start == second.start.replace(tzinfo=None)
    assert row.superseded_booking is not None
    assert row.superseded_booking["event_uri"] == OLD_EVENT

    await owner_approve(application, business_id, reference, "approve-two")
    row = await conversation_row(session_factory, business_id)
    assert row.state == "approved"
    assert row.requested_slot_start == second.start.replace(tzinfo=None)
    assert row.superseded_booking is None
    assert availability.cancel_calls == [(business_id, OLD_EVENT)]


@pytest.mark.asyncio
async def test_declining_the_new_time_restores_the_original_booking(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    availability = AvailabilityFake(result=BookingResult(kind=BookingKind.BOOKED))
    agent = IntakeAgentFake(
        [
            collected_turn(name="Jane Doe", email=EMAIL, phone="+15555550100", problem="leak"),
            collected_turn(address="1 Main St"),
            IntakeTurn(reply="Great, here are openings.", ready_for_slots=True),
            IntakeTurn(reply="Here are other times.", wants_reschedule=True),
        ]
    )
    application, _o, business_id, reference, conversation_id, token = await drive(
        session_factory, availability=availability, agent=agent
    )
    await owner_approve(application, business_id, reference, "approve-one")
    row = await conversation_row(session_factory, business_id)
    original_start = row.requested_slot_start
    await record_event(application, business_id, row)

    later = slot(datetime.now(UTC), days=3)
    availability.slots = (later,)
    async with http_client(application) as client:
        await post(client, conversation_id, token, "move it please")
        await post(client, conversation_id, token, f"slot:{later.start.isoformat()}")

    await application.ingest_service.ingest(
        inbound(
            business_id,
            f"decline booking {reference} can't do it",
            message_key="decline-resched",
        )
    )
    for _ in range(6):
        await immediate_worker(application).drain()

    row = await conversation_row(session_factory, business_id)
    assert row.state == "approved"
    assert row.requested_slot_start == original_start
    assert row.booked_event_uri == OLD_EVENT
    assert row.superseded_booking is None
    assert availability.cancel_calls == []
    emails = await commands_of(session_factory, business_id, INTAKE_CUSTOMER_EMAIL_COMMAND_TYPE)
    bodies = [c.payload["body"] for c in emails if isinstance(c.payload["body"], str)]
    assert any("stays at" in body for body in bodies)


@pytest.mark.asyncio
async def test_a_new_conversation_sees_the_pending_request(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Returning visitor without the stored id: the same e-mail finds the
    live request — a reschedule is refused while it waits on the owner, a
    cancel is allowed."""
    agent = IntakeAgentFake(
        [
            collected_turn(name="Jane Doe", email=EMAIL, phone="+15555550100", problem="leak"),
            collected_turn(address="1 Main St"),
            IntakeTurn(reply="Great, here are openings.", ready_for_slots=True),
            collected_turn(name="Jane Doe", email=EMAIL),
            IntakeTurn(reply="Refuse.", wants_reschedule=True),
            IntakeTurn(reply="Okay.", wants_cancel=True),
        ]
    )
    application, owner, business_id, reference, first_cid, _t = await drive(
        session_factory, availability=AvailabilityFake(), agent=agent
    )
    async with http_client(application) as client:
        created = await client.post(f"/v1/businesses/{PUBLIC_KEY}/intake/conversations")
        token = created.json()["conversationToken"]
        conversation_id = created.json()["conversationId"]
        await post(client, conversation_id, token, "hi, it's Jane again")
        moved = await post(client, conversation_id, token, "can we move it?")
        assert moved.status_code == 200
        assert "waiting on approval" in moved.json()["reply"]
        assert moved.json()["slots"] is None
        cancelled = await post(client, conversation_id, token, "cancel it then")
        assert cancelled.status_code == 200
        assert "canceled" in cancelled.json()["reply"]
    for _ in range(4):
        await immediate_worker(application).drain()
    first = await row_by_id(session_factory, business_id, first_cid)
    assert first.state == "closed"
    notices = [t for t in texts_of(owner, "Booking") if "canceled" in t]
    assert notices
    request = agent.requests[-2]  # the "can we move it?" turn
    assert request.existing_booking is not None
    assert request.existing_booking.status.value == "requested"
    assert request.existing_booking.slot_label


@pytest.mark.asyncio
async def test_a_new_conversation_adopts_an_approved_booking(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    availability = AvailabilityFake(result=BookingResult(kind=BookingKind.BOOKED))
    agent = IntakeAgentFake(
        [
            collected_turn(name="Jane Doe", email=EMAIL, phone="+15555550100", problem="leak"),
            collected_turn(address="1 Main St"),
            IntakeTurn(reply="Great, here are openings.", ready_for_slots=True),
            collected_turn(name="Jane Doe", email=EMAIL),
            IntakeTurn(reply="Which times?", wants_reschedule=True),
        ]
    )
    application, _o, business_id, reference, first_cid, _t = await drive(
        session_factory, availability=availability, agent=agent
    )
    await owner_approve(application, business_id, reference, "approve-adopt")
    row = await conversation_row(session_factory, business_id)
    assert row.state == "approved"
    booked_start = row.requested_slot_start

    later = slot(datetime.now(UTC), days=4)
    availability.slots = (later,)
    async with http_client(application) as client:
        created = await client.post(f"/v1/businesses/{PUBLIC_KEY}/intake/conversations")
        token = created.json()["conversationToken"]
        conversation_id = created.json()["conversationId"]
        assert conversation_id != first_cid
        first = await post(client, conversation_id, token, "hi, Jane again")
        assert first.status_code == 200
        moved = await post(client, conversation_id, token, "can I move my call?")
        assert moved.status_code == 200
        assert moved.json()["state"] == "approved"
        assert moved.json()["slots"], "the adopted booking offers new times"
    first_row = await row_by_id(session_factory, business_id, first_cid)
    assert first_row.state == "closed", "the old chat closes once custody moved"
    adopted = await row_by_id(session_factory, business_id, conversation_id)
    assert adopted.state == "approved"
    assert adopted.requested_slot_start == booked_start


@pytest.mark.asyncio
async def test_owner_email_carries_working_approve_and_decline_links(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    settings = IntakeSettings(
        max_conversations_per_day=0,
        decision_link_secret=LINK_SIGNING_KEY,
        decision_link_base_url=DECISION_ORIGIN,
    )
    availability = AvailabilityFake(result=BookingResult(kind=BookingKind.BOOKED))
    application, _o, business_id, reference, _c, _t = await drive(
        session_factory,
        availability=availability,
        intake_settings=settings,
        notification_email="owner@example.com",
    )
    copies = await commands_of(session_factory, business_id, OWNER_NOTICE_EMAIL_COMMAND_TYPE)
    assert len(copies) == 1
    body = copies[0].payload["body"]
    assert isinstance(body, str)
    approve_path, decline_path = decision_paths(body)
    assert LINK_SIGNING_KEY not in body

    async with http_client(application) as client:
        preview = await client.get(approve_path)
        assert preview.status_code == 200
        assert "Approve" in preview.text and reference in preview.text
        assert "already" not in preview.text.lower()

        # A GET never decides: the request still waits on the owner.
        row = await conversation_row(session_factory, business_id)
        assert row.state == "awaiting_owner"

        applied = await client.post(approve_path)
        assert applied.status_code == 200
        assert "approved" in applied.text.lower()
    for _ in range(6):
        await immediate_worker(application).drain()

    row = await conversation_row(session_factory, business_id)
    assert row.state == "approved"
    assert availability.book_calls, "the link ran the same approve path"

    async with http_client(application) as client:
        again = await client.post(approve_path)
        assert again.status_code == 200
        assert "already" in again.text.lower()

        tampered = await client.get(approve_path[:-2] + "zz")
        assert tampered.status_code == 200
        assert "copied in full" in tampered.text

        decline_preview = await client.get(decline_path)
        assert "already" in decline_preview.text.lower()


@pytest.mark.asyncio
async def test_a_stale_decision_link_cannot_act_on_the_new_request(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    settings = IntakeSettings(
        max_conversations_per_day=0,
        decision_link_secret=LINK_SIGNING_KEY,
        decision_link_base_url=DECISION_ORIGIN,
    )
    agent = IntakeAgentFake(
        [
            collected_turn(name="Jane Doe", email=EMAIL, phone="+15555550100", problem="leak"),
            collected_turn(address="1 Main St"),
            IntakeTurn(reply="Great, here are openings.", ready_for_slots=True),
            IntakeTurn(reply="Here are other times.", wants_reschedule=True),
        ]
    )
    availability = AvailabilityFake()
    application, _o, business_id, reference, conversation_id, token = await drive(
        session_factory,
        availability=availability,
        agent=agent,
        intake_settings=settings,
        notification_email="owner@example.com",
    )
    copies = await commands_of(session_factory, business_id, OWNER_NOTICE_EMAIL_COMMAND_TYPE)
    first_body = copies[0].payload["body"]
    assert isinstance(first_body, str)
    approve_path, _decline = decision_paths(first_body)

    later = slot(datetime.now(UTC), days=3)
    availability.slots = (later,)
    async with http_client(application) as client:
        await post(client, conversation_id, token, "move it")
        picked = await post(client, conversation_id, token, f"slot:{later.start.isoformat()}")
        assert picked.json()["state"] == "awaiting_owner"
        stale = await client.post(approve_path)
        assert stale.status_code == 200
        assert "earlier request" in stale.text
    row = await conversation_row(session_factory, business_id)
    assert row.state == "awaiting_owner"
    assert row.requested_slot_start == later.start.replace(tzinfo=None)
    assert availability.book_calls == []
