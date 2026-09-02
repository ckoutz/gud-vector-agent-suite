import re
from datetime import datetime
from typing import Protocol
from uuid import UUID, uuid5

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from gvas.domain.appointments import Appointment
from gvas.domain.enums import (
    DeliveryStatus,
    HostedLinkKind,
    QuoteSendAction,
    QuoteStatus,
)
from gvas.domain.identifiers import (
    BusinessId,
    ConversationId,
    MessageKey,
    OutboxCommandId,
    QuoteId,
    WorkflowIntent,
)
from gvas.domain.messages import (
    ConversationRef,
    CustomerRecipient,
    DeliveryReceipt,
    NormalizedOwnerMessage,
    TextPart,
)
from gvas.domain.outbox import OutboxCommand

QUOTE_INTENT = WorkflowIntent("quote")
QUOTE_TRIGGER_PREFIX = "quote:"
#: ``quote:`` or ``quote for <name>:``; the name becomes a ``for:`` line.
QUOTE_TRIGGER_PATTERN = re.compile(r"^quote(?:\s+for\s+(?P<name>[^:\n]+?))?\s*:", re.IGNORECASE)
CUSTOMER_LINE_KEYS = frozenset({"customer", "email"})
CUSTOMER_NAME_LINE_KEY = "for"
QUOTE_DELIVERY_COMMAND_TYPE = "customer_quote.deliver"
QUOTE_TEXT_COMMAND_TYPE = "customer_quote.text"
QUOTE_ID_NAMESPACE = UUID("391d4c69-e58a-4621-ad77-1f45ac243ae2")
QUOTE_DELIVERY_COMMAND_NAMESPACE = UUID("e940a293-273c-4914-b17b-63ac10db4db4")
QUOTE_TEXT_COMMAND_NAMESPACE = UUID("5c1f0b7e-2d84-4f6a-9b3e-8a7d6c5e4f30")
#: One GSM-7 segment; the customer text must never need a second one.
SMS_SEGMENT_LIMIT = 160
CUSTOMER_TEXT_PREFIX = "Your Güd Vector quote for "
CUSTOMER_TEXT_SUFFIX = " is ready: "


class QuoteModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class HostedLinkReference(QuoteModel):
    kind: HostedLinkKind
    reference: str = Field(min_length=1)

    @field_validator("reference")
    @classmethod
    def reference_is_opaque(cls, value: str) -> str:
        if "://" in value or value.lower().startswith(("http:", "https:", "www.")):
            raise ValueError("hosted link reference must be an opaque token")
        return value


class QuoteLineItem(QuoteModel):
    description: str = Field(min_length=1)
    quantity: int = Field(ge=1)
    unit_price_minor: int = Field(ge=0)

    @property
    def total_minor(self) -> int:
        return self.quantity * self.unit_price_minor


class QuoteDraftProposal(QuoteModel):
    quote_id: QuoteId
    business_id: BusinessId
    recipient: CustomerRecipient
    currency: str = Field(min_length=3, max_length=3)
    line_items: tuple[QuoteLineItem, ...] = Field(min_length=1)
    tax_minor: int = Field(default=0, ge=0)
    discount_minor: int = Field(default=0, ge=0)
    owner_note: str | None = None
    hosted_links: tuple[HostedLinkReference, ...] = Field(default_factory=tuple)
    confidence: float | None = Field(default=None, ge=0, le=1)
    risk_flags: tuple[str, ...] = Field(default_factory=tuple)
    # True when the items were read out of the owner's free text rather than
    # the structured format; the approval reply then asks the owner to check them.
    drafted_from_free_text: bool = False

    @field_validator("currency")
    @classmethod
    def normalize_currency(cls, value: str) -> str:
        if not value.isalpha():
            raise ValueError("currency must contain three letters")
        return value.upper()

    @property
    def subtotal_minor(self) -> int:
        return sum(item.total_minor for item in self.line_items)

    @property
    def total_minor(self) -> int:
        return self.subtotal_minor + self.tax_minor - self.discount_minor

    @model_validator(mode="after")
    def total_must_not_be_negative(self) -> "QuoteDraftProposal":
        if self.total_minor < 0:
            raise ValueError("quote total must not be negative")
        return self


