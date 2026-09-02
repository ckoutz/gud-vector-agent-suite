import logging
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from gvas.domain.appointments import (
    Appointment,
    AppointmentLookupError,
    AppointmentLookupPort,
    surrounding_days_window,
)
from gvas.domain.enums import (
    DeliveryStatus,
    QuoteSendAction,
    QuoteStatus,
    RecipientAddressKind,
    WorkflowRunStatus,
)
from gvas.domain.identifiers import BusinessId, ConversationId, MessageKey, QuoteId
from gvas.domain.intents import IntentResolution
from gvas.domain.messages import (
    ConversationRef,
    CustomerDeliveryRequest,
    CustomerRecipient,
    NormalizedOwnerMessage,
    OutboundOwnerMessage,
    TextPart,
)
from gvas.domain.money import format_money
from gvas.domain.ports import CustomerQuoteDeliveryPort, QuoteDraftingPort
from gvas.domain.quotes import (
    QUOTE_INTENT,
    OwnerApprovalRequiredPolicy,
    Quote,
    QuoteAppointmentContext,
    QuoteConcurrencyError,
    QuoteDraftProposal,
    QuoteDraftRejectedError,
    QuoteDraftRequest,
    QuoteSendAssessment,
    QuoteSendPolicy,
    has_customer_line,
    new_quote,
    quote_delivery_command,
    quote_trigger_request_text,
    requested_customer_name,
)
from gvas.domain.repositories import UnitOfWork
from gvas.domain.workflows import WorkflowContext, WorkflowResult

logger = logging.getLogger(__name__)

FREE_TEXT_DRAFT_NOTICE = "Drafted from your message — check items before approving."
CUSTOMER_LOOKUP_UNAVAILABLE = (
    "The appointment calendar could not be reached. "
    "Please send the quote again with a customer: <email> line this time."
)


class QuoteIntakeError(ValueError):
    pass


class QuoteDeliveryError(RuntimeError):
    pass


class QuoteUnitOfWorkFactory(Protocol):
    def __call__(self) -> UnitOfWork: ...


class QuoteIntentSelector:
    def select(
        self,
        message: NormalizedOwnerMessage,
        conversation_id: ConversationId,
        active_quote: Quote | None,
    ) -> IntentResolution | None:
        if active_quote is not None:
            if (
                active_quote.business_id != message.business_id
                or active_quote.conversation_id != conversation_id
                or not active_quote.is_active
            ):
                raise ValueError("active quote does not match the normalized conversation")
            return IntentResolution(intent=QUOTE_INTENT, confidence=1)
        if quote_trigger_request_text(normalized_text(message)) is not None:
            quote_request_text(message)
            return IntentResolution(intent=QUOTE_INTENT, confidence=1)
        return None


