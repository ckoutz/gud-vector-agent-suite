"""Website intake conversations: the AI booking agent's domain model.

A customer chats on the business's site; the agent collects the request,
offers real calendar availability and — only when the owner approves — the
booking is arranged. Conversations carry their own bearer credential (the
``conversationToken``), stored hashed like portal sessions, and are always
scoped to a single business: ``(business_id, id)`` is the tenant boundary
every repository and every message row honours.

Nothing here books anything: state moves to ``awaiting_owner`` when the
customer picks a slot and only owner decisions change it from there.
"""

import re
from collections.abc import Mapping
from datetime import date, datetime, timedelta
from enum import StrEnum
from typing import Protocol
from uuid import UUID, uuid4, uuid5

from pydantic import BaseModel, ConfigDict, Field, field_validator

from gvas.domain.customers import hash_portal_token, new_portal_token, portal_token_matches
from gvas.domain.identifiers import (
    BusinessId,
    CustomerId,
    IntakeConversationId,
    IntakeMessageId,
    OutboxCommandId,
    WorkflowIntent,
)
from gvas.domain.outbox import OutboxCommand

BOOKING_INTENT = WorkflowIntent("booking_decision")
INTAKE_CHANNEL_WEB = "web"
INTAKE_CONVERSATION_TTL = timedelta(hours=24)
INTAKE_MESSAGE_MAX_CHARS = 2000
INTAKE_MAX_USER_MESSAGES = 30
SERVICE_REQUEST_SOURCE_INTAKE = "intake"
SLOT_OFFER_LIMIT = 5
SLOT_OFFER_BUSINESS_DAYS = 7
DEFAULT_SLOT_MINUTES = 60

INTAKE_BOOKING_ARRANGE_COMMAND_TYPE = "intake_booking.arrange"
INTAKE_BOOKING_ARRANGE_COMMAND_NAMESPACE = UUID("8c1f7a2e-4b63-4d59-a6e2-9f0c1d2b3a47")
INTAKE_CUSTOMER_EMAIL_COMMAND_TYPE = "intake_customer.email"
INTAKE_CUSTOMER_EMAIL_COMMAND_NAMESPACE = UUID("3a9d1c5f-7e24-4b18-9c36-2e5f8a1d4b60")
INTAKE_CUSTOMER_TEXT_COMMAND_TYPE = "intake_customer.text"
INTAKE_CUSTOMER_TEXT_COMMAND_NAMESPACE = UUID("5f2b8d1a-9c47-4e63-b1d5-8a3f6c2e9d15")

DECLINE_REASON_MAX_CHARS = 200

PRICE_GUARD_REPLY = (
    "I can't discuss pricing — the owner will confirm pricing when they review your request."
)

_CURRENCY_AMOUNT = re.compile(
    r"(?:[$€£]\s*\d|\b\d+(?:\.\d{1,2})?\s*(?:usd|dollars?|euros?|bucks)\b)", re.IGNORECASE
)
_SLOT_PREFIX = re.compile(r"^slot:(?P<start>\S+)\s*$", re.IGNORECASE)
_BOOKING_COMMAND = re.compile(
    r"^\s*(?P<action>approve|decline)\s+booking\s+(?P<reference>[0-9a-zA-Z-]{4,32})"
    r"(?:\s+(?P<reason>.+?))?\s*$",
    re.IGNORECASE | re.DOTALL,
)


class IntakeModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", populate_by_name=True)


class IntakeState(StrEnum):
    COLLECTING = "collecting"
    PROPOSING_SLOTS = "proposing_slots"
    AWAITING_OWNER = "awaiting_owner"
    APPROVED = "approved"
    DECLINED = "declined"
    CLOSED = "closed"


class IntakeMessageRole(StrEnum):
    USER = "user"
    AGENT = "agent"
    OWNER = "owner"


TERMINAL_INTAKE_STATES = frozenset({IntakeState.APPROVED, IntakeState.DECLINED, IntakeState.CLOSED})


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("intake timestamps must be timezone-aware")
    return value


