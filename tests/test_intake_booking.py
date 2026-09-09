"""Website booking intake: chat lifecycle, tenant isolation, caps, owner
decisions and both booking paths — all behind fakes.

The hard rule under test: nothing reaches the availability provider's ``book``
until the owner replies ``approve booking <ref>``; declining never books.
"""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import uuid4
from zoneinfo import ZoneInfo

import httpx
import pytest
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from composition_fakes import (
    CustomerDeliveryFake,
    OwnerReplyFake,
    TranscriptionFake,
)
from gvas.application.intake import (
    NO_AVAILABILITY_REPLY,
    UNAVAILABLE_REPLY,
    IntakeClosedError,
    IntakeDeliveryError,
    SendIntakeCustomerEmailService,
    SendIntakeCustomerTextService,
)
from gvas.composition import Application, build_application
from gvas.config import IntakeSettings
from gvas.domain.customers import CustomerRecord
from gvas.domain.enums import DeliveryStatus
from gvas.domain.identifiers import BusinessId, CustomerId
from gvas.domain.intake import (
    INTAKE_BOOKING_ARRANGE_COMMAND_TYPE,
    INTAKE_CUSTOMER_EMAIL_COMMAND_TYPE,
    INTAKE_CUSTOMER_TEXT_COMMAND_TYPE,
    PRICE_GUARD_REPLY,
    AvailabilityError,
    AvailableSlot,
    BookingKind,
    BookingRequest,
    BookingResult,
    IntakeAgentError,
    IntakeCollected,
    IntakeTurn,
    IntakeTurnRequest,
    pick_offer_slots,
)
from gvas.domain.messages import (
    CustomerDeliveryRequest,
    CustomerTextRequest,
    DeliveryReceipt,
)
from gvas.infrastructure.intake_models import IntakeConversation as IntakeRow
from gvas.infrastructure.models import OutboxMessage
from gvas.infrastructure.repositories import SqlBusinessRepository
from gvas.interfaces.http.app import create_app
from gvas.interfaces.http.portal import create_portal_router
from gvas.interfaces.http.public import create_public_router
from test_composition import Clock, inbound, seed_business
from test_hosted_quotes import CustomerTextFake
from test_pilot_runtime import deterministic_ports, immediate_worker, texts_of

NOW = datetime(2026, 1, 5, 12, 0, tzinfo=UTC)  # a Monday
PUBLIC_KEY = "gvb_intake_test"
SITE_URL = "https://site.example.test"
CALENDLY_LINK = "https://calendly.com/test/inspection"
EMAIL = "customer@example.test"


def next_weekday(anchor: datetime, *, days: int = 2) -> datetime:
    """A 9 AM slot strictly after ``anchor`` on a weekday."""

    day = anchor.date() + timedelta(days=days)
    while day.weekday() >= 5:
        day += timedelta(days=1)
    return datetime(day.year, day.month, day.day, 9, 0, tzinfo=UTC)


def slot(anchor: datetime, *, days: int = 2) -> AvailableSlot:
    start = next_weekday(anchor, days=days)
    return AvailableSlot(start=start, end=start + timedelta(hours=1))


class IntakeAgentFake:
    """Scripted model: pops turns, records every request it was asked."""

    def __init__(
        self, turns: list[IntakeTurn] | None = None, *, error: Exception | None = None
    ) -> None:
        self._turns = list(turns or [])
        self._error = error
        self.requests: list[IntakeTurnRequest] = []
        self.calls = 0

    async def turn(self, request: IntakeTurnRequest) -> IntakeTurn:
        self.requests.append(request)
        self.calls += 1
        if self._error is not None:
            raise self._error
        if not self._turns:
            return IntakeTurn(reply="Anything else I should tell the owner?")
        return self._turns.pop(0)