class QuoteWorkflowHandler:
    intent = QUOTE_INTENT

    def __init__(
        self,
        unit_of_work_factory: QuoteUnitOfWorkFactory,
        drafting_port: QuoteDraftingPort,
        send_policy: QuoteSendPolicy | None = None,
        appointment_lookup: AppointmentLookupPort | None = None,
    ) -> None:
        self._unit_of_work_factory = unit_of_work_factory
        self._drafting_port = drafting_port
        self._send_policy = send_policy or OwnerApprovalRequiredPolicy()
        self._appointment_lookup = appointment_lookup

    async def handle(self, context: WorkflowContext) -> WorkflowResult:
        conversation_id = context.conversation_id
        if conversation_id is None:
            raise ValueError("quote workflow requires a persisted conversation identity")
        message = context.message
        quote = await self._get_by_message(message, conversation_id)
        if quote is not None:
            if quote.status is QuoteStatus.DRAFTING:
                return await self._complete_draft(quote, message)
            if quote.status is QuoteStatus.AWAITING_CUSTOMER_SELECTION:
                return _customer_selection_reply(quote, message.message_key)
            if quote.status is QuoteStatus.AWAITING_APPROVAL and quote.draft is not None:
                return _workflow_reply(
                    quote,
                    message.message_key,
                    detail=self._send_decision_detail(quote.draft),
                )
            return _workflow_reply(quote, message.message_key)

        quote = await self._get_active(message.business_id, conversation_id)
        if quote is None:
            if owner_command(message) is not None:
                return _no_awaiting_approval_reply(message)
            request_text = quote_request_text(message)
            quote = new_quote(
                business_id=message.business_id,
                conversation_id=conversation_id,
                conversation_ref=message.conversation_ref,
                message_key=message.message_key,
                request_text=request_text,
                now=message.received_at,
            )
            async with self._unit_of_work_factory() as unit_of_work:
                await unit_of_work.quotes.add(quote)
                await unit_of_work.commit()
            return await self._complete_draft(quote, message)

        if quote.status is QuoteStatus.DRAFTING:
            return WorkflowResult(
                status=WorkflowRunStatus.SUCCEEDED,
                replies=(
                    _owner_reply(
                        quote,
                        message.message_key,
                        "A quote draft is already in progress.",
                    ),
                ),
            )
        if quote.status is QuoteStatus.AWAITING_CUSTOMER_SELECTION:
            return await self._handle_customer_selection(quote, message)
        return await self._handle_owner_reply(quote, message)

    async def _handle_customer_selection(
        self, quote: Quote, message: NormalizedOwnerMessage
    ) -> WorkflowResult:
        text = normalized_text(message).strip()
        if text.casefold() == "reject":
            return await self._abandon_draft(quote, message, "Quote cancelled.")
        candidates = quote.customer_candidates or ()
        choice = text.rstrip(".")
        if not choice.isdigit() or not 1 <= int(choice) <= len(candidates):
            return _customer_selection_reply(
                quote,
                message.message_key,
                prefix=f"Reply with a number from 1 to {len(candidates)}, or reject.",
            )
        drafting = quote.select_customer(int(choice), message.message_key, message.received_at)
        await self._save(drafting, expected_version=quote.version)
        return await self._complete_draft(drafting, message)

    async def _handle_owner_reply(
        self, quote: Quote, message: NormalizedOwnerMessage
    ) -> WorkflowResult:
        text = normalized_text(message).strip()
        command = owner_command(message)
        if command is not None and quote.status is not QuoteStatus.AWAITING_APPROVAL:
            return _no_awaiting_approval_reply(message)
        if command == "approve":
            approved = quote.approve(message.message_key, message.received_at)
            async with self._unit_of_work_factory() as unit_of_work:
                await unit_of_work.quotes.save(approved, expected_version=quote.version)
                await unit_of_work.outbox.enqueue(quote_delivery_command(approved))
                await unit_of_work.commit()
            return _workflow_reply(approved, message.message_key)
        if command == "reject":
            rejected = quote.reject(message.message_key, message.received_at)
            await self._save(rejected, expected_version=quote.version)
            return _workflow_reply(rejected, message.message_key)
        if command == "correct":
            correction = text.split(":", 1)[1].strip()
            if not correction:
                raise QuoteIntakeError("correction text must not be empty")
            drafting = quote.begin_correction(
                message.message_key,
                correction,
                message.received_at,
            )
            await self._save(drafting, expected_version=quote.version)
            return await self._complete_draft(drafting, message)
        return WorkflowResult(
            status=WorkflowRunStatus.SUCCEEDED,
            replies=(
                _owner_reply(
                    quote,
                    message.message_key,
                    "Reply with approve, reject, or correct: <changes>.",
                ),
            ),
        )

    async def _complete_draft(
        self, quote: Quote, message: NormalizedOwnerMessage
    ) -> WorkflowResult:
        if (
            self._appointment_lookup is not None
            and quote.customer_appointment is None
            and not has_customer_line(quote.pending_request_text)
        ):
            try:
                candidates = await self._find_appointments(quote, message)
            except AppointmentLookupError as error:
                logger.warning("appointment lookup failed for quote %s: %s", quote.quote_id, error)
                return await self._abandon_draft(quote, message, CUSTOMER_LOOKUP_UNAVAILABLE)
            if len(candidates) == 1:
                quote = quote.with_customer_appointment(candidates[0])
            elif len(candidates) > 1:
                awaiting = quote.await_customer_selection(
                    candidates, message.message_key, message.received_at
                )
                await self._save(awaiting, expected_version=quote.version)
                return _customer_selection_reply(awaiting, message.message_key)
        appointment = quote.customer_appointment
        request = QuoteDraftRequest(
            quote_id=quote.quote_id,
            business_id=quote.business_id,
            conversation_id=quote.conversation_id,
            request_text=quote.pending_request_text,
            revision=quote.revision,
            idempotency_key=f"quote-draft:{quote.quote_id}:{quote.revision}",
            recipient=(None if appointment is None else _appointment_recipient(appointment)),
            appointment=(None if appointment is None else _appointment_context(appointment)),
        )
        try:
            proposal = await self._drafting_port.draft(request)
        except QuoteDraftRejectedError as error:
            return await self._abandon_draft(quote, message, str(error))
        drafted = quote.apply_draft(proposal, message.message_key, message.received_at)
        detail = self._send_decision_detail(proposal)
        try:
            await self._save(drafted, expected_version=quote.version)
        except QuoteConcurrencyError:
            current = await self._get_by_message(message, quote.conversation_id)
            if current is None:
                raise
            drafted = current
        return _workflow_reply(drafted, message.message_key, detail=detail)

    async def _abandon_draft(
        self, quote: Quote, message: NormalizedOwnerMessage, reason: str
    ) -> WorkflowResult:
        abandoned = quote.abandon_draft(message.message_key, message.received_at)
        await self._save(abandoned, expected_version=quote.version)
        return WorkflowResult(
            status=WorkflowRunStatus.SUCCEEDED,
            replies=(
                _owner_reply(
                    abandoned,
                    message.message_key,
                    f"{reason}\nSend the corrected request as a new quote.",
                ),
            ),
        )

    async def _find_appointments(
        self, quote: Quote, message: NormalizedOwnerMessage
    ) -> tuple[Appointment, ...]:
        if self._appointment_lookup is None:
            return ()
        window = surrounding_days_window(quote.business_id, message.received_at)
        found = await self._appointment_lookup.find(window)
        name_filter = requested_customer_name(quote.pending_request_text)
        if name_filter is not None:
            needle = name_filter.casefold()
            found = tuple(
                appointment
                for appointment in found
                if needle in appointment.invitee_name.casefold()
            )
        return tuple(sorted(found, key=lambda appointment: appointment.start_time))

    def _send_decision_detail(self, proposal: QuoteDraftProposal) -> str | None:
        decision = self._send_policy.decide(
            QuoteSendAssessment(
                confidence=proposal.confidence,
                risk_flags=proposal.risk_flags,
                suspected_mistake=bool(proposal.risk_flags),
            )
        )
        detail = decision.detail
        if decision.action is QuoteSendAction.AUTO_SEND:
            detail = "automatic sending is configured but disabled until a later phase"
        return detail

    async def _get_active(
        self, business_id: BusinessId, conversation_id: ConversationId
    ) -> Quote | None:
        async with self._unit_of_work_factory() as unit_of_work:
            quote = await unit_of_work.quotes.get_active(business_id, conversation_id)
            await unit_of_work.commit()
        return quote

    async def _get_by_message(
        self, message: NormalizedOwnerMessage, conversation_id: ConversationId
    ) -> Quote | None:
        async with self._unit_of_work_factory() as unit_of_work:
            quote = await unit_of_work.quotes.get_by_message(
                message.business_id, conversation_id, message.message_key
            )
            await unit_of_work.commit()
        return quote

    async def _save(self, quote: Quote, *, expected_version: int) -> None:
        async with self._unit_of_work_factory() as unit_of_work:
            await unit_of_work.quotes.save(quote, expected_version=expected_version)
            await unit_of_work.commit()


