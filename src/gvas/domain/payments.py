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

from gvas.domain.enums import BillingInterval, QuotePaymentStatus
from gvas.domain.identifiers import BusinessId, CustomerId, QuoteId, SubscriptionId

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
    # Set for a subscription checkout: every line item recurs on this
    # interval and the session is opened for ``customer_ref``, the provider's
    # handle for the paying customer. Unset means a one-time payment.
    recurring_interval: BillingInterval | None = None
    customer_ref: str | None = None

    @field_validator("currency")
    @classmethod
    def normalize_currency(cls, value: str) -> str:
        if not value.isalpha():
            raise ValueError("currency must contain three letters")
        return value.lower()

    @property
    def is_subscription(self) -> bool:
        return self.recurring_interval is not None


class PaymentCheckoutResult(PaymentModel):
    session_id: str = Field(min_length=1)
    checkout_url: str = Field(min_length=1)
    payment_intent_id: str | None = None
    # When the provider abandons an unpaid session; None means it does not
    # expire on its own.
    expires_at: datetime | None = None


class BillingCustomerRequest(PaymentModel):
    """Opens the provider-side customer a subscription is billed to."""

    business_id: BusinessId
    customer_id: CustomerId
    email: str = Field(min_length=3)
    name: str | None = None
    phone: str | None = None
    idempotency_key: str = Field(min_length=1)
    metadata: dict[str, str] = Field(default_factory=dict)


class BillingCustomerResult(PaymentModel):
    customer_ref: str = Field(min_length=1)


class BillingPortalRequest(PaymentModel):
    business_id: BusinessId
    customer_ref: str = Field(min_length=1)
    return_url: str = Field(min_length=1)


class BillingPortalResult(PaymentModel):
    url: str = Field(min_length=1)


class PaymentCheckoutError(RuntimeError):
    """Raised when a checkout provider could not open a session.

    Adapters sanitize the message: no credentials and no raw provider
    responses; the caller maps it to a neutral reply.
    """


class PaymentEventOutcome(StrEnum):
    """What one provider event means for the checkout attempt it names."""

    SUCCEEDED = "succeeded"
    PENDING = "pending"  # completed but the money has not settled yet
    FAILED = "failed"
    OTHER = "other"
    # Subscription lifecycle, keyed by ``subscription.subscription_ref``.
    SUBSCRIPTION_RENEWED = "subscription_renewed"
    SUBSCRIPTION_PAYMENT_FAILED = "subscription_payment_failed"
    SUBSCRIPTION_UPDATED = "subscription_updated"
    SUBSCRIPTION_CANCELLED = "subscription_cancelled"


class SubscriptionEventData(PaymentModel):
    """What a provider tells us about one subscription in one event; only
    the fields the event carried are set."""

    subscription_ref: str = Field(min_length=1)
    customer_ref: str | None = None
    status: str | None = None
    interval: BillingInterval | None = None
    #: The recurring price; only subscription objects carry it.
    amount_minor: int | None = Field(default=None, ge=0)
    #: What one invoice actually collected (credits, proration, tax included);
    #: reported to the owner, never written over the recurring price.
    paid_minor: int | None = Field(default=None, ge=0)
    currency: str | None = None
    current_period_end: datetime | None = None
    cancel_at_period_end: bool | None = None


