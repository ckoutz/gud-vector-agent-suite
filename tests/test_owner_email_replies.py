"""Owner replies to the notification e-mail: the Resend webhook adapter
feeding the channel-agnostic understanding step, ending in decide_booking."""

import base64
import hashlib
import hmac
import json
import time

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from composition_fakes import FAKE_NOW
from gvas.composition import Application
from gvas.config import IntakeSettings
from gvas.domain.enums import DeliveryStatus
from gvas.domain.identifiers import BusinessId, IntakeConversationId
from gvas.domain.intake import BookingKind, BookingResult
from gvas.domain.messages import DeliveryReceipt, TextPart
from gvas.domain.owner_email import (
    OWNER_EMAIL_REPLY_COMMAND_TYPE,
    OwnerEmailRequest,
    owner_reply_thread,
    owner_reply_token,
    parse_owner_reply_token,
)
from gvas.infrastructure.resend import ResendReceivedEmail
from gvas.infrastructure.resend_inbound import ResendReceivingIngress, SvixWebhookVerifier
from gvas.interfaces.http.app import create_app
from gvas.interfaces.http.resend import RESEND_WEBHOOK_PATH, create_resend_webhook_router
from test_intake_booking import AvailabilityFake, commands_of, conversation_row
from test_intake_reschedule import DECISION_ORIGIN, LINK_SIGNING_KEY, drive
from test_pilot_runtime import immediate_worker

REPLY_DOMAIN = "reply.gvas.example.test"
OWNER = "owner@example.com"
WEBHOOK_KEY = b"resend-webhook-test-key"
WEBHOOK_SECRET = "whsec_" + base64.b64encode(WEBHOOK_KEY).decode()
SETTINGS = IntakeSettings(
    max_conversations_per_day=0,
    decision_link_secret=LINK_SIGNING_KEY,
    decision_link_base_url=DECISION_ORIGIN,
    owner_reply_domain=REPLY_DOMAIN,
)
PASS = {"spf": "pass", "dkim": "pass", "dmarc": "pass"}


class OwnerEmailFake:
    def __init__(self) -> None:
        self.sent: list[OwnerEmailRequest] = []

    async def send(self, request: OwnerEmailRequest) -> DeliveryReceipt:
        self.sent.append(request)
        return DeliveryReceipt(
            status=DeliveryStatus.ACCEPTED,
            provider_message_id=f"email-{len(self.sent)}",
            occurred_at=FAKE_NOW,
        )


class ReceivedEmailsFake:
    def __init__(self) -> None:
        self.emails: dict[str, ResendReceivedEmail] = {}

    def add(self, email_id: str, **fields: object) -> None:
        self.emails[email_id] = ResendReceivedEmail.model_validate(
            {"id": email_id, "authentication": PASS, **fields}
        )

    async def get(self, email_id: str) -> ResendReceivedEmail:
        return self.emails[email_id]


def signed_headers(body: bytes, svix_id: str) -> dict[str, str]:
    timestamp = str(int(time.time()))
    digest = hmac.new(WEBHOOK_KEY, f"{svix_id}.{timestamp}.".encode() + body, hashlib.sha256)
    return {
        "svix-id": svix_id,
        "svix-timestamp": timestamp,
        "svix-signature": f"v1,{base64.b64encode(digest.digest()).decode()}",
        "content-type": "application/json",
    }


def webhook_client(application: Application, emails: ReceivedEmailsFake) -> httpx.AsyncClient:
    assert application.owner_email_replies is not None
    ingress = ResendReceivingIngress(
        SvixWebhookVerifier(WEBHOOK_SECRET), emails, application.owner_email_replies
    )

    async def origins() -> frozenset[str]:
        return frozenset()

    app = create_app(routers=(create_resend_webhook_router(ingress),), cors_origins=origins)
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