class QuoteDeliveryStatus(StrEnum):
    COMPLETED = "completed"
    ALREADY_COMPLETED = "already_completed"
    NOT_APPROVED = "not_approved"
    MISSING = "missing"


@dataclass(frozen=True)
class QuoteDeliveryOutcome:
    status: QuoteDeliveryStatus
    quote_id: QuoteId


class DeliverApprovedQuoteService:
    def __init__(
        self,
        unit_of_work_factory: QuoteUnitOfWorkFactory,
        delivery_port: CustomerQuoteDeliveryPort,
    ) -> None:
        self._unit_of_work_factory = unit_of_work_factory
        self._delivery_port = delivery_port

    async def deliver(self, business_id: BusinessId, quote_id: QuoteId) -> QuoteDeliveryOutcome:
        async with self._unit_of_work_factory() as unit_of_work:
            quote = await unit_of_work.quotes.get(business_id, quote_id)
            await unit_of_work.commit()
        if quote is None:
            return QuoteDeliveryOutcome(QuoteDeliveryStatus.MISSING, quote_id)
        if quote.status in {QuoteStatus.DELIVERY_PENDING, QuoteStatus.DELIVERED}:
            return QuoteDeliveryOutcome(QuoteDeliveryStatus.ALREADY_COMPLETED, quote_id)
        if quote.status is not QuoteStatus.APPROVED or quote.draft is None:
            return QuoteDeliveryOutcome(QuoteDeliveryStatus.NOT_APPROVED, quote_id)

        draft = quote.draft
        receipt = await self._delivery_port.deliver(
            CustomerDeliveryRequest(
                business_id=quote.business_id,
                recipient=draft.recipient,
                idempotency_key=f"quote-delivery:{quote.quote_id}",
                subject="Your quote",
                body_text=_customer_quote_body(draft),
                links=tuple(link.reference for link in draft.hosted_links),
            )
        )
        if receipt.status is DeliveryStatus.FAILED:
            raise QuoteDeliveryError(receipt.detail or "quote delivery failed")
        delivered = quote.record_delivery(receipt)
        try:
            async with self._unit_of_work_factory() as unit_of_work:
                await unit_of_work.quotes.save(delivered, expected_version=quote.version)
                await unit_of_work.commit()
        except QuoteConcurrencyError:
            async with self._unit_of_work_factory() as unit_of_work:
                current = await unit_of_work.quotes.get(business_id, quote_id)
                await unit_of_work.commit()
            if current is None or current.status not in {
                QuoteStatus.DELIVERY_PENDING,
                QuoteStatus.DELIVERED,
            }:
                raise
        return QuoteDeliveryOutcome(QuoteDeliveryStatus.COMPLETED, quote_id)