class QuoteAppointmentContext(QuoteModel):
    """What the matched appointment says about the job, for a drafter that
    reads free text. It informs descriptions and the note only, never prices."""

    event_name: str = Field(min_length=1)
    start_time: datetime
    address: str | None = None
    invitee_name: str = Field(min_length=1)
    # The invitee's booking answers as "question: answer" strings.
    notes: tuple[str, ...] = Field(default_factory=tuple)


class QuoteDraftRequest(QuoteModel):
    quote_id: QuoteId
    business_id: BusinessId
    conversation_id: ConversationId
    request_text: str = Field(min_length=1)
    revision: int = Field(ge=1)
    idempotency_key: str = Field(min_length=1)
    # Filled from an appointment when the request carries no customer line;
    # the drafter uses it only when the text names no customer itself.
    recipient: CustomerRecipient | None = None
    appointment: QuoteAppointmentContext | None = None


class FreeTextQuoteItem(QuoteModel):
    """A line item as read out of free text; the unit price is the owner's
    amount as written (e.g. ``"250"``, ``"1,250.00"``), not yet validated,
    or ``None`` when the text names no price for the item."""

    description: str = Field(min_length=1)
    quantity: int = Field(default=1, ge=1)
    unit_price: str | None = None


class FreeTextQuoteDraft(QuoteModel):
    line_items: tuple[FreeTextQuoteItem, ...] = Field(default_factory=tuple)
    owner_note: str | None = None
    ambiguities: tuple[str, ...] = Field(default_factory=tuple)


class FreeTextQuoteDraftingPort(Protocol):
    """Reads line items out of an owner's free-text request.

    Raises :class:`FreeTextQuoteDraftingError` when the request could not be
    read; the caller decides what the owner is told.
    """

    async def draft(self, request: QuoteDraftRequest) -> FreeTextQuoteDraft: ...


class FreeTextQuoteDraftingError(RuntimeError):
    """The free-text drafter failed. Adapters sanitize the message: no
    credentials and no raw provider responses."""


class QuoteSendAssessment(QuoteModel):
    confidence: float | None = Field(default=None, ge=0, le=1)
    risk_flags: tuple[str, ...] = Field(default_factory=tuple)
    suspected_mistake: bool = False


class QuoteSendDecision(QuoteModel):
    action: QuoteSendAction
    detail: str | None = None