class PaymentWebhookEvent(PaymentModel):
    """One verified provider notification, normalized for the application."""

    provider: str = Field(min_length=1)
    event_id: str = Field(min_length=1)
    event_type: str = Field(min_length=1)
    outcome: PaymentEventOutcome = PaymentEventOutcome.OTHER
    checkout_session_id: str | None = None
    payment_intent_id: str | None = None
    metadata: dict[str, str] = Field(default_factory=dict)
    # Present on a subscription-mode checkout completion and on every
    # subscription lifecycle event.
    subscription: SubscriptionEventData | None = None


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
    expires_at: datetime | None = None
    amount_minor: int = Field(ge=0)
    currency: str = Field(min_length=3, max_length=3)
    status: QuotePaymentStatus = QuotePaymentStatus.OPEN
    created_at: datetime
    updated_at: datetime

    @field_validator("created_at", "updated_at", "expires_at")
    @classmethod
    def timestamps_are_aware(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return value
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("payment timestamps must be timezone-aware")
        return value

    @property
    def is_open(self) -> bool:
        return self.status is QuotePaymentStatus.OPEN

    def is_expired(self, now: datetime) -> bool:
        """An open session the provider has already abandoned: unusable even
        though the provider never told us so directly."""

        return self.status is QuotePaymentStatus.OPEN and (
            self.expires_at is not None and self.expires_at <= now
        )

    def mark_paid(self, payment_intent_id: str | None, now: datetime) -> "QuotePaymentRecord":
        if self.status is QuotePaymentStatus.PAID:
            return self
        # An async attempt can fail first and still settle later, a pending
        # one settles once its delayed payment lands, and a session that
        # completed just before its deadline arrives at us already expired —
        # the provider's success is authoritative either way.
        if self.status not in (
            QuotePaymentStatus.OPEN,
            QuotePaymentStatus.PENDING,
            QuotePaymentStatus.FAILED,
            QuotePaymentStatus.EXPIRED,
        ):
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

    def mark_expired(self, now: datetime) -> "QuotePaymentRecord":
        """Retire an attempt the provider will no longer settle. Also closes a
        pending attempt superseded by a sibling that already collected."""

        if self.status not in (QuotePaymentStatus.OPEN, QuotePaymentStatus.PENDING):
            return self
        return self.model_copy(update={"status": QuotePaymentStatus.EXPIRED, "updated_at": now})

    def mark_pending(self, now: datetime) -> "QuotePaymentRecord":
        """Checkout completed but the payment is still settling: the attempt
        can no longer expire and resolves only as paid or failed."""

        if self.status is not QuotePaymentStatus.OPEN:
            return self
        return self.model_copy(update={"status": QuotePaymentStatus.PENDING, "updated_at": now})


class InvalidPaymentTransitionError(ValueError):
    pass


class QuotePaymentConflictError(RuntimeError):
    """A payment row for the same checkout session already exists."""


class QuotePaymentRepository(Protocol):
    async def find_open(
        self, business_id: BusinessId, quote_id: QuoteId
    ) -> QuotePaymentRecord | None:
        """The newest attempt still in play — open, or completed and awaiting
        asynchronous settlement; ``is_expired`` decides whether an open one is
        still usable."""
        ...

    async def find_by_checkout_session(
        self, checkout_session_id: str
    ) -> QuotePaymentRecord | None: ...

    async def count_for_quote(self, business_id: BusinessId, quote_id: QuoteId) -> int: ...

    async def create(self, record: QuotePaymentRecord) -> None:
        """Raises :class:`QuotePaymentConflictError` when the session id is taken."""
        ...

    async def save(
        self,
        record: QuotePaymentRecord,
        *,
        expected_from: QuotePaymentStatus,
    ) -> None:
        """Write the transition only if the row still holds ``expected_from``;
        a concurrent webhook that moved it first raises
        :class:`QuotePaymentConflictError` so the caller's event rolls back and
        is retried against fresh state."""
        ...


class QuoteSubscriptionRecord(PaymentModel):
    """The recurring agreement a paid recurring quote turned into."""

    subscription_id: SubscriptionId
    business_id: BusinessId
    quote_id: QuoteId
    customer_id: CustomerId
    provider: str = Field(min_length=1)
    subscription_ref: str = Field(min_length=1)
    status: str = Field(min_length=1)
    interval: BillingInterval
    amount_minor: int = Field(ge=0)
    currency: str = Field(min_length=3, max_length=3)
    current_period_end: datetime | None = None
    cancel_at_period_end: bool = False
    created_at: datetime
    updated_at: datetime

    @field_validator("created_at", "updated_at", "current_period_end")
    @classmethod
    def timestamps_are_aware(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return value
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("subscription timestamps must be timezone-aware")
        return value

    def apply(self, data: SubscriptionEventData, now: datetime) -> "QuoteSubscriptionRecord":
        """Fold the fields an event carried into the row."""

        update: dict[str, object] = {"updated_at": now}
        if data.status is not None:
            update["status"] = data.status
        if data.interval is not None:
            update["interval"] = data.interval
        if data.amount_minor is not None:
            update["amount_minor"] = data.amount_minor
        if data.currency is not None:
            update["currency"] = data.currency.upper()
        if data.current_period_end is not None:
            update["current_period_end"] = data.current_period_end
        if data.cancel_at_period_end is not None:
            update["cancel_at_period_end"] = data.cancel_at_period_end
        return self.model_copy(update=update)


class QuoteSubscriptionRepository(Protocol):
    async def find_by_subscription_ref(
        self, subscription_ref: str
    ) -> QuoteSubscriptionRecord | None: ...

    async def list_for_customer(
        self, business_id: BusinessId, customer_id: CustomerId
    ) -> tuple[QuoteSubscriptionRecord, ...]: ...

    async def create(self, record: QuoteSubscriptionRecord) -> None:
        """Raises :class:`QuotePaymentConflictError` when the provider
        subscription is already recorded."""
        ...

    async def save(self, record: QuoteSubscriptionRecord) -> None: ...


class PaymentEventRepository(Protocol):
    """Replay ledger for provider webhooks: first record wins, so a retried
    event is answered from what the first delivery already did."""

    async def try_record(self, provider: str, event_id: str, now: datetime) -> bool:
        """True when this delivery recorded the event; False on a replay."""
        ...
