"""Hosted quote payments: checkout handoff and webhook bookkeeping.

Provider-neutral: the records name a ``provider`` string but never import or
know a vendor's API. The checkout port lives in ``gvas.domain.ports``; these
are the values it moves.
"""

from datetime import datetime
from enum import StrEnum
from typing import Protocol
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from gvas.domain.enums import QuotePaymentStatus
from gvas.domain.identifiers import BusinessId, QuoteId

#: The only card-checkout provider wired today; kept as data, not code, so a
#: second provider is a new adapter plus a new literal here.
STRIPE_PROVIDER = "stripe"


class PaymentModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class PaymentLineItem(PaymentModel):
    """One chargeable line handed to the checkout provider."""

    description: str = Field(min_length=1)
    quantity: int = Field(ge=1)
    amount_minor: int = Field(ge=0)


class PaymentCheckoutRequest(PaymentModel):
    """Everything a checkout adapter needs to open one payment session."""

    business_id: BusinessId
    quote_id: QuoteId
    # The public-facing identifier the provider records; never the row id.
    client_reference: str = Field(min_length=1)
    currency: str = Field(min_length=3, max_length=3)
    line_items: tuple[PaymentLineItem, ...] = Field(min_length=1)
    success_url: str = Field(min_length=1)
    cancel_url: str = Field(min_length=1)
    idempotency_key: str = Field(min_length=1)
    metadata: dict[str, str] = Field(default_factory=dict)

    @field_validator("currency")
    @classmethod
    def normalize_currency(cls, value: str) -> str:
        if not value.isalpha():
            raise ValueError("currency must contain three letters")
        return value.lower()


class PaymentCheckoutResult(PaymentModel):
    session_id: str = Field(min_length=1)
    checkout_url: str = Field(min_length=1)
    payment_intent_id: str | None = None


class PaymentCheckoutError(RuntimeError):
    """Raised when a checkout provider could not open a session.

    Adapters sanitize the message: no credentials and no raw provider
    responses; the caller maps it to a neutral reply.
    """


class PaymentEventOutcome(StrEnum):
    """What one provider event means for the checkout attempt it names."""

    SUCCEEDED = "succeeded"
    FAILED = "failed"
    OTHER = "other"


class PaymentWebhookEvent(PaymentModel):
    """One verified provider notification, normalized for the application."""

    provider: str = Field(min_length=1)
    event_id: str = Field(min_length=1)
    event_type: str = Field(min_length=1)
    outcome: PaymentEventOutcome = PaymentEventOutcome.OTHER
    checkout_session_id: str | None = None
    payment_intent_id: str | None = None
    metadata: dict[str, str] = Field(default_factory=dict)


class QuotePaymentRecord(PaymentModel):
    """One checkout attempt against one quote.

    ``checkout_session_id`` is unique: two accepts cannot create two payment
    rows for the same provider session.
    """

    payment_id: UUID
    business_id: BusinessId
    quote_id: QuoteId
    provider: str = Field(min_length=1)
    checkout_session_id: str = Field(min_length=1)
    checkout_url: str = Field(min_length=1)
    payment_intent_id: str | None = None
    amount_minor: int = Field(ge=0)
    currency: str = Field(min_length=3, max_length=3)
    status: QuotePaymentStatus = QuotePaymentStatus.OPEN
    created_at: datetime
    updated_at: datetime

    @field_validator("created_at", "updated_at")
    @classmethod
    def timestamps_are_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("payment timestamps must be timezone-aware")
        return value

    @property
    def is_open(self) -> bool:
        return self.status is QuotePaymentStatus.OPEN

    def mark_paid(self, payment_intent_id: str | None, now: datetime) -> "QuotePaymentRecord":
        if self.status is QuotePaymentStatus.PAID:
            return self
        # An async attempt can fail first and still settle later.
        if self.status not in (QuotePaymentStatus.OPEN, QuotePaymentStatus.FAILED):
            raise InvalidPaymentTransitionError(f"payment cannot be paid from {self.status}")
        return self.model_copy(
            update={
                "status": QuotePaymentStatus.PAID,
                "payment_intent_id": payment_intent_id or self.payment_intent_id,
                "updated_at": now,
            }
        )

    def mark_failed(self, now: datetime) -> "QuotePaymentRecord":
        if self.status is QuotePaymentStatus.PAID:
            return self
        return self.model_copy(update={"status": QuotePaymentStatus.FAILED, "updated_at": now})


class InvalidPaymentTransitionError(ValueError):
    pass


class QuotePaymentConflictError(RuntimeError):
    """A payment row for the same checkout session already exists."""


class QuotePaymentRepository(Protocol):
    async def find_open(
        self, business_id: BusinessId, quote_id: QuoteId
    ) -> QuotePaymentRecord | None: ...

    async def find_by_checkout_session(
        self, checkout_session_id: str
    ) -> QuotePaymentRecord | None: ...

    async def create(self, record: QuotePaymentRecord) -> None:
        """Raises :class:`QuotePaymentConflictError` when the session id is taken."""
        ...

    async def save(self, record: QuotePaymentRecord) -> None: ...


class PaymentEventRepository(Protocol):
    """Replay ledger for provider webhooks: first record wins, so a retried
    event is answered from what the first delivery already did."""

    async def try_record(self, provider: str, event_id: str, now: datetime) -> bool:
        """True when this delivery recorded the event; False on a replay."""
        ...