class Quote(QuoteModel):
    quote_id: QuoteId
    business_id: BusinessId
    conversation_id: ConversationId
    conversation_ref: ConversationRef
    status: QuoteStatus
    revision: int = Field(ge=1)
    source_message_key: MessageKey
    last_message_key: MessageKey
    pending_request_text: str = Field(min_length=1)
    draft: QuoteDraftProposal | None = None
    approval_correlation_id: str | None = None
    delivery_receipt: DeliveryReceipt | None = None
    customer_appointment: Appointment | None = None
    customer_candidates: tuple[Appointment, ...] | None = None
    version: int = Field(default=1, ge=1)
    created_at: datetime
    updated_at: datetime

    @field_validator("created_at", "updated_at")
    @classmethod
    def timestamps_are_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("quote timestamps must be timezone-aware")
        return value

    @model_validator(mode="after")
    def validate_lifecycle(self) -> "Quote":
        drafted = {
            QuoteStatus.AWAITING_APPROVAL,
            QuoteStatus.APPROVED,
            QuoteStatus.DELIVERY_PENDING,
            QuoteStatus.DELIVERED,
        }
        if self.status in drafted and (self.draft is None or self.approval_correlation_id is None):
            raise ValueError("drafted quote states require a draft and approval correlation")
        if self.draft is not None and (
            self.draft.quote_id != self.quote_id or self.draft.business_id != self.business_id
        ):
            raise ValueError("draft identity must match quote identity")
        if self.conversation_ref.business_id != self.business_id:
            raise ValueError("quote conversation must belong to the quote business")
        if self.status in {QuoteStatus.DELIVERY_PENDING, QuoteStatus.DELIVERED} and (
            self.delivery_receipt is None
        ):
            raise ValueError("delivery states require a receipt")
        awaiting_selection = self.status is QuoteStatus.AWAITING_CUSTOMER_SELECTION
        if awaiting_selection != (self.customer_candidates is not None):
            raise ValueError("customer candidates exist exactly while a selection is awaited")
        if self.customer_candidates is not None and len(self.customer_candidates) < 2:
            raise ValueError("a customer selection needs at least two candidates")
        return self

    @property
    def is_active(self) -> bool:
        return self.status not in {
            QuoteStatus.REJECTED,
            QuoteStatus.DELIVERY_PENDING,
            QuoteStatus.DELIVERED,
        }

    def apply_draft(
        self, proposal: QuoteDraftProposal, message_key: MessageKey, now: datetime
    ) -> "Quote":
        if self.status is not QuoteStatus.DRAFTING:
            raise InvalidQuoteTransitionError(f"cannot draft quote in {self.status}")
        if proposal.quote_id != self.quote_id or proposal.business_id != self.business_id:
            raise ValueError("draft identity must match quote identity")
        return self.model_copy(
            update={
                "status": QuoteStatus.AWAITING_APPROVAL,
                "draft": proposal,
                "approval_correlation_id": approval_correlation_id(self.quote_id, self.revision),
                "last_message_key": message_key,
                "updated_at": now,
                "version": self.version + 1,
            }
        )

    def abandon_draft(self, message_key: MessageKey, now: datetime) -> "Quote":
        """Close a draft the drafting port refused so the owner can send a new one."""

        if self.status not in {QuoteStatus.DRAFTING, QuoteStatus.AWAITING_CUSTOMER_SELECTION}:
            raise InvalidQuoteTransitionError(f"cannot abandon quote in {self.status}")
        return self.model_copy(
            update={
                "status": QuoteStatus.REJECTED,
                "customer_candidates": None,
                "last_message_key": message_key,
                "updated_at": now,
                "version": self.version + 1,
            }
        )

    def await_customer_selection(
        self, candidates: tuple[Appointment, ...], message_key: MessageKey, now: datetime
    ) -> "Quote":
        if self.status is not QuoteStatus.DRAFTING:
            raise InvalidQuoteTransitionError(f"cannot ask for a customer in {self.status}")
        if len(candidates) < 2:
            raise ValueError("a customer selection needs at least two candidates")
        return self.model_copy(
            update={
                "status": QuoteStatus.AWAITING_CUSTOMER_SELECTION,
                "customer_candidates": candidates,
                "customer_appointment": None,
                "last_message_key": message_key,
                "updated_at": now,
                "version": self.version + 1,
            }
        )

    def select_customer(self, choice: int, message_key: MessageKey, now: datetime) -> "Quote":
        """Resume drafting with the ``choice``-th (1-based) listed appointment."""

        if self.status is not QuoteStatus.AWAITING_CUSTOMER_SELECTION:
            raise InvalidQuoteTransitionError(f"no customer selection is pending in {self.status}")
        candidates = self.customer_candidates or ()
        if not 1 <= choice <= len(candidates):
            raise ValueError(f"choice must be between 1 and {len(candidates)}")
        return self.model_copy(
            update={
                "status": QuoteStatus.DRAFTING,
                "customer_candidates": None,
                "customer_appointment": candidates[choice - 1],
                "last_message_key": message_key,
                "updated_at": now,
                "version": self.version + 1,
            }
        )

    def with_customer_appointment(self, appointment: Appointment) -> "Quote":
        if self.status is not QuoteStatus.DRAFTING:
            raise InvalidQuoteTransitionError(f"cannot pick a customer in {self.status}")
        return self.model_copy(update={"customer_appointment": appointment})

    def approve(self, message_key: MessageKey, now: datetime) -> "Quote":
        self._require_awaiting_approval()
        return self.model_copy(
            update={
                "status": QuoteStatus.APPROVED,
                "last_message_key": message_key,
                "updated_at": now,
                "version": self.version + 1,
            }
        )

    def reject(self, message_key: MessageKey, now: datetime) -> "Quote":
        self._require_awaiting_approval()
        return self.model_copy(
            update={
                "status": QuoteStatus.REJECTED,
                "last_message_key": message_key,
                "updated_at": now,
                "version": self.version + 1,
            }
        )

    def begin_correction(
        self,
        message_key: MessageKey,
        request_text: str,
        now: datetime,
    ) -> "Quote":
        self._require_awaiting_approval()
        return self.model_copy(
            update={
                "status": QuoteStatus.DRAFTING,
                "revision": self.revision + 1,
                "last_message_key": message_key,
                "pending_request_text": request_text,
                "draft": None,
                "approval_correlation_id": None,
                "updated_at": now,
                "version": self.version + 1,
            }
        )

    def record_delivery(self, receipt: DeliveryReceipt) -> "Quote":
        if self.status is not QuoteStatus.APPROVED:
            raise InvalidQuoteTransitionError(f"cannot deliver quote in {self.status}")
        if receipt.status is DeliveryStatus.FAILED:
            raise ValueError("failed delivery receipts do not complete quote delivery")
        status = (
            QuoteStatus.DELIVERED
            if receipt.status is DeliveryStatus.DELIVERED
            else QuoteStatus.DELIVERY_PENDING
        )
        return self.model_copy(
            update={
                "status": status,
                "delivery_receipt": receipt,
                "updated_at": receipt.occurred_at,
                "version": self.version + 1,
            }
        )

    def _require_awaiting_approval(self) -> None:
        if self.status is not QuoteStatus.AWAITING_APPROVAL:
            raise InvalidQuoteTransitionError(f"quote is not awaiting approval: {self.status}")


