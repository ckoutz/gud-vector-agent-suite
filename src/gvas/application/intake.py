"""Website booking intake: start a chat, collect the request, offer real
availability and route the pick to the owner.

The hard rule: nothing is booked until the owner replies ``approve booking
<ref>``. The service collects, proposes, and notifies — the booking itself is
an outbox command so provider calls stay retryable and never run inside the
request that saved the approval.
"""

import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from gvas.domain.customer_linking import enqueue_intake_owner_notice
from gvas.domain.customers import CustomerRecord, ServiceRequest
from gvas.domain.enums import DeliveryStatus, RecipientAddressKind, WorkflowRunStatus
from gvas.domain.identifiers import (
    BusinessId,
    IntakeConversationId,
    IntakeMessageId,
    ServiceRequestId,
)
from gvas.domain.intake import (
    BOOKING_INTENT,
    INTAKE_CHANNEL_WEB,
    INTAKE_CONVERSATION_TTL,
    INTAKE_MAX_USER_MESSAGES,
    INTAKE_MESSAGE_MAX_CHARS,
    SERVICE_REQUEST_SOURCE_INTAKE,
    AvailabilityError,
    AvailableSlot,
    BookingDecision,
    BookingDecisionAction,
    BookingKind,
    BookingRequest,
    IntakeAgentError,
    IntakeCollected,
    IntakeConversation,
    IntakeCustomerEmail,
    IntakeMessage,
    IntakeMessageRole,
    IntakeState,
    IntakeTurnRequest,
    booking_decision,
    booking_request_notice,
    conversation_token_hash,
    escalation_notice,
    format_slot_label,
    intake_booking_arrange_command,
    intake_customer_email_command,
    intake_customer_email_request,
    intake_customer_text_command,
    new_conversation_token,
    new_reference,
    pick_offer_slots,
    scrub_agent_reply,
    slot_confirmed_reply,
    slot_message_start,
)
from gvas.domain.messages import (
    CustomerDeliveryRequest,
    CustomerRecipient,
    CustomerTextRequest,
    NormalizedOwnerMessage,
    OutboundOwnerMessage,
    TextPart,
)
from gvas.domain.ports import (
    AvailabilityPort,
    CustomerQuoteDeliveryPort,
    CustomerTextDeliveryPort,
    IntakeAgentPort,
)
from gvas.domain.repositories import BusinessRecord, UnitOfWork
from gvas.domain.usage import UsageCeilingGuard, UsageKind
from gvas.domain.workflows import WorkflowContext, WorkflowResult

logger = logging.getLogger(__name__)

UnitOfWorkFactory = Callable[[], UnitOfWork]
SLOT_LOOKAHEAD_DAYS = 14

MESSAGE_LIMIT_REPLY = (
    "This conversation has reached its message limit — the owner will follow up with you directly."
)
UNAVAILABLE_REPLY = "I'm having trouble right now — the owner will follow up with you shortly."
NO_AVAILABILITY_REPLY = (
    "I don't have an opening to offer just now — the owner will confirm a "
    "time when they review your request."
)
SLOTS_OFFER_REPLY = "Here are the next openings I can offer — pick whichever works for you:"
SLOT_NOT_OFFERED_REPLY = "That time isn't one I can offer — please pick one of the listed times."
ESCALATION_REPLY = "Let me bring the owner in on this — they'll follow up with you directly."
OPENING_REPLY = (
    "Hi! I can help you book an inspection or estimate. What's going on, and where is the property?"
)
PORTAL_OPENING_REPLY = "Welcome back! What do you need this time, and where is the property?"


class IntakeError(ValueError):
    """Base for intake failures already safe to surface."""


class IntakeNotFoundError(IntakeError):
    """Unknown business or conversation."""


class IntakeAuthenticationError(IntakeError):
    """Missing, wrong or expired conversation token."""


class IntakeClosedError(IntakeError):
    """The conversation is decided, expired or over its message cap."""


class IntakeLimitError(IntakeError):
    """The business is over its daily intake cap."""


class IntakeAvailabilityError(IntakeError):
    """Availability could not be arranged; commands that see this retry."""