class IntakeCollected(IntakeModel):
    """What the agent has gathered so far; ``None`` means not yet asked."""

    name: str | None = Field(default=None, max_length=200)
    email: str | None = Field(default=None, max_length=320)
    phone: str | None = Field(default=None, max_length=64)
    address: str | None = Field(default=None, max_length=500)
    problem: str | None = Field(default=None, max_length=2000)
    property_type: str | None = Field(default=None, alias="propertyType", max_length=200)
    urgency: str | None = Field(default=None, max_length=200)

    def merge(self, reported: "IntakeCollected") -> "IntakeCollected":
        """Fills blanks with newly reported values; never overwrites."""

        updates: dict[str, object] = {}
        for key in (
            "name",
            "email",
            "phone",
            "address",
            "problem",
            "property_type",
            "urgency",
        ):
            value = getattr(reported, key)
            if getattr(self, key) is None and value is not None and value.strip():
                updates[key] = value.strip()
        return self.model_copy(update=updates)

    @property
    def ready_for_slots(self) -> bool:
        """Contact details and the job description needed before scheduling."""

        return all(
            value is not None and value.strip()
            for value in (self.name, self.email, self.address, self.problem)
        )

    def as_stored(self) -> dict[str, object]:
        return self.model_dump(mode="json", by_alias=True)

    def summary(self) -> dict[str, object] | None:
        """The public summary projection: only once the request is complete."""

        if not self.ready_for_slots:
            return None
        return {
            "name": self.name,
            "email": self.email,
            "phone": self.phone,
            "address": self.address,
            "problem": self.problem,
        }


class AvailableSlot(IntakeModel):
    start: datetime
    end: datetime

    _aware_start = field_validator("start")(_aware)
    _aware_end = field_validator("end")(_aware)


class IntakeMessage(IntakeModel):
    message_id: IntakeMessageId
    conversation_id: IntakeConversationId
    business_id: BusinessId
    role: IntakeMessageRole
    content: str = Field(min_length=1, max_length=INTAKE_MESSAGE_MAX_CHARS * 2)
    created_at: datetime

    _aware_created = field_validator("created_at")(_aware)


class IntakeConversation(IntakeModel):
    conversation_id: IntakeConversationId
    business_id: BusinessId
    customer_id: CustomerId | None = None
    reference: str = Field(min_length=4, max_length=16)
    token_hash: str = Field(min_length=64, max_length=64)
    channel: str = INTAKE_CHANNEL_WEB
    state: IntakeState = IntakeState.COLLECTING
    collected: IntakeCollected = Field(default_factory=IntakeCollected)
    proposed_slots: tuple[AvailableSlot, ...] = ()
    requested_slot_start: datetime | None = None
    requested_slot_end: datetime | None = None
    booking_kind: str | None = None
    booking_link: str | None = None
    # Set before the provider call runs: a retried arrange command that sees
    # this reconciles the booking instead of booking twice.
    booking_attempted_at: datetime | None = None
    decision_reason: str | None = None
    decision_at: datetime | None = None
    owner_notified_at: datetime | None = None
    escalation_notified_at: datetime | None = None
    expires_at: datetime
    created_at: datetime
    updated_at: datetime

    _aware_expires = field_validator("expires_at")(_aware)
    _aware_created = field_validator("created_at")(_aware)
    _aware_updated = field_validator("updated_at")(_aware)

    def token_matches(self, raw_token: str) -> bool:
        return portal_token_matches(raw_token, self.token_hash)

    def is_live(self, now: datetime) -> bool:
        return now < self.expires_at and self.state not in TERMINAL_INTAKE_STATES

    def find_proposed_slot(self, start: datetime) -> AvailableSlot | None:
        for slot in self.proposed_slots:
            if slot.start == start:
                return slot
        return None

    def with_updates(self, now: datetime, **changes: object) -> "IntakeConversation":
        changes["updated_at"] = now
        return self.model_copy(update=changes)


