"""The dashboard's money, decision and settings actions, calendar blocks and
customer messages, as owner-text change tools.

Each tool resolves the owner's words to one quote in code, writes the exact
sentence the owner confirms, and on YES calls the same ``OwnerService`` method
the dashboard button calls, so the rules are the dashboard's rules.
"""

import re
from collections.abc import Awaitable, Callable, Mapping, Sequence
from datetime import UTC, date, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from gvas.application.calendar_blocks import BLOCK_NOT_CONFIGURED_REPLY, BLOCK_UNREACHABLE_REPLY
from gvas.application.owner import (
    OwnerConflictError,
    OwnerContext,
    OwnerInputError,
    OwnerNotFoundError,
    OwnerService,
    SettingsUpdate,
)
from gvas.application.owner_assistant import business_zone, money_text
from gvas.domain.calendar_blocks import (
    MINUTES_PER_DAY,
    CalendarBlock,
    CalendarBlockState,
    format_day,
    format_minute,
    subtract_interval,
)
from gvas.domain.customers import CustomerRecord, customer_phone
from gvas.domain.enums import BillingInterval, CustomerQuoteStatus, QuoteBilling, QuoteStatus
from gvas.domain.identifiers import BusinessId, CustomerId, JsonValue
from gvas.domain.intake import (
    AvailabilityError,
    IntakeConversation,
    IntakeCustomerEmail,
    format_slot_label,
    intake_customer_email_command,
    intake_customer_text_command,
    sanitize_owner_reason,
)
from gvas.domain.invoices import Invoice
from gvas.domain.owner import OwnerSession
from gvas.domain.owner_assistant import (
    OWNER_MESSAGE_KEY_PREFIX,
    OwnerAssistantTool,
    OwnerChangePlan,
    OwnerChangeRefusedError,
    OwnerChangeTool,
    OwnerChangeUncertainError,
)
from gvas.domain.payments import LedgerPayment, PaymentKind, PaymentMethod, PaymentSource
from gvas.domain.ports import ScheduleBlockPort
from gvas.domain.quotes import Quote, public_quote_id
from gvas.domain.repositories import BusinessRecord, UnitOfWork
from gvas.domain.time_zones import zone_label

QUOTE_LIST_LIMIT = 500
BOOKING_LIST_LIMIT = 200
TEXTS_RECORDED_BY = "owner texts"
TEXTS_NOTE = "Recorded by text."
_METHODS = {"check": PaymentMethod.CHECK, "cash": PaymentMethod.CASH, "other": PaymentMethod.OTHER}

_TARGET = {
    "type": "string",
    "description": "An invoice number like LL-1036, or the customer's name.",
}
_CUSTOMER = {"type": "string", "description": "The customer's name."}
_METHOD = {"type": "string", "enum": sorted(_METHODS), "description": "How they paid."}
_PAID_ON = {
    "type": ["string", "null"],
    "description": "The day it was paid, YYYY-MM-DD, or null for today.",
}


class _Money:
    """Shared lookups; the business always comes from the caller, never the model."""

    def __init__(
        self,
        unit_of_work_factory: Callable[[], UnitOfWork],
        owner: OwnerService,
        now: Callable[[], datetime],
    ) -> None:
        self._unit_of_work_factory = unit_of_work_factory
        self.owner = owner
        self._now = now

    def context(self, business: BusinessRecord) -> OwnerContext:
        now = self._now()
        session = OwnerSession(
            token_hash="0" * 64,
            business_id=business.business_id,
            email=TEXTS_RECORDED_BY,
            expires_at=now + timedelta(minutes=1),
            created_at=now,
        )
        return OwnerContext(session=session, business=business)

    def today(self, business: BusinessRecord) -> date:
        return self._now().astimezone(business_zone(business)).date()

    async def quotes(self, business: BusinessRecord) -> tuple[list[Quote], list[Invoice]]:
        async with self._unit_of_work_factory() as unit_of_work:
            quotes = await unit_of_work.quotes.list_for_business(
                business.business_id, limit=QUOTE_LIST_LIMIT
            )
            invoices = (
                await unit_of_work.invoices.list_for_business(business.business_id)
                if business.invoice_prefix
                else ()
            )
        return [quote for quote in quotes if quote.draft is not None], list(invoices)

    async def ledger(self, business_id: BusinessId, quote: Quote) -> tuple[LedgerPayment, ...]:
        async with self._unit_of_work_factory() as unit_of_work:
            return await unit_of_work.payments.list_for_quote(business_id, quote.quote_id)

    async def find(
        self,
        business: BusinessRecord,
        target: object,
        eligible: Callable[[Quote], bool],
        what: str,
    ) -> tuple[Quote, Invoice | None]:
        """The one eligible quote the owner means, by invoice number or name."""

        needle = " ".join(str(target or "").split()).casefold()
        if len(needle) < 2:
            raise OwnerChangeRefusedError(
                "Say which one: an invoice number or the customer's name."
            )
        quotes, invoices = await self.quotes(business)
        by_quote = {invoice.quote_id: invoice for invoice in invoices}
        numbered = [invoice for invoice in invoices if invoice.number.casefold() == needle]
        if numbered:
            matches = [quote for quote in quotes if quote.quote_id == numbered[0].quote_id]
        else:
            matches = [quote for quote in quotes if needle in _name(quote).casefold()]
        found = [quote for quote in matches if eligible(quote)]
        if not found:
            if matches:
                raise OwnerChangeRefusedError(f"{_name(matches[0])} has nothing to {what}.")
            raise OwnerChangeRefusedError(f'I can\'t find "{target}".')
        if len(found) > 1:
            names = ", ".join(_label(quote, by_quote.get(quote.quote_id)) for quote in found[:5])
            raise OwnerChangeRefusedError(f"More than one matches ({names}). Say which one.")
        return found[0], by_quote.get(found[0].quote_id)

    def paid_on(self, business: BusinessRecord, value: object) -> date:
        if value in (None, ""):
            return self.today(business)
        try:
            day = date.fromisoformat(str(value))
        except ValueError as error:
            raise OwnerChangeRefusedError("Give the paid date as YYYY-MM-DD.") from error
        if day > self.today(business):
            raise OwnerChangeRefusedError("The paid date can't be in the future.")
        return day


def _name(quote: Quote) -> str:
    assert quote.draft is not None
    recipient = quote.draft.recipient
    return recipient.display_name or recipient.address


def _total(quote: Quote) -> str:
    assert quote.draft is not None
    return money_text(quote.draft.total_minor, quote.draft.currency)


def _label(quote: Quote, invoice: Invoice | None) -> str:
    if invoice is not None:
        return f"invoice {invoice.number} ({_name(quote)}, {_total(quote)})"
    assert quote.draft is not None
    items = ", ".join(item.description for item in quote.draft.line_items[:2])
    return f"{_name(quote)}'s quote ({items}, {_total(quote)})"


def _day(value: date) -> str:
    return value.strftime("%a %b %-d")


def _method(value: object) -> PaymentMethod:
    method = _METHODS.get(str(value or "").strip().casefold())
    if method is None:
        raise OwnerChangeRefusedError("Say how they paid: check, cash or other.")
    return method


def _one_off(quote: Quote) -> bool:
    return quote.draft is not None and quote.draft.billing is QuoteBilling.ONE_TIME