class AvailabilityFake:
    """Fixed openings; ``book`` returns the configured result or raises."""

    def __init__(
        self,
        slots: tuple[AvailableSlot, ...] = (),
        *,
        result: BookingResult | None = None,
        error: Exception | None = None,
        found: BookingResult | None = None,
        slot_error: Exception | None = None,
    ) -> None:
        self.slots = slots
        self._result = result or BookingResult(kind=BookingKind.BOOKED)
        self._error = error
        self._found = found
        self._slot_error = slot_error
        self.slot_calls: list[tuple[BusinessId, datetime, datetime]] = []
        self.book_calls: list[BookingRequest] = []
        self.find_calls: list[BookingRequest] = []

    async def available_slots(
        self, business_id: BusinessId, start: datetime, end: datetime
    ) -> tuple[AvailableSlot, ...]:
        self.slot_calls.append((business_id, start, end))
        if self._slot_error is not None:
            raise self._slot_error
        return self.slots

    async def find_booking(self, request: BookingRequest) -> BookingResult | None:
        self.find_calls.append(request)
        return self._found

    async def book(self, request: BookingRequest) -> BookingResult:
        self.book_calls.append(request)
        if self._error is not None:
            raise self._error
        return self._result


async def intake_business(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    public_key: str = PUBLIC_KEY,
) -> BusinessId:
    business_id = BusinessId(uuid4())
    await seed_business(session_factory, business_id)
    async with session_factory() as session:
        await SqlBusinessRepository(session).configure_site(
            business_id,
            site_url=SITE_URL,
            display_name="Test Co",
            calendly_url=CALENDLY_LINK,
            public_key=public_key,
            now=NOW,
        )
        await session.commit()
    return business_id


def intake_app(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    agent: IntakeAgentFake | None = None,
    availability: AvailabilityFake | None = None,
    owner_replies: OwnerReplyFake | None = None,
    customer_email: CustomerDeliveryFake | None = None,
    customer_text: CustomerTextFake | None = None,
    intake_settings: IntakeSettings | None = None,
) -> tuple[Application, OwnerReplyFake]:
    owner = owner_replies or OwnerReplyFake()
    ports = deterministic_ports(owner, TranscriptionFake({}), CustomerDeliveryFake())
    ports = replace(
        ports,
        intake_agent=agent or IntakeAgentFake(),
        availability=availability,
        customer_email=customer_email or CustomerDeliveryFake(),
        customer_text=customer_text,
    )
    return (
        build_application(
            ports,
            session_factory=session_factory,
            now=Clock(),
            intake_settings=intake_settings or IntakeSettings(max_conversations_per_day=0),
        ),
        owner,
    )


def http_client(application: Application) -> httpx.AsyncClient:
    async def origins() -> frozenset[str]:
        return frozenset({SITE_URL})

    app = create_app(
        routers=(
            create_public_router(application.public_quotes, intake=application.intake),
            create_portal_router(application.portal, intake=application.intake),
        ),
        cors_origins=origins,
    )
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


async def seed_owner_thread(application: Application, business_id: BusinessId) -> None:
    """Owner notices anchor to the business's latest inbound message."""

    await application.ingest_service.ingest(
        inbound(business_id, "hi", message_key=f"seed-{business_id}")
    )


async def commands_of(
    session_factory: async_sessionmaker[AsyncSession],
    business_id: BusinessId,
    command_type: str,
) -> list[OutboxMessage]:
    async with session_factory() as session:
        rows = await session.scalars(
            select(OutboxMessage).where(
                OutboxMessage.business_id == business_id,
                OutboxMessage.command_type == command_type,
            )
        )
        return list(rows.all())


async def conversation_row(
    session_factory: async_sessionmaker[AsyncSession], business_id: BusinessId
) -> IntakeRow:
    async with session_factory() as session:
        row = await session.scalar(select(IntakeRow).where(IntakeRow.business_id == business_id))
        assert row is not None
        return row


def collected_turn(**fields: str) -> IntakeTurn:
    return IntakeTurn(
        reply="Noted.",
        collected=IntakeCollected(**fields),
    )