class QuoteSendPolicy(Protocol):
    def decide(self, assessment: QuoteSendAssessment) -> QuoteSendDecision: ...


class QuoteRepository(Protocol):
    async def get(self, business_id: BusinessId, quote_id: QuoteId) -> Quote | None: ...

    async def get_active(
        self, business_id: BusinessId, conversation_id: ConversationId
    ) -> Quote | None: ...

    async def get_by_message(
        self,
        business_id: BusinessId,
        conversation_id: ConversationId,
        message_key: MessageKey,
    ) -> Quote | None: ...

    async def add(self, quote: Quote) -> None: ...

    async def save(self, quote: Quote, *, expected_version: int) -> None: ...


class OwnerApprovalRequiredPolicy:
    def decide(self, assessment: QuoteSendAssessment) -> QuoteSendDecision:
        detail = "automatic sending is disabled"
        if assessment.suspected_mistake or assessment.risk_flags:
            detail = "owner review required because the draft may need correction"
        return QuoteSendDecision(action=QuoteSendAction.REQUIRE_APPROVAL, detail=detail)


class InvalidQuoteTransitionError(ValueError):
    pass


class QuoteConcurrencyError(RuntimeError):
    pass


class QuoteDraftRejectedError(ValueError):
    """A drafting port refused the request. The message is shown to the owner,
    so it must describe how to fix the request and must never carry provider
    responses, internals, or credentials."""


def new_quote(
    *,
    business_id: BusinessId,
    conversation_id: ConversationId,
    conversation_ref: ConversationRef,
    message_key: MessageKey,
    request_text: str,
    now: datetime,
) -> Quote:
    quote_id = QuoteId(
        uuid5(
            QUOTE_ID_NAMESPACE,
            f"{business_id}:{conversation_id}:{message_key}",
        )
    )
    return Quote(
        quote_id=quote_id,
        business_id=business_id,
        conversation_id=conversation_id,
        conversation_ref=conversation_ref,
        status=QuoteStatus.DRAFTING,
        revision=1,
        source_message_key=message_key,
        last_message_key=message_key,
        pending_request_text=request_text,
        created_at=now,
        updated_at=now,
    )


