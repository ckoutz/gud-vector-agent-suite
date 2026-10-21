"""Owner assistant: plain-language answers to an owner's questions, read only.

The business is the one the owner's message came from; the model never picks
it. The model may only call the read tools below, each runs here against that
one business, and none of them can change anything. Text customers wrote
(names, notes, chat details) goes back marked as ``customer_wrote`` data.
"""

import logging
import re
from collections.abc import Callable, Iterable, Mapping
from datetime import UTC, date, datetime, time, timedelta, tzinfo
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from gvas.domain.calendar_blocks import CalendarBlockState
from gvas.domain.enums import CustomerQuoteStatus, QuoteStatus
from gvas.domain.identifiers import BusinessId
from gvas.domain.intake import IntakeConversation, IntakeState
from gvas.domain.invoices import InvoiceStatus, invoice_status
from gvas.domain.owner import CalendarEventSource
from gvas.domain.owner_assistant import (
    OWNER_ASSISTANT_REPLY_MAX_CHARS,
    BusinessCalendarPort,
    OwnerAssistantAlertPort,
    OwnerAssistantError,
    OwnerAssistantPort,
    OwnerAssistantRequest,
    OwnerAssistantSetupError,
    OwnerAssistantTool,
    OwnerChangePlan,
    OwnerChangeRefusedError,
    OwnerChangeTool,
    OwnerChangeUncertainError,
)
from gvas.domain.owner_commands import (
    OWNER_COMMAND_TEXT_MAX_CHARS,
    OWNER_COMMAND_TTL,
    OwnerCommand,
    OwnerCommandConflictError,
    OwnerCommandState,
    conversation_key,
    is_no,
    is_yes,
    is_yes_and_more,
)
from gvas.domain.payments import month_totals
from gvas.domain.quotes import Quote
from gvas.domain.repositories import BusinessRecord, UnitOfWork
from gvas.domain.usage import UsageCeilingGuard, UsageKind

logger = logging.getLogger(__name__)

DEFAULT_ZONE = "America/Los_Angeles"
# The dashboard's own list limits, so the answers match what it shows.
QUOTE_LIST_LIMIT = 500
BOOKING_LIST_LIMIT = 200
SCHEDULE_MAX_DAYS = 7
LIST_MAX_ITEMS = 15
CUSTOMER_MATCH_MAX = 5
QUOTE_FILTERS = ("all", "waiting_for_owner", "sent", "accepted", "paid", "declined")

UNAVAILABLE_REPLY = "Sorry, I can't answer right now. The dashboard has everything in the meantime."
SETUP_ALERT_INTERVAL = timedelta(hours=1)
SETUP_REPLY = (
    "Sorry, I can't answer texts right now: the AI service is refusing my requests, "
    "a setup problem on Güd Vector's side. The dashboard has everything in the meantime."
)
CONFIRM_PROMPT = "Reply YES in the next 10 minutes to do it, or NO to cancel it."
ALREADY_ANSWERED_REPLY = (
    "Your change was already answered by another message, so this message didn't cancel "
    "or change it."
)
ONE_CHANGE_NOTE = (
    "(I can set up one change per YES, so the rest of your text isn't in it. "
    "Once this one is done or cancelled, ask me for the rest.)"
)


def cancelled_note(summary: str) -> str:
    """``I cancelled the pending change: block … Nothing was changed.``"""

    what = summary.split(". ", 1)[0].rstrip(".")
    what = what.removeprefix("I will ")
    if len(what) > 200:
        what = what[:199].rstrip() + "…"
    return f"I cancelled the pending change: {what}. Nothing was changed."


def still_waiting_note(summary: str) -> str:
    return (
        f"Nothing is done yet. This change is still waiting for a plain YES:\n{summary}\n\n"
        "Reply YES on its own to do it, or NO to cancel it. Then ask me for the rest."
    )


