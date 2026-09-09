from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from gvas.domain.enums import QuotePaymentStatus
from gvas.domain.identifiers import BusinessId, QuoteId
from gvas.domain.payments import QuotePaymentConflictError, QuotePaymentRecord
from gvas.infrastructure.payment_models import PaymentProviderEvent, QuotePayment


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

    async def save(self, record: QuotePaymentRecord) -> None:
        row = await self.session.scalar(
            select(QuotePayment).where(QuotePayment.id == record.payment_id)
        )
        if row is None:
            raise QuotePaymentConflictError("payment row is missing")
        row.checkout_session_id = record.checkout_session_id
        row.checkout_url = record.checkout_url
        row.payment_intent_id = record.payment_intent_id
        row.expires_at = record.expires_at
        row.amount_cents = record.amount_minor
        row.currency = record.currency
        row.status = record.status.value
        row.updated_at = record.updated_at


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