class BookingDecisionAction(StrEnum):
    APPROVE = "approve"
    DECLINE = "decline"


class BookingDecision(IntakeModel):
    action: BookingDecisionAction
    reference: str = Field(min_length=4, max_length=32)
    reason: str | None = None


def booking_decision(text: str) -> BookingDecision | None:
    """``approve booking <ref>`` / ``decline booking <ref> [reason]``."""

    match = _BOOKING_COMMAND.match(text)
    if match is None:
        return None
    reason = match.group("reason")
    return BookingDecision(
        action=BookingDecisionAction(match.group("action").lower()),
        reference=match.group("reference").lower(),
        reason=sanitize_owner_reason(reason) if reason else None,
    )


def sanitize_owner_reason(reason: str) -> str:
    """The owner's decline note is shown to the customer: one line, no control
    characters, capped."""

    text = " ".join("".join(c if c.isprintable() else " " for c in reason).split())
    return text[:DECLINE_REASON_MAX_CHARS]


def slot_message_start(text: str) -> datetime | None:
    """``slot:<start>`` — the widget's deterministic pick."""

    match = _SLOT_PREFIX.match(text.strip())
    if match is None:
        return None
    try:
        value = datetime.fromisoformat(match.group("start"))
    except ValueError:
        return None
    if value.tzinfo is None or value.utcoffset() is None:
        return None
    return value


def contains_currency_amount(text: str) -> bool:
    return bool(_CURRENCY_AMOUNT.search(text))


def scrub_agent_reply(reply: str) -> str:
    """A reply that names a price is replaced wholesale: the owner confirms
    pricing, never the agent."""

    cleaned = " ".join(reply.split()).strip()
    if not cleaned:
        return "Could you tell me a bit more about what you need?"
    if contains_currency_amount(cleaned):
        return PRICE_GUARD_REPLY
    return cleaned


def new_conversation_token() -> str:
    return new_portal_token()


def conversation_token_hash(token: str) -> str:
    return hash_portal_token(token)


def new_reference() -> str:
    return uuid4().hex[:8]


def next_business_days(anchor: datetime, count: int) -> set[date]:
    """The dates of the next ``count`` Mon–Fri days in the anchor's timezone.

    Today's date is excluded so same-day slots are never offered.
    """

    days: set[date] = set()
    day = anchor.date()
    while len(days) < count:
        day += timedelta(days=1)
        if day.weekday() < 5:
            days.add(day)
    return days


def pick_offer_slots(
    slots: tuple[AvailableSlot, ...], *, now: datetime, limit: int = SLOT_OFFER_LIMIT
) -> tuple[AvailableSlot, ...]:
    """Up to ``limit`` slots spread over the next business days.

    Only slots inside the following ``SLOT_OFFER_BUSINESS_DAYS`` weekdays are
    considered; one slot per day is taken first, then days are revisited so a
    sparse week still yields up to ``limit`` options.
    """

    if not slots:
        return ()
    # Slot dates are business-local; the day window must be anchored in the
    # same zone or a business a day behind the server's clock loses the first
    # day entirely.
    anchor = now.astimezone(slots[0].start.tzinfo)
    allowed_days = next_business_days(anchor, SLOT_OFFER_BUSINESS_DAYS)
    by_day: dict[date, list[AvailableSlot]] = {}
    for slot in sorted(slots, key=lambda slot: slot.start):
        if slot.start <= now:
            continue
        if slot.start.date() not in allowed_days:
            continue
        by_day.setdefault(slot.start.date(), []).append(slot)
    picked: list[AvailableSlot] = []
    while len(picked) < limit:
        progressed = False
        for day in sorted(by_day):
            if by_day[day]:
                picked.append(by_day[day].pop(0))
                progressed = True
                if len(picked) >= limit:
                    break
        if not progressed:
            break
    return tuple(picked)