NOTHING_PENDING_REPLY = "Nothing is waiting for a YES, so nothing was changed."
NO_CHANGE_SET_UP_NOTE = (
    "(Nothing is set up for a YES, so a YES now changes nothing. "
    "To make a change, ask again, one change per text.)"
)
# Only code asks for YES. When a model reply seems to ask for one with no
# change proposed, the answer is kept and this note is added, so a quoted
# customer "YES" costs one extra line and a real false ask can't mislead.
_ASKS_FOR_YES = (
    re.compile(r"\bYES\b"),
    re.compile(
        r"\b(?:reply|text|say|respond|answer|send)\b[^.!?\n]{0,15}\byes\b|\bconfirm\b",
        re.IGNORECASE,
    ),
)
BUSY_REPLY = (
    "Another change was being set up at the same moment, so I set up nothing. "
    "Please send that again."
)
CHANGE_FAILED_REPLY = (
    "Something went wrong while making that change, so it may not be done. "
    "Please check the dashboard."
)
LIMIT_REPLY = (
    "I've reached this month's limit for plain-language answers. "
    "The dashboard still has everything."
)

TOOLS: tuple[OwnerAssistantTool, ...] = (
    OwnerAssistantTool(
        "schedule",
        "Bookings, booking requests and calendar events from start_day for `days` days "
        f"(1-{SCHEDULE_MAX_DAYS}), in the business's local time.",
        {
            "start_day": {"type": "string", "description": "Local date, YYYY-MM-DD."},
            "days": {"type": "integer", "minimum": 1, "maximum": SCHEDULE_MAX_DAYS},
        },
    ),
    OwnerAssistantTool(
        "needs_you",
        "What is waiting for the owner: quotes to approve and booking requests to approve or "
        "decline.",
    ),
    OwnerAssistantTool(
        "money",
        "Paid this month and what is outstanding (open invoices, or accepted quotes not paid "
        "yet when the business doesn't use invoices).",
    ),
    OwnerAssistantTool(
        "find_customer",
        "Look up customers by name, e-mail or phone: contact details, quotes and bookings.",
        {"query": {"type": "string", "description": "Part of a name, e-mail or phone."}},
    ),
    OwnerAssistantTool(
        "quotes",
        f"The newest quotes (up to {LIST_MAX_ITEMS}) with their status.",
        {"status": {"type": "string", "enum": list(QUOTE_FILTERS)}},
    ),
)


def money_text(minor: int, currency: str = "usd") -> str:
    amount = f"${minor / 100:,.2f}".replace(".00", "")
    return amount if currency.lower() == "usd" else f"{amount} {currency.upper()}"


def totals_text(amounts: Iterable[tuple[int, str]]) -> str:
    """One total per currency, never added across currencies."""

    sums: dict[str, int] = {}
    for minor, currency in amounts:
        sums[currency.lower()] = sums.get(currency.lower(), 0) + minor
    return " + ".join(money_text(minor, currency) for currency, minor in sums.items()) or (
        money_text(0)
    )


def business_zone(business: BusinessRecord) -> tzinfo:
    try:
        return ZoneInfo(business.timezone or DEFAULT_ZONE)
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo(DEFAULT_ZONE)


def _when(moment: datetime | None, zone: tzinfo) -> str | None:
    if moment is None:
        return None
    return moment.astimezone(zone).strftime("%a %b %-d, %-I:%M %p %Z")


def _wrote(text: str | None) -> dict[str, str] | None:
    return {"customer_wrote": text} if text else None


def _quote_state(quote: Quote) -> str:
    if quote.customer_status is CustomerQuoteStatus.PAID:
        return "paid"
    if quote.customer_status is CustomerQuoteStatus.ACCEPTED:
        return "accepted, not paid yet"
    if quote.customer_status is CustomerQuoteStatus.DECLINED:
        return "declined by the customer"
    if quote.status is QuoteStatus.AWAITING_APPROVAL:
        return "waiting for the owner's approval"
    if quote.status is QuoteStatus.REJECTED:
        return "rejected by the owner"
    if quote.status is QuoteStatus.DELIVERED:
        return "viewed by the customer" if quote.customer_status else "sent to the customer"
    return "being prepared"


def _quote_filter(quote: Quote, status: str) -> bool:
    state = _quote_state(quote)
    return {
        "all": True,
        "waiting_for_owner": state.startswith("waiting"),
        "sent": state.startswith(("sent", "viewed")),
        "accepted": state.startswith("accepted"),
        "paid": state == "paid",
        "declined": state.startswith(("declined", "rejected")),
    }.get(status, True)