async def deliver(
    application: Application, emails: ReceivedEmailsFake, email_id: str, svix_id: str
) -> httpx.Response:
    body = json.dumps(
        {
            "type": "email.received",
            "created_at": "2026-10-05T21:00:00Z",
            "data": {"email_id": email_id},
        }
    ).encode()
    async with webhook_client(application, emails) as client:
        return await client.post(
            RESEND_WEBHOOK_PATH, content=body, headers=signed_headers(body, svix_id)
        )


async def drain(application: Application) -> None:
    for _ in range(8):
        await immediate_worker(application).drain()


async def booking_notice(
    session_factory: async_sessionmaker[AsyncSession], availability: AvailabilityFake
) -> tuple[Application, list[OwnerEmailRequest], OwnerEmailFake, BusinessId, str, object]:
    sender = OwnerEmailFake()
    application, owner, business_id, reference, _cid, _token = await drive(
        session_factory,
        availability=availability,
        intake_settings=SETTINGS,
        notification_email=OWNER,
        owner_email=sender,
    )
    notices = [request for request in sender.sent if request.reply_to]
    assert len(notices) == 1
    return application, notices, sender, business_id, reference, owner


@pytest.mark.asyncio
async def test_booking_notice_is_multipart_with_reply_routing(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    application, notices, _s, _b, reference, _o = await booking_notice(
        session_factory, AvailabilityFake()
    )
    notice = notices[0]
    assert notice.to == OWNER
    assert notice.html is not None and ">Approve</a>" in notice.html
    assert ">Decline</a>" in notice.html
    assert notice.reply_to is not None
    assert notice.reply_to.startswith(f"owner+{reference}-")
    assert notice.reply_to.endswith(f"@{REPLY_DOMAIN}")
    assert f"<{notice.reply_to}>" in notice.references
    assert LINK_SIGNING_KEY not in notice.html and LINK_SIGNING_KEY not in notice.text
    assert "Approve: " in notice.text


@pytest.mark.asyncio
async def test_natural_reply_approves_through_the_shared_transition(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    availability = AvailabilityFake(result=BookingResult(kind=BookingKind.BOOKED))
    application, notices, sender, business_id, reference, owner = await booking_notice(
        session_factory, availability
    )
    emails = ReceivedEmailsFake()
    emails.add(
        "rcv-1",
        **{
            "from": "Owner <Owner@Example.com>",
            "to": [notices[0].reply_to],
            "subject": f"Re: {notices[0].subject}",
            "text": "Okay, book it!\n\nOn Mon, Oct 5, 2026 at 2:52 PM Test Co wrote:\n> old",
            "message_id": "<m1@mail.example.com>",
        },
    )
    response = await deliver(application, emails, "rcv-1", "msg_1")
    assert response.status_code == 200 and response.json()["status"] == "accepted"
    replay = await deliver(application, emails, "rcv-1", "msg_1")
    assert replay.json()["status"] == "duplicate"
    await drain(application)

    row = await conversation_row(session_factory, business_id)
    assert row.state == "approved"
    assert availability.book_calls, "the reply ran the same approve path"
    assert len(await commands_of(session_factory, business_id, OWNER_EMAIL_REPLY_COMMAND_TYPE)) == 1
    approved = [
        (ref.external_conversation_id, part.text)
        for ref, message in owner.sent  # type: ignore[attr-defined]
        for part in message.parts
        if isinstance(part, TextPart) and part.text.startswith("Approved booking")
    ]
    threads = {thread for thread, _ in approved}
    assert "email:rcv-1" in threads, "confirmation goes back by e-mail"
    assert len(threads) == 2, "and the usual owner-thread update is posted"
    del sender


@pytest.mark.asyncio
async def test_decline_with_reason_via_references_fallback(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    availability = AvailabilityFake()
    application, notices, _s, business_id, _ref, _o = await booking_notice(
        session_factory, availability
    )
    emails = ReceivedEmailsFake()
    emails.add(
        "rcv-2",
        **{
            "from": OWNER,
            "to": ["info@gudvector.example"],
            "subject": "Re: request",
            "text": "Can't make it, out of town that week",
            "headers": {"references": f"<{notices[0].reply_to}> <x@resend>"},
        },
    )
    assert (await deliver(application, emails, "rcv-2", "msg_2")).json()["status"] == "accepted"
    await drain(application)
    row = await conversation_row(session_factory, business_id)
    assert row.state == "declined"
    assert availability.book_calls == []


@pytest.mark.asyncio
async def test_unclear_reply_asks_and_never_acts(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    application, notices, _s, business_id, reference, owner = await booking_notice(
        session_factory, AvailabilityFake()
    )
    emails = ReceivedEmailsFake()
    emails.add(
        "rcv-3",
        **{"from": OWNER, "to": [notices[0].reply_to], "text": "maybe, let me check my van"},
    )
    await deliver(application, emails, "rcv-3", "msg_3")
    await drain(application)
    row = await conversation_row(session_factory, business_id)
    assert row.state == "awaiting_owner"
    questions = [
        part.text
        for ref, message in owner.sent  # type: ignore[attr-defined]
        if ref.external_conversation_id == "email:rcv-3"
        for part in message.parts
        if isinstance(part, TextPart)
    ]
    assert questions and '"approve"' in questions[0] and reference in questions[0]


@pytest.mark.asyncio
async def test_other_senders_and_forged_events_are_dropped(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    application, notices, _s, business_id, _ref, _o = await booking_notice(
        session_factory, AvailabilityFake()
    )
    emails = ReceivedEmailsFake()
    emails.add(
        "rcv-4", **{"from": "mallory@evil.example", "to": [notices[0].reply_to], "text": "yes"}
    )
    emails.add(
        "rcv-5",
        **{"from": OWNER, "to": [notices[0].reply_to], "text": "yes"},
        authentication={"spf": "fail", "dkim": "fail", "dmarc": "fail"},
    )
    assert (await deliver(application, emails, "rcv-4", "msg_4")).json()["status"] == "rejected"
    assert (await deliver(application, emails, "rcv-5", "msg_5")).json()["status"] == "rejected"
    body = b'{"type":"email.received","data":{"email_id":"rcv-4"}}'
    async with webhook_client(application, emails) as client:
        headers = signed_headers(body, "msg_6")
        headers["svix-signature"] = "v1,AAAA"
        forged = await client.post(RESEND_WEBHOOK_PATH, content=body, headers=headers)
    assert forged.status_code == 401
    assert await commands_of(session_factory, business_id, OWNER_EMAIL_REPLY_COMMAND_TYPE) == []
    row = await conversation_row(session_factory, business_id)
    assert row.state == "awaiting_owner"


@pytest.mark.asyncio
async def test_reply_to_an_older_request_epoch_is_refused(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    availability = AvailabilityFake()
    application, notices, _s, business_id, reference, owner = await booking_notice(
        session_factory, availability
    )
    assert notices[0].reply_to is not None
    current = parse_owner_reply_token(notices[0].reply_to.split("+", 1)[1].split("@", 1)[0])
    assert current is not None
    row = await conversation_row(session_factory, business_id)
    stale = owner_reply_thread(
        owner_reply_token(
            LINK_SIGNING_KEY,
            business_id=business_id,
            conversation_id=IntakeConversationId(row.id),
            reference=reference,
            request_epoch=current.request_epoch - 3600,
        ),
        REPLY_DOMAIN,
    )
    emails = ReceivedEmailsFake()
    emails.add("rcv-7", **{"from": OWNER, "to": [stale.reply_to], "text": "approve"})
    assert (await deliver(application, emails, "rcv-7", "msg_7")).json()["status"] == "accepted"
    await drain(application)
    row = await conversation_row(session_factory, business_id)
    assert row.state == "awaiting_owner"
    assert availability.book_calls == []
    texts = [
        part.text
        for ref, message in owner.sent  # type: ignore[attr-defined]
        if ref.external_conversation_id == "email:rcv-7"
        for part in message.parts
        if isinstance(part, TextPart)
    ]
    assert texts and "changed since" in texts[0]
