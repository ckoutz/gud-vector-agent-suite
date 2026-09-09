"""Public quote use cases: view, accept, decline and hosted checkout.

Everything here is scoped by claim token, never by business id: a token
resolves to exactly one quote and every record it touches stays inside that
quote's business. Tokens are looked up by their SHA-256 hash and compared in
constant time so the raw token is the only secret.
"""

import logging
from collections.abc import Callable
from datetime import UTC, datetime
from uuid import uuid4

from gvas.domain.enums import CustomerQuoteStatus, QuotePaymentStatus
from gvas.domain.identifiers import BusinessId
from gvas.domain.messages import OutboundOwnerMessage, TextPart
from gvas.domain.money import format_money
from gvas.domain.outbox import owner_reply_command
from gvas.domain.payments import (
    STRIPE_PROVIDER,
    PaymentCheckoutRequest,
    PaymentEventOutcome,
    PaymentLineItem,
    PaymentWebhookEvent,
    QuotePaymentConflictError,
    QuotePaymentRecord,
)
from gvas.domain.ports import PaymentCheckoutPort
from gvas.domain.quotes import (
    InvalidQuoteTransitionError,
    Quote,
    QuoteConcurrencyError,
    QuoteDraftProposal,
    claim_token_matches,
    delivery_line_items,
    hash_claim_token,
    public_quote_id,
)
from gvas.domain.repositories import BusinessRecord, UnitOfWork

logger = logging.getLogger(__name__)


class QuoteNotFoundError(ValueError):
    """The token did not cleanly identify one claimable quote; shown as 404."""


class OpenCheckoutUnavailableError(RuntimeError):
    """Accepting is configured off: no checkout provider is wired."""


class UnknownPaymentSessionError(RuntimeError):
    """A webhook named a checkout session we have not recorded yet.

    The provider can deliver its event before ``accept_quote`` commits the
    session row; answering 2xx would consume the event forever, so the
    endpoint answers 503 and the provider's retry lands after the record
    exists.
    """


class PublicQuoteView:
    """The customer-facing projection of one hosted quote: only fields a
    customer may see — no internal ids beyond the public opaque id and no
    owner contact details."""

    def __init__(self, business: BusinessRecord, quote: Quote) -> None:
        draft = quote.draft
        if draft is None:
            raise QuoteNotFoundError("quote is not ready")
        self.business_display_name = business.display_name or business.name
        self.business_site_url = business.site_url or ""
        self.quote_public_id = public_quote_id(quote.quote_id)
        self.quote_status = (
            quote.customer_status.value
            if quote.customer_status is not None
            else CustomerQuoteStatus.VIEWED.value
        )
        self.customer_name = draft.recipient.display_name
        self.service_address = draft.recipient.service_address
        self.items = [
            {
                "description": item.description,
                "quantity": item.quantity,
                "amountCents": item.quantity * item.unit_price_minor,
            }
            for item in delivery_line_items(draft)
        ]
        self.subtotal_cents = draft.subtotal_minor
        self.total_cents = draft.total_minor
        self.currency = draft.currency
        self.note = draft.owner_note
        self.created_at = quote.created_at
        self.approved_at = quote.approved_at

    def as_payload(self) -> dict[str, object]:
        return {
            "business": {
                "displayName": self.business_display_name,
                "siteUrl": self.business_site_url,
            },
            "quote": {
                "id": self.quote_public_id,
                "status": self.quote_status,
                "customerName": self.customer_name,
                "serviceAddress": self.service_address,
                "items": self.items,
                "subtotalCents": self.subtotal_cents,
                "totalCents": self.total_cents,
                "currency": self.currency,
                "note": self.note,
                "createdAt": self.created_at.isoformat(),
                "approvedAt": (
                    self.approved_at.isoformat() if self.approved_at is not None else None
                ),
            },
        }


def checkout_line_items(draft: QuoteDraftProposal) -> tuple[PaymentLineItem, ...]:
    """The draft as checkout line items.

    Checkout providers cannot take negative-priced lines, so when a discount
    is in play the whole quote collapses into a single total line — the charge
    then still equals ``draft.total_minor``. Tax travels as its own positive
    line.
    """

    items = delivery_line_items(draft)
    if any(item.unit_price_minor < 0 for item in items):
        return (
            PaymentLineItem(description="Quote total", quantity=1, amount_minor=draft.total_minor),
        )
    return tuple(
        PaymentLineItem(
            description=item.description,
            quantity=item.quantity,
            amount_minor=item.unit_price_minor,
        )
        for item in items
    )