def format_slot_label(start: datetime) -> str:
    """``Tue Sep 15, 9:00 AM PDT`` — business-local, as the owner reads it."""

    hour = start.hour % 12 or 12
    meridiem = "AM" if start.hour < 12 else "PM"
    zone = start.strftime("%Z")
    label = f"{start:%a} {start:%b} {start.day}, {hour}:{start:%M} {meridiem}"
    return f"{label} {zone}".rstrip()


def booking_request_notice(
    conversation: IntakeConversation,
    *,
    business_name: str | None = None,
) -> str:
    """The owner notice posted when a customer picks a slot."""

    collected = conversation.collected
    name = collected.name or "A customer"
    address = collected.address or "address unknown"
    problem = collected.problem or "New request"
    requested = (
        format_slot_label(conversation.requested_slot_start)
        if conversation.requested_slot_start is not None
        else "no time picked"
    )
    ref = conversation.reference
    lines = [
        f"Booking request #{ref} — {name}, {address}. {problem}. Requested {requested}.",
        f"Reply `approve booking {ref}` or `decline booking {ref} <reason>`.",
    ]
    if business_name:
        lines.append(f"Business: {business_name}.")
    return "\n".join(lines)


def escalation_notice(conversation: IntakeConversation, summary: str) -> str:
    """The owner notice for ``needs_human`` turns: transcript summary only."""

    collected = conversation.collected
    details = ", ".join(
        part
        for part in (
            collected.name or "",
            collected.email or "",
            collected.problem or "",
        )
        if part
    )
    text = f"Chat {conversation.reference} needs you — {details or 'a customer asked for help'}."
    if summary.strip():
        text += f"\n{summary.strip()[:800]}"
    return text


def slot_confirmed_reply(slot: AvailableSlot) -> str:
    return (
        f"Great — I've requested {format_slot_label(slot.start)}. "
        "We'll confirm by email/text once the owner approves."
    )


def intake_booking_arrange_command(
    conversation: IntakeConversation,
) -> OutboxCommand:
    """Worker command that turns an approval into a calendar booking."""

    command_id = OutboxCommandId(
        uuid5(
            INTAKE_BOOKING_ARRANGE_COMMAND_NAMESPACE,
            f"{conversation.business_id}:{conversation.conversation_id}",
        )
    )
    return OutboxCommand(
        command_id=command_id,
        business_id=conversation.business_id,
        command_type=INTAKE_BOOKING_ARRANGE_COMMAND_TYPE,
        payload={"conversation_id": str(conversation.conversation_id)},
        dedup_key=f"intake_booking:{conversation.conversation_id}",
    )


class IntakeCustomerEmail(IntakeModel):
    """A plain customer-facing email the worker can send verbatim."""

    business_id: BusinessId
    to: str = Field(min_length=3)
    subject: str = Field(min_length=1)
    body: str = Field(min_length=1)
    idempotency_key: str = Field(min_length=1)


def intake_customer_email_command(email: IntakeCustomerEmail) -> OutboxCommand:
    command_id = OutboxCommandId(
        uuid5(INTAKE_CUSTOMER_EMAIL_COMMAND_NAMESPACE, email.idempotency_key)
    )
    return OutboxCommand(
        command_id=command_id,
        business_id=email.business_id,
        command_type=INTAKE_CUSTOMER_EMAIL_COMMAND_TYPE,
        payload={
            "to": email.to,
            "subject": email.subject,
            "body": email.body,
            "idempotency_key": email.idempotency_key,
        },
        dedup_key=f"intake_email:{email.idempotency_key}",
    )


def intake_customer_email_request(
    business_id: BusinessId, payload: Mapping[str, object]
) -> IntakeCustomerEmail:
    fields = {key: payload.get(key) for key in ("to", "subject", "body", "idempotency_key")}
    if not all(isinstance(value, str) and value for value in fields.values()):
        raise ValueError("intake email command payload is incomplete")
    return IntakeCustomerEmail(
        business_id=business_id,
        to=str(fields["to"]),
        subject=str(fields["subject"]),
        body=str(fields["body"]),
        idempotency_key=str(fields["idempotency_key"]),
    )


