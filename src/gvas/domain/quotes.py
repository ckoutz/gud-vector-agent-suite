from datetime import datetime
from typing import Protocol
from uuid import UUID, uuid5

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

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
QUOTE_DELIVERY_COMMAND_TYPE = "customer_quote.deliver"
QUOTE_ID_NAMESPACE = UUID("391d4c69-e58a-4621-ad77-1f45ac243ae2")
QUOTE_DELIVERY_COMMAND_NAMESPACE = UUID("e940a293-273c-4914-b17b-63ac10db4db4")


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


class QuoteDraftRequest(QuoteModel):
    quote_id: QuoteId
    business_id: BusinessId
    conversation_id: ConversationId
    request_text: str = Field(min_length=1)
    revision: int = Field(ge=1)
    idempotency_key: str = Field(min_length=1)


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

        if self.status is not QuoteStatus.DRAFTING:
            raise InvalidQuoteTransitionError(f"cannot abandon quote in {self.status}")
        return self.model_copy(
            update={
                "status": QuoteStatus.REJECTED,
                "last_message_key": message_key,
                "updated_at": now,
                "version": self.version + 1,
            }
        )

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


def has_quote_trigger(message: NormalizedOwnerMessage) -> bool:
    text = "\n".join(part.text for part in message.parts if isinstance(part, TextPart))
    return text.lstrip().lower().startswith(QUOTE_TRIGGER_PREFIX)


def approval_correlation_id(quote_id: QuoteId, revision: int) -> str:
    return f"quote:{quote_id}:approval:{revision}"


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