class PublicQuoteService:
    """Runs the customer-facing use cases inside the unit of work."""

    def __init__(
        self,
        unit_of_work_factory: Callable[[], UnitOfWork],
        checkout: PaymentCheckoutPort | None = None,
    ) -> None:
        self._unit_of_work_factory = unit_of_work_factory
        self._checkout = checkout

    async def fetch_quote(self, claim_token: str) -> PublicQuoteView:
        """Return the projection; the first successful fetch marks the quote
        viewed and repeat fetches change nothing."""

        async with self._unit_of_work_factory() as unit_of_work:
            quote = await self._find_claimable(unit_of_work, claim_token)
            business = await self._business(unit_of_work, quote.business_id)
            viewed = quote.record_customer_view(_now())
            if viewed is not quote:
                try:
                    await unit_of_work.quotes.save(viewed, expected_version=quote.version)
                except QuoteConcurrencyError:
                    # A concurrent fetch won the view marker; the answer is the same.
                    pass
            await unit_of_work.commit()
            return PublicQuoteView(business, viewed)

    async def accept_quote(self, claim_token: str) -> str:
        """Return the hosted checkout URL, opening a session on first accept.

        While an open session stands, repeat accepts return its URL. The
        provider call happens between two transactions: the first records the
        customer status, the second records the session — a crash or a racing
        accept reuses the same provider idempotency key, so the same session
        comes back and the unique ``checkout_session_id`` settles it.
        """

        if self._checkout is None:
            raise OpenCheckoutUnavailableError("checkout is not configured")
        async with self._unit_of_work_factory() as unit_of_work:
            quote = await self._find_claimable(unit_of_work, claim_token)
            accepted = quote.record_customer_accept(_now())
            open_payment = await unit_of_work.quote_payments.find_open(
                quote.business_id, quote.quote_id
            )
            if open_payment is not None and open_payment.is_expired(_now()):
                # The provider abandons unpaid sessions without telling us;
                # retire the attempt and open a fresh one below.
                await unit_of_work.quote_payments.save(open_payment.mark_expired(_now()))
                open_payment = None
            if open_payment is not None:
                if accepted is not quote:
                    await self._tolerate_race(unit_of_work, quote, accepted)
                await unit_of_work.commit()
                return open_payment.checkout_url
            if accepted is not quote:
                await self._tolerate_race(unit_of_work, quote, accepted)
            draft = accepted.draft
            business = await self._business(unit_of_work, quote.business_id)
            if draft is None or accepted.claim_token is None:
                raise QuoteNotFoundError("quote is not ready")
            quote_url = accepted.quote_url(business.site_url or "")
            items = checkout_line_items(draft)
            currency = draft.currency
            # One idempotency scope per attempt: the first accept reuses the
            # delivery key; a regenerated session (the previous one expired)
            # must not replay the provider's earlier answer.
            attempt = await unit_of_work.quote_payments.count_for_quote(
                quote.business_id, quote.quote_id
            )
            idempotency_key = (
                f"quote-delivery:{quote.quote_id}"
                if attempt == 0
                else f"quote-delivery:{quote.quote_id}:attempt-{attempt + 1}"
            )
            request = PaymentCheckoutRequest(
                business_id=quote.business_id,
                quote_id=quote.quote_id,
                client_reference=public_quote_id(quote.quote_id),
                currency=currency,
                line_items=items,
                success_url=f"{quote_url}?paid=1",
                cancel_url=quote_url,
                idempotency_key=idempotency_key,
                metadata={
                    "gvas_quote_id": public_quote_id(quote.quote_id),
                    "business_id": str(quote.business_id),
                },
            )
            await unit_of_work.commit()
        result = await self._checkout.create_checkout(request)
        async with self._unit_of_work_factory() as unit_of_work:
            record = QuotePaymentRecord(
                payment_id=uuid4(),
                business_id=quote.business_id,
                quote_id=quote.quote_id,
                provider=STRIPE_PROVIDER,
                checkout_session_id=result.session_id,
                checkout_url=result.checkout_url,
                payment_intent_id=result.payment_intent_id,
                expires_at=result.expires_at,
                amount_minor=draft.total_minor,
                currency=currency,
                created_at=_now(),
                updated_at=_now(),
            )
            try:
                await unit_of_work.quote_payments.create(record)
            except QuotePaymentConflictError:
                existing = await unit_of_work.quote_payments.find_by_checkout_session(
                    result.session_id
                )
                if existing is None:
                    raise
                await unit_of_work.commit()
                return existing.checkout_url
            await unit_of_work.commit()
            return record.checkout_url

    async def decline_quote(self, claim_token: str) -> str:
        async with self._unit_of_work_factory() as unit_of_work:
            quote = await self._find_claimable(unit_of_work, claim_token)
            declined = quote.record_customer_decline(_now())
            if declined is not quote:
                try:
                    await unit_of_work.quotes.save(declined, expected_version=quote.version)
                except QuoteConcurrencyError as error:
                    # The quote moved underneath this request (a racing accept
                    # or a payment); reporting a decline that did not persist
                    # would lie, so the caller retries against fresh state.
                    raise InvalidQuoteTransitionError(
                        "the quote changed while declining; retry the request"
                    ) from error
            await unit_of_work.commit()
            return (
                declined.customer_status.value
                if declined.customer_status is not None
                else CustomerQuoteStatus.DECLINED.value
            )

    async def booking_link(self, public_key: str) -> dict[str, str]:
        async with self._unit_of_work_factory() as unit_of_work:
            business = await unit_of_work.businesses.get_by_public_key(public_key)
            await unit_of_work.commit()
        if business is None or business.calendly_url is None:
            raise QuoteNotFoundError("unknown business")
        return {
            "calendlyUrl": business.calendly_url,
            "displayName": business.display_name or business.name,
        }

    async def record_payment_event(self, event: PaymentWebhookEvent) -> bool:
        """Apply one verified payment event; False when the event id was
        already recorded so the caller answers without redoing work.

        The dedup row, the payment transition, the quote's customer status and
        the owner notice all land in one transaction, so a mid-failure rolls
        the recording back and the provider's retry replays cleanly.
        """

        if event.outcome is PaymentEventOutcome.OTHER:
            return False
        async with self._unit_of_work_factory() as unit_of_work:
            recorded = await unit_of_work.payment_events.try_record(
                event.provider, event.event_id, _now()
            )
            if not recorded:
                await unit_of_work.commit()
                return False
            if event.checkout_session_id is None:
                await unit_of_work.commit()
                return True
            payment = await unit_of_work.quote_payments.find_by_checkout_session(
                event.checkout_session_id
            )
            if payment is None:
                # Raise instead of committing: the session row may still be on
                # its way (accept commits it after the provider call), and a
                # failed response lets the provider retry once it exists.
                raise UnknownPaymentSessionError(event.checkout_session_id)
            now = _now()
            if event.outcome is PaymentEventOutcome.FAILED:
                await unit_of_work.quote_payments.save(payment.mark_failed(now))
                await unit_of_work.commit()
                return True
            if payment.status is not QuotePaymentStatus.PAID:
                await unit_of_work.quote_payments.save(
                    payment.mark_paid(event.payment_intent_id, now)
                )
            quote = await unit_of_work.quotes.get(payment.business_id, payment.quote_id)
            if quote is None:
                await unit_of_work.commit()
                return True
            paid = quote.record_customer_payment(now)
            if paid is not quote:
                await unit_of_work.quotes.save(paid, expected_version=quote.version)
            await self._enqueue_paid_notice(unit_of_work, paid)
            await unit_of_work.commit()
            return True

    async def _enqueue_paid_notice(self, unit_of_work: UnitOfWork, quote: Quote) -> None:
        """Tell the owner the money landed, in the conversation where the
        quote was approved. The correlation id makes the notice replay-safe
        beyond the event ledger."""

        source = await unit_of_work.inbound_messages.find_by_key(
            quote.business_id, quote.conversation_id, quote.source_message_key
        )
        if source is None:
            logger.warning("paid notice has no anchor message for the quote")
            return
        correlation_id = f"quote:{quote.quote_id}:paid"
        existing = await unit_of_work.outbound_messages.find_by_correlation(
            quote.business_id, quote.conversation_id, correlation_id
        )
        if existing is not None:
            return
        draft = quote.draft
        customer = ""
        total = ""
        if draft is not None:
            customer = f" for {draft.recipient.display_name or 'customer'}"
            total = f" — {format_money(draft.total_minor, draft.currency)} paid"
        message = OutboundOwnerMessage(
            business_id=quote.business_id,
            conversation_ref=quote.conversation_ref,
            parts=(TextPart(text=f"Quote {public_quote_id(quote.quote_id)}{customer}{total}"),),
            correlation_id=correlation_id,
        )
        outbound_message_id = await unit_of_work.outbound_messages.create(
            message, quote.conversation_id, source.inbound_message_id
        )
        await unit_of_work.outbox.enqueue(
            owner_reply_command(quote.business_id, outbound_message_id)
        )

    async def _find_claimable(self, unit_of_work: UnitOfWork, claim_token: str) -> Quote:
        quote = await unit_of_work.quotes.get_by_claim_hash(hash_claim_token(claim_token))
        if quote is None or quote.claim_token_hash is None:
            raise QuoteNotFoundError("unknown or expired quote link")
        if not claim_token_matches(claim_token, quote.claim_token_hash):
            raise QuoteNotFoundError("unknown or expired quote link")
        if not quote.is_claimable():
            raise QuoteNotFoundError("unknown or expired quote link")
        return quote

    @staticmethod
    async def _tolerate_race(unit_of_work: UnitOfWork, quote: Quote, updated: Quote) -> None:
        """Losing the optimistic write means a racing request already moved
        the quote — both sides converge on the same provider identity, so the
        loser simply continues."""

        try:
            await unit_of_work.quotes.save(updated, expected_version=quote.version)
        except QuoteConcurrencyError:
            logger.info("a racing request already moved the quote forward")

    async def _business(self, unit_of_work: UnitOfWork, business_id: BusinessId) -> BusinessRecord:
        business = await unit_of_work.businesses.get(business_id)
        if business is None:
            raise QuoteNotFoundError("unknown or expired quote link")
        return business


def _now() -> datetime:
    return datetime.now(UTC)


__all__ = [
    "OpenCheckoutUnavailableError",
    "PublicQuoteService",
    "UnknownPaymentSessionError",
    "PublicQuoteView",
    "QuoteNotFoundError",
    "checkout_line_items",
]