def intake_customer_text_command(
    business_id: BusinessId, *, phone: str, text: str, idempotency_key: str
) -> OutboxCommand:
    command_id = OutboxCommandId(uuid5(INTAKE_CUSTOMER_TEXT_COMMAND_NAMESPACE, idempotency_key))
    return OutboxCommand(
        command_id=command_id,
        business_id=business_id,
        command_type=INTAKE_CUSTOMER_TEXT_COMMAND_TYPE,
        payload={"phone": phone, "text": text, "idempotency_key": idempotency_key},
        dedup_key=f"intake_text:{idempotency_key}",
    )


class IntakeConversationRepository(Protocol):
    async def add(self, conversation: IntakeConversation) -> None: ...

    async def get(
        self, business_id: BusinessId, conversation_id: IntakeConversationId
    ) -> IntakeConversation | None: ...

    async def find_by_reference(
        self, business_id: BusinessId, reference: str
    ) -> IntakeConversation | None: ...

    async def lock(
        self, business_id: BusinessId, conversation_id: IntakeConversationId
    ) -> IntakeConversation | None:
        """Row-level lock for the turn being written: concurrent customer
        posts serialize instead of overwriting each other's snapshot."""
        ...

    async def lock_by_reference(
        self, business_id: BusinessId, reference: str
    ) -> IntakeConversation | None:
        """Row-level lock by owner-facing reference: concurrent approve and
        decline decisions serialize so only one reaches a terminal state."""
        ...

    async def find_by_token(
        self, conversation_id: IntakeConversationId, token_hash: str
    ) -> IntakeConversation | None:
        """Token-as-credential lookup: the bearer proves tenancy, so no
        business id is supplied or assumed here."""
        ...

    async def save(self, conversation: IntakeConversation) -> None: ...

    async def count_created_since(self, business_id: BusinessId, since: datetime) -> int: ...


class IntakeMessageRepository(Protocol):
    async def add(self, message: IntakeMessage) -> None: ...

    async def list_for(
        self, business_id: BusinessId, conversation_id: IntakeConversationId
    ) -> tuple[IntakeMessage, ...]: ...

    async def count_user(
        self, business_id: BusinessId, conversation_id: IntakeConversationId
    ) -> int: ...


class IntakeTurnRequest(IntakeModel):
    """One agent step: the transcript, collected state and (when proposing) the
    slots the customer may pick from."""

    business_id: BusinessId
    conversation_id: IntakeConversationId
    business_name: str = Field(min_length=1)
    transcript: tuple[IntakeMessage, ...] = ()
    collected: IntakeCollected = Field(default_factory=IntakeCollected)
    offered_slots: tuple[AvailableSlot, ...] = ()
    # Portal-linked customers already identified themselves; the agent skips
    # name/email/phone questions and only asks about the new service.
    known_customer: bool = False


class IntakeTurn(IntakeModel):
    reply: str = Field(min_length=1)
    collected: IntakeCollected = Field(default_factory=IntakeCollected)
    ready_for_slots: bool = False
    chosen_slot: datetime | None = None
    needs_human: bool = False
    # Short transcript summary for the escalation notice.
    summary: str = ""


class IntakeAgentError(RuntimeError):
    """The model could not be reached or read; sanitized, like the other
    provider errors — no key, no raw response."""


class AvailabilityError(RuntimeError):
    """The calendar provider could not be reached or read; sanitized — no
    token, no raw response."""


class BookingKind(StrEnum):
    BOOKED = "booked"
    LINK = "link"


class BookingRequest(IntakeModel):
    business_id: BusinessId
    slot_start: datetime
    slot_end: datetime
    invitee_name: str = Field(min_length=1)
    invitee_email: str = Field(min_length=3)
    invitee_phone: str | None = None
    address: str | None = None
    details: str | None = None

    _aware_start = field_validator("slot_start")(_aware)
    _aware_end = field_validator("slot_end")(_aware)


class BookingResult(IntakeModel):
    kind: BookingKind
    link: str | None = None