def _payable(quote: Quote) -> bool:
    """The dashboard's rule: only a quote sent to the customer, not declined."""

    return (
        quote.is_claimable()
        and quote.customer_status is not CustomerQuoteStatus.DECLINED
        and not (quote.customer_status is None and quote.status is QuoteStatus.APPROVED)
    )


def _recurring(quote: Quote) -> bool:
    return quote.draft is not None and quote.draft.billing is QuoteBilling.RECURRING


async def _dashboard(call: Any) -> Any:
    try:
        return await call
    except (OwnerConflictError, OwnerInputError, OwnerNotFoundError) as error:
        raise OwnerChangeRefusedError(str(error)) from error


def _str(arguments: Mapping[str, JsonValue], key: str) -> str:
    return str(arguments[key])


class SendInvoiceTool:
    spec = OwnerAssistantTool(
        "change_send_invoice",
        "Propose sending the invoice for an accepted one-off quote (the dashboard's Send invoice).",
        {"target": _CUSTOMER},
    )
    sensitive = True

    def __init__(self, money: _Money) -> None:
        self._money = money

    async def prepare(self, business: BusinessRecord, args: Mapping[str, Any]) -> OwnerChangePlan:
        if not business.invoice_prefix:
            raise OwnerChangeRefusedError("Invoices are off for this business.")
        _, invoices = await self._money.quotes(business)
        invoiced = {invoice.quote_id for invoice in invoices}
        quote, _ = await self._money.find(
            business,
            args.get("target"),
            lambda q: _one_off(q)
            and q.quote_id not in invoiced
            and q.customer_status in (CustomerQuoteStatus.ACCEPTED, CustomerQuoteStatus.PAID),
            "invoice",
        )
        return OwnerChangePlan(
            summary=(
                f"I will send {_name(quote)} an invoice for {_label(quote, None)}, due in "
                "14 days. It shows in the dashboard; the customer isn't e-mailed yet."
            ),
            arguments={"quote": public_quote_id(quote.quote_id), "customer": _name(quote)},
        )

    async def apply(self, business: BusinessRecord, arguments: Mapping[str, JsonValue]) -> str:
        context = self._money.context(business)
        invoice: Invoice = await _dashboard(
            self._money.owner.send_invoice(context, _str(arguments, "quote"))
        )
        total = money_text(invoice.total_minor, invoice.currency)
        return (
            f"Done: invoice {invoice.number} for {arguments['customer']}, {total}, "
            f"due {_day(invoice.due_on)}."
        )


class MarkPaidTool:
    spec = OwnerAssistantTool(
        "change_mark_paid",
        "Propose marking a one-off quote or invoice paid by check, cash or other "
        "(the dashboard's Mark paid). Card payments are recorded automatically.",
        {"target": _TARGET, "method": _METHOD, "paid_on": _PAID_ON},
    )
    sensitive = True

    def __init__(self, money: _Money) -> None:
        self._money = money

    async def prepare(self, business: BusinessRecord, args: Mapping[str, Any]) -> OwnerChangePlan:
        method = _method(args.get("method"))
        paid_on = self._money.paid_on(business, args.get("paid_on"))
        quote, invoice = await self._money.find(
            business,
            args.get("target"),
            lambda q: _one_off(q)
            and _payable(q)
            and q.customer_status is not CustomerQuoteStatus.PAID,
            "mark paid",
        )
        label = _label(quote, invoice)
        return OwnerChangePlan(
            summary=f"I will mark {label} paid by {method.value}, on {_day(paid_on)}.",
            arguments={
                "quote": public_quote_id(quote.quote_id),
                "label": label,
                "method": method.value,
                "paid_on": paid_on.isoformat(),
            },
        )

    async def apply(self, business: BusinessRecord, arguments: Mapping[str, JsonValue]) -> str:
        await _dashboard(
            self._money.owner.mark_paid(
                self._money.context(business),
                _str(arguments, "quote"),
                paid_on=date.fromisoformat(_str(arguments, "paid_on")),
                method=PaymentMethod(_str(arguments, "method")),
                note=TEXTS_NOTE,
            )
        )
        return f"Done: {arguments['label']} is marked paid."


class MarkUnpaidTool:
    spec = OwnerAssistantTool(
        "change_mark_unpaid",
        "Propose undoing a check/cash/other payment on a one-off quote or invoice so "
        "it shows unpaid again (the dashboard's Mark unpaid). Card payments can't be undone.",
        {"target": _TARGET},
    )
    sensitive = True

    def __init__(self, money: _Money) -> None:
        self._money = money

    async def prepare(self, business: BusinessRecord, args: Mapping[str, Any]) -> OwnerChangePlan:
        quote, invoice = await self._money.find(
            business,
            args.get("target"),
            lambda q: _one_off(q) and q.customer_status is CustomerQuoteStatus.PAID,
            "mark unpaid",
        )
        manual = [
            payment
            for payment in await self._money.ledger(business.business_id, quote)
            if payment.kind is PaymentKind.ONE_OFF
            and payment.source is PaymentSource.MANUAL
            and payment.voided_at is None
        ]
        if not manual:
            raise OwnerChangeRefusedError(
                "That was paid by card, so it can't be undone here. Refund it in Stripe."
            )
        payment = manual[0]
        # A card payment that also settled takes over, so the quote stays paid.
        card = any(
            other.kind is PaymentKind.ONE_OFF
            and other.source is not PaymentSource.MANUAL
            and other.voided_at is None
            for other in await self._money.ledger(business.business_id, quote)
        )
        label = _label(quote, invoice)
        paid = money_text(payment.amount_minor, payment.currency)
        day = _day(payment.paid_at.astimezone(business_zone(business)).date())
        after = (
            "It stays paid: a card payment also covers it." if card else "It shows unpaid again."
        )
        return OwnerChangePlan(
            summary=(
                f"I will undo the {paid} {payment.method.value} payment on {label}, "
                f"paid {day}. {after}"
            ),
            arguments={
                "quote": public_quote_id(quote.quote_id),
                "label": label,
                "card": card,
            },
        )

    async def apply(self, business: BusinessRecord, arguments: Mapping[str, JsonValue]) -> str:
        quote: Quote = await _dashboard(
            self._money.owner.mark_unpaid(self._money.context(business), _str(arguments, "quote"))
        )
        if quote.customer_status is CustomerQuoteStatus.PAID:
            return (
                f"Done: the manual payment on {arguments['label']} is undone. "
                "It stays paid: a card payment covers it."
            )
        return f"Done: {arguments['label']} shows unpaid again."


