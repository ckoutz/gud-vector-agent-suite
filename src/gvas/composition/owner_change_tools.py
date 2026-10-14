"""The dashboard's money and decision actions as owner-text change tools.

Each tool resolves the owner's words to one quote in code, writes the exact
sentence the owner confirms, and on YES calls the same ``OwnerService`` method
the dashboard button calls, so the rules are the dashboard's rules.
"""

from collections.abc import Callable, Mapping
from datetime import UTC, date, datetime, timedelta
from typing import Any
from uuid import uuid4

from gvas.application.owner import (
    OwnerConflictError,
    OwnerContext,
    OwnerInputError,
    OwnerNotFoundError,
    OwnerService,
)
from gvas.application.owner_assistant import business_zone, money_text
from gvas.domain.enums import BillingInterval, CustomerQuoteStatus, QuoteBilling, QuoteStatus
from gvas.domain.identifiers import BusinessId, JsonValue
from gvas.domain.intake import IntakeConversation, format_slot_label, sanitize_owner_reason
from gvas.domain.invoices import Invoice
from gvas.domain.owner import OwnerSession
from gvas.domain.owner_assistant import (
    OwnerAssistantTool,
    OwnerChangePlan,
    OwnerChangeRefusedError,
    OwnerChangeTool,
)
from gvas.domain.payments import LedgerPayment, PaymentKind, PaymentMethod, PaymentSource
from gvas.domain.quotes import Quote, public_quote_id
from gvas.domain.repositories import BusinessRecord, UnitOfWork

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
            return f" It moves their visit from {kept}, which is canceled."
        return f" Their original time, {kept}, stays booked and they'll be told so."

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
        if request.superseded_booking is not None:
            # Declining a move keeps the original time; the dashboard sends no note then.
            return OwnerChangePlan(
                summary=(
                    f"I will decline {self._decisions.visit(business, request)}."
                    f"{self._decisions.replaced(business, request, approve=False)}"
                ),
                arguments=arguments,
            )
        told = f', with your note: "{reason}"' if reason else ", with no note."
        if reason:
            arguments["reason"] = reason
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
