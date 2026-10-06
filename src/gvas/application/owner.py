"""The owner dashboard: sign-in, the business at a glance, approvals and the
settings GVAS needs to run.

Every read and write is scoped to the business of the owner's session; a
session never reaches another business, and customer portal sessions never
reach these methods. Approvals run the same transitions as approving by text
or the owner channel (``gvas.domain.owner_actions``).
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Literal
from uuid import uuid4

from gvas.domain.customers import (
    CustomerRecord,
    ServiceRequest,
    hash_portal_token,
    new_portal_token,
    portal_token_matches,
)
from gvas.domain.enums import CustomerQuoteStatus, QuoteBilling, QuoteStatus
from gvas.domain.identifiers import MessageKey
from gvas.domain.intake import (
    INTAKE_BRIEF_MAX_CHARS,
    INTAKE_OPENING_MAX_CHARS,
    INTAKE_QUESTIONS_MAX_CHARS,
    BookingDecision,
    BookingDecisionAction,
    IntakeConversation,
    sanitize_owner_reason,
)
from gvas.domain.money import format_money
from gvas.domain.owner import (
    CALENDAR_WINDOW_MAX_DAYS,
    DISPLAY_NAME_MAX_CHARS,
    OWNER_SESSION_TTL,
    CalendarEvent,
    CalendarEventSource,
    OwnerCalendarError,
    OwnerSession,
    normalize_booking_link,
    normalize_calendar_feed_url,
)
from gvas.domain.owner_actions import (
    BookingDecisionOutcome,
    approve_quote,
    decide_booking,
    reject_quote,
)
from gvas.domain.payments import (
    LedgerPayment,
    PaymentKind,
    PaymentMethod,
    PaymentSource,
    QuoteSubscriptionRecord,
    checkout_expire_command,
    manual_receipt_command,
)
from gvas.domain.ports import BookedEventsPort, CalendarFeedPort
from gvas.domain.quotes import (
    InvalidQuoteTransitionError,
    Quote,
    QuoteConcurrencyError,
    normalize_customer_email,
    public_quote_id,
)
from gvas.domain.reporting import normalize_email_address
from gvas.domain.repositories import BusinessRecord, UnitOfWork
from gvas.domain.time_zones import business_zone, normalize_time_zone

logger = logging.getLogger(__name__)

QUOTE_LIST_LIMIT = 500
BOOKING_LIST_LIMIT = 200
REQUEST_LIST_LIMIT = 200
DUPLICATE_WINDOW = timedelta(minutes=1)
PAYMENT_NOTE_MAX_CHARS = 500
MANUAL_PAYMENT_METHODS = frozenset({PaymentMethod.CHECK, PaymentMethod.CASH, PaymentMethod.OTHER})
_RECEIPT_METHOD = {PaymentMethod.CHECK: " by check", PaymentMethod.CASH: " in cash"}


class OwnerAuthenticationError(PermissionError):
    """Unknown, expired, revoked or no-longer-owner credentials. Callers map
    every case to the same generic 401."""


class OwnerNotFoundError(LookupError):
    pass


class OwnerConflictError(ValueError):
    """The item is not in a state the action applies to; message is safe."""


class OwnerInputError(ValueError):
    """A settings value was rejected; message is safe to show."""


@dataclass(frozen=True)
class OwnerContext:
    session: OwnerSession
    business: BusinessRecord


@dataclass(frozen=True)
class CustomerSummary:
    customer: CustomerRecord
    quotes: tuple[Quote, ...]


@dataclass(frozen=True)
class ServiceRequestView:
    request: ServiceRequest
    customer: CustomerRecord | None


@dataclass(frozen=True)
class CalendarView:
    events: tuple[CalendarEvent, ...]
    #: Owner-facing notices for sources that failed; the rest still render.
    problems: tuple[str, ...]


@dataclass(frozen=True)
class SettingsUpdate:
    """``None`` leaves a field alone. An empty string clears the intake
    texts, the notification e-mail and the calendar link."""

    display_name: str | None = None
    calendly_url: str | None = None
    intake_brief: str | None = None
    intake_questions: str | None = None
    intake_opening: str | None = None
    notification_email: str | None = None
    calendar_feed_url: str | None = None
    timezone: str | None = None


class OwnerService:
    def __init__(
        self,
        unit_of_work_factory: Callable[[], UnitOfWork],
        *,
        booked_events: BookedEventsPort | None = None,
        calendar_feed: CalendarFeedPort | None = None,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._unit_of_work_factory = unit_of_work_factory
        self._booked_events = booked_events
        self._calendar_feed = calendar_feed
        self._now = now

    # -- sign-in -----------------------------------------------------------

    async def exchange_login_token(self, raw_token: str) -> tuple[str, OwnerContext] | None:
        """``None`` when ``raw_token`` is not an owner link (the caller then
        tries the customer portal); raises when it is one but cannot be used."""

        now = self._now()
        token_hash = hash_portal_token(raw_token)
        async with self._unit_of_work_factory() as unit_of_work:
            token = await unit_of_work.owner_login_tokens.find_by_hash(token_hash)
            if token is None or not portal_token_matches(raw_token, token.token_hash):
                return None
            if not token.is_usable(now):
                raise OwnerAuthenticationError("owner login token is not usable")
            business = await unit_of_work.businesses.get(token.business_id)
            if business is None or not _is_owner(business, token.email):
                raise OwnerAuthenticationError("owner login token is not usable")
            if not await unit_of_work.owner_login_tokens.mark_used(token_hash, now):
                raise OwnerAuthenticationError("owner login token is not usable")
            raw_session = new_portal_token()
            session = OwnerSession(
                token_hash=hash_portal_token(raw_session),
                business_id=business.business_id,
                email=token.email,
                expires_at=now + OWNER_SESSION_TTL,
                created_at=now,
            )
            await unit_of_work.owner_sessions.add(session)
            await unit_of_work.commit()
        return raw_session, OwnerContext(session=session, business=business)

    async def authenticate(self, raw_session_token: str) -> OwnerContext:
        now = self._now()
        token_hash = hash_portal_token(raw_session_token)
        async with self._unit_of_work_factory() as unit_of_work:
            session = await unit_of_work.owner_sessions.find_by_hash(token_hash)
            if (
                session is None
                or not portal_token_matches(raw_session_token, session.token_hash)
                or not session.is_active(now)
            ):
                raise OwnerAuthenticationError("owner session is not active")
            business = await unit_of_work.businesses.get(session.business_id)
            # Changing the owner e-mail ends every session of the old one.
            if business is None or not _is_owner(business, session.email):
                raise OwnerAuthenticationError("owner session is not active")
        return OwnerContext(session=session, business=business)

    async def revoke_session(self, raw_session_token: str) -> None:
        async with self._unit_of_work_factory() as unit_of_work:
            await unit_of_work.owner_sessions.revoke(
                hash_portal_token(raw_session_token), self._now()
            )
            await unit_of_work.commit()

    # -- reads -------------------------------------------------------------

    async def quotes(self, context: OwnerContext) -> tuple[Quote, ...]:
        async with self._unit_of_work_factory() as unit_of_work:
            quotes = await unit_of_work.quotes.list_for_business(
                context.business.business_id, limit=QUOTE_LIST_LIMIT
            )
        return tuple(quote for quote in quotes if quote.draft is not None)

    async def customers(self, context: OwnerContext) -> tuple[CustomerSummary, ...]:
        business_id = context.business.business_id
        async with self._unit_of_work_factory() as unit_of_work:
            customers = await unit_of_work.customers.list_for_business(business_id)
            # Per customer, so lifetime totals don't depend on a business-wide page.
            summaries: list[CustomerSummary] = []
            for customer in customers:
                quotes = await unit_of_work.quotes.list_for_customer(
                    business_id, customer.customer_id
                )
                summaries.append(
                    CustomerSummary(
                        customer=customer,
                        quotes=tuple(quote for quote in quotes if quote.draft is not None),
                    )
                )
        return tuple(summaries)

    async def payments(self, context: OwnerContext) -> tuple[LedgerPayment, ...]:
        async with self._unit_of_work_factory() as unit_of_work:
            return await unit_of_work.payments.list_for_business(context.business.business_id)

    async def subscriptions(self, context: OwnerContext) -> tuple[QuoteSubscriptionRecord, ...]:
        async with self._unit_of_work_factory() as unit_of_work:
            return await unit_of_work.quote_subscriptions.list_for_business(
                context.business.business_id
            )

    async def bookings(self, context: OwnerContext) -> tuple[IntakeConversation, ...]:
        async with self._unit_of_work_factory() as unit_of_work:
            return await unit_of_work.intake_conversations.list_booking_requests(
                context.business.business_id, limit=BOOKING_LIST_LIMIT
            )

    async def _awaiting_owner(self, context: OwnerContext) -> tuple[IntakeConversation, ...]:
        async with self._unit_of_work_factory() as unit_of_work:
            return await unit_of_work.intake_conversations.list_awaiting_owner(
                context.business.business_id, limit=BOOKING_LIST_LIMIT
            )

    async def service_requests(self, context: OwnerContext) -> tuple[ServiceRequestView, ...]:
        business_id = context.business.business_id
        async with self._unit_of_work_factory() as unit_of_work:
            requests = await unit_of_work.service_requests.list_for_business(
                business_id, limit=REQUEST_LIST_LIMIT
            )
            customers = {
                customer.customer_id: customer
                for customer in await unit_of_work.customers.list_for_business(business_id)
            }
        return tuple(
            ServiceRequestView(request=request, customer=customers.get(request.customer_id))
            for request in requests
        )

    async def calendar(self, context: OwnerContext, start: datetime, end: datetime) -> CalendarView:
        if start.tzinfo is None or end.tzinfo is None:
            raise OwnerInputError("start and end must carry a timezone")
        if end <= start or end - start > timedelta(days=CALENDAR_WINDOW_MAX_DAYS):
            raise OwnerInputError(
                f"the window must be positive and at most {CALENDAR_WINDOW_MAX_DAYS} days"
            )
        business = context.business
        problems: list[str] = []
        booked: tuple[CalendarEvent, ...] = ()
        own: tuple[CalendarEvent, ...] = ()
        if self._booked_events is not None and self._booked_events.serves(business.business_id):
            try:
                booked = await self._booked_events.upcoming(business.business_id, start, end)
            except OwnerCalendarError as error:
                problems.append(str(error))
        if business.calendar_feed_url and self._calendar_feed is not None:
            try:
                own = await self._calendar_feed.events(business.calendar_feed_url, start, end)
            except OwnerCalendarError as error:
                problems.append(str(error))
        requests = tuple(
            CalendarEvent(
                source=CalendarEventSource.REQUEST,
                title=f"Booking request: {conversation.collected.name or 'customer'}",
                start=conversation.requested_slot_start,
                end=conversation.requested_slot_end,
                location=conversation.collected.address,
                invitee_name=conversation.collected.name,
                invitee_email=conversation.collected.email,
                reference=conversation.reference,
            )
            for conversation in await self._awaiting_owner(context)
            if conversation.requested_slot_start is not None
            and start <= conversation.requested_slot_start < end
        )
        # Calendly writes each booking into the owner's own calendar too; show
        # it once, as the booking (it carries the customer).
        own = tuple(event for event in own if not _mirrors_booking(event, booked))
        events = sorted((*booked, *own, *requests), key=lambda event: event.start)
        return CalendarView(events=tuple(events), problems=tuple(problems))

    # -- actions -----------------------------------------------------------

    async def decide_booking(
        self, context: OwnerContext, reference: str, *, approve: bool, reason: str | None = None
    ) -> BookingDecisionOutcome:
        decision = BookingDecision(
            action=BookingDecisionAction.APPROVE if approve else BookingDecisionAction.DECLINE,
            reference=reference.strip().lower(),
            reason=sanitize_owner_reason(reason) if reason and reason.strip() else None,
        )
        async with self._unit_of_work_factory() as unit_of_work:
            return await decide_booking(
                unit_of_work, context.business.business_id, decision, self._now()
            )

    async def decide_quote(self, context: OwnerContext, quote_id: str, *, approve: bool) -> Quote:
        now = self._now()
        async with self._unit_of_work_factory() as unit_of_work:
            # Only quotes still waiting can be decided, so search those alone:
            # an old pending quote stays reachable however many newer ones exist.
            quotes = await unit_of_work.quotes.list_for_business(
                context.business.business_id,
                limit=QUOTE_LIST_LIMIT,
                status=QuoteStatus.AWAITING_APPROVAL,
            )
            quote = next(
                (
                    candidate
                    for candidate in quotes
                    if public_quote_id(candidate.quote_id) == quote_id
                ),
                None,
            )
            if quote is None:
                if any(
                    public_quote_id(candidate.quote_id) == quote_id
                    for candidate in await unit_of_work.quotes.list_for_business(
                        context.business.business_id, limit=QUOTE_LIST_LIMIT
                    )
                ):
                    raise OwnerConflictError("This quote isn't waiting for your OK.")
                raise OwnerNotFoundError("quote not found")
            key = MessageKey(f"dashboard:{uuid4()}")
            try:
                if approve:
                    decided = await approve_quote(unit_of_work, quote, key, now)
                else:
                    decided = await reject_quote(unit_of_work, quote, key, now)
                await unit_of_work.commit()
            except (InvalidQuoteTransitionError, QuoteConcurrencyError) as error:
                raise OwnerConflictError("This quote changed; refresh and try again.") from error
        return decided

    async def mark_paid(
        self,
        context: OwnerContext,
        quote_id: str,
        *,
        paid_on: date,
        method: PaymentMethod,
        note: str | None = None,
    ) -> Quote:
        """Record a check, cash or other payment for the full amount of a
        sent one-off quote, accepted or not. The ledger keeps one active payment per
        quote, an open card checkout is closed, and the customer is e-mailed
        a short receipt."""

        if method not in MANUAL_PAYMENT_METHODS:
            raise OwnerInputError("Pick check, cash or other.")
        note = (note or "").strip() or None
        if note is not None and len(note) > PAYMENT_NOTE_MAX_CHARS:
            raise OwnerInputError(f"Keep the note under {PAYMENT_NOTE_MAX_CHARS} characters.")
        now = self._now()
        zone = business_zone(context.business.timezone) or UTC
        if paid_on > now.astimezone(zone).date():
            raise OwnerInputError("The paid date can't be in the future.")
        business_id = context.business.business_id
        async with self._unit_of_work_factory() as unit_of_work:
            quote = await self._find_quote(unit_of_work, context, quote_id)
            draft = quote.draft
            if draft is None or draft.billing is not QuoteBilling.ONE_TIME:
                raise OwnerConflictError("Only one-off quotes can be marked paid.")
            if quote.customer_status is CustomerQuoteStatus.PAID:
                raise OwnerConflictError("This quote is already paid.")
            if quote.customer_status is CustomerQuoteStatus.DECLINED:
                raise OwnerConflictError("The customer declined this quote.")
            if not quote.is_claimable():
                raise OwnerConflictError("Only quotes sent to the customer can be marked paid.")
            payment_id = uuid4()
            recorded = await unit_of_work.payments.record(
                LedgerPayment(
                    payment_id=payment_id,
                    business_id=business_id,
                    quote_id=quote.quote_id,
                    kind=PaymentKind.ONE_OFF,
                    source=PaymentSource.MANUAL,
                    method=method,
                    reference=f"manual:{payment_id}",
                    amount_minor=draft.total_minor,
                    currency=draft.currency,
                    # Midday in the business's zone keeps the day stable.
                    paid_at=datetime.combine(paid_on, time(12), tzinfo=zone).astimezone(UTC),
                    recorded_by=context.session.email,
                    recorded_at=now,
                    note=note,
                    customer_status_before=_status_before(quote.customer_status),
                )
            )
            if recorded is None or recorded.duplicate:
                raise OwnerConflictError("This quote already has a payment; refresh.")
            paid = quote.record_customer_payment(now)
            try:
                await unit_of_work.quotes.save(paid, expected_version=quote.version)
            except QuoteConcurrencyError as error:
                raise OwnerConflictError("This quote changed; refresh and try again.") from error
            checkout = await unit_of_work.quote_payments.find_open(business_id, quote.quote_id)
            if checkout is not None:
                await unit_of_work.quote_payments.save(
                    checkout.mark_expired(now), expected_from=checkout.status
                )
                if checkout.checkout_session_id:
                    await unit_of_work.outbox.enqueue(
                        checkout_expire_command(business_id, checkout.checkout_session_id)
                    )
            email = draft.recipient.email_address
            if email is not None:
                business = context.business.display_name or context.business.name
                work = ", ".join(item.description for item in draft.line_items)
                await unit_of_work.outbox.enqueue(
                    manual_receipt_command(
                        business_id=business_id,
                        quote_id=quote.quote_id,
                        payment_id=payment_id,
                        to=email,
                        subject=f"Receipt from {business}",
                        body="\n\n".join(
                            [
                                f"Thanks! {business} received your payment of"
                                f" {format_money(draft.total_minor, draft.currency)}"
                                f"{_RECEIPT_METHOD.get(method, '')}"
                                f" on {paid_on:%B} {paid_on.day}, {paid_on.year}.",
                                f"For: {work}" if work else "",
                                "This quote is paid in full. Keep this e-mail as your receipt.",
                            ]
                        ).replace("\n\n\n\n", "\n\n"),
                    )
                )
            await unit_of_work.commit()
        return paid

    async def mark_unpaid(self, context: OwnerContext, quote_id: str) -> Quote:
        """Void the manual payment of a one-off quote, keeping who voided it
        and when. Card payments are refunded in Stripe, never undone here."""

        now = self._now()
        business_id = context.business.business_id
        async with self._unit_of_work_factory() as unit_of_work:
            quote = await self._find_quote(unit_of_work, context, quote_id)
            payments = [
                payment
                for payment in await unit_of_work.payments.list_for_quote(
                    business_id, quote.quote_id
                )
                if payment.kind is PaymentKind.ONE_OFF and payment.voided_at is None
            ]
            manual = next((p for p in payments if p.source is PaymentSource.MANUAL), None)
            if manual is None:
                if payments:
                    raise OwnerConflictError(
                        "Card payments can't be undone here. Refund it in Stripe."
                    )
                raise OwnerConflictError("This quote has no payment to undo.")
            if not await unit_of_work.payments.void_manual(
                business_id, manual.payment_id, by=context.session.email, at=now
            ):
                raise OwnerConflictError("This payment changed; refresh and try again.")
            # A card payment that raced the manual one counts instead: it
            # already did when it settled first, or it takes over now.
            standby = next(
                (p for p in payments if p.duplicate and p.payment_id != manual.payment_id),
                None,
            )
            if manual.duplicate:
                updated = quote
            elif standby is not None:
                await unit_of_work.payments.mark_duplicate(
                    business_id, standby.payment_id, duplicate=False
                )
                updated = quote
            else:
                updated = quote.undo_customer_payment(
                    now, _status_after_undo(manual.customer_status_before)
                )
                try:
                    await unit_of_work.quotes.save(updated, expected_version=quote.version)
                except QuoteConcurrencyError as error:
                    raise OwnerConflictError(
                        "This quote changed; refresh and try again."
                    ) from error
            await unit_of_work.commit()
        return updated

    async def _find_quote(
        self, unit_of_work: UnitOfWork, context: OwnerContext, quote_id: str
    ) -> Quote:
        for candidate in await unit_of_work.quotes.list_for_business(
            context.business.business_id, limit=QUOTE_LIST_LIMIT
        ):
            if public_quote_id(candidate.quote_id) == quote_id:
                return candidate
        raise OwnerNotFoundError("quote not found")

    async def update_settings(
        self, context: OwnerContext, update: SettingsUpdate
    ) -> BusinessRecord:
        fields: dict[str, str] = {}
        if update.display_name is not None:
            name = " ".join(update.display_name.split())
            if not name or len(name) > DISPLAY_NAME_MAX_CHARS:
                raise OwnerInputError(
                    f"Business name must be 1-{DISPLAY_NAME_MAX_CHARS} characters."
                )
            fields["display_name"] = name
        if update.calendly_url is not None:
            try:
                fields["calendly_url"] = normalize_booking_link(update.calendly_url)
            except ValueError as error:
                raise OwnerInputError(f"Calendly link {error}.") from error
        for field, label, limit in (
            ("intake_brief", "About your business", INTAKE_BRIEF_MAX_CHARS),
            ("intake_questions", "Questions Gus asks", INTAKE_QUESTIONS_MAX_CHARS),
            ("intake_opening", "Gus's greeting", INTAKE_OPENING_MAX_CHARS),
        ):
            value = getattr(update, field)
            if value is None:
                continue
            text = value.strip()
            if len(text) > limit:
                raise OwnerInputError(f"{label} must be at most {limit} characters.")
            fields[field] = text
        if update.notification_email is not None:
            raw = update.notification_email.strip()
            if raw:
                address = normalize_email_address(raw)
                if address is None:
                    raise OwnerInputError("Notification email isn't a valid address.")
                fields["notification_email"] = address
            else:
                fields["notification_email"] = ""
        if update.calendar_feed_url is not None:
            raw = update.calendar_feed_url.strip()
            if raw:
                try:
                    fields["calendar_feed_url"] = normalize_calendar_feed_url(raw)
                except ValueError as error:
                    raise OwnerInputError(f"Calendar link {error}.") from error
            else:
                fields["calendar_feed_url"] = ""
        if update.timezone is not None:
            try:
                fields["timezone"] = (
                    normalize_time_zone(update.timezone) if update.timezone.strip() else ""
                )
            except ValueError as error:
                raise OwnerInputError(f"Time zone {error}.") from error
        async with self._unit_of_work_factory() as unit_of_work:
            record = await unit_of_work.businesses.configure_site(
                context.business.business_id,
                now=self._now(),
                **fields,
            )
            await unit_of_work.commit()
        return record


def _is_owner(business: BusinessRecord, email: str) -> bool:
    return bool(business.owner_email) and normalize_customer_email(
        business.owner_email or ""
    ) == normalize_customer_email(email)


def _mirrors_booking(event: CalendarEvent, booked: tuple[CalendarEvent, ...]) -> bool:
    return any(
        abs(event.start - booking.start) < DUPLICATE_WINDOW
        and (
            event.end is None
            or booking.end is None
            or abs(event.end - booking.end) < DUPLICATE_WINDOW
        )
        for booking in booked
    )


__all__ = [
    "CalendarView",
    "CustomerSummary",
    "OwnerAuthenticationError",
    "OwnerConflictError",
    "OwnerContext",
    "OwnerInputError",
    "OwnerNotFoundError",
    "OwnerService",
    "ServiceRequestView",
    "SettingsUpdate",
]


def _status_before(status: CustomerQuoteStatus | None) -> Literal["sent", "viewed", "accepted"]:
    if status is CustomerQuoteStatus.VIEWED:
        return "viewed"
    if status is CustomerQuoteStatus.ACCEPTED:
        return "accepted"
    return "sent"


def _status_after_undo(
    before: Literal["sent", "viewed", "accepted"] | None,
) -> CustomerQuoteStatus | None:
    # Rows recorded before this was kept were all on accepted quotes.
    if before == "sent":
        return None
    if before == "viewed":
        return CustomerQuoteStatus.VIEWED
    return CustomerQuoteStatus.ACCEPTED