def normalized_text(message: NormalizedOwnerMessage) -> str:
    return "\n".join(part.text for part in message.parts if isinstance(part, TextPart))


def owner_command(message: NormalizedOwnerMessage) -> str | None:
    text = normalized_text(message).strip().casefold()
    if text in {"approve", "reject"}:
        return text
    if text.startswith("correct:"):
        return "correct"
    return None


def quote_request_text(message: NormalizedOwnerMessage) -> str:
    request_text = quote_trigger_request_text(normalized_text(message))
    if request_text is None:
        raise QuoteIntakeError("new quote workflows require an explicit quote: prefix")
    if not request_text:
        raise QuoteIntakeError("quote request text must not be empty")
    return request_text


def _no_awaiting_approval_reply(message: NormalizedOwnerMessage) -> WorkflowResult:
    reply = OutboundOwnerMessage(
        business_id=message.business_id,
        conversation_ref=message.conversation_ref,
        parts=(TextPart(text="No quote is awaiting approval in this conversation."),),
        correlation_id=f"quote:message:{message.message_key}",
    )
    return WorkflowResult(
        status=WorkflowRunStatus.SUCCEEDED,
        replies=(reply,),
    )


def _appointment_recipient(appointment: Appointment) -> CustomerRecipient:
    return CustomerRecipient(
        address=appointment.invitee_email,
        address_kind=RecipientAddressKind.EMAIL,
        display_name=appointment.invitee_name,
    )