def _quote_row(quote: Quote, zone: tzinfo) -> dict[str, Any]:
    draft = quote.draft
    assert draft is not None
    return {
        "customer": _wrote(draft.recipient.display_name or draft.recipient.address),
        "total": money_text(draft.total_minor, draft.currency),
        "billing": draft.billing.value
        + (f" every {draft.interval.value}" if draft.interval is not None else ""),
        "items": [_wrote(item.description) for item in draft.line_items[:3]],
        "status": _quote_state(quote),
        "created": _when(quote.created_at, zone),
    }


def _booking_row(conversation: IntakeConversation, zone: tzinfo) -> dict[str, Any]:
    state = {
        IntakeState.AWAITING_OWNER: "waiting for the owner to approve or decline",
        IntakeState.APPROVED: "booked",
        IntakeState.DECLINED: "declined",
    }.get(conversation.state, conversation.state.value)
    collected = conversation.collected
    return {
        "reference": conversation.reference,
        "customer": _wrote(collected.name),
        "phone": collected.phone,
        "email": collected.email,
        "address": _wrote(collected.address),
        "requested_time": _when(conversation.requested_slot_start, zone),
        "status": state,
        "job": _wrote(collected.details),
        "notes": _wrote(collected.notes),
    }


_EVENT_STATUS = {
    CalendarEventSource.BOOKING: "booked",
    CalendarEventSource.REQUEST: "requested, waiting for the owner to approve or decline",
    CalendarEventSource.CALENDAR: "on the owner's own calendar",
}


