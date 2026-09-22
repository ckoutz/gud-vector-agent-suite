from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from gvas.infrastructure.models import Base


class QuotePayment(Base):
    """One checkout attempt against one quote.

    ``checkout_session_id`` is unique so two accepts cannot create two rows for
    the same provider session; ``(business_id, quote_id)`` keeps lookups
    tenant-scoped.
    """

    __tablename__ = "quote_payments"
    __table_args__ = (
        ForeignKeyConstraint(
            ["business_id", "quote_id"],
            ["quotes.business_id", "quotes.id"],
            ondelete="CASCADE",
        ),
        UniqueConstraint("checkout_session_id", name="uq_quote_payments_checkout_session_id"),
        Index("ix_quote_payments_business_id_quote_id", "business_id", "quote_id"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    business_id: Mapped[UUID] = mapped_column(
        ForeignKey("businesses.id", ondelete="CASCADE"), nullable=False
    )
    quote_id: Mapped[UUID] = mapped_column(nullable=False)
    provider: Mapped[str] = mapped_column(String(50), nullable=False)
    checkout_session_id: Mapped[str] = mapped_column(String(255), nullable=False)
    checkout_url: Mapped[str] = mapped_column(String(2048), nullable=False)
    payment_intent_id: Mapped[str | None] = mapped_column(String(255))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    amount_cents: Mapped[int] = mapped_column(BigInteger, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    status: Mapped[str] = mapped_column(String(50), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class QuoteSubscription(Base):
    """The recurring agreement a paid recurring quote became; one row per
    provider subscription, tenant-scoped to its quote and customer."""

    __tablename__ = "quote_subscriptions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["business_id", "quote_id"],
            ["quotes.business_id", "quotes.id"],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["business_id", "customer_id"],
            ["customers.business_id", "customers.id"],
            ondelete="CASCADE",
        ),
        UniqueConstraint(
            "stripe_subscription_id", name="uq_quote_subscriptions_stripe_subscription_id"
        ),
        Index("ix_quote_subscriptions_business_id_customer_id", "business_id", "customer_id"),
        Index("ix_quote_subscriptions_business_id_quote_id", "business_id", "quote_id"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    business_id: Mapped[UUID] = mapped_column(
        ForeignKey("businesses.id", ondelete="CASCADE"), nullable=False
    )
    quote_id: Mapped[UUID] = mapped_column(nullable=False)
    customer_id: Mapped[UUID] = mapped_column(nullable=False)
    provider: Mapped[str] = mapped_column(String(50), nullable=False)
    stripe_subscription_id: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(50), nullable=False)
    interval: Mapped[str] = mapped_column(String(10), nullable=False)
    amount_cents: Mapped[int] = mapped_column(BigInteger, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    current_period_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancel_at_period_end: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class PaymentProviderEvent(Base):
    """Replay ledger for payment webhooks: ``(provider, event_id)`` recorded in
    the same transaction as its effects, so a retried delivery answers from the
    row instead of repeating work."""

    __tablename__ = "payment_provider_events"

    provider: Mapped[str] = mapped_column(String(50), primary_key=True)
    event_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


__all__ = ["PaymentProviderEvent", "QuotePayment", "QuoteSubscription"]