class RecordPlanPaymentTool:
    spec = OwnerAssistantTool(
        "change_record_plan_payment",
        "Propose recording a check/cash/other payment that covers months of a "
        "customer's recurring plan (the dashboard's Record payment).",
        {
            "customer": _CUSTOMER,
            "amount": {"type": "number", "description": "Dollars received, e.g. 540."},
            "months": {"type": "integer", "description": "Months it covers, 1-24."},
            "method": _METHOD,
            "paid_on": _PAID_ON,
        },
    )
    sensitive = True

    def __init__(self, money: _Money) -> None:
        self._money = money

    async def prepare(self, business: BusinessRecord, args: Mapping[str, Any]) -> OwnerChangePlan:
        method = _method(args.get("method"))
        paid_on = self._money.paid_on(business, args.get("paid_on"))
        try:
            amount_minor = round(float(args.get("amount") or 0) * 100)
            months = int(args.get("months") or 0)
        except (TypeError, ValueError) as error:
            raise OwnerChangeRefusedError("Give the amount and the months it covers.") from error
        if amount_minor <= 0 or not 1 <= months <= 24:
            raise OwnerChangeRefusedError("Give the amount received and 1-24 months.")
        quote, _ = await self._money.find(
            business,
            args.get("customer"),
            lambda q: _recurring(q) and _payable(q),
            "record a plan payment for",
        )
        assert quote.draft is not None and quote.draft.interval is not None
        if quote.draft.interval is BillingInterval.YEAR and months % 12:
            raise OwnerChangeRefusedError(
                f"{_name(quote)}'s plan is yearly, so a payment covers 12 or 24 months."
            )
        paid = money_text(amount_minor, quote.draft.currency)
        span = "1 month" if months == 1 else f"{months} months"
        plan = f"{_total(quote)} every {quote.draft.interval.value}"
        return OwnerChangePlan(
            summary=(
                f"I will record a {paid} {method.value} payment from {_name(quote)} covering "
                f"{span} of their {plan} plan, paid {_day(paid_on)}."
            ),
            arguments={
                "quote": public_quote_id(quote.quote_id),
                "customer": _name(quote),
                "key": f"text-{uuid4().hex}",
                "amount_minor": amount_minor,
                "months": months,
                "method": method.value,
                "paid_on": paid_on.isoformat(),
            },
        )

    async def apply(self, business: BusinessRecord, arguments: Mapping[str, JsonValue]) -> str:
        months = arguments["months"]
        amount = arguments["amount_minor"]
        assert isinstance(months, int) and isinstance(amount, int)
        plan = await _dashboard(
            self._money.owner.record_plan_payment(
                self._money.context(business),
                _str(arguments, "quote"),
                key=_str(arguments, "key"),
                paid_on=date.fromisoformat(_str(arguments, "paid_on")),
                method=PaymentMethod(_str(arguments, "method")),
                months=months,
                amount_minor=amount,
                note=TEXTS_NOTE,
            )
        )
        through = f", paid through {_day(plan.paid_through)}" if plan.paid_through else ""
        return f"Done: payment recorded for {arguments['customer']}{through}."


class UndoPlanPaymentTool:
    spec = OwnerAssistantTool(
        "change_undo_plan_payment",
        "Propose undoing the latest check/cash/other payment recorded on a customer's "
        "recurring plan (the dashboard's Undo).",
        {"customer": _CUSTOMER},
    )
    sensitive = True

    def __init__(self, money: _Money) -> None:
        self._money = money

    async def prepare(self, business: BusinessRecord, args: Mapping[str, Any]) -> OwnerChangePlan:
        quote, _ = await self._money.find(
            business, args.get("customer"), _recurring, "undo a plan payment for"
        )
        manual = sorted(
            (
                payment
                for payment in await self._money.ledger(business.business_id, quote)
                if payment.kind is PaymentKind.PLAN
                and payment.source is PaymentSource.MANUAL
                and payment.voided_at is None
            ),
            key=lambda payment: payment.recorded_at,
        )
        if not manual:
            raise OwnerChangeRefusedError(f"{_name(quote)} has no recorded plan payment to undo.")
        payment = manual[-1]
        paid = money_text(payment.amount_minor, payment.currency)
        day = _day(payment.paid_at.astimezone(business_zone(business)).date())
        months = payment.months_covered or 1
        span = "1 month" if months == 1 else f"{months} months"
        return OwnerChangePlan(
            summary=(
                f"I will undo the {paid} {payment.method.value} payment from {_name(quote)} "
                f"paid {day} ({span}), so their paid-through date moves back."
            ),
            arguments={
                "quote": public_quote_id(quote.quote_id),
                "payment": str(payment.payment_id),
                "customer": _name(quote),
            },
        )

    async def apply(self, business: BusinessRecord, arguments: Mapping[str, JsonValue]) -> str:
        plan = await _dashboard(
            self._money.owner.void_plan_payment(
                self._money.context(business),
                _str(arguments, "quote"),
                _str(arguments, "payment"),
            )
        )
        through = (
            f" They're now paid through {_day(plan.paid_through)}."
            if plan.paid_through
            else " No payments are left on the plan."
        )
        return f"Done: the payment from {arguments['customer']} is undone.{through}"