def _appointment_context(appointment: Appointment) -> QuoteAppointmentContext:
    return QuoteAppointmentContext(
        event_name=appointment.event_name,
        start_time=appointment.start_time,
        address=appointment.address,
        invitee_name=appointment.invitee_name,
        notes=appointment.notes,
    )


def _customer_selection_reply(
    quote: Quote, message_key: MessageKey, prefix: str | None = None
) -> WorkflowResult:
    candidates = quote.customer_candidates or ()
    listing = "  ".join(
        f"{index}. {candidate.choice_label}" for index, candidate in enumerate(candidates, 1)
    )
    body = f"Which appointment is this quote for? {listing} — reply with the number"
    if prefix:
        body = f"{prefix}\n{body}"
    return WorkflowResult(
        status=WorkflowRunStatus.SUCCEEDED,
        replies=(_owner_reply(quote, message_key, body),),
    )


def _workflow_reply(
    quote: Quote, message_key: MessageKey, detail: str | None = None
) -> WorkflowResult:
    if quote.status is QuoteStatus.AWAITING_APPROVAL:
        if quote.draft is None or quote.approval_correlation_id is None:
            raise ValueError("approval reply requires a complete quote draft")
        body = _owner_quote_body(quote)
        if detail:
            body = f"{body}\n\n{detail}"
        reply = OutboundOwnerMessage(
            business_id=quote.business_id,
            conversation_ref=_conversation_ref(quote),
            parts=(TextPart(text=body),),
            correlation_id=quote.approval_correlation_id,
        )
    elif quote.status is QuoteStatus.APPROVED:
        reply = _owner_reply(quote, message_key, "Quote approved and queued for customer delivery.")
    elif quote.status is QuoteStatus.REJECTED:
        reply = _owner_reply(quote, message_key, "Quote rejected.")
    elif quote.status in {QuoteStatus.DELIVERY_PENDING, QuoteStatus.DELIVERED}:
        reply = _owner_reply(quote, message_key, "Quote delivery is complete.")
    else:
        reply = _owner_reply(quote, message_key, "Quote drafting is in progress.")
    return WorkflowResult(
        status=WorkflowRunStatus.SUCCEEDED,
        replies=(reply,),
    )


def _owner_reply(quote: Quote, message_key: MessageKey, text: str) -> OutboundOwnerMessage:
    return OutboundOwnerMessage(
        business_id=quote.business_id,
        conversation_ref=_conversation_ref(quote),
        parts=(TextPart(text=text),),
        correlation_id=f"quote:{quote.quote_id}:message:{message_key}",
    )


def _conversation_ref(quote: Quote) -> ConversationRef:
    return quote.conversation_ref


def _owner_quote_body(quote: Quote) -> str:
    draft = quote.draft
    if draft is None:
        raise ValueError("quote draft is missing")
    lines: list[str] = []
    appointment = quote.customer_appointment
    if appointment is not None and draft.recipient.address == appointment.invitee_email:
        lines.append(
            f"Customer: {appointment.invitee_name} ({appointment.invitee_email})"
            f" — {appointment.summary}"
        )
    lines.extend(
        f"{item.quantity} × {item.description}: {format_money(item.total_minor, draft.currency)}"
        for item in draft.line_items
    )
    lines.append(f"Total: {format_money(draft.total_minor, draft.currency)}")
    if draft.drafted_from_free_text:
        lines.append(FREE_TEXT_DRAFT_NOTICE)
    lines.append("Reply with approve, reject, or correct: <changes>.")
    return "\n".join(lines)


def _customer_quote_body(draft: QuoteDraftProposal) -> str:
    lines = [
        f"{item.quantity} × {item.description}: {format_money(item.total_minor, draft.currency)}"
        for item in draft.line_items
    ]
    lines.append(f"Total: {format_money(draft.total_minor, draft.currency)}")
    if draft.owner_note:
        lines.append(draft.owner_note)
    return "\n".join(lines)