class OwnerReadTools:
    """The assistant's fixed read tools, each bound to one business."""

    def __init__(
        self,
        unit_of_work_factory: Callable[[], UnitOfWork],
        owner: BusinessCalendarPort,
        *,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._unit_of_work_factory = unit_of_work_factory
        self._owner = owner
        self._now = now

    async def run(self, business: BusinessRecord, name: str, args: Mapping[str, Any]) -> Any:
        zone = business_zone(business)
        if name == "schedule":
            return await self._schedule(business, zone, args)
        if name == "needs_you":
            return await self._needs_you(business, zone)
        if name == "money":
            return await self._money(business, zone)
        if name == "find_customer":
            return await self._find_customer(business, zone, str(args.get("query") or ""))
        if name == "quotes":
            return await self._quotes(business, zone, str(args.get("status") or "all"))
        return {"error": f"unknown tool {name!r}"}

    async def _schedule(
        self, business: BusinessRecord, zone: tzinfo, args: Mapping[str, Any]
    ) -> Any:
        today = self._now().astimezone(zone).date()
        try:
            start_day = date.fromisoformat(str(args.get("start_day") or today.isoformat()))
        except ValueError:
            return {"error": "start_day must be YYYY-MM-DD"}
        try:
            days = min(max(int(args.get("days") or 1), 1), SCHEDULE_MAX_DAYS)
        except (TypeError, ValueError):
            days = 1
        try:
            start = datetime.combine(start_day, time(), zone)
            end = datetime.combine(start_day + timedelta(days=days), time(), zone)
        except OverflowError:
            return {"error": "that date is out of range"}
        view = await self._owner.business_calendar(business, start, end)
        return {
            "from": start_day.isoformat(),
            "days": days,
            "events": [
                {
                    "status": _EVENT_STATUS.get(event.source, event.source.value),
                    "start": _when(event.start, zone),
                    "end": _when(event.end, zone),
                    "title": _wrote(event.title),
                    "customer": _wrote(event.invitee_name),
                    "address": _wrote(event.location),
                }
                for event in view.events[: LIST_MAX_ITEMS * 2]
            ],
            "problems": list(view.problems),
        }

    async def _needs_you(self, business: BusinessRecord, zone: tzinfo) -> Any:
        async with self._unit_of_work_factory() as unit_of_work:
            quotes = await unit_of_work.quotes.list_for_business(
                business.business_id, limit=QUOTE_LIST_LIMIT, status=QuoteStatus.AWAITING_APPROVAL
            )
            bookings = await unit_of_work.intake_conversations.list_awaiting_owner(
                business.business_id, limit=BOOKING_LIST_LIMIT
            )
        waiting = [
            quote
            for quote in quotes
            if quote.draft is not None and quote.status is QuoteStatus.AWAITING_APPROVAL
        ]
        return {
            "quotes_to_approve": [_quote_row(quote, zone) for quote in waiting[:LIST_MAX_ITEMS]],
            "booking_requests": [_booking_row(item, zone) for item in bookings[:LIST_MAX_ITEMS]],
        }

    async def _money(self, business: BusinessRecord, zone: tzinfo) -> Any:
        now = self._now()
        async with self._unit_of_work_factory() as unit_of_work:
            payments = await unit_of_work.payments.list_for_business(business.business_id)
            invoices = (
                await unit_of_work.invoices.list_for_business(business.business_id)
                if business.invoice_prefix
                else ()
            )
            quotes = (
                ()
                if business.invoice_prefix
                else await unit_of_work.quotes.list_for_business(
                    business.business_id, limit=QUOTE_LIST_LIMIT
                )
            )
        paid = {
            currency: money_text(minor, currency)
            for currency, minor in month_totals(payments, zone, now).items()
        }
        result: dict[str, Any] = {
            "month": now.astimezone(zone).strftime("%B %Y"),
            "paid_this_month": paid or {"usd": money_text(0)},
        }
        if business.invoice_prefix:
            counted = {payment.quote_id: payment for payment in payments if payment.counts}
            today = now.astimezone(zone).date()
            open_invoices = []
            for invoice in invoices:
                status = invoice_status(invoice, counted.get(invoice.quote_id), today=today)
                if status not in (InvoiceStatus.PAID, InvoiceStatus.VOID):
                    open_invoices.append((invoice, status))
            # A draft isn't owed until it is sent.
            result["outstanding"] = totals_text(
                (i.total_minor, i.currency)
                for i, status in open_invoices
                if status is not InvoiceStatus.DRAFT
            )
            result["open_invoices"] = [
                {
                    "number": invoice.number,
                    "customer": _wrote(invoice.customer_name),
                    "total": money_text(invoice.total_minor, invoice.currency),
                    "due": invoice.due_on.strftime("%a %b %-d"),
                    "status": status.value,
                }
                for invoice, status in open_invoices[:LIST_MAX_ITEMS]
            ]
        else:
            unpaid = [
                quote
                for quote in quotes
                if quote.draft is not None and quote.customer_status is CustomerQuoteStatus.ACCEPTED
            ]
            result["outstanding"] = totals_text(
                (quote.draft.total_minor, quote.draft.currency)
                for quote in unpaid
                if quote.draft is not None
            )
            result["accepted_not_paid"] = [
                _quote_row(quote, zone) for quote in unpaid[:LIST_MAX_ITEMS]
            ]
        return result

    async def _find_customer(self, business: BusinessRecord, zone: tzinfo, query: str) -> Any:
        needle = query.strip().casefold()
        if len(needle) < 2:
            return {"error": "give at least 2 letters of a name, e-mail or phone"}
        digits = "".join(ch for ch in needle if ch.isdigit())

        def hit(*values: str | None) -> bool:
            for value in values:
                if not value:
                    continue
                if needle in value.casefold():
                    return True
                if len(digits) >= 4 and digits in "".join(ch for ch in value if ch.isdigit()):
                    return True
            return False

        async with self._unit_of_work_factory() as unit_of_work:
            customers = await unit_of_work.customers.list_for_business(business.business_id)
            bookings = await unit_of_work.intake_conversations.list_booking_requests(
                business.business_id, limit=BOOKING_LIST_LIMIT
            )
            quotes = await unit_of_work.quotes.list_for_business(
                business.business_id, limit=QUOTE_LIST_LIMIT
            )
        matches = [c for c in customers if hit(c.display_name, c.email, c.phone)]
        matched_ids = {customer.customer_id for customer in matches}
        matched_emails = {customer.email.casefold() for customer in matches}

        def booking_matches(item: IntakeConversation) -> bool:
            collected = item.collected
            return (item.customer_id is not None and item.customer_id in matched_ids) or hit(
                collected.name, collected.email, collected.phone
            )

        def quote_matches(quote: Quote) -> bool:
            if quote.draft is None:
                return False
            recipient = quote.draft.recipient
            return (
                (quote.customer_id is not None and quote.customer_id in matched_ids)
                or recipient.address.casefold() in matched_emails
                or hit(recipient.display_name, recipient.address, recipient.phone)
            )

        return {
            "customers": [
                {
                    "name": _wrote(customer.display_name),
                    "email": customer.email,
                    "phone": customer.phone,
                }
                for customer in matches[:CUSTOMER_MATCH_MAX]
            ],
            "bookings": [_booking_row(item, zone) for item in bookings if booking_matches(item)][
                :LIST_MAX_ITEMS
            ],
            "quotes": [_quote_row(quote, zone) for quote in quotes if quote_matches(quote)][
                :LIST_MAX_ITEMS
            ],
        }

    async def _quotes(self, business: BusinessRecord, zone: tzinfo, status: str) -> Any:
        async with self._unit_of_work_factory() as unit_of_work:
            quotes = await unit_of_work.quotes.list_for_business(
                business.business_id, limit=QUOTE_LIST_LIMIT
            )
        rows = [
            _quote_row(quote, zone)
            for quote in quotes
            if quote.draft is not None and _quote_filter(quote, status)
        ]
        return {"status": status, "count": len(rows), "quotes": rows[:LIST_MAX_ITEMS]}


class OwnerAssistant:
    """Answers one owner message for the business it came from."""

    def __init__(
        self,
        unit_of_work_factory: Callable[[], UnitOfWork],
        model: OwnerAssistantPort,
        tools: OwnerReadTools,
        *,
        changes: Iterable[OwnerChangeTool] = (),
        ceilings: UsageCeilingGuard | None = None,
        alerts: OwnerAssistantAlertPort | None = None,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._unit_of_work_factory = unit_of_work_factory
        self._model = model
        self._tools = tools
        self._changes = {change.spec.name: change for change in changes}
        self._ceilings = ceilings
        self._alerts = alerts
        self._last_alert_at: datetime | None = None
        self._unsent_alert: tuple[BusinessId, str, str, datetime] | None = None
        self._now = now

    async def _alert(self, business_id: BusinessId, business_name: str, detail: str) -> None:
        """At most one sent alert per ``SETUP_ALERT_INTERVAL``; later failures are only
        logged. A send that fails doesn't count. If the next failure is the same problem,
        the unsent alert is resent unchanged (same e-mail idempotency key, in case the
        first send did land); a different problem is reported as itself."""

        if self._alerts is None:
            return
        now = self._now()
        if self._last_alert_at is not None and now - self._last_alert_at < SETUP_ALERT_INTERVAL:
            return
        alert = (business_id, business_name, detail, now)
        if self._unsent_alert is not None and self._unsent_alert[:3] == alert[:3]:
            alert = self._unsent_alert
        self._last_alert_at = now
        try:
            await self._alerts.setup_problem(*alert)
        except Exception:
            self._last_alert_at = None
            self._unsent_alert = alert
            logger.exception("ALERT owner assistant setup alert could not be sent")
        else:
            self._unsent_alert = None

    async def answer(
        self,
        business_id: BusinessId,
        text: str,
        history: tuple[tuple[str, str], ...] = (),
        *,
        conversation_ref: str = "",
    ) -> str:
        async with self._unit_of_work_factory() as unit_of_work:
            business = await unit_of_work.businesses.get(business_id)
        if business is None:
            return UNAVAILABLE_REPLY
        now = self._now()
        pending = await self._pending(business_id, now)
        cancelled = ""
        if pending is not None:
            same_thread = pending.conversation_ref == conversation_key(conversation_ref)
            if same_thread and is_yes(text):
                return await self._confirm(business, pending)
            if same_thread and is_yes_and_more(text):
                return still_waiting_note(pending.summary)
            if not await self._close(pending, OwnerCommandState.CANCELLED, now):
                if is_yes(text) or is_no(text):
                    return ALREADY_ANSWERED_REPLY
                cancelled = f"{ALREADY_ANSWERED_REPLY}\n\n"
            else:
                cancelled = cancelled_note(pending.summary)
                # A YES from another thread is not the one this change asked for.
                if is_yes(text) or is_no(text):
                    return cancelled
                cancelled += "\n\n"
        elif is_yes(text):
            return NOTHING_PENDING_REPLY
        if self._ceilings is not None and await self._ceilings.is_reached(
            business_id, UsageKind.REVIEW_TOKENS, now=now
        ):
            return cancelled + LIMIT_REPLY
        return cancelled + await self._ask(business, text, history, conversation_ref, now)

    async def _ask(
        self,
        business: BusinessRecord,
        text: str,
        history: tuple[tuple[str, str], ...],
        conversation_ref: str,
        now: datetime,
    ) -> str:
        business_id = business.business_id
        request = OwnerAssistantRequest(
            business_id=business_id,
            business_name=business.display_name or business.name,
            local_now=now.astimezone(business_zone(business)).strftime("%A %Y-%m-%d, %-I:%M %p %Z"),
            text=text,
            tools=TOOLS + tuple(change.spec for change in self._changes.values()),
            history=history,
        )
        staged: list[tuple[OwnerChangeTool, OwnerChangePlan]] = []
        more_asked: list[str] = []

        async def run_tool(name: str, args: Mapping[str, Any]) -> Any:
            change = self._changes.get(name)
            if change is None:
                return await self._tools.run(business, name, args)
            if staged:
                more_asked.append(name)
                return {"error": "Only one change at a time. Propose just the first one."}
            try:
                plan = await change.prepare(business, args)
            except OwnerChangeRefusedError as refusal:
                logger.info(
                    "owner command refused",
                    extra={"business_id": str(business_id), "tool": name},
                )
                return {"error": str(refusal)}
            staged.append((change, plan))
            return {
                "proposed": plan.summary,
                "status": "not done: the owner must reply YES first; the system asks",
            }

        try:
            answer = await self._model.answer(request, run_tool)
        except OwnerAssistantSetupError as error:
            logger.error(
                "ALERT owner assistant setup problem: %s",
                error,
                extra={"business_id": str(business_id), "alert": "owner_assistant_setup"},
            )
            await self._alert(business_id, request.business_name, str(error))
            return SETUP_REPLY
        except OwnerAssistantError:
            logger.warning("owner assistant failed", exc_info=True)
            return UNAVAILABLE_REPLY
        if staged:
            change, plan = staged[0]
            proposal = await self._propose(business, change, plan, text, conversation_ref)
            return f"{proposal}\n\n{ONE_CHANGE_NOTE}" if more_asked else proposal
        reply = answer.text.strip()
        if not reply:
            return UNAVAILABLE_REPLY
        logger.info(
            "owner assistant answered",
            extra={"business_id": str(business_id), "tools": list(answer.tools_called)},
        )
        note = ""
        if any(pattern.search(reply) for pattern in _ASKS_FOR_YES):
            logger.warning(
                "owner assistant asked for YES without a proposal",
                extra={"business_id": str(business_id), "tools": list(answer.tools_called)},
            )
            note = f"\n\n{NO_CHANGE_SET_UP_NOTE}"
        limit = OWNER_ASSISTANT_REPLY_MAX_CHARS - len(note)
        if len(reply) > limit:
            reply = reply[: limit - 1].rstrip() + "…"
        return reply + note

    async def _pending(self, business_id: BusinessId, now: datetime) -> OwnerCommand | None:
        """The live proposal, if any; one past its time is closed as expired."""

        async with self._unit_of_work_factory() as unit_of_work:
            command = await unit_of_work.owner_commands.latest_proposed(business_id)
        if command is None or command.is_live(now):
            return command
        await self._close(command, OwnerCommandState.EXPIRED, now)
        return None

    async def _close(self, command: OwnerCommand, state: OwnerCommandState, now: datetime) -> bool:
        """False when the proposal was answered meanwhile (a YES claimed it)."""

        async with self._unit_of_work_factory() as unit_of_work:
            closed = await unit_of_work.owner_commands.close(command, state, now)
            await unit_of_work.commit()
        return closed

    async def _propose(
        self,
        business: BusinessRecord,
        change: OwnerChangeTool,
        plan: OwnerChangePlan,
        text: str,
        conversation_ref: str,
        *,
        keyed: bool = False,
    ) -> str:
        """Stores the one pending change (closing any other proposal, a calendar
        block's included) and asks for YES in words written by code."""

        now = self._now()
        command = OwnerCommand(
            command_id=uuid4(),
            business_id=business.business_id,
            conversation_ref=conversation_ref if keyed else conversation_key(conversation_ref),
            tool=change.spec.name,
            arguments=dict(plan.arguments),
            summary=plan.summary,
            sensitive=change.sensitive,
            requested_text=text[:OWNER_COMMAND_TEXT_MAX_CHARS],
            created_at=now,
            expires_at=now + OWNER_COMMAND_TTL,
        )
        async with self._unit_of_work_factory() as unit_of_work:
            commands = unit_of_work.owner_commands
            while (other := await commands.latest_proposed(command.business_id)) is not None:
                await commands.close(other, OwnerCommandState.CANCELLED, now)
            block = await unit_of_work.calendar_blocks.latest_proposed(command.business_id)
            if block is not None:
                await unit_of_work.calendar_blocks.save(
                    block.with_updates(state=CalendarBlockState.CANCELLED, decided_at=now)
                )
            try:
                await unit_of_work.owner_commands.add(command)
            except OwnerCommandConflictError:
                return BUSY_REPLY
            await unit_of_work.commit()
        logger.info(
            "owner command proposed",
            extra={"business_id": str(command.business_id), "tool": command.tool},
        )
        return f"{plan.summary}\n\n{CONFIRM_PROMPT}"

    async def _confirm(self, business: BusinessRecord, command: OwnerCommand) -> str:
        now = self._now()
        async with self._unit_of_work_factory() as unit_of_work:
            claimed = await unit_of_work.owner_commands.claim(command, now)
            await unit_of_work.commit()
        if not claimed:
            return NOTHING_PENDING_REPLY
        change = self._changes.get(command.tool)
        state, outcome = OwnerCommandState.FAILED, CHANGE_FAILED_REPLY
        try:
            if change is None:
                raise OwnerChangeRefusedError("I can't make that change by text any more.")
            outcome = await change.apply(business, command.arguments)
            state = OwnerCommandState.DONE
        except OwnerChangeUncertainError as unsure:
            outcome = str(unsure)
        except OwnerChangeRefusedError as refusal:
            outcome = f"Nothing was changed: {refusal}"
        except Exception:
            logger.exception(
                "owner command failed",
                extra={"business_id": str(command.business_id), "tool": command.tool},
            )
        async with self._unit_of_work_factory() as unit_of_work:
            await unit_of_work.owner_commands.save(
                command.with_updates(state=state, outcome=outcome, decided_at=self._now())
            )
            await unit_of_work.commit()
        logger.info(
            "owner command applied",
            extra={
                "business_id": str(command.business_id),
                "tool": command.tool,
                "state": state.value,
            },
        )
        if state is OwnerCommandState.DONE and change is not None:
            outcome = await self._follow_up(business, change, command, outcome)
        return outcome

    async def _follow_up(
        self, business: BusinessRecord, change: OwnerChangeTool, command: OwnerCommand, done: str
    ) -> str:
        """A change that offers a next one (send the invoice just made) proposes
        it at once; it waits for its own YES like any other."""

        follow_up = getattr(change, "follow_up", None)
        if follow_up is None:
            return done
        try:
            offered = await follow_up(business, command.arguments)
        except Exception:
            logger.exception(
                "owner command follow-up failed",
                extra={"business_id": str(command.business_id), "tool": command.tool},
            )
            return done
        if offered is None or (next_change := self._changes.get(offered[0])) is None:
            return done
        proposal = await self._propose(
            business, next_change, offered[1], "", command.conversation_ref, keyed=True
        )
        return f"{done}\n\n{proposal}"