class IntakeDeliveryError(RuntimeError):
    """A customer email or text was rejected; the outbox retries."""


@dataclass(frozen=True)
class IntakeStart:
    """What a create-conversation response projects from."""

    conversation: IntakeConversation
    token: str
    reply: str


@dataclass(frozen=True)
class IntakeReply:
    conversation: IntakeConversation
    reply: str
    slots: tuple[AvailableSlot, ...]


class IntakeService:
    def __init__(
        self,
        unit_of_work_factory: UnitOfWorkFactory,
        *,
        agent: IntakeAgentPort,
        availability: AvailabilityPort | None = None,
        ceiling: UsageCeilingGuard | None = None,
        max_conversations_per_day: int = 50,
        max_user_messages: int = INTAKE_MAX_USER_MESSAGES,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._unit_of_work_factory = unit_of_work_factory
        self._agent = agent
        self._availability = availability
        self._ceiling = ceiling or UsageCeilingGuard()
        self._max_conversations_per_day = max_conversations_per_day
        self._max_user_messages = max_user_messages
        self._now = now

    async def start_conversation(self, public_key: str) -> IntakeStart:
        async with self._unit_of_work_factory() as unit_of_work:
            business = await unit_of_work.businesses.get_by_public_key(public_key)
            if business is None:
                raise IntakeNotFoundError("unknown business")
            await self._check_daily_cap(unit_of_work, business)
            return await self._open(unit_of_work, business, customer=None)

    async def start_portal_conversation(
        self, business: BusinessRecord, customer: CustomerRecord
    ) -> IntakeStart:
        """Portal-authenticated start: the customer is already identified, so
        the collected record comes pre-filled and identity questions are
        skipped."""

        async with self._unit_of_work_factory() as unit_of_work:
            await self._check_daily_cap(unit_of_work, business)
            return await self._open(unit_of_work, business, customer=customer)

    async def _check_daily_cap(self, unit_of_work: UnitOfWork, business: BusinessRecord) -> None:
        if self._max_conversations_per_day <= 0:
            return
        # Locking the business row serializes concurrent starts so two
        # requests cannot both pass the count before either inserts.
        await unit_of_work.businesses.lock(business.business_id)
        now = self._now()
        day_start = now.astimezone(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
        count = await unit_of_work.intake_conversations.count_created_since(
            business.business_id, day_start
        )
        if count >= self._max_conversations_per_day:
            raise IntakeLimitError("this business is not taking new requests today")

    async def _open(
        self,
        unit_of_work: UnitOfWork,
        business: BusinessRecord,
        *,
        customer: CustomerRecord | None,
    ) -> IntakeStart:
        customer_record = customer
        now = self._now()
        token = new_conversation_token()
        collected = IntakeCollected()
        if customer_record is not None:
            collected = IntakeCollected(
                name=customer_record.display_name,
                email=customer_record.email,
                phone=customer_record.phone,
            )
        reply = PORTAL_OPENING_REPLY if customer_record is not None else OPENING_REPLY
        conversation = IntakeConversation(
            conversation_id=IntakeConversationId(uuid4()),
            business_id=business.business_id,
            customer_id=None if customer_record is None else customer_record.customer_id,
            reference=new_reference(),
            token_hash=conversation_token_hash(token),
            channel=INTAKE_CHANNEL_WEB,
            collected=collected,
            expires_at=now + INTAKE_CONVERSATION_TTL,
            created_at=now,
            updated_at=now,
        )
        await unit_of_work.intake_conversations.add(conversation)
        await self._append(unit_of_work, conversation, IntakeMessageRole.AGENT, reply, now)
        await unit_of_work.commit()
        return IntakeStart(conversation=conversation, token=token, reply=reply)

    async def authenticate(
        self, conversation_id: IntakeConversationId, raw_token: str
    ) -> IntakeConversation:
        """The bearer token is the credential: a conversation answers only to
        the token minted for it, so a token from one business can never read
        another's chat."""

        token_hash = conversation_token_hash(raw_token)
        async with self._unit_of_work_factory() as unit_of_work:
            conversation = await unit_of_work.intake_conversations.find_by_token(
                conversation_id, token_hash
            )
        if conversation is None or self._now() >= conversation.expires_at:
            raise IntakeAuthenticationError("invalid or expired conversation token")
        return conversation

    async def get_view(self, conversation: IntakeConversation) -> tuple[IntakeMessage, ...]:
        async with self._unit_of_work_factory() as unit_of_work:
            return await unit_of_work.intake_messages.list_for(
                conversation.business_id, conversation.conversation_id
            )

    async def post_message(self, conversation: IntakeConversation, text: str) -> IntakeReply:
        """One customer turn: persist, decide, persist the agent reply.

        The deterministic checks (closed, cap, slot picks) all run before the
        model is asked anything; the model never sees a request it cannot
        answer and its reply is scrubbed before it is stored.
        """

        content = text.strip()
        if not content or len(content) > INTAKE_MESSAGE_MAX_CHARS:
            raise IntakeError("message must be between 1 and 2000 characters")
        now = self._now()
        if not conversation.is_live(now):
            raise IntakeClosedError("this conversation is closed")

        async with self._unit_of_work_factory() as unit_of_work:
            # The row lock makes the whole turn read-modify-write on one
            # snapshot: concurrent posts serialize instead of losing each
            # other's collected fields or state transitions.
            locked = await unit_of_work.intake_conversations.lock(
                conversation.business_id, conversation.conversation_id
            )
            if locked is None or not locked.is_live(now):
                raise IntakeClosedError("this conversation is closed")
            conversation = locked
            await self._append(unit_of_work, conversation, IntakeMessageRole.USER, content, now)
            user_count = await unit_of_work.intake_messages.count_user(
                conversation.business_id, conversation.conversation_id
            )
            if 0 < self._max_user_messages < user_count:
                # Terminal: without a closed state every further post would
                # still append transcript rows forever.
                closed = conversation.with_updates(now, state=IntakeState.CLOSED)
                await unit_of_work.intake_conversations.save(closed)
                reply = await self._reply(unit_of_work, closed, MESSAGE_LIMIT_REPLY, now)
                await unit_of_work.commit()
                return IntakeReply(closed, reply, closed.proposed_slots)

            result = await self._step(unit_of_work, conversation, content, now)
            await unit_of_work.commit()
            return result

    async def _append(
        self,
        unit_of_work: UnitOfWork,
        conversation: IntakeConversation,
        role: IntakeMessageRole,
        content: str,
        now: datetime,
    ) -> None:
        await unit_of_work.intake_messages.add(
            IntakeMessage(
                message_id=IntakeMessageId(uuid4()),
                conversation_id=conversation.conversation_id,
                business_id=conversation.business_id,
                role=role,
                content=content,
                created_at=now,
            )
        )

    async def _reply(
        self,
        unit_of_work: UnitOfWork,
        conversation: IntakeConversation,
        reply: str,
        now: datetime,
    ) -> str:
        await self._append(unit_of_work, conversation, IntakeMessageRole.AGENT, reply, now)
        return reply

    async def _step(
        self,
        unit_of_work: UnitOfWork,
        conversation: IntakeConversation,
        content: str,
        now: datetime,
    ) -> IntakeReply:
        picked = slot_message_start(content)
        if picked is not None:
            return await self._handle_slot_pick(
                unit_of_work, conversation, picked, now, explicit=True
            )
        if conversation.state is IntakeState.AWAITING_OWNER:
            reply = await self._reply(
                unit_of_work,
                conversation,
                "The owner is reviewing your requested time — we'll confirm "
                "by email/text once approved.",
                now,
            )
            return IntakeReply(conversation, reply, conversation.proposed_slots)

        if await self._ceiling.is_reached(
            conversation.business_id, UsageKind.REVIEW_TOKENS, now=now
        ):
            reply = await self._reply(unit_of_work, conversation, UNAVAILABLE_REPLY, now)
            return IntakeReply(conversation, reply, ())

        transcript = await unit_of_work.intake_messages.list_for(
            conversation.business_id, conversation.conversation_id
        )
        business = await self._business(unit_of_work, conversation.business_id)
        try:
            turn = await self._agent.turn(
                IntakeTurnRequest(
                    business_id=conversation.business_id,
                    conversation_id=conversation.conversation_id,
                    business_name=business.display_name or business.name,
                    transcript=transcript,
                    collected=conversation.collected,
                    offered_slots=conversation.proposed_slots,
                    known_customer=conversation.customer_id is not None,
                )
            )
        except (IntakeAgentError, AvailabilityError) as error:
            # A provider outage must not 500 the chat: the customer's message
            # is already persisted, so answer with the sanitized fallback and
            # leave the conversation live for the next message.
            logger.warning("intake agent unavailable for %s: %s", conversation.reference, error)
            reply = await self._reply(unit_of_work, conversation, UNAVAILABLE_REPLY, now)
            return IntakeReply(conversation, reply, conversation.proposed_slots)
        collected = conversation.collected.merge(turn.collected)
        current = conversation.with_updates(now, collected=collected)
        await unit_of_work.intake_conversations.save(current)

        if turn.needs_human:
            reply_text = f"{scrub_agent_reply(turn.reply)} {ESCALATION_REPLY}"
            await self._reply(unit_of_work, current, reply_text, now)
            if current.escalation_notified_at is None:
                notified = await enqueue_intake_owner_notice(
                    unit_of_work,
                    current.business_id,
                    correlation_id=f"intake_escalation:{current.conversation_id}",
                    text=escalation_notice(current, turn.summary),
                )
                if notified:
                    current = current.with_updates(now, escalation_notified_at=now)
                    await unit_of_work.intake_conversations.save(current)
            return IntakeReply(current, reply_text, current.proposed_slots)

        if turn.chosen_slot is not None and current.state is IntakeState.PROPOSING_SLOTS:
            return await self._handle_slot_pick(
                unit_of_work, current, turn.chosen_slot, now, explicit=False
            )

        if turn.ready_for_slots and current.collected.ready_for_slots:
            if current.state is IntakeState.PROPOSING_SLOTS:
                # A re-offer while slots are on the table: the customer can
                # still pick, just show them again.
                pass
            else:
                offered = await self._offer_slots(unit_of_work, current, now)
                if offered is None:
                    reply = await self._reply(unit_of_work, current, NO_AVAILABILITY_REPLY, now)
                    return IntakeReply(current, reply, ())
                current = offered
                await self._reply(unit_of_work, current, SLOTS_OFFER_REPLY, now)
                return IntakeReply(current, SLOTS_OFFER_REPLY, current.proposed_slots)

        reply_text = scrub_agent_reply(turn.reply)
        await self._reply(unit_of_work, current, reply_text, now)
        return IntakeReply(current, reply_text, current.proposed_slots)

    async def _offer_slots(
        self,
        unit_of_work: UnitOfWork,
        conversation: IntakeConversation,
        now: datetime,
    ) -> IntakeConversation | None:
        if self._availability is None:
            return None
        start = now
        end = now + timedelta(days=SLOT_LOOKAHEAD_DAYS)
        try:
            openings = await self._availability.available_slots(
                conversation.business_id, start, end
            )
        except AvailabilityError as error:
            logger.warning("availability lookup failed for %s: %s", conversation.reference, error)
            return None
        offered = pick_offer_slots(tuple(openings), now=now)
        if not offered:
            return None
        updated = conversation.with_updates(
            now, state=IntakeState.PROPOSING_SLOTS, proposed_slots=offered
        )
        await unit_of_work.intake_conversations.save(updated)
        return updated

    async def _handle_slot_pick(
        self,
        unit_of_work: UnitOfWork,
        conversation: IntakeConversation,
        start: datetime,
        now: datetime,
        *,
        explicit: bool,
    ) -> IntakeReply:
        if conversation.state is not IntakeState.PROPOSING_SLOTS:
            reply = await self._reply(unit_of_work, conversation, SLOT_NOT_OFFERED_REPLY, now)
            return IntakeReply(conversation, reply, conversation.proposed_slots)
        slot = conversation.find_proposed_slot(start)
        if slot is None:
            reply = await self._reply(unit_of_work, conversation, SLOT_NOT_OFFERED_REPLY, now)
            return IntakeReply(conversation, reply, conversation.proposed_slots)

        collected = conversation.collected
        customer_id = conversation.customer_id
        if customer_id is None and collected.email:
            customer = await unit_of_work.customers.upsert(
                conversation.business_id,
                collected.email,
                display_name=collected.name,
                phone=collected.phone,
                now=now,
            )
            customer_id = customer.customer_id
        if customer_id is not None:
            preferred = format_slot_label(slot.start)
            await unit_of_work.service_requests.add(
                ServiceRequest(
                    request_id=ServiceRequestId(uuid4()),
                    business_id=conversation.business_id,
                    customer_id=customer_id,
                    message=collected.problem or "Booking request",
                    preferred_dates=preferred,
                    source=SERVICE_REQUEST_SOURCE_INTAKE,
                    created_at=now,
                )
            )
        updated = conversation.with_updates(
            now,
            state=IntakeState.AWAITING_OWNER,
            customer_id=customer_id,
            requested_slot_start=slot.start,
            requested_slot_end=slot.end,
        )
        business = await self._business(unit_of_work, conversation.business_id)
        notified = await enqueue_intake_owner_notice(
            unit_of_work,
            conversation.business_id,
            correlation_id=f"intake_request:{conversation.conversation_id}",
            text=booking_request_notice(
                updated, business_name=business.display_name or business.name
            ),
        )
        if notified:
            updated = updated.with_updates(now, owner_notified_at=now)
        else:
            logger.warning(
                "booking request %s stored without an owner notice",
                updated.reference,
            )
        await unit_of_work.intake_conversations.save(updated)
        reply = slot_confirmed_reply(slot)
        await self._append(unit_of_work, updated, IntakeMessageRole.AGENT, reply, now)
        return IntakeReply(updated, reply, ())

    async def _business(self, unit_of_work: UnitOfWork, business_id: BusinessId) -> BusinessRecord:
        business = await unit_of_work.businesses.get(business_id)
        if business is None:
            raise IntakeNotFoundError("unknown business")
        return business


class ArrangeIntakeBookingService:
    """Turns an approval into a calendar booking via the availability port.

    The approval is already persisted (state ``approved``); this runs from the
    outbox so a provider hiccup retries instead of losing the booking, and the
    customer notifications are enqueued only after the booking result is
    known.
    """

    def __init__(
        self,
        unit_of_work_factory: UnitOfWorkFactory,
        *,
        availability: AvailabilityPort | None,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._unit_of_work_factory = unit_of_work_factory
        self._availability = availability
        self._now = now

    async def arrange(self, business_id: BusinessId, conversation_id: IntakeConversationId) -> None:
        if self._availability is None:
            raise IntakeAvailabilityError("no availability provider is configured")
        async with self._unit_of_work_factory() as unit_of_work:
            conversation = await unit_of_work.intake_conversations.get(business_id, conversation_id)
            if conversation is None or conversation.state is not IntakeState.APPROVED:
                return
            if conversation.requested_slot_start is None or conversation.requested_slot_end is None:
                return
            if conversation.booking_kind is not None:
                return  # already arranged — an enqueued retry
            collected = conversation.collected
            if not collected.name or not collected.email:
                raise IntakeAvailabilityError(
                    "approved booking is missing the customer's name or email"
                )
            request = BookingRequest(
                business_id=business_id,
                slot_start=conversation.requested_slot_start,
                slot_end=conversation.requested_slot_end,
                invitee_name=collected.name,
                invitee_email=collected.email,
                invitee_phone=collected.phone,
                address=collected.address,
                details=collected.problem,
            )
            attempted = conversation.booking_attempted_at is not None
            if not attempted:
                # Persist the attempt before the provider call: if the process
                # dies after ``book`` succeeded, the retried command sees the
                # marker and reconciles instead of booking twice.
                await unit_of_work.intake_conversations.save(
                    conversation.with_updates(self._now(), booking_attempted_at=self._now())
                )
                await unit_of_work.commit()

        result = None
        if attempted:
            result = await self._availability.find_booking(request)
        if result is None:
            result = await self._availability.book(request)

        async with self._unit_of_work_factory() as unit_of_work:
            conversation = await unit_of_work.intake_conversations.get(business_id, conversation_id)
            if (
                conversation is None
                or conversation.state is not IntakeState.APPROVED
                or conversation.requested_slot_start is None
                or conversation.booking_kind is not None
            ):
                return
            business = await unit_of_work.businesses.get(business_id)
            now = self._now()
            business_name = (
                "" if business is None else (business.display_name or business.name)
            ) or "the business"
            slot_label = format_slot_label(conversation.requested_slot_start)
            if result.kind is BookingKind.BOOKED:
                body = (
                    f"Good news — your {business_name} appointment for "
                    f"{slot_label} is booked. You'll get the calendar invite "
                    "by email shortly."
                )
                subject = "Your appointment is confirmed"
            else:
                link = result.link or ""
                body = f"{business_name} approved {slot_label}. Confirm your spot: {link}"
                subject = "Confirm your appointment"
            key_base = f"intake_booking:{conversation.conversation_id}"
            await unit_of_work.outbox.enqueue(
                intake_customer_email_command(
                    IntakeCustomerEmail(
                        business_id=business_id,
                        to=collected.email,
                        subject=subject,
                        body=body,
                        idempotency_key=f"{key_base}:email",
                    )
                )
            )
            if collected.phone:
                await unit_of_work.outbox.enqueue(
                    intake_customer_text_command(
                        business_id,
                        phone=collected.phone,
                        text=body,
                        idempotency_key=f"{key_base}:text",
                    )
                )
            updated = conversation.with_updates(
                now, booking_kind=result.kind.value, booking_link=result.link
            )
            await unit_of_work.intake_conversations.save(updated)
            await unit_of_work.commit()


def _decision_reply(message: NormalizedOwnerMessage, text: str) -> OutboundOwnerMessage:
    return OutboundOwnerMessage(
        business_id=message.business_id,
        conversation_ref=message.conversation_ref,
        parts=(TextPart(text=text),),
        correlation_id=f"booking:{message.message_key}",
    )


def _booking_text(message: NormalizedOwnerMessage) -> str:
    return "\n".join(part.text for part in message.parts if isinstance(part, TextPart))


class BookingDecisionHandler:
    """``approve booking <ref>`` / ``decline booking <ref> [reason]``.

    Tenant-scoped by the business of the owner's message; deciding the same
    reference twice is a no-op with a clear reply; an unknown reference gets
    one clear reply.
    """

    intent = BOOKING_INTENT

    def __init__(
        self,
        unit_of_work_factory: UnitOfWorkFactory,
        *,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._unit_of_work_factory = unit_of_work_factory
        self._now = now

    async def handle(self, context: WorkflowContext) -> WorkflowResult:
        message = context.message
        decision = booking_decision(_booking_text(message))
        if decision is None:
            return self._result(
                message, "Reply `approve booking <id>` or `decline booking <id> <reason>`."
            )
        reference = decision.reference
        now = self._now()
        async with self._unit_of_work_factory() as unit_of_work:
            conversation = await unit_of_work.intake_conversations.lock_by_reference(
                message.business_id, reference
            )
            if conversation is None:
                return self._result(message, f"I can't find booking {reference}.")
            if conversation.state is IntakeState.APPROVED:
                return self._result(message, f"Booking {reference} is already approved.")
            if conversation.state is IntakeState.DECLINED:
                return self._result(message, f"Booking {reference} was already declined.")
            if conversation.state is not IntakeState.AWAITING_OWNER:
                return self._result(message, f"Booking {reference} isn't waiting on a decision.")
            if decision.action is BookingDecisionAction.APPROVE:
                return await self._approve(unit_of_work, message, conversation, now)
            return await self._decline(unit_of_work, message, conversation, decision, now)

    @staticmethod
    def _result(message: NormalizedOwnerMessage, text: str) -> WorkflowResult:
        return WorkflowResult(
            status=WorkflowRunStatus.SUCCEEDED,
            replies=(_decision_reply(message, text),),
        )

    async def _approve(
        self,
        unit_of_work: UnitOfWork,
        message: NormalizedOwnerMessage,
        conversation: IntakeConversation,
        now: datetime,
    ) -> WorkflowResult:
        if (
            conversation.requested_slot_start is not None
            and conversation.requested_slot_start <= now
        ):
            return self._result(
                message,
                f"Booking {conversation.reference}'s requested time has already "
                "passed — decline it or line up a new time with the customer.",
            )
        updated = conversation.with_updates(now, state=IntakeState.APPROVED, decision_at=now)
        await unit_of_work.intake_conversations.save(updated)
        command = intake_booking_arrange_command(updated)
        await unit_of_work.outbox.enqueue(command)
        await unit_of_work.commit()
        name = updated.collected.name or "the customer"
        slot = (
            format_slot_label(updated.requested_slot_start)
            if updated.requested_slot_start is not None
            else "their requested time"
        )
        return self._result(
            message, f"Approved booking {updated.reference} — arranging {slot} for {name}."
        )

    async def _decline(
        self,
        unit_of_work: UnitOfWork,
        message: NormalizedOwnerMessage,
        conversation: IntakeConversation,
        decision: BookingDecision,
        now: datetime,
    ) -> WorkflowResult:
        updated = conversation.with_updates(
            now,
            state=IntakeState.DECLINED,
            decision_at=now,
            decision_reason=decision.reason,
        )
        await unit_of_work.intake_conversations.save(updated)
        business = await unit_of_work.businesses.get(message.business_id)
        email = updated.collected.email
        notified = "the customer has been notified"
        if email:
            booking_link = business.calendly_url if business is not None else None
            body = _decline_body(updated, booking_link)
            await unit_of_work.outbox.enqueue(
                intake_customer_email_command(
                    IntakeCustomerEmail(
                        business_id=message.business_id,
                        to=email,
                        subject="About your requested appointment",
                        body=body,
                        idempotency_key=f"intake_decline:{conversation.conversation_id}",
                    )
                )
            )
        else:
            notified = "no customer email was collected, so nothing was sent"
        await unit_of_work.commit()
        return WorkflowResult(
            status=WorkflowRunStatus.SUCCEEDED,
            replies=(
                _decision_reply(message, f"Declined booking {updated.reference}; {notified}."),
            ),
        )


def _decline_body(conversation: IntakeConversation, booking_link: str | None) -> str:
    lines = [
        "Thanks for reaching out. The owner reviewed your request and can't "
        "take it on at the requested time.",
    ]
    if conversation.decision_reason:
        lines.append(f"Reason: {conversation.decision_reason}")
    if booking_link:
        lines.append(f"If another time works, you can pick one directly: {booking_link}")
    else:
        lines.append("If another time works, reply here and we'll sort it out.")
    return "\n".join(lines)


class SendIntakeCustomerEmailService:
    """Delivers one customer email verbatim — decline notices and booking
    confirmations are composed before they are queued."""

    def __init__(self, delivery: CustomerQuoteDeliveryPort) -> None:
        self._delivery = delivery

    async def send(self, business_id: BusinessId, payload: Mapping[str, object]) -> None:
        email = intake_customer_email_request(business_id, payload)
        receipt = await self._delivery.deliver(
            CustomerDeliveryRequest(
                business_id=business_id,
                recipient=CustomerRecipient(
                    address=email.to, address_kind=RecipientAddressKind.EMAIL
                ),
                idempotency_key=email.idempotency_key,
                subject=email.subject,
                body_text=email.body,
            )
        )
        if receipt.status is DeliveryStatus.FAILED:
            raise IntakeDeliveryError(receipt.detail or "customer email failed")


class SendIntakeCustomerTextService:
    """Delivers one customer SMS verbatim."""

    def __init__(self, texts: CustomerTextDeliveryPort) -> None:
        self._texts = texts

    async def send(self, business_id: BusinessId, payload: Mapping[str, object]) -> None:
        phone = payload.get("phone")
        text = payload.get("text")
        key = payload.get("idempotency_key")
        if not all(isinstance(value, str) and value for value in (phone, text, key)):
            raise ValueError("intake text command payload is incomplete")
        receipt = await self._texts.send_text(
            CustomerTextRequest(
                business_id=business_id,
                phone_number=str(phone),
                text=str(text),
                idempotency_key=str(key),
            )
        )
        if receipt.status is DeliveryStatus.FAILED:
            raise IntakeDeliveryError(receipt.detail or "customer text failed")
