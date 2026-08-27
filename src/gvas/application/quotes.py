from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from gvas.domain.enums import DeliveryStatus, QuoteSendAction, QuoteStatus, WorkflowRunStatus
from gvas.domain.identifiers import BusinessId, ConversationId, MessageKey, QuoteId
from gvas.domain.intents import IntentResolution
from gvas.domain.messages import (
    ConversationRef,
    CustomerDeliveryRequest,
    NormalizedOwnerMessage,
    OutboundOwnerMessage,
    TextPart,
)
from gvas.domain.ports import CustomerQuoteDeliveryPort, QuoteDraftingPort
from gvas.domain.quotes import (
    QUOTE_INTENT,
    OwnerApprovalRequiredPolicy,
    Quote,
    QuoteConcurrencyError,
    QuoteCorrelationError,
    QuoteDraftProposal,
    QuoteDraftRequest,
    QuoteSendAssessment,
    QuoteSendPolicy,
    new_quote,
    quote_delivery_command,
)
from gvas.domain.repositories import UnitOfWork
from gvas.domain.workflows import WorkflowContext, WorkflowResult


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
        text = normalized_text(message)
        if text.lstrip().lower().startswith("quote:"):
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
    ) -> None:
        self._unit_of_work_factory = unit_of_work_factory
        self._drafting_port = drafting_port
        self._send_policy = send_policy or OwnerApprovalRequiredPolicy()

    async def handle(self, context: WorkflowContext) -> WorkflowResult:
        conversation_id = context.conversation_id
        if conversation_id is None:
            raise ValueError("quote workflow requires a persisted conversation identity")
        message = context.message
        quote = await self._get_by_message(message, conversation_id)
        if quote is not None:
            if quote.status is QuoteStatus.DRAFTING:
                return await self._complete_draft(quote, message)
            if quote.status is QuoteStatus.AWAITING_APPROVAL and quote.draft is not None:
                return _workflow_reply(
                    quote,
                    message.message_key,
                    detail=self._send_decision_detail(quote.draft),
                )
            return _workflow_reply(quote, message.message_key)

        quote = await self._get_active(message.business_id, conversation_id)
        if quote is None:
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
        return await self._handle_owner_reply(quote, message)

    async def _handle_owner_reply(
        self, quote: Quote, message: NormalizedOwnerMessage
    ) -> WorkflowResult:
        correlation_id = message.reply_to.correlation_id if message.reply_to is not None else ""
        text = normalized_text(message).strip()
        try:
            if text.casefold() == "approve":
                approved = quote.approve(correlation_id, message.message_key, message.received_at)
                async with self._unit_of_work_factory() as unit_of_work:
                    await unit_of_work.quotes.save(approved, expected_version=quote.version)
                    await unit_of_work.outbox.enqueue(quote_delivery_command(approved))
                    await unit_of_work.commit()
                return _workflow_reply(approved, message.message_key)
            if text.casefold() == "reject":
                rejected = quote.reject(correlation_id, message.message_key, message.received_at)
                await self._save(rejected, expected_version=quote.version)
                return _workflow_reply(rejected, message.message_key)
            if text.casefold().startswith("correct:"):
                correction = text.split(":", 1)[1].strip()
                if not correction:
                    raise QuoteIntakeError("correction text must not be empty")
                drafting = quote.begin_correction(
                    correlation_id,
                    message.message_key,
                    correction,
                    message.received_at,
                )
                await self._save(drafting, expected_version=quote.version)
                return await self._complete_draft(drafting, message)
        except QuoteCorrelationError:
            return WorkflowResult(
                status=WorkflowRunStatus.SUCCEEDED,
                replies=(
                    _owner_reply(
                        quote,
                        message.message_key,
                        "Reply to the active approval request with approve, reject, "
                        "or correct: <changes>.",
                    ),
                ),
            )
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
        request = QuoteDraftRequest(
            quote_id=quote.quote_id,
            business_id=quote.business_id,
            conversation_id=quote.conversation_id,
            request_text=quote.pending_request_text,
            revision=quote.revision,
            idempotency_key=f"quote-draft:{quote.quote_id}:{quote.revision}",
        )
        proposal = await self._drafting_port.draft(request)
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


def quote_request_text(message: NormalizedOwnerMessage) -> str:
    text = normalized_text(message).lstrip()
    if not text.lower().startswith("quote:"):
        raise QuoteIntakeError("new quote workflows require an explicit quote: prefix")
    request_text = text.split(":", 1)[1].strip()
    if not request_text:
        raise QuoteIntakeError("quote request text must not be empty")
    return request_text


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
    lines = [
        f"{item.quantity} × {item.description}: {_money(item.total_minor, draft.currency)}"
        for item in draft.line_items
    ]
    lines.append(f"Total: {_money(draft.total_minor, draft.currency)}")
    lines.append("Reply with approve, reject, or correct: <changes>.")
    return "\n".join(lines)


def _customer_quote_body(draft: QuoteDraftProposal) -> str:
    lines = [
        f"{item.quantity} × {item.description}: {_money(item.total_minor, draft.currency)}"
        for item in draft.line_items
    ]
    lines.append(f"Total: {_money(draft.total_minor, draft.currency)}")
    if draft.owner_note:
        lines.append(draft.owner_note)
    return "\n".join(lines)


def _money(amount_minor: int, currency: str) -> str:
    return f"{currency} {amount_minor / 100:.2f}"