def quote_trigger_request_text(text: str) -> str | None:
    """The request after the ``quote:`` trigger, or ``None`` without a trigger.

    ``quote for Jane: ...`` is the same as ``quote:\\nfor: Jane\\n...``; the
    name filter is folded into the request text so one code path reads it.
    """

    match = QUOTE_TRIGGER_PATTERN.match(text.lstrip())
    if match is None:
        return None
    request_text = text.lstrip()[match.end() :].strip()
    name = match.group("name")
    if name is not None and name.strip():
        request_text = f"{CUSTOMER_NAME_LINE_KEY}: {name.strip()}\n{request_text}".strip()
    return request_text


def has_quote_trigger(message: NormalizedOwnerMessage) -> bool:
    text = "\n".join(part.text for part in message.parts if isinstance(part, TextPart))
    return quote_trigger_request_text(text) is not None


def _request_lines(request_text: str) -> list[tuple[str, str]]:
    lines: list[tuple[str, str]] = []
    for line in request_text.splitlines():
        key, separator, value = line.partition(":")
        if separator:
            lines.append((key.strip().casefold(), value.strip()))
    return lines


def has_customer_line(request_text: str) -> bool:
    return any(key in CUSTOMER_LINE_KEYS for key, _ in _request_lines(request_text))


def requested_customer_name(request_text: str) -> str | None:
    """The ``for: <name>`` filter, if the owner wrote one."""

    for key, value in _request_lines(request_text):
        if key == CUSTOMER_NAME_LINE_KEY and value:
            return value
    return None


def approval_correlation_id(quote_id: QuoteId, revision: int) -> str:
    return f"quote:{quote_id}:approval:{revision}"


def quote_text_command(quote: Quote) -> OutboxCommand:
    """Text the customer the link a delivery produced; only meaningful after
    ``record_delivery`` left a ``customer_link`` on the quote."""

    if quote.status not in {QuoteStatus.DELIVERY_PENDING, QuoteStatus.DELIVERED}:
        raise InvalidQuoteTransitionError("only delivered quotes can be texted to the customer")
    command_id = OutboxCommandId(uuid5(QUOTE_TEXT_COMMAND_NAMESPACE, str(quote.quote_id)))
    return OutboxCommand(
        command_id=command_id,
        business_id=quote.business_id,
        command_type=QUOTE_TEXT_COMMAND_TYPE,
        payload={"quote_id": str(quote.quote_id)},
        dedup_key=f"quote_text:{quote.quote_id}",
    )


def customer_quote_text(draft: QuoteDraftProposal, link: str) -> str:
    """The one-segment text that carries the quote link to the customer.

    The first item's description is shortened to fit; the link never is.
    """

    description = " ".join(draft.line_items[0].description.split())
    room = SMS_SEGMENT_LIMIT - len(CUSTOMER_TEXT_PREFIX) - len(CUSTOMER_TEXT_SUFFIX) - len(link)
    if room < 1:
        return f"Your Güd Vector quote is ready: {link}"
    if len(description) > room:
        description = description[:room].rstrip()
    return f"{CUSTOMER_TEXT_PREFIX}{description}{CUSTOMER_TEXT_SUFFIX}{link}"


def quote_delivery_command(quote: Quote) -> OutboxCommand:
    if quote.status is not QuoteStatus.APPROVED:
        raise InvalidQuoteTransitionError("only approved quotes can be queued for delivery")
    command_id = OutboxCommandId(uuid5(QUOTE_DELIVERY_COMMAND_NAMESPACE, str(quote.quote_id)))
    return OutboxCommand(
        command_id=command_id,
        business_id=quote.business_id,
        command_type=QUOTE_DELIVERY_COMMAND_TYPE,
        payload={"quote_id": str(quote.quote_id)},
        dedup_key=f"quote_delivery:{quote.quote_id}",
    )
