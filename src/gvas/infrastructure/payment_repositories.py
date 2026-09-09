from datetime import UTC, datetime

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from gvas.domain.enums import BillingInterval, QuotePaymentStatus
from gvas.domain.identifiers import BusinessId, CustomerId, QuoteId, SubscriptionId
from gvas.domain.payments import (
    QuotePaymentConflictError,
    QuotePaymentRecord,
    QuoteSubscriptionRecord,
)
from gvas.infrastructure.payment_models import (
    PaymentProviderEvent,
    QuotePayment,
    QuoteSubscription,
)


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _aware_or_none(value: datetime | None) -> datetime | None:
    return None if value is None else _aware(value)


class SqlQuotePaymentRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    @staticmethod
    def _record(row: QuotePayment) -> QuotePaymentRecord:
        return QuotePaymentRecord(
            payment_id=row.id,
            business_id=BusinessId(row.business_id),
            quote_id=QuoteId(row.quote_id),
            provider=row.provider,
            checkout_session_id=row.checkout_session_id,
            checkout_url=row.checkout_url,
            payment_intent_id=row.payment_intent_id,
            expires_at=_aware_or_none(row.expires_at),
            amount_minor=row.amount_cents,
            currency=row.currency,
            status=QuotePaymentStatus(row.status),
            created_at=_aware(row.created_at),
            updated_at=_aware(row.updated_at),
        )

    async def find_open(
        self, business_id: BusinessId, quote_id: QuoteId
    ) -> QuotePaymentRecord | None:
        row = await self.session.scalar(
            select(QuotePayment)
            .where(
                QuotePayment.business_id == business_id,
                QuotePayment.quote_id == quote_id,
                QuotePayment.status.in_(
                    (
                        QuotePaymentStatus.OPEN.value,
                        QuotePaymentStatus.PENDING.value,
                    )
                ),
            )
            .order_by(QuotePayment.created_at.desc())
            .limit(1)
        )
        return None if row is None else self._record(row)

    async def count_for_quote(self, business_id: BusinessId, quote_id: QuoteId) -> int:
        return (
            await self.session.scalar(
                select(func.count())
                .select_from(QuotePayment)
                .where(
                    QuotePayment.business_id == business_id,
                    QuotePayment.quote_id == quote_id,
                )
            )
            or 0
        )

    async def find_by_checkout_session(self, checkout_session_id: str) -> QuotePaymentRecord | None:
        row = await self.session.scalar(
            select(QuotePayment).where(QuotePayment.checkout_session_id == checkout_session_id)
        )
        return None if row is None else self._record(row)

    async def create(self, record: QuotePaymentRecord) -> None:
        row = QuotePayment(
            id=record.payment_id,
            business_id=record.business_id,
            quote_id=record.quote_id,
            provider=record.provider,
            checkout_session_id=record.checkout_session_id,
            checkout_url=record.checkout_url,
            payment_intent_id=record.payment_intent_id,
            expires_at=record.expires_at,
            amount_cents=record.amount_minor,
            currency=record.currency,
            status=record.status.value,
            created_at=record.created_at,
            updated_at=record.updated_at,
        )
        try:
            async with self.session.begin_nested():
                self.session.add(row)
                await self.session.flush()
        except IntegrityError as error:
            raise QuotePaymentConflictError(
                "a payment for this checkout session already exists"
            ) from error

    async def save(
        self,
        record: QuotePaymentRecord,
        *,
        expected_from: QuotePaymentStatus,
    ) -> None:
        """Status-guarded write: one UPDATE conditioned on the row still
        holding ``expected_from``, so two concurrently processed events cannot
        overwrite each other — the loser raises and is retried against fresh
        state."""

        result = await self.session.execute(
            update(QuotePayment)
            .where(
                QuotePayment.id == record.payment_id,
                QuotePayment.status == expected_from.value,
            )
            .values(
                checkout_session_id=record.checkout_session_id,
                checkout_url=record.checkout_url,
                payment_intent_id=record.payment_intent_id,
                expires_at=record.expires_at,
                amount_cents=record.amount_minor,
                currency=record.currency,
                status=record.status.value,
                updated_at=record.updated_at,
            )
        )
        if self._rowcount(result) != 1:
            raise QuotePaymentConflictError(f"payment attempt already moved past {expected_from}")

    @staticmethod
    def _rowcount(result: object) -> int:
        rowcount = getattr(result, "rowcount", None)
        return -1 if rowcount is None else int(rowcount)


class SqlQuoteSubscriptionRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    @staticmethod
    def _record(row: QuoteSubscription) -> QuoteSubscriptionRecord:
        return QuoteSubscriptionRecord(
            subscription_id=SubscriptionId(row.id),
            business_id=BusinessId(row.business_id),
            quote_id=QuoteId(row.quote_id),
            customer_id=CustomerId(row.customer_id),
            provider=row.provider,
            subscription_ref=row.stripe_subscription_id,
            status=row.status,
            interval=BillingInterval(row.interval),
            amount_minor=row.amount_cents,
            currency=row.currency,
            current_period_end=_aware_or_none(row.current_period_end),
            cancel_at_period_end=row.cancel_at_period_end,
            created_at=_aware(row.created_at),
            updated_at=_aware(row.updated_at),
        )

    async def find_by_subscription_ref(
        self, subscription_ref: str
    ) -> QuoteSubscriptionRecord | None:
        row = await self.session.scalar(
            select(QuoteSubscription).where(
                QuoteSubscription.stripe_subscription_id == subscription_ref
            )
        )
        return None if row is None else self._record(row)

    async def list_for_customer(
        self, business_id: BusinessId, customer_id: CustomerId
    ) -> tuple[QuoteSubscriptionRecord, ...]:
        rows = await self.session.scalars(
            select(QuoteSubscription)
            .where(
                QuoteSubscription.business_id == business_id,
                QuoteSubscription.customer_id == customer_id,
            )
            .order_by(QuoteSubscription.created_at.desc())
        )
        return tuple(self._record(row) for row in rows)

    async def create(self, record: QuoteSubscriptionRecord) -> None:
        row = QuoteSubscription(
            id=record.subscription_id,
            business_id=record.business_id,
            quote_id=record.quote_id,
            customer_id=record.customer_id,
            provider=record.provider,
            stripe_subscription_id=record.subscription_ref,
            status=record.status,
            interval=record.interval.value,
            amount_cents=record.amount_minor,
            currency=record.currency,
            current_period_end=record.current_period_end,
            cancel_at_period_end=record.cancel_at_period_end,
            created_at=record.created_at,
            updated_at=record.updated_at,
        )
        try:
            async with self.session.begin_nested():
                self.session.add(row)
                await self.session.flush()
        except IntegrityError as error:
            raise QuotePaymentConflictError("this subscription is already recorded") from error

    async def save(self, record: QuoteSubscriptionRecord) -> None:
        await self.session.execute(
            update(QuoteSubscription)
            .where(QuoteSubscription.id == record.subscription_id)
            .values(
                status=record.status,
                interval=record.interval.value,
                amount_cents=record.amount_minor,
                currency=record.currency,
                current_period_end=record.current_period_end,
                cancel_at_period_end=record.cancel_at_period_end,
                updated_at=record.updated_at,
            )
        )


class SqlPaymentEventRepository:
    """First writer wins: the row lands inside the caller's transaction, so a
    failed effect un-records the event and the provider's retry replays it."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def try_record(self, provider: str, event_id: str, now: datetime) -> bool:
        row = PaymentProviderEvent(provider=provider, event_id=event_id, received_at=now)
        try:
            async with self.session.begin_nested():
                self.session.add(row)
                await self.session.flush()
        except IntegrityError:
            return False
        return True