async def reach_awaiting_owner(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    availability: AvailabilityFake,
    agent: IntakeAgentFake | None = None,
    customer_text: CustomerTextFake | None = None,
) -> tuple[Application, OwnerReplyFake, BusinessId, str]:
    """Drives one conversation to ``awaiting_owner`` via HTTP; returns the ref."""

    business_id = await intake_business(session_factory)
    offered = slot(datetime.now(UTC))
    availability.slots = (offered,)
    agent = agent or IntakeAgentFake(
        [
            collected_turn(
                name="Jane Doe",
                email=EMAIL,
                phone="+15555550100",
                problem="roof leak",
            ),
            collected_turn(address="1 Main St"),
            IntakeTurn(reply="Great, here are openings.", ready_for_slots=True),
        ]
    )
    application, owner = intake_app(
        session_factory,
        agent=agent,
        availability=availability,
        customer_text=customer_text,
    )
    await seed_owner_thread(application, business_id)
    await immediate_worker(application).drain()

    async with http_client(application) as client:
        created = await client.post(f"/v1/businesses/{PUBLIC_KEY}/intake/conversations")
        assert created.status_code == 201
        body = created.json()
        token = body["conversationToken"]
        headers = {"Authorization": f"Bearer {token}"}
        conversation_id = body["conversationId"]

        await client.post(
            f"/v1/intake/conversations/{conversation_id}/messages",
            json={"message": "roof leak at my place"},
            headers=headers,
        )
        await client.post(
            f"/v1/intake/conversations/{conversation_id}/messages",
            json={"message": "1 Main St"},
            headers=headers,
        )
        proposed = await client.post(
            f"/v1/intake/conversations/{conversation_id}/messages",
            json={"message": "what times do you have?"},
            headers=headers,
        )
        assert proposed.status_code == 200
        payload = proposed.json()
        assert payload["state"] == "proposing_slots"
        assert payload["slots"] == [
            {"start": offered.start.isoformat(), "end": offered.end.isoformat()}
        ]
        picked = await client.post(
            f"/v1/intake/conversations/{conversation_id}/messages",
            json={"message": f"slot:{offered.start.isoformat()}"},
            headers=headers,
        )
        assert picked.status_code == 200
        assert picked.json()["state"] == "awaiting_owner"

    for _ in range(4):
        await immediate_worker(application).drain()
    row = await conversation_row(session_factory, business_id)
    return application, owner, business_id, row.reference