def money_change_tools(
    unit_of_work_factory: Callable[[], UnitOfWork],
    owner: OwnerService,
    *,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> tuple[OwnerChangeTool, ...]:
    money = _Money(unit_of_work_factory, owner, now)
    return (
        SendInvoiceTool(money),
        MarkPaidTool(money),
        MarkUnpaidTool(money),
        RecordPlanPaymentTool(money),
        UndoPlanPaymentTool(money),
    )


def _needle(target: object) -> str:
    needle = " ".join(str(target or "").split()).casefold()
    if len(needle) < 2:
        raise OwnerChangeRefusedError("Say which one: the customer's name.")
    return needle


def _one_match[T](found: list[T], target: object, label: Callable[[T], str], what: str) -> T:
    if not found:
        raise OwnerChangeRefusedError(f'Nothing from "{target}" is waiting for your {what}.')
    if len(found) > 1:
        names = ", ".join(label(item) for item in found[:5])
        raise OwnerChangeRefusedError(f"More than one matches ({names}). Say which one.")
    return found[0]


class _Decisions:
    """Finds the request or quote the owner means among the ones waiting on them."""

    def __init__(self, unit_of_work_factory: Callable[[], UnitOfWork], money: _Money) -> None:
        self._unit_of_work_factory = unit_of_work_factory
        self.money = money

    async def booking(self, business: BusinessRecord, target: object) -> IntakeConversation:
        needle = _needle(target)
        async with self._unit_of_work_factory() as unit_of_work:
            waiting = await unit_of_work.intake_conversations.list_awaiting_owner(
                business.business_id, limit=BOOKING_LIST_LIMIT
            )
        found = [
            request
            for request in waiting
            if request.reference.casefold() == needle
            or needle in (request.collected.name or "").casefold()
        ]
        return _one_match(found, target, lambda r: self.visit(business, r), "OK")

    def visit(self, business: BusinessRecord, request: IntakeConversation) -> str:
        name = request.collected.name or "the customer"
        if request.requested_slot_start is None:
            return f"{name}'s visit request"
        when = format_slot_label(request.requested_slot_start, business_zone(business))
        return f"{name}'s visit request for {when}"

    def replaced(
        self, business: BusinessRecord, request: IntakeConversation, *, approve: bool
    ) -> str:
        """For a request to move a booking: what happens to the original time."""
        old = request.superseded_booking
        if old is None:
            return ""
        kept = old.slot_label(business_zone(business))
        if approve:
            if old.event_uri:
                return f" It moves their visit from {kept}, which is canceled."
            return f" It replaces their earlier request for {kept}."
        return f" Their original time, {kept}, stands and they'll be told so."

    async def quote(self, business: BusinessRecord, target: object) -> Quote:
        needle = _needle(target)
        async with self._unit_of_work_factory() as unit_of_work:
            waiting = await unit_of_work.quotes.list_for_business(
                business.business_id, limit=QUOTE_LIST_LIMIT, status=QuoteStatus.AWAITING_APPROVAL
            )
        found = [q for q in waiting if q.draft is not None and needle in _name(q).casefold()]
        return _one_match(found, target, lambda q: f"{_name(q)} {_total(q)}", "OK")


async def _decide_booking(
    decisions: _Decisions,
    business: BusinessRecord,
    arguments: Mapping[str, JsonValue],
    *,
    approve: bool,
) -> str:
    reason = arguments.get("reason")
    outcome = await _dashboard(
        decisions.money.owner.decide_booking(
            decisions.money.context(business),
            _str(arguments, "reference"),
            approve=approve,
            reason=str(reason) if reason else None,
        )
    )
    if not outcome.applied:
        raise OwnerChangeRefusedError(outcome.text)
    return f"Done: {outcome.text}"


class ApproveBookingTool:
    spec = OwnerAssistantTool(
        "change_approve_booking",
        "Propose approving a visit request waiting for the owner (the dashboard's Approve).",
        {"customer": _CUSTOMER},
    )
    sensitive = False

    def __init__(self, decisions: _Decisions) -> None:
        self._decisions = decisions

    async def prepare(self, business: BusinessRecord, args: Mapping[str, Any]) -> OwnerChangePlan:
        request = await self._decisions.booking(business, args.get("customer"))
        return OwnerChangePlan(
            summary=(
                f"I will approve {self._decisions.visit(business, request)}."
                f"{self._decisions.replaced(business, request, approve=True)} "
                "They'll be told it's approved, by email with the booking or a link to confirm it."
            ),
            arguments={"reference": request.reference},
        )

    async def apply(self, business: BusinessRecord, arguments: Mapping[str, JsonValue]) -> str:
        return await _decide_booking(self._decisions, business, arguments, approve=True)


class DeclineBookingTool:
    spec = OwnerAssistantTool(
        "change_decline_booking",
        "Propose declining a visit request waiting for the owner (the dashboard's Decline).",
        {
            "customer": _CUSTOMER,
            "reason": {
                "type": ["string", "null"],
                "description": "A short note for the customer in the owner's words, or null.",
            },
        },
    )
    sensitive = False

    def __init__(self, decisions: _Decisions) -> None:
        self._decisions = decisions

    async def prepare(self, business: BusinessRecord, args: Mapping[str, Any]) -> OwnerChangePlan:
        request = await self._decisions.booking(business, args.get("customer"))
        raw = str(args.get("reason") or "").strip()
        reason = sanitize_owner_reason(raw) if raw else ""
        arguments: dict[str, JsonValue] = {"reference": request.reference}
        if reason:
            arguments["reason"] = reason
        if request.superseded_booking is not None:
            # Declining a move keeps the original time; the note is kept on the
            # request, like the dashboard, but not sent.
            return OwnerChangePlan(
                summary=(
                    f"I will decline {self._decisions.visit(business, request)}."
                    f"{self._decisions.replaced(business, request, approve=False)}"
                ),
                arguments=arguments,
            )
        told = f', with your note: "{reason}"' if reason else ", with no note."
        return OwnerChangePlan(
            summary=(
                f"I will decline {self._decisions.visit(business, request)}. "
                f"They'll be told it's declined{told}"
            ),
            arguments=arguments,
        )

    async def apply(self, business: BusinessRecord, arguments: Mapping[str, JsonValue]) -> str:
        return await _decide_booking(self._decisions, business, arguments, approve=False)


async def _decide_quote(
    decisions: _Decisions,
    business: BusinessRecord,
    arguments: Mapping[str, JsonValue],
    *,
    approve: bool,
) -> str:
    await _dashboard(
        decisions.money.owner.decide_quote(
            decisions.money.context(business), _str(arguments, "quote"), approve=approve
        )
    )
    customer = arguments["customer"]
    if approve:
        return f"Done: the quote for {customer} is approved and on its way to them."
    return f"Done: the quote for {customer} is rejected. It wasn't sent."


class ApproveQuoteTool:
    spec = OwnerAssistantTool(
        "change_approve_quote",
        "Propose approving a quote waiting for the owner's OK, which sends it to the customer.",
        {"customer": _CUSTOMER},
    )
    sensitive = True

    def __init__(self, decisions: _Decisions) -> None:
        self._decisions = decisions

    async def prepare(self, business: BusinessRecord, args: Mapping[str, Any]) -> OwnerChangePlan:
        quote = await self._decisions.quote(business, args.get("customer"))
        return OwnerChangePlan(
            summary=(
                f"I will approve the {_total(quote)} quote for {_name(quote)} "
                f"({_items(quote)}) and send it to them."
            ),
            arguments={"quote": public_quote_id(quote.quote_id), "customer": _name(quote)},
        )

    async def apply(self, business: BusinessRecord, arguments: Mapping[str, JsonValue]) -> str:
        return await _decide_quote(self._decisions, business, arguments, approve=True)


class RejectQuoteTool:
    spec = OwnerAssistantTool(
        "change_reject_quote",
        "Propose rejecting a quote waiting for the owner's OK, so it is never sent.",
        {"customer": _CUSTOMER},
    )
    sensitive = False

    def __init__(self, decisions: _Decisions) -> None:
        self._decisions = decisions

    async def prepare(self, business: BusinessRecord, args: Mapping[str, Any]) -> OwnerChangePlan:
        quote = await self._decisions.quote(business, args.get("customer"))
        return OwnerChangePlan(
            summary=(
                f"I will reject the {_total(quote)} quote for {_name(quote)} "
                f"({_items(quote)}). It won't be sent."
            ),
            arguments={"quote": public_quote_id(quote.quote_id), "customer": _name(quote)},
        )

    async def apply(self, business: BusinessRecord, arguments: Mapping[str, JsonValue]) -> str:
        return await _decide_quote(self._decisions, business, arguments, approve=False)


def _items(quote: Quote) -> str:
    lines = quote.draft.line_items if quote.draft is not None else ()
    text = ", ".join(item.description for item in lines[:3])
    return text + (", …" if len(lines) > 3 else "")


def decision_change_tools(
    unit_of_work_factory: Callable[[], UnitOfWork],
    owner: OwnerService,
    *,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> tuple[OwnerChangeTool, ...]:
    decisions = _Decisions(unit_of_work_factory, _Money(unit_of_work_factory, owner, now))
    return (
        ApproveBookingTool(decisions),
        DeclineBookingTool(decisions),
        ApproveQuoteTool(decisions),
        RejectQuoteTool(decisions),
    )


#: Owner-text argument, ``SettingsUpdate`` field, what the owner reads.
#: Notification e-mail, Calendly link and calendar feed stay dashboard-only.
_SETTINGS = (
    ("business_name", "display_name", "your business name"),
    ("time_zone", "timezone", "your time zone"),
    ("about", "intake_brief", "what Gus says about your business"),
    ("questions", "intake_questions", "the questions Gus asks"),
    ("greeting", "intake_opening", "Gus's greeting"),
    ("offer_line", "intake_offer_line", "Gus's line above the times"),
    ("visit_question", "intake_visit_question", "Gus's question before the visit"),
)
_CLEARABLE = frozenset(
    {
        "intake_brief",
        "intake_questions",
        "intake_opening",
        "intake_offer_line",
        "intake_visit_question",
    }
)


def _setting(description: str) -> dict[str, Any]:
    return {"type": ["string", "null"], "description": f"{description} Null to leave it."}


def _current_setting(business: BusinessRecord, field: str) -> str:
    profile = business.intake_profile
    value = {
        "display_name": business.display_name,
        "timezone": business.timezone,
        "intake_brief": profile.brief,
        "intake_questions": profile.questions,
        "intake_opening": profile.opening,
        "intake_offer_line": profile.offer_line,
        "intake_visit_question": profile.visit_question,
    }[field]
    return value or ""


class SettingsTool:
    spec = OwnerAssistantTool(
        "change_settings",
        "Propose changing the business's Settings: business name, time zone, or Gus's "
        "wording for customers. Give the exact new text only for the settings the owner "
        "asked to change and null for the rest; an empty string puts Gus's wording back "
        "to his default.",
        {
            "business_name": _setting("The new business name."),
            "time_zone": _setting("An IANA time zone name, like America/Los_Angeles."),
            "about": _setting("What the business is and books, for Gus."),
            "questions": _setting("What Gus finds out beyond contact details."),
            "greeting": _setting("The first message a visitor reads."),
            "offer_line": _setting("The line above offered times."),
            "visit_question": _setting("The question Gus asks once a time is picked."),
        },
    )
    sensitive = False

    def __init__(self, money: _Money) -> None:
        self._money = money

    async def prepare(self, business: BusinessRecord, args: Mapping[str, Any]) -> OwnerChangePlan:
        asked = {
            field: value for key, field, _ in _SETTINGS if isinstance(value := args.get(key), str)
        }
        if not asked:
            raise OwnerChangeRefusedError(
                "Tell me which setting to change: business name, time zone, or Gus's wording."
            )
        for field, value in asked.items():
            if not value.strip() and field not in _CLEARABLE:
                raise OwnerChangeRefusedError("That setting can't be left empty.")
        fields = await _dashboard(_checked(self._money.owner, SettingsUpdate(**asked)))
        changed = {
            field: value
            for field, value in fields.items()
            if value != _current_setting(business, field)
        }
        changes: list[str] = []
        for _, field, label in _SETTINGS:
            if field not in changed:
                continue
            new = changed[field]
            if field == "timezone":
                changes.append(f"set {label} to {_zone_name(new)}")
            elif not new:
                changes.append(f"remove {label}, so Gus uses his default")
            else:
                changes.append(f'change {label} to: "{new}"')
        if not changes:
            raise OwnerChangeRefusedError("That's already what Settings say.")
        return OwnerChangePlan(
            summary=_sentence(f"I will {'; and '.join(changes)}"),
            # Only what the summary names: YES must not rewrite anything else.
            arguments={"fields": dict(changed)},
        )

    async def apply(self, business: BusinessRecord, arguments: Mapping[str, JsonValue]) -> str:
        fields = arguments["fields"]
        assert isinstance(fields, dict)
        update = SettingsUpdate(**{field: str(value) for field, value in fields.items()})
        await _dashboard(self._money.owner.update_settings(self._money.context(business), update))
        return "Done: Settings are updated, and the dashboard shows the change."


async def _checked(owner: OwnerService, update: SettingsUpdate) -> dict[str, str]:
    return owner.settings_fields(update)


def _sentence(text: str) -> str:
    return text if text.endswith('"') else f"{text}."


def _zone_name(name: str) -> str:
    return f"{zone_label(datetime.now(UTC).astimezone(ZoneInfo(name)))} ({name})"


def settings_change_tools(
    unit_of_work_factory: Callable[[], UnitOfWork],
    owner: OwnerService,
    *,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> tuple[OwnerChangeTool, ...]:
    return (SettingsTool(_Money(unit_of_work_factory, owner, now)),)


def _phone_label(phone: str | None) -> str:
    """``(510) 555-0163`` for a US number, as the dashboard shows it."""

    if phone and len(phone) == 12 and phone.startswith("+1"):
        return f"({phone[2:5]}) {phone[5:8]}-{phone[8:]}"
    return phone or "no phone"


_NAME_MAX = 200  # IntakeCollected.name: a longer one would break their next chat.


class EditCustomerTool:
    spec = OwnerAssistantTool(
        "change_customer",
        "Propose changing a customer's name or phone number in the business's records. "
        "`customer` is who, as the owner named them: current name, e-mail or phone. Give "
        "the new value only for what the owner asked to change and null for the rest. "
        "E-mail addresses can't be changed by text, since customers sign in with them.",
        {
            "customer": {
                "type": "string",
                "description": "The customer's current name, e-mail or phone.",
            },
            "name": _setting("The customer's new full name."),
            "phone": _setting("The customer's new phone number."),
        },
    )
    sensitive = False

    def __init__(
        self, unit_of_work_factory: Callable[[], UnitOfWork], now: Callable[[], datetime]
    ) -> None:
        self._unit_of_work_factory = unit_of_work_factory
        self._now = now

    async def _customer(self, business: BusinessRecord, target: object) -> CustomerRecord:
        needle = _needle(target)
        digits = "".join(character for character in needle if character.isdigit())
        async with self._unit_of_work_factory() as unit_of_work:
            customers = await unit_of_work.customers.list_for_business(business.business_id)
        exact = [c for c in customers if needle in {(c.display_name or "").casefold(), c.email}]
        found = exact or [
            c
            for c in customers
            if needle in (c.display_name or "").casefold()
            or needle in c.email
            or (len(digits) >= 4 and digits in (c.phone or ""))
        ]
        if not found:
            raise OwnerChangeRefusedError(f'No customer in your records matches "{target}".')
        if len(found) > 1:
            names = ", ".join(customer.name for customer in found[:5])
            raise OwnerChangeRefusedError(f"More than one matches ({names}). Say which one.")
        return found[0]

    async def prepare(self, business: BusinessRecord, args: Mapping[str, Any]) -> OwnerChangePlan:
        name = " ".join(str(args.get("name") or "").split())
        phone = str(args.get("phone") or "").strip()
        if not name and not phone:
            raise OwnerChangeRefusedError(
                "Tell me what to change: the customer's name or phone. "
                "E-mail addresses can't be changed by text."
            )
        customer = await self._customer(business, args.get("customer"))
        changes: dict[str, JsonValue] = {}
        said: list[str] = []
        if name:
            if len(name) > _NAME_MAX:
                raise OwnerChangeRefusedError("That name is too long.")
            if name != customer.display_name:
                changes["display_name"] = name
                said.append(f'name to "{name}"')
        if phone:
            normalized = customer_phone(phone)
            if normalized is None:
                raise OwnerChangeRefusedError(
                    "That doesn't look like a phone number. Give it like (510) 555-0123."
                )
            if normalized != customer.phone:
                changes["phone"] = normalized
                said.append(f"phone to {_phone_label(normalized)}")
        if not changes:
            raise OwnerChangeRefusedError(f"That's already what you have for {customer.name}.")
        summary = f"I will change {customer.name}'s {' and '.join(said)}."
        if "phone" in changes and customer.sms_consent is True:
            summary += (
                " They agreed to texts at the old number, so Gus won't text the new one "
                "until they agree again."
            )
        return OwnerChangePlan(
            summary=summary,
            arguments={
                "customer_id": str(customer.customer_id),
                "before": {"display_name": customer.display_name, "phone": customer.phone},
                "changes": changes,
            },
        )

    async def apply(self, business: BusinessRecord, arguments: Mapping[str, JsonValue]) -> str:
        before, changes = arguments["before"], arguments["changes"]
        assert isinstance(before, dict) and isinstance(changes, dict)
        customer_id = CustomerId(UUID(str(arguments["customer_id"])))
        async with self._unit_of_work_factory() as unit_of_work:
            customer = await unit_of_work.customers.get(business.business_id, customer_id)
            if customer is None:
                raise OwnerChangeRefusedError(
                    "That customer isn't in your records any more, so nothing changed."
                )
            old_name, old_phone = before.get("display_name"), before.get("phone")
            name = changes.get("display_name", old_name)
            phone = changes.get("phone", old_phone)
            # Compared under the row lock, so a newer edit is never overwritten.
            updated = await unit_of_work.customers.update_details(
                business.business_id,
                customer_id,
                display_name=None if name is None else str(name),
                phone=None if phone is None else str(phone),
                expected=(
                    None if old_name is None else str(old_name),
                    None if old_phone is None else str(old_phone),
                ),
                now=self._now(),
            )
            if updated is None:
                raise OwnerChangeRefusedError(
                    f"{customer.name}'s details changed since I asked, so nothing changed. "
                    "Ask again if you still want it."
                )
            await unit_of_work.commit()
        return (
            f"Done: {updated.name}'s details are updated, and the dashboard shows them. "
            "Invoices already sent keep the old ones."
        )


def customer_change_tools(
    unit_of_work_factory: Callable[[], UnitOfWork],
    *,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> tuple[OwnerChangeTool, ...]:
    return (EditCustomerTool(unit_of_work_factory, now),)


_DAY_ARG = {"type": "string", "description": "The date, YYYY-MM-DD, in the business's time zone."}
_BLOCK_UNSURE_REPLY = (
    "I couldn't reach the calendar, so I can't tell whether the block landed. "
    'Text "unblock {day}" to make sure that time is open, or ask again to block it.'
)
_TIME = re.compile(r"(\d{1,2}):(\d{2})")


def _minute(value: object, *, end: bool) -> int:
    match = _TIME.fullmatch(str(value).strip())
    if match is not None:
        total = int(match[1]) * 60 + int(match[2])
        if int(match[2]) < 60 and (total < MINUTES_PER_DAY or (end and total == MINUTES_PER_DAY)):
            return total
    raise OwnerChangeRefusedError("Give the times like 8:00 to 12:00.")


def _last(end: int) -> str:
    return "midnight" if end == MINUTES_PER_DAY else format_minute(end)


def _all_day(start: int, end: int) -> bool:
    return start == 0 and end == MINUTES_PER_DAY


def _span(day: date, start: int, end: int) -> str:
    """``Tue Oct 6, 8:00 AM–12:00 PM``, or ``all day Tue Oct 6``."""

    if _all_day(start, end):
        return f"all day {format_day(day)}"
    return f"{format_day(day)}, {format_minute(start)}–{_last(end)}"


def _and(items: Sequence[str]) -> str:
    return items[0] if len(items) == 1 else f"{', '.join(items[:-1])} and {items[-1]}"


def _days(days: Sequence[date]) -> str:
    return _and([format_day(day) for day in days])


def _spans(days: Sequence[date], start: int, end: int) -> str:
    """``all day Thu Oct 15 and all day Fri Oct 16``, or ``Thu Oct 15 and Fri Oct 16,
    12:00 PM–2:00 PM``."""

    if _all_day(start, end) or len(days) == 1:
        return _and([_span(day, start, end) for day in days])
    return f"{_days(days)}, {format_minute(start)}–{_last(end)}"


def _it(days: Sequence[object]) -> str:
    return "it" if len(days) == 1 else "them"


def _key(day: date) -> str:
    return f"{day.month}/{day.day}"


def _reopen(days: Sequence[date]) -> str:
    """The text that opens ``days`` again (``unblock 10/15`` is one day only)."""

    if len(days) == 1:
        return f"unblock {_key(days[0])}"
    return f"open {_and([_key(day) for day in days])} again"


def _hours(block: CalendarBlock) -> str:
    if _all_day(block.start_minute, block.end_minute):
        return "all day"
    return f"{format_minute(block.start_minute)}–{_last(block.end_minute)}"


class _DayNotChanged(Exception):  # noqa: N818 - a per-day outcome, not a failure of the change
    """One day of a change that didn't happen: ``reason`` for a list of days,
    ``reply`` when it was the only day."""

    def __init__(self, reason: str, reply: str, *, uncertain: bool = False) -> None:
        super().__init__(reply)
        self.reason = reason
        self.reply = reply
        self.uncertain = uncertain


def _misses(missed: Sequence[tuple[date, _DayNotChanged]], not_done: str) -> str:
    """``Not blocked: Fri Oct 16 (why). Not sure: Sat Oct 17 (why).``"""

    parts = []
    for label, uncertain in ((not_done, False), ("Not sure", True)):
        days = [(day, why) for day, why in missed if why.uncertain is uncertain]
        if days:
            reasons = "; ".join(f"{format_day(day)} ({why.reason})" for day, why in days)
            parts.append(f"{label}: {reasons}.")
    return " ".join(parts)


def _refuse_if_none_done(
    done: Sequence[date], missed: Sequence[tuple[date, _DayNotChanged]], not_done: str
) -> None:
    if done:
        return
    unsure = any(why.uncertain for _, why in missed)
    error = OwnerChangeUncertainError if unsure else OwnerChangeRefusedError
    if len(missed) == 1:
        raise error(missed[0][1].reply)
    header = "I couldn't confirm any change." if unsure else "Nothing changed."
    raise error(f"{header} {_misses(missed, not_done)}")


def _outcome(
    done: Sequence[date],
    missed: Sequence[tuple[date, _DayNotChanged]],
    *,
    did: str,
    not_done: str,
    after: str,
) -> str:
    """``Done: …`` for the days that changed and ``Not …: day (why)`` for the rest."""

    reply = f"Done: {did}. {after}"
    if missed:
        reply += f"\n{_misses(missed, not_done)} Ask again for {_it(missed)} if you still want."
    return reply


_DAYS_MAX = 31
_DAYS_ARG = {
    "type": "array",
    "items": {"type": "string"},
    "description": (
        "Every date the owner named, YYYY-MM-DD in the business's time zone; a range "
        "like Monday through Wednesday is each date in it."
    ),
}


class _Calendar:
    def __init__(
        self,
        unit_of_work_factory: Callable[[], UnitOfWork],
        schedule: ScheduleBlockPort,
        now: Callable[[], datetime],
    ) -> None:
        self.unit_of_work_factory = unit_of_work_factory
        self.now = now
        self._schedule = schedule

    def schedule(self, business: BusinessRecord) -> ScheduleBlockPort:
        if not self._schedule.serves(business.business_id):
            raise OwnerChangeRefusedError(BLOCK_NOT_CONFIGURED_REPLY)
        return self._schedule

    async def day(self, business: BusinessRecord, value: object) -> date:
        """A date from today on, in the calendar's own zone (its hours' wall clock)."""

        try:
            day = date.fromisoformat(str(value))
        except ValueError as error:
            raise OwnerChangeRefusedError("Which day? Give a date, like Tue Oct 6.") from error
        name = await _reachable(self._schedule.schedule_timezone(business.business_id))
        try:
            zone = ZoneInfo(name) if name else business_zone(business)
        except ZoneInfoNotFoundError:
            zone = business_zone(business)
        if day < self.now().astimezone(zone).date():
            raise OwnerChangeRefusedError(f"{format_day(day)} has already passed.")
        return day

    async def days(self, business: BusinessRecord, args: Mapping[str, Any]) -> list[date]:
        """The ``days`` asked for (one ``day`` still works), in order, each once."""

        values = args.get("days", [args["day"]] if "day" in args else [])
        if not isinstance(values, list) or not values:
            raise OwnerChangeRefusedError("Which day? Give a date, like Tue Oct 6.")
        if len(values) > _DAYS_MAX:
            raise OwnerChangeRefusedError(f"I can change up to {_DAYS_MAX} days at a time.")
        return sorted({await self.day(business, value) for value in values})


async def _reachable[T](call: Awaitable[T]) -> T:
    try:
        result = await call
    except AvailabilityError as error:
        raise OwnerChangeRefusedError(BLOCK_UNREACHABLE_REPLY) from error
    return result


class BlockTimeTool:
    spec = OwnerAssistantTool(
        "change_block_time",
        "Propose blocking time on the booking calendar so no customer can book it, on the "
        "website chat or the booking link. One time range, on one or more dates (all of "
        "them in one change); a whole day is 00:00 to 24:00.",
        {
            "days": _DAYS_ARG,
            "start": {"type": "string", "description": "Start, 24-hour HH:MM."},
            "end": {"type": "string", "description": "End, 24-hour HH:MM; 24:00 is midnight."},
        },
    )
    sensitive = False

    def __init__(self, calendar: _Calendar) -> None:
        self._calendar = calendar

    async def prepare(self, business: BusinessRecord, args: Mapping[str, Any]) -> OwnerChangePlan:
        schedule = self._calendar.schedule(business)
        days = await self._calendar.days(business, args)
        start, end = _minute(args.get("start"), end=False), _minute(args.get("end"), end=True)
        if end <= start:
            raise OwnerChangeRefusedError("The end time must be after the start time.")
        open_days: list[date] = []
        closed: list[date] = []
        for day in days:
            hours = await _reachable(schedule.day_hours(business.business_id, day))
            shut = subtract_interval(hours.intervals, start, end) == hours.intervals
            (closed if shut else open_days).append(day)
        if not open_days:
            raise OwnerChangeRefusedError(
                f"You aren't open then ({_spans(days, start, end)}), so there's nothing to block."
            )
        summary = (
            f"I will block {_spans(open_days, start, end)} on your booking calendar, so no "
            "customer can book it. Visits already booked stay as they are."
        )
        if closed:
            summary += f" You aren't open then on {_days(closed)}, so I left {_it(closed)} out."
        return OwnerChangePlan(
            summary=summary,
            arguments={"days": [day.isoformat() for day in open_days], "start": start, "end": end},
        )

    async def apply(self, business: BusinessRecord, arguments: Mapping[str, JsonValue]) -> str:
        schedule = self._calendar.schedule(business)
        start, end = int(str(arguments["start"])), int(str(arguments["end"]))
        values = arguments.get("days", [arguments.get("day")])
        assert isinstance(values, list)
        done: list[date] = []
        missed: list[tuple[date, _DayNotChanged]] = []
        # Day by day, each with its own undo record: one day failing leaves the
        # others blocked, and each can be unblocked on its own.
        for value in values:
            day = date.fromisoformat(str(value))
            try:
                await self._block(business, schedule, day, start, end)
            except _DayNotChanged as why:
                missed.append((day, why))
            else:
                done.append(day)
        _refuse_if_none_done(done, missed, "Not blocked")
        return _outcome(
            done,
            missed,
            did=f"blocked {_spans(done, start, end)}, so no one can book {_it(done)}",
            not_done="Not blocked",
            after=f'To open {_it(done)} again, text "{_reopen(done)}".',
        )

    async def _block(
        self, business: BusinessRecord, schedule: ScheduleBlockPort, day: date, start: int, end: int
    ) -> None:
        try:
            # Rechecked at YES: past midnight the day may be gone.
            await self._calendar.day(business, day.isoformat())
        except OwnerChangeRefusedError as refusal:
            raise _DayNotChanged("it has already passed", str(refusal)) from refusal
        unreachable = _DayNotChanged("I couldn't reach the calendar", BLOCK_UNREACHABLE_REPLY)
        now = self._calendar.now()
        business_id = business.business_id
        async with self._calendar.unit_of_work_factory() as unit_of_work:
            try:
                hours = await schedule.day_hours(business_id, day)
            except AvailabilityError as error:
                raise unreachable from error
            remaining = subtract_interval(hours.intervals, start, end)
            if remaining == hours.intervals:
                raise _DayNotChanged(
                    "that time isn't open any more",
                    "That time isn't open any more, so nothing changed.",
                )
            # Saved, with the hours it replaces, before Calendly is touched: a
            # write that lands and then errors can still be unblocked.
            block = CalendarBlock(
                block_id=uuid4(),
                business_id=business_id,
                day=day,
                start_minute=start,
                end_minute=end,
                state=CalendarBlockState.APPLIED,
                previous=hours,
                created_at=now,
                expires_at=now,
                decided_at=now,
            )
            await unit_of_work.calendar_blocks.add(block)
            await unit_of_work.commit()
            try:
                await schedule.set_day_hours(business_id, day, remaining)
            except AvailabilityError as error:
                try:
                    now_open = (await schedule.day_hours(business_id, day)).intervals
                    # Landed if the range is closed now, whatever else changed.
                    landed = subtract_interval(now_open, start, end) == now_open
                except AvailabilityError:
                    raise _DayNotChanged(
                        f'I couldn\'t tell whether it saved; text "unblock {_key(day)}" to be '
                        "sure it's open",
                        _BLOCK_UNSURE_REPLY.format(day=_key(day)),
                        uncertain=True,
                    ) from error
                if not landed:
                    await unit_of_work.calendar_blocks.save(
                        block.with_updates(state=CalendarBlockState.REMOVED)
                    )
                    await unit_of_work.commit()
                    raise unreachable from error


class UnblockDayTool:
    spec = OwnerAssistantTool(
        "change_unblock_day",
        "Propose removing every block on one or more dates (all of them in one change), so "
        "their usual hours can be booked again.",
        {"days": _DAYS_ARG},
    )
    sensitive = False

    def __init__(self, calendar: _Calendar) -> None:
        self._calendar = calendar

    async def prepare(self, business: BusinessRecord, args: Mapping[str, Any]) -> OwnerChangePlan:
        self._calendar.schedule(business)
        days = await self._calendar.days(business, args)
        found: list[tuple[date, Sequence[CalendarBlock]]] = []
        async with self._calendar.unit_of_work_factory() as unit_of_work:
            for day in days:
                blocks = await unit_of_work.calendar_blocks.applied_on(business.business_id, day)
                if blocks:
                    found.append((day, blocks))
        if not found:
            raise OwnerChangeRefusedError(f"Nothing is blocked on {_days(days)}.")
        listed = _and(
            [
                f"{format_day(day)} ({', '.join(_hours(block) for block in blocks)})"
                for day, blocks in found
            ]
        )
        summary = f"I will unblock {listed}, so your usual hours can be booked again."
        unblocked = [day for day in days if day not in {day for day, _ in found}]
        if unblocked:
            summary += f" Nothing is blocked on {_days(unblocked)}, so I left {_it(unblocked)} out."
        return OwnerChangePlan(
            summary=summary,
            # The blocks read back: YES must not remove one added since.
            arguments={
                "days": [
                    {"day": day.isoformat(), "blocks": [str(b.block_id) for b in blocks]}
                    for day, blocks in found
                ]
            },
        )

    async def apply(self, business: BusinessRecord, arguments: Mapping[str, JsonValue]) -> str:
        schedule = self._calendar.schedule(business)
        entries = arguments.get(
            "days", [{"day": arguments.get("day"), "blocks": arguments.get("blocks")}]
        )
        assert isinstance(entries, list)
        done: list[date] = []
        missed: list[tuple[date, _DayNotChanged]] = []
        for entry in entries:
            assert isinstance(entry, dict)
            day = date.fromisoformat(str(entry["day"]))
            named = entry["blocks"]
            assert isinstance(named, list)
            try:
                await self._unblock(business, schedule, day, [str(block) for block in named])
            except _DayNotChanged as why:
                missed.append((day, why))
            else:
                done.append(day)
        _refuse_if_none_done(done, missed, "Still blocked")
        return _outcome(
            done,
            missed,
            did=f"unblocked {_days(done)}",
            not_done="Still blocked",
            after="Your usual hours are open again.",
        )

    async def _unblock(
        self, business: BusinessRecord, schedule: ScheduleBlockPort, day: date, named: list[str]
    ) -> None:
        now = self._calendar.now()
        async with self._calendar.unit_of_work_factory() as unit_of_work:
            blocks = await unit_of_work.calendar_blocks.applied_on(business.business_id, day)
            if sorted(str(block.block_id) for block in blocks) != sorted(named):
                raise _DayNotChanged(
                    "its blocks changed since I asked",
                    f"The blocks on {format_day(day)} changed since I asked, so nothing "
                    "changed. Ask again if you still want it open.",
                )
            previous = blocks[0].previous
            target = previous.intervals if previous is not None and previous.overridden else None
            try:
                await schedule.set_day_hours(business.business_id, day, target)
            except AvailabilityError as error:
                # The write may have landed: read the day back before saying so.
                try:
                    hours = await schedule.day_hours(business.business_id, day)
                except AvailabilityError:
                    raise _DayNotChanged(
                        "I couldn't tell whether it opened; "
                        f'text "unblock {_key(day)}" to be sure',
                        "I couldn't reach the calendar, so I can't tell whether "
                        f'{format_day(day)} opened. Text "unblock {_key(day)}" to try again.',
                        uncertain=True,
                    ) from error
                opened = not hours.overridden if target is None else hours.intervals == target
                if not opened:
                    raise _DayNotChanged(
                        "I couldn't reach the calendar", BLOCK_UNREACHABLE_REPLY
                    ) from error
            for block in blocks:
                await unit_of_work.calendar_blocks.save(
                    block.with_updates(state=CalendarBlockState.REMOVED, decided_at=now)
                )
            await unit_of_work.commit()


def calendar_change_tools(
    unit_of_work_factory: Callable[[], UnitOfWork],
    schedule: ScheduleBlockPort | None,
    *,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> tuple[OwnerChangeTool, ...]:
    if schedule is None:
        return ()
    calendar = _Calendar(unit_of_work_factory, schedule, now)
    return (BlockTimeTool(calendar), UnblockDayTool(calendar))


# The read-back, and any failure notice, quote the whole message in one owner text.
MESSAGE_LIMITS = {"text": 480, "e-mail": 900}


def _phone_text(phone: str) -> str:
    digits = "".join(ch for ch in phone if ch.isdigit())
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    if len(digits) == 10:
        return f"({digits[:3]}) {digits[3:6]}-{digits[6:]}"
    return phone


def _route(customer: CustomerRecord, can_text: bool) -> tuple[str, str]:
    """By text only to a customer who said yes to texts, when texting is set up;
    otherwise e-mail."""

    if can_text and customer.sms_consent is True and customer.phone:
        return "text", customer.phone
    return "e-mail", customer.email


class MessageCustomerTool:
    spec = OwnerAssistantTool(
        "change_message_customer",
        "Propose sending one of the business's customers a message from the owner, word "
        "for word. It goes by text only to a customer who agreed to texts, otherwise by "
        "e-mail. The owner sees the full message and the recipient before YES.",
        {
            "customer": _CUSTOMER,
            "message": {
                "type": "string",
                "description": "The exact words to send, as the owner gave them. Add nothing.",
            },
        },
    )
    sensitive = True

    def __init__(self, unit_of_work_factory: Callable[[], UnitOfWork], can_text: bool) -> None:
        self._unit_of_work_factory = unit_of_work_factory
        self._can_text = can_text

    async def prepare(self, business: BusinessRecord, args: Mapping[str, Any]) -> OwnerChangePlan:
        message = str(args.get("message") or "").strip()
        if not message:
            raise OwnerChangeRefusedError("Say what the message should say.")
        customer = await self._customer(business, args.get("customer"))
        channel, address = _route(customer, self._can_text)
        if len(message) > MESSAGE_LIMITS[channel]:
            raise OwnerChangeRefusedError(
                f"That's {len(message)} characters; a {channel} from me can be at most "
                f"{MESSAGE_LIMITS[channel]}. Make it shorter."
            )
        subject = f"Message from {business.display_name or business.name}"
        if channel == "text":
            how = f"text {customer.name} at {_phone_text(address)}"
        else:
            how = f'e-mail {customer.name} at {address}, subject "{subject}"'
        return OwnerChangePlan(
            summary=f'I will {how}, this message, word for word:\n"{message}"',
            arguments={
                "customer_id": str(customer.customer_id),
                "name": customer.name,
                "channel": channel,
                "address": address,
                "subject": subject,
                "message": message,
                "key": uuid4().hex,
            },
        )

    async def apply(self, business: BusinessRecord, arguments: Mapping[str, JsonValue]) -> str:
        customer_id = CustomerId(UUID(_str(arguments, "customer_id")))
        channel, address = _str(arguments, "channel"), _str(arguments, "address")
        key = f"{OWNER_MESSAGE_KEY_PREFIX}{business.business_id}:{_str(arguments, 'key')}"
        async with self._unit_of_work_factory() as unit_of_work:
            customer = await unit_of_work.customers.get(business.business_id, customer_id)
            if customer is None or _route(customer, self._can_text) != (channel, address):
                raise OwnerChangeRefusedError(
                    f"{arguments['name']}'s contact details changed since I read them "
                    "back. Ask me again."
                )
            if channel == "text":
                command = intake_customer_text_command(
                    business.business_id,
                    customer_id=customer_id,
                    phone=address,
                    text=_str(arguments, "message"),
                    idempotency_key=key,
                )
            else:
                command = intake_customer_email_command(
                    IntakeCustomerEmail(
                        business_id=business.business_id,
                        to=address,
                        subject=_str(arguments, "subject"),
                        body=_str(arguments, "message"),
                        idempotency_key=key,
                    )
                )
            await unit_of_work.outbox.enqueue(command)
            await unit_of_work.commit()
        return f"Done: your message to {arguments['name']} is on its way by {channel}."

    async def _customer(self, business: BusinessRecord, target: object) -> CustomerRecord:
        needle = _needle(target)
        async with self._unit_of_work_factory() as unit_of_work:
            customers = await unit_of_work.customers.list_for_business(business.business_id)
        exact = [c for c in customers if c.name.casefold() == needle]
        found = exact or [
            c for c in customers if needle in (c.display_name or "").casefold() or needle == c.email
        ]
        if not found:
            raise OwnerChangeRefusedError(
                f'I can\'t find a customer named "{target}". I can only message customers '
                "in your customer list."
            )
        if len(found) > 1:
            names = ", ".join(c.name for c in found[:5])
            raise OwnerChangeRefusedError(f"More than one matches ({names}). Say which one.")
        return found[0]


def message_change_tools(
    unit_of_work_factory: Callable[[], UnitOfWork], *, can_text: bool
) -> tuple[OwnerChangeTool, ...]:
    return (MessageCustomerTool(unit_of_work_factory, can_text),)