@pytest.mark.asyncio
async def test_conversation_lifecycle_collects_then_proposes_slots(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    business_id = await intake_business(session_factory)
    offered = slot(datetime.now(UTC))
    availability = AvailabilityFake((offered,))
    agent = IntakeAgentFake(
        [
            collected_turn(name="Jane Doe", problem="ants"),
            collected_turn(email=EMAIL, address="2 Elm St"),
            IntakeTurn(reply="Here is what is open.", ready_for_slots=True),
        ]
    )
    application, _ = intake_app(session_factory, agent=agent, availability=availability)
    await seed_owner_thread(application, business_id)

    async with http_client(application) as client:
        created = await client.post(f"/v1/businesses/{PUBLIC_KEY}/intake/conversations")
        assert created.status_code == 201
        body = created.json()
        assert body["state"] == "collecting"
        assert body["slots"] is None
        assert body["reply"]
        token = body["conversationToken"]
        headers = {"Authorization": f"Bearer {token}"}
        conversation_id = body["conversationId"]

        first = await client.post(
            f"/v1/intake/conversations/{conversation_id}/messages",
            json={"message": "I saw ants in the kitchen"},
            headers=headers,
        )
        assert first.status_code == 200
        assert first.json()["state"] == "collecting"

        second = await client.post(
            f"/v1/intake/conversations/{conversation_id}/messages",
            json={"message": "2 Elm St, jane@example.test"},
            headers=headers,
        )
        assert second.status_code == 200

        third = await client.post(
            f"/v1/intake/conversations/{conversation_id}/messages",
            json={"message": "when can someone come?"},
            headers=headers,
        )
        payload = third.json()
        assert payload["state"] == "proposing_slots"
        assert payload["slots"] == [
            {"start": offered.start.isoformat(), "end": offered.end.isoformat()}
        ]
        assert payload["summary"]["email"] == EMAIL

        view = await client.get(f"/v1/intake/conversations/{conversation_id}", headers=headers)
        assert view.status_code == 200
        view_body = view.json()
        assert view_body["state"] == "proposing_slots"
        assert [m["role"] for m in view_body["messages"]][0] == "agent"
        assert any(m["role"] == "user" for m in view_body["messages"])
        assert view_body["slots"] == payload["slots"]

    assert availability.slot_calls
    transcript_ids = [(request.business_id, request.conversation_id) for request in agent.requests]
    assert all(business_id == call[0] for call in transcript_ids)


@pytest.mark.asyncio
async def test_token_from_another_business_cannot_read_or_post(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    business_a = await intake_business(session_factory)
    business_b = await intake_business(session_factory, public_key="gvb_intake_other")
    assert business_a != business_b
    application, _ = intake_app(session_factory)

    async with http_client(application) as client:
        first = await client.post(f"/v1/businesses/{PUBLIC_KEY}/intake/conversations")
        second = await client.post("/v1/businesses/gvb_intake_other/intake/conversations")
        assert first.status_code == 201 and second.status_code == 201
        a_id = first.json()["conversationId"]
        b_token = second.json()["conversationToken"]

        read = await client.get(
            f"/v1/intake/conversations/{a_id}",
            headers={"Authorization": f"Bearer {b_token}"},
        )
        assert read.status_code == 401
        posted = await client.post(
            f"/v1/intake/conversations/{a_id}/messages",
            json={"message": "hello"},
            headers={"Authorization": f"Bearer {b_token}"},
        )
        assert posted.status_code == 401
        missing_auth = await client.get(f"/v1/intake/conversations/{a_id}")
        assert missing_auth.status_code == 401


@pytest.mark.asyncio
async def test_daily_conversation_cap_rejects_new_conversations(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await intake_business(session_factory)
    application, _ = intake_app(
        session_factory,
        intake_settings=IntakeSettings(max_conversations_per_day=1),
    )
    async with http_client(application) as client:
        first = await client.post(f"/v1/businesses/{PUBLIC_KEY}/intake/conversations")
        second = await client.post(f"/v1/businesses/{PUBLIC_KEY}/intake/conversations")
        assert first.status_code == 201
        assert second.status_code == 429


@pytest.mark.asyncio
async def test_per_conversation_message_cap_stops_the_model(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await intake_business(session_factory)
    agent = IntakeAgentFake()
    application, _ = intake_app(
        session_factory,
        agent=agent,
        intake_settings=IntakeSettings(
            max_conversations_per_day=0, max_messages_per_conversation=1
        ),
    )
    assert application.intake is not None
    async with http_client(application) as client:
        created = await client.post(f"/v1/businesses/{PUBLIC_KEY}/intake/conversations")
        token = created.json()["conversationToken"]
        conversation_id = created.json()["conversationId"]
        headers = {"Authorization": f"Bearer {token}"}
        await client.post(
            f"/v1/intake/conversations/{conversation_id}/messages",
            json={"message": "hi"},
            headers=headers,
        )
        capped = await client.post(
            f"/v1/intake/conversations/{conversation_id}/messages",
            json={"message": "again"},
            headers=headers,
        )
        assert capped.status_code == 200
        assert "message limit" in capped.json()["reply"]
        assert capped.json()["state"] == "closed"
        third = await client.post(
            f"/v1/intake/conversations/{conversation_id}/messages",
            json={"message": "still there?"},
            headers=headers,
        )
        assert third.status_code == 409
        view = await client.get(f"/v1/intake/conversations/{conversation_id}", headers=headers)
        user_rows = [m for m in view.json()["messages"] if m["role"] == "user"]
        assert len(user_rows) == 2, "the cap is terminal: nothing past it is stored"
    assert agent.calls == 1


@pytest.mark.asyncio
async def test_unlisted_slot_pick_is_rejected(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    business_id = await intake_business(session_factory)
    offered = slot(datetime.now(UTC))
    not_offered = slot(datetime.now(UTC), days=3)
    availability = AvailabilityFake((offered,))
    agent = IntakeAgentFake(
        [
            collected_turn(name="Jane", email=EMAIL, address="2 Elm St", problem="ants"),
            IntakeTurn(reply="ok", ready_for_slots=True),
        ]
    )
    application, _ = intake_app(session_factory, agent=agent, availability=availability)
    await seed_owner_thread(application, business_id)
    async with http_client(application) as client:
        created = await client.post(f"/v1/businesses/{PUBLIC_KEY}/intake/conversations")
        token = created.json()["conversationToken"]
        conversation_id = created.json()["conversationId"]
        headers = {"Authorization": f"Bearer {token}"}
        await client.post(
            f"/v1/intake/conversations/{conversation_id}/messages",
            json={"message": "ants"},
            headers=headers,
        )
        proposed = await client.post(
            f"/v1/intake/conversations/{conversation_id}/messages",
            json={"message": "times?"},
            headers=headers,
        )
        assert proposed.json()["state"] == "proposing_slots"
        rejected = await client.post(
            f"/v1/intake/conversations/{conversation_id}/messages",
            json={"message": f"slot:{not_offered.start.isoformat()}"},
            headers=headers,
        )
        assert rejected.status_code == 200
        assert rejected.json()["state"] == "proposing_slots"
        assert "listed times" in rejected.json()["reply"]


@pytest.mark.asyncio
async def test_agent_reply_with_price_is_scrubbed(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    business_id = await intake_business(session_factory)
    agent = IntakeAgentFake([IntakeTurn(reply="It will cost about $500 for the inspection.")])
    application, _ = intake_app(session_factory, agent=agent)
    await seed_owner_thread(application, business_id)
    async with http_client(application) as client:
        created = await client.post(f"/v1/businesses/{PUBLIC_KEY}/intake/conversations")
        token = created.json()["conversationToken"]
        conversation_id = created.json()["conversationId"]
        reply = await client.post(
            f"/v1/intake/conversations/{conversation_id}/messages",
            json={"message": "how much is it?"},
            headers={"Authorization": f"Bearer {token}"},
        )
        body = reply.json()
        assert body["reply"] == PRICE_GUARD_REPLY
        assert "$500" not in body["reply"]
        view = await client.get(
            f"/v1/intake/conversations/{conversation_id}",
            headers={"Authorization": f"Bearer {token}"},
        )
        contents = [m["content"] for m in view.json()["messages"]]
        assert not any("$500" in content for content in contents)


@pytest.mark.asyncio
async def test_slot_pick_notifies_owner_with_decision_commands(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    availability = AvailabilityFake()
    application, owner, business_id, reference = await reach_awaiting_owner(
        session_factory, availability=availability
    )
    notices = texts_of(owner, "Booking request")
    assert notices
    notice = notices[0]
    assert f"#{reference}" in notice
    assert f"approve booking {reference}" in notice
    assert f"decline booking {reference}" in notice
    assert availability.book_calls == []


@pytest.mark.asyncio
async def test_owner_approve_books_directly_and_emails_customer(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    availability = AvailabilityFake(result=BookingResult(kind=BookingKind.BOOKED))
    application, owner, business_id, reference = await reach_awaiting_owner(
        session_factory, availability=availability
    )
    await application.ingest_service.ingest(
        inbound(business_id, f"approve booking {reference}", message_key="approve-1")
    )
    for _ in range(6):
        await immediate_worker(application).drain()

    replies = texts_of(owner, "Approved booking")
    assert replies and reference in replies[0]
    assert availability.book_calls, "approval must reach the availability port"
    row = await conversation_row(session_factory, business_id)
    assert row.state == "approved"
    assert row.booking_kind == "booked"

    email_commands = await commands_of(
        session_factory, business_id, INTAKE_CUSTOMER_EMAIL_COMMAND_TYPE
    )
    assert email_commands
    booked_body = email_commands[0].payload["body"]
    assert isinstance(booked_body, str) and "is booked" in booked_body


@pytest.mark.asyncio
async def test_owner_approve_with_link_fallback_sends_link_and_text(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    link = "https://calendly.com/test/inspection?s=single-use"
    availability = AvailabilityFake(result=BookingResult(kind=BookingKind.LINK, link=link))
    customer_text = CustomerTextFake()
    application, owner, business_id, reference = await reach_awaiting_owner(
        session_factory, availability=availability, customer_text=customer_text
    )
    await application.ingest_service.ingest(
        inbound(business_id, f"approve booking {reference}", message_key="approve-2")
    )
    for _ in range(6):
        await immediate_worker(application).drain()

    row = await conversation_row(session_factory, business_id)
    assert row.state == "approved" and row.booking_kind == "link"
    email_commands = await commands_of(
        session_factory, business_id, INTAKE_CUSTOMER_EMAIL_COMMAND_TYPE
    )
    link_body = email_commands[0].payload["body"] if email_commands else None
    assert isinstance(link_body, str) and link in link_body
    text_commands = await commands_of(
        session_factory, business_id, INTAKE_CUSTOMER_TEXT_COMMAND_TYPE
    )
    assert text_commands, "a phone was collected, so a text goes out"
    assert customer_text.requests, "the text command dispatched"
    assert link in customer_text.requests[0].text


@pytest.mark.asyncio
async def test_owner_decline_marks_declined_and_emails_reason(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    availability = AvailabilityFake()
    application, owner, business_id, reference = await reach_awaiting_owner(
        session_factory, availability=availability
    )
    await application.ingest_service.ingest(
        inbound(
            business_id,
            f"decline booking {reference} booked solid\nthis week",
            message_key="decline-1",
        )
    )
    for _ in range(4):
        await immediate_worker(application).drain()

    assert texts_of(owner, "Declined booking")
    row = await conversation_row(session_factory, business_id)
    assert row.state == "declined"
    assert row.decision_reason is not None and "\n" not in row.decision_reason
    email_commands = await commands_of(
        session_factory, business_id, INTAKE_CUSTOMER_EMAIL_COMMAND_TYPE
    )
    assert email_commands
    body = email_commands[0].payload["body"]
    assert isinstance(body, str)
    assert "booked solid this week" in body
    assert CALENDLY_LINK in body
    arrange = await commands_of(session_factory, business_id, INTAKE_BOOKING_ARRANGE_COMMAND_TYPE)
    assert arrange == []
    assert availability.book_calls == []


@pytest.mark.asyncio
async def test_unknown_and_repeat_decisions_get_clear_replies(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    availability = AvailabilityFake()
    application, owner, business_id, reference = await reach_awaiting_owner(
        session_factory, availability=availability
    )
    worker = immediate_worker(application)

    await application.ingest_service.ingest(
        inbound(business_id, "approve booking deadbeef", message_key="approve-unknown")
    )
    for _ in range(3):
        await worker.drain()
    assert any("can't find booking deadbeef" in t for t in texts_of(owner, "I can't find"))

    await application.ingest_service.ingest(
        inbound(business_id, f"approve booking {reference}", message_key="approve-a")
    )
    for _ in range(5):
        await worker.drain()
    await application.ingest_service.ingest(
        inbound(business_id, f"approve booking {reference}", message_key="approve-b")
    )
    for _ in range(3):
        await worker.drain()
    assert any("already approved" in t for t in texts_of(owner, "Booking"))
    arrange = await commands_of(session_factory, business_id, INTAKE_BOOKING_ARRANGE_COMMAND_TYPE)
    assert len(arrange) == 1


@pytest.mark.asyncio
async def test_needs_human_escalates_to_owner_once(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    business_id = await intake_business(session_factory)
    agent = IntakeAgentFake(
        [
            IntakeTurn(
                reply="Hmm, I'm not sure.",
                needs_human=True,
                summary="Customer asked about commercial pricing tiers.",
            ),
            IntakeTurn(
                reply="Still unsure.",
                needs_human=True,
                summary="Again ambiguous.",
            ),
        ]
    )
    application, owner = intake_app(session_factory, agent=agent)
    await seed_owner_thread(application, business_id)
    await immediate_worker(application).drain()
    async with http_client(application) as client:
        created = await client.post(f"/v1/businesses/{PUBLIC_KEY}/intake/conversations")
        token = created.json()["conversationToken"]
        conversation_id = created.json()["conversationId"]
        headers = {"Authorization": f"Bearer {token}"}
        for text in ("do you do commercial buildings?", "what about warehouses?"):
            reply = await client.post(
                f"/v1/intake/conversations/{conversation_id}/messages",
                json={"message": text},
                headers=headers,
            )
            assert reply.status_code == 200
    for _ in range(3):
        await immediate_worker(application).drain()
    escalations = texts_of(owner, "Chat ")
    assert len(escalations) == 1
    assert "commercial pricing" in escalations[0]


@pytest.mark.asyncio
async def test_portal_conversation_is_prefilled_and_linked(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    business_id = await intake_business(session_factory)
    agent = IntakeAgentFake()
    application, _ = intake_app(session_factory, agent=agent)
    assert application.intake is not None

    customer = CustomerRecord(
        customer_id=CustomerId(uuid4()),
        business_id=business_id,
        email=EMAIL,
        display_name="Jane Doe",
        phone="+15555550100",
        created_at=NOW,
    )
    async with session_factory() as db_session:
        business_row = await SqlBusinessRepository(db_session).get(business_id)
    assert business_row is not None
    start = await application.intake.start_portal_conversation(business_row, customer)
    assert start.conversation.customer_id == customer.customer_id
    assert start.conversation.collected.email == EMAIL
    assert "Welcome back" in start.reply


@pytest.mark.asyncio
async def test_declined_conversation_rejects_new_messages(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    availability = AvailabilityFake()
    application, _, business_id, reference = await reach_awaiting_owner(
        session_factory, availability=availability
    )
    await application.ingest_service.ingest(
        inbound(business_id, f"decline booking {reference}", message_key="decline-x")
    )
    for _ in range(4):
        await immediate_worker(application).drain()

    assert application.intake is not None
    async with application.unit_of_work_factory() as unit_of_work:
        conversation = await unit_of_work.intake_conversations.find_by_reference(
            business_id, reference
        )
        assert conversation is not None
    with pytest.raises(IntakeClosedError):
        await application.intake.post_message(conversation, "one more thing")


@pytest.mark.asyncio
async def test_retried_booking_reconciles_instead_of_double_booking(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """A crashed attempt leaves the marker set; the retry reconciles first."""

    availability = AvailabilityFake(found=BookingResult(kind=BookingKind.BOOKED))
    application, _, business_id, reference = await reach_awaiting_owner(
        session_factory, availability=availability
    )
    async with session_factory() as session:
        await session.execute(
            update(IntakeRow)
            .where(IntakeRow.business_id == business_id)
            .values(booking_attempted_at=NOW)
        )
        await session.commit()
    await application.ingest_service.ingest(
        inbound(business_id, f"approve booking {reference}", message_key="approve-reconciled")
    )
    for _ in range(6):
        await immediate_worker(application).drain()

    assert availability.find_calls, "a marked attempt reconciles before booking"
    assert availability.book_calls == [], "the recovered booking is not created twice"
    row = await conversation_row(session_factory, business_id)
    assert row.booking_kind == "booked"
    email_commands = await commands_of(
        session_factory, business_id, INTAKE_CUSTOMER_EMAIL_COMMAND_TYPE
    )
    assert email_commands, "the customer still gets the confirmation"


@pytest.mark.asyncio
async def test_retried_booking_still_books_when_reconcile_finds_nothing(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    availability = AvailabilityFake(found=None)
    application, _, business_id, reference = await reach_awaiting_owner(
        session_factory, availability=availability
    )
    async with session_factory() as session:
        await session.execute(
            update(IntakeRow)
            .where(IntakeRow.business_id == business_id)
            .values(booking_attempted_at=NOW)
        )
        await session.commit()
    await application.ingest_service.ingest(
        inbound(business_id, f"approve booking {reference}", message_key="approve-retry")
    )
    for _ in range(6):
        await immediate_worker(application).drain()

    assert availability.find_calls
    assert availability.book_calls, "nothing found — the booking is made"
    row = await conversation_row(session_factory, business_id)
    assert row.booking_kind == "booked"


@pytest.mark.asyncio
async def test_approval_after_the_requested_time_is_rejected(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    availability = AvailabilityFake()
    application, owner, business_id, reference = await reach_awaiting_owner(
        session_factory, availability=availability
    )
    past = datetime.now(UTC) - timedelta(hours=2)
    async with session_factory() as session:
        await session.execute(
            update(IntakeRow)
            .where(IntakeRow.business_id == business_id)
            .values(requested_slot_start=past, requested_slot_end=past + timedelta(hours=1))
        )
        await session.commit()
    await application.ingest_service.ingest(
        inbound(business_id, f"approve booking {reference}", message_key="approve-late")
    )
    for _ in range(4):
        await immediate_worker(application).drain()

    replies = [text for text in texts_of(owner, "Booking") if "already passed" in text]
    assert replies and reference in replies[0]
    row = await conversation_row(session_factory, business_id)
    assert row.state == "awaiting_owner"
    assert availability.book_calls == []
    arrange = await commands_of(session_factory, business_id, INTAKE_BOOKING_ARRANGE_COMMAND_TYPE)
    assert arrange == []


@pytest.mark.asyncio
async def test_failed_customer_deliveries_raise_so_the_outbox_retries(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    class FailingDelivery:
        async def deliver(self, request: CustomerDeliveryRequest) -> DeliveryReceipt:
            return DeliveryReceipt(
                status=DeliveryStatus.FAILED, detail="mailbox rejected", occurred_at=NOW
            )

    class FailingText:
        async def send_text(self, request: CustomerTextRequest) -> DeliveryReceipt:
            return DeliveryReceipt(
                status=DeliveryStatus.FAILED, detail="carrier rejected", occurred_at=NOW
            )

    email = SendIntakeCustomerEmailService(FailingDelivery())
    with pytest.raises(IntakeDeliveryError):
        await email.send(
            BusinessId(uuid4()),
            {
                "to": "customer@example.test",
                "subject": "About your appointment",
                "body": "details",
                "idempotency_key": "key-1",
            },
        )
    text = SendIntakeCustomerTextService(FailingText())
    with pytest.raises(IntakeDeliveryError):
        await text.send(
            BusinessId(uuid4()),
            {"phone": "+15555550100", "text": "details", "idempotency_key": "key-2"},
        )


@pytest.mark.asyncio
async def test_agent_outage_returns_the_unavailable_reply(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """A provider failure must not 500 the chat or lose the message."""

    await intake_business(session_factory)
    agent = IntakeAgentFake(error=IntakeAgentError("openai down"))
    application, _ = intake_app(session_factory, agent=agent)
    async with http_client(application) as client:
        created = await client.post(f"/v1/businesses/{PUBLIC_KEY}/intake/conversations")
        token = created.json()["conversationToken"]
        conversation_id = created.json()["conversationId"]
        headers = {"Authorization": f"Bearer {token}"}
        reply = await client.post(
            f"/v1/intake/conversations/{conversation_id}/messages",
            json={"message": "I need an inspection"},
            headers=headers,
        )
        assert reply.status_code == 200
        assert reply.json()["reply"] == UNAVAILABLE_REPLY
        assert reply.json()["state"] == "collecting"
        view = await client.get(f"/v1/intake/conversations/{conversation_id}", headers=headers)
        contents = [m["content"] for m in view.json()["messages"]]
        assert "I need an inspection" in contents


@pytest.mark.asyncio
async def test_availability_outage_keeps_the_conversation_collecting(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await intake_business(session_factory)
    availability = AvailabilityFake(slot_error=AvailabilityError("calendly down"))
    agent = IntakeAgentFake(
        [
            collected_turn(name="Jane", email=EMAIL, address="2 Elm St", problem="ants"),
            IntakeTurn(reply="Here is what is open.", ready_for_slots=True),
        ]
    )
    application, _ = intake_app(session_factory, agent=agent, availability=availability)
    async with http_client(application) as client:
        created = await client.post(f"/v1/businesses/{PUBLIC_KEY}/intake/conversations")
        token = created.json()["conversationToken"]
        conversation_id = created.json()["conversationId"]
        headers = {"Authorization": f"Bearer {token}"}
        await client.post(
            f"/v1/intake/conversations/{conversation_id}/messages",
            json={"message": "ants"},
            headers=headers,
        )
        reply = await client.post(
            f"/v1/intake/conversations/{conversation_id}/messages",
            json={"message": "when?"},
            headers=headers,
        )
        assert reply.status_code == 200
        assert reply.json()["reply"] == NO_AVAILABILITY_REPLY
        assert reply.json()["state"] == "collecting"


def test_pick_offer_slots_anchors_the_day_window_on_the_slot_timezone() -> None:
    pacific = ZoneInfo("America/Los_Angeles")
    # Sunday 6 PM Pacific is already Monday 2 AM UTC: the business's Monday
    # must still be inside the offer window.
    now = datetime(2026, 1, 12, 2, 0, tzinfo=UTC)
    start = datetime(2026, 1, 12, 9, 0, tzinfo=pacific)
    offered = pick_offer_slots(
        (AvailableSlot(start=start, end=start + timedelta(hours=1)),), now=now
    )
    assert offered, "the business-local Monday is lost when anchored on UTC"
    assert offered[0].start == start
