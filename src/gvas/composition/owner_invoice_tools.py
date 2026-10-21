"""Invoices an owner makes by text: a new draft for a customer, sending a
draft (its own YES, with the exact message read back), and an amount change
as void-and-reissue, never an edit in place.

An invoice is always backed by one quote, since payments (by card in the
portal, or marked paid by text) are kept per quote. A text-made invoice's
quote is the owner's own: approved and accepted the moment it is made."""

from collections.abc import Callable, Mapping, Sequence
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, cast
from uuid import UUID, uuid4

from gvas.application.owner_assistant import business_zone, money_text
from gvas.composition.owner_change_tools import (
    MESSAGE_LIMITS,
    TEXTS_RECORDED_BY,
    _contact,
    _phone_text,
    _route,
    _str,
)
from gvas.domain.customers import CustomerRecord
from gvas.domain.enums import (
    CustomerQuoteStatus,
    QuotePaymentStatus,
    QuoteStatus,
    RecipientAddressKind,
)
from gvas.domain.identifiers import (
    BusinessId,
    ConversationId,
    CustomerId,
    JsonValue,
    MessageKey,
    QuoteId,
)
from gvas.domain.intake import (
    IntakeCustomerEmail,
    intake_customer_email_command,
    intake_customer_text_command,
)
from gvas.domain.invoices import Invoice, InvoiceLine, due_on, invoice_number
from gvas.domain.messages import ChannelEndpointRef, ConversationRef, CustomerRecipient
from gvas.domain.owner_assistant import (
    OWNER_MESSAGE_KEY_PREFIX,
    OwnerAssistantTool,
    OwnerChangePlan,
    OwnerChangeRefusedError,
    OwnerChangeTool,
)
from gvas.domain.payments import PaymentSource, checkout_expire_command
from gvas.domain.quotes import (
    Quote,
    QuoteConcurrencyError,
    QuoteDraftProposal,
    QuoteLineItem,
    hash_claim_token,
    new_claim_token,
)
from gvas.domain.repositories import BusinessRecord, UnitOfWork

SEND_DRAFT_TOOL = "change_send_draft_invoice"
_ENDPOINT_NAMESPACE = "gvas-owner-texts"
_ENDPOINT_ID = "invoices"
_CURRENCY = "USD"
_MAX_MINOR = 100_000_000  # $1,000,000
_MAX_LINES = 10
_DESCRIPTION_MAX = 200
_LINE = {
    "type": "object",
    "additionalProperties": False,
    "required": ["description", "amount"],
    "properties": {
        "description": {"type": "string", "description": "What the line is for."},
        "amount": {"type": "string", "description": "Dollars, like 120 or 85.50."},
    },
}
_LINES = {
    "type": ["array", "null"],
    "items": _LINE,
    "description": "The invoice lines the owner gave. Null if they gave none yet.",
}
_INVOICE = {"type": "string", "description": "The invoice number (LL-1042) or customer's name."}


def _minor(value: object) -> int:
    text = str(value or "").strip().replace("$", "").replace(",", "")
    try:
        amount = Decimal(text)
    except InvalidOperation as error:
        raise OwnerChangeRefusedError(
            f'"{value}" isn\'t an amount. Give it like 120 or 85.50.'
        ) from error
    if not amount.is_finite() or amount <= 0:
        raise OwnerChangeRefusedError(f'"{value}" isn\'t an amount. Give it like 120 or 85.50.')
    if amount * 100 > _MAX_MINOR:
        raise OwnerChangeRefusedError("That amount is too large for an invoice by text.")
    if amount != amount.quantize(Decimal("0.01")):
        raise OwnerChangeRefusedError(f'"{value}" isn\'t an amount. Give it like 120 or 85.50.')
    return int(amount * 100)


def _lines(raw: object) -> list[dict[str, JsonValue]]:
    if not isinstance(raw, Sequence) or isinstance(raw, str) or not raw:
        raise OwnerChangeRefusedError(
            "Ask the owner what the invoice is for: the line items with an amount each, "
            "or one amount and a short description."
        )
    if len(raw) > _MAX_LINES:
        raise OwnerChangeRefusedError(f"An invoice by text can have at most {_MAX_LINES} lines.")
    lines: list[dict[str, JsonValue]] = []
    for item in raw:
        entry = item if isinstance(item, Mapping) else {}
        description = " ".join(str(entry.get("description") or "").split())
        if not description:
            raise OwnerChangeRefusedError("Ask the owner what each amount is for.")
        if len(description) > _DESCRIPTION_MAX:
            raise OwnerChangeRefusedError("That description is too long.")
        if entry.get("amount") in (None, ""):
            raise OwnerChangeRefusedError(f'Ask the owner how much "{description}" is.')
        lines.append({"description": description, "amount_minor": _minor(entry.get("amount"))})
    return lines


def _line_items(arguments: Mapping[str, JsonValue]) -> tuple[QuoteLineItem, ...]:
    raw = arguments["lines"]
    assert isinstance(raw, list)
    return tuple(
        QuoteLineItem(
            description=str(line["description"]),
            quantity=1,
            unit_price_minor=int(str(line["amount_minor"])),
        )
        for line in raw
        if isinstance(line, dict)
    )


def _read_back(lines: Sequence[Mapping[str, JsonValue]], currency: str = _CURRENCY) -> str:
    return "\n".join(
        f"- {line['description']}: {money_text(int(str(line['amount_minor'])), currency)}"
        for line in lines
    )


def _total(lines: Sequence[Mapping[str, JsonValue]]) -> int:
    return sum(int(str(line["amount_minor"])) for line in lines)


def _day(value: date) -> str:
    return value.strftime("%a %b %-d")


class _Invoices:
    """Shared lookups and writes; the business always comes from the caller."""

    def __init__(
        self,
        unit_of_work_factory: Callable[[], UnitOfWork],
        now: Callable[[], datetime],
        can_text: bool,
    ) -> None:
        self.unit_of_work_factory = unit_of_work_factory
        self.now = now
        self.can_text = can_text

    @staticmethod
    def require_invoices(business: BusinessRecord) -> str:
        if not business.invoice_prefix:
            raise OwnerChangeRefusedError("Invoices are off for this business.")
        return business.invoice_prefix

    async def customer(self, business: BusinessRecord, target: object) -> CustomerRecord:
        needle = " ".join(str(target or "").split()).casefold()
        if len(needle) < 2:
            raise OwnerChangeRefusedError("Ask the owner which customer the invoice is for.")
        digits = "".join(character for character in needle if character.isdigit())
        async with self.unit_of_work_factory() as unit_of_work:
            customers = await unit_of_work.customers.list_for_business(business.business_id)
        exact = [c for c in customers if needle in {(c.display_name or "").casefold(), c.email}]
        found = exact or [
            c
            for c in customers
            if needle in (c.display_name or "").casefold()
            or needle in (c.email or "")
            or (len(digits) >= 4 and digits in (c.phone or ""))
        ]
        if not found:
            raise OwnerChangeRefusedError(
                f'No customer in your customers matches "{target}". Ask the owner whether to '
                "add them as a new customer first (name, and an e-mail or phone), or who "
                "they meant."
            )
        if len(found) > 1:
            names = ", ".join(f"{c.name} ({_contact(c)})" for c in found[:5])
            raise OwnerChangeRefusedError(f"More than one matches: {names}. Ask which one.")
        return found[0]

    async def open_invoice(
        self,
        business: BusinessRecord,
        target: object,
        eligible: Callable[[Invoice], bool],
        what: str,
    ) -> Invoice:
        needle = " ".join(str(target or "").split()).casefold()
        if len(needle) < 2:
            raise OwnerChangeRefusedError("Ask which invoice: its number or the customer's name.")
        async with self.unit_of_work_factory() as unit_of_work:
            invoices = [
                invoice
                for invoice in await unit_of_work.invoices.list_for_business(business.business_id)
                if invoice.is_open
            ]
        numbered = [invoice for invoice in invoices if invoice.number.casefold() == needle]
        matches = numbered or [
            invoice for invoice in invoices if needle in (invoice.customer_name or "").casefold()
        ]
        found = [invoice for invoice in matches if eligible(invoice)]
        if not found:
            if matches:
                raise OwnerChangeRefusedError(f"Invoice {matches[0].number} has nothing to {what}.")
            raise OwnerChangeRefusedError(f'I can\'t find an open invoice for "{target}".')
        if len(found) > 1:
            names = ", ".join(
                f"{i.number} ({money_text(i.total_minor, i.currency)})" for i in found[:5]
            )
            raise OwnerChangeRefusedError(f"More than one matches: {names}. Ask which one.")
        return found[0]

    async def create(
        self,
        unit_of_work: UnitOfWork,
        business: BusinessRecord,
        customer: CustomerRecord,
        arguments: Mapping[str, JsonValue],
        *,
        replaces: str | None = None,
    ) -> Invoice:
        """The owner's accepted quote and its draft invoice, in the caller's unit of work."""

        prefix = self.require_invoices(business)
        now = self.now()
        business_id = business.business_id
        quote_id = QuoteId(UUID(_str(arguments, "quote_id")))
        key = MessageKey(f"owner-invoice-{quote_id}")
        endpoint_id = await unit_of_work.owner_channel_endpoints.get_or_create(
            ChannelEndpointRef(
                business_id=business_id,
                source_namespace=_ENDPOINT_NAMESPACE,
                external_endpoint_id=_ENDPOINT_ID,
            ),
            {},
        )
        reference = ConversationRef(business_id=business_id, external_conversation_id=key)
        conversation_id = await unit_of_work.conversations.get_or_create(reference, endpoint_id, {})
        items = _line_items(arguments)
        draft = QuoteDraftProposal(
            quote_id=quote_id,
            business_id=business_id,
            recipient=(
                CustomerRecipient(
                    address=customer.email,
                    address_kind=RecipientAddressKind.EMAIL,
                    display_name=customer.display_name,
                    phone=customer.phone,
                )
                if customer.email
                else CustomerRecipient(
                    address=customer.phone or "",
                    address_kind=RecipientAddressKind.PHONE,
                    display_name=customer.display_name,
                )
            ),
            currency=str(arguments.get("currency") or _CURRENCY),
            line_items=items,
        )
        token = new_claim_token()
        quote = Quote(
            quote_id=quote_id,
            business_id=business_id,
            conversation_id=ConversationId(conversation_id),
            conversation_ref=reference,
            status=QuoteStatus.APPROVED,
            revision=1,
            source_message_key=key,
            last_message_key=key,
            pending_request_text=items[0].description,
            draft=draft,
            approval_correlation_id=key,
            claim_token=token,
            claim_token_hash=hash_claim_token(token),
            customer_status=CustomerQuoteStatus.ACCEPTED,
            approved_at=now,
            customer_id=customer.customer_id,
            created_at=now,
            updated_at=now,
        )
        await unit_of_work.quotes.add(quote)
        sequence = await unit_of_work.invoices.next_sequence(business_id)
        invoice = Invoice(
            invoice_id=UUID(_str(arguments, "invoice_id")),
            business_id=business_id,
            quote_id=quote.quote_id,
            sequence=sequence,
            number=invoice_number(prefix, sequence),
            customer_name=customer.display_name,
            customer_email=customer.email,
            customer_phone=customer.phone,
            line_items=tuple(
                InvoiceLine(
                    description=item.description,
                    quantity=item.quantity,
                    unit_price_minor=item.unit_price_minor,
                )
                for item in items
            ),
            total_minor=draft.total_minor,
            currency=draft.currency,
            sent_at=None,
            due_on=due_on(now, business_zone(business)),
            created_at=now,
            replaces=replaces,
        )
        await unit_of_work.invoices.create(invoice)
        return invoice

    async def send_plan(self, business: BusinessRecord, invoice_id: UUID) -> OwnerChangePlan | None:
        async with self.unit_of_work_factory() as unit_of_work:
            invoice = next(
                (
                    i
                    for i in await unit_of_work.invoices.list_for_business(business.business_id)
                    if i.invoice_id == invoice_id
                ),
                None,
            )
            if invoice is None or invoice.sent_at is not None or not invoice.is_open:
                return None
            quote = await unit_of_work.quotes.get(business.business_id, invoice.quote_id)
            customer = (
                None
                if quote is None or quote.customer_id is None
                else await unit_of_work.customers.get(business.business_id, quote.customer_id)
            )
        if quote is None or customer is None:
            return None
        return self._send_plan(business, invoice, quote, customer)

    def _send_plan(
        self, business: BusinessRecord, invoice: Invoice, quote: Quote, customer: CustomerRecord
    ) -> OwnerChangePlan:
        channel, address = _route(customer, self.can_text)
        sender = business.display_name or business.name
        due = due_on(self.now(), business_zone(business))
        total = money_text(invoice.total_minor, invoice.currency)
        items = "; ".join(line.description for line in invoice.line_items[:3])
        if len(invoice.line_items) > 3:
            items += "; and more"
        message = (
            f"Invoice {invoice.number} from {sender}: {items}. Total {total}, due {_day(due)}."
        )
        if business.site_url:
            message += f" View and pay: {quote.quote_url(business.site_url)}"
        if len(message) > MESSAGE_LIMITS[channel]:
            message = f"Invoice {invoice.number} from {sender}. Total {total}, due {_day(due)}." + (
                f" View and pay: {quote.quote_url(business.site_url)}" if business.site_url else ""
            )
        subject = f"Invoice {invoice.number} from {sender}"
        if channel == "text":
            how = f"text {customer.name} at {_phone_text(address)}"
        else:
            how = f'e-mail {customer.name} at {address}, subject "{subject}"'
        return OwnerChangePlan(
            summary=(
                f"I will send invoice {invoice.number} ({total}, due {_day(due)}): I'll {how}, "
                f'this message, word for word:\n"{message}"'
            ),
            arguments={
                "invoice_id": str(invoice.invoice_id),
                "number": invoice.number,
                "customer_id": str(customer.customer_id),
                "name": customer.name,
                "channel": channel,
                "address": address,
                "subject": subject,
                "message": message,
                "due_on": due.isoformat(),
                "key": uuid4().hex,
            },
        )


class CreateInvoiceTool:
    spec = OwnerAssistantTool(
        "change_create_invoice",
        "Propose making a new draft invoice for one of the business's customers (not sent "
        "until the owner says so). Needs the customer and the lines: each line a short "
        "description and an amount, or one amount and a description. Call it with what "
        "the owner gave and null for the rest; it says what is missing, and you ask the "
        "owner for exactly that. Making the draft sends the customer nothing.",
        {
            "customer": {"type": ["string", "null"], "description": "The customer's name."},
            "lines": _LINES,
        },
    )
    sensitive = True

    def __init__(self, invoices: _Invoices) -> None:
        self._invoices = invoices

    async def prepare(self, business: BusinessRecord, args: Mapping[str, Any]) -> OwnerChangePlan:
        self._invoices.require_invoices(business)
        customer = await self._invoices.customer(business, args.get("customer"))
        lines = _lines(args.get("lines"))
        total = money_text(_total(lines), _CURRENCY)
        return OwnerChangePlan(
            summary=(
                f"I will make a draft invoice for {customer.name} ({_contact(customer)}), "
                f"total {total}:\n{_read_back(lines)}\nNothing is sent to them yet."
            ),
            arguments={
                "customer_id": str(customer.customer_id),
                "name": customer.name,
                "lines": cast(list[JsonValue], lines),
                "quote_id": str(uuid4()),
                "invoice_id": str(uuid4()),
            },
        )

    async def apply(self, business: BusinessRecord, arguments: Mapping[str, JsonValue]) -> str:
        customer_id = CustomerId(UUID(_str(arguments, "customer_id")))
        async with self._invoices.unit_of_work_factory() as unit_of_work:
            customer = await unit_of_work.customers.get(business.business_id, customer_id)
            if customer is None:
                raise OwnerChangeRefusedError(
                    f"{arguments['name']} isn't in your customers any more."
                )
            invoice = await self._invoices.create(unit_of_work, business, customer, arguments)
            await unit_of_work.commit()
        total = money_text(invoice.total_minor, invoice.currency)
        return (
            f"Done: draft invoice {invoice.number} for {customer.name}, {total}. "
            "It's in the dashboard as a draft."
        )

    async def follow_up(
        self, business: BusinessRecord, arguments: Mapping[str, JsonValue]
    ) -> tuple[str, OwnerChangePlan] | None:
        plan = await self._invoices.send_plan(business, UUID(_str(arguments, "invoice_id")))
        return None if plan is None else (SEND_DRAFT_TOOL, plan)


class SendDraftInvoiceTool:
    spec = OwnerAssistantTool(
        SEND_DRAFT_TOOL,
        "Propose sending a draft invoice to its customer, by text if they agreed to texts, "
        "otherwise by e-mail. The owner sees the full message and the recipient before YES.",
        {"invoice": _INVOICE},
    )
    sensitive = True

    def __init__(self, invoices: _Invoices) -> None:
        self._invoices = invoices

    async def prepare(self, business: BusinessRecord, args: Mapping[str, Any]) -> OwnerChangePlan:
        self._invoices.require_invoices(business)
        invoice = await self._invoices.open_invoice(
            business, args.get("invoice"), lambda i: i.sent_at is None, "send (it was sent already)"
        )
        plan = await self._invoices.send_plan(business, invoice.invoice_id)
        if plan is None:
            raise OwnerChangeRefusedError(
                f"I can't find who invoice {invoice.number} goes to. Send it from the dashboard."
            )
        return plan

    async def apply(self, business: BusinessRecord, arguments: Mapping[str, JsonValue]) -> str:
        customer_id = CustomerId(UUID(_str(arguments, "customer_id")))
        channel, address = _str(arguments, "channel"), _str(arguments, "address")
        key = f"{OWNER_MESSAGE_KEY_PREFIX}{business.business_id}:{_str(arguments, 'key')}"
        number = arguments["number"]
        async with self._invoices.unit_of_work_factory() as unit_of_work:
            customer = await unit_of_work.customers.get(business.business_id, customer_id)
            if customer is None or _route(customer, self._invoices.can_text) != (channel, address):
                raise OwnerChangeRefusedError(
                    f"{arguments['name']}'s contact details changed since I read them back. "
                    "Ask me again."
                )
            planned_due = date.fromisoformat(_str(arguments, "due_on"))
            sent_at = self._invoices.now()
            if due_on(sent_at, business_zone(business)) != planned_due:
                # The message read back names the due date; a new day means a new read-back.
                raise OwnerChangeRefusedError(
                    f"The due date I read back for invoice {number} is out of date now. "
                    f'Text me "send invoice {number}" again.'
                )
            sent = await unit_of_work.invoices.send_draft(
                business.business_id,
                UUID(_str(arguments, "invoice_id")),
                sent_at=sent_at,
                due_on=planned_due,
                by=TEXTS_RECORDED_BY,
            )
            if sent is None:
                raise OwnerChangeRefusedError(f"Invoice {number} isn't a draft any more.")
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
        return f"Done: invoice {number} is on its way to {arguments['name']} by {channel}."


class ReissueInvoiceTool:
    spec = OwnerAssistantTool(
        "change_reissue_invoice",
        "Propose changing an invoice's amount: the invoice is voided and a new draft "
        "invoice for the new amount replaces it (never edited in place). Not for an "
        "invoice paid by card. `description` is the new line's description if the owner "
        "gave one, else null.",
        {
            "invoice": _INVOICE,
            "amount": {"type": ["string", "null"], "description": "The new total in dollars."},
            "description": {"type": ["string", "null"], "description": "The new line, or null."},
        },
    )
    sensitive = True

    def __init__(self, invoices: _Invoices) -> None:
        self._invoices = invoices

    async def _paid(self, business: BusinessRecord, invoice: Invoice) -> None:
        async with self._invoices.unit_of_work_factory() as unit_of_work:
            payments = await unit_of_work.payments.list_for_quote(
                business.business_id, invoice.quote_id
            )
        counted = [payment for payment in payments if payment.counts]
        if any(payment.source is PaymentSource.STRIPE for payment in counted):
            raise OwnerChangeRefusedError(
                f"Invoice {invoice.number} was paid by card, so it can't be voided or "
                "reissued. A refund is done in Stripe."
            )
        if counted:
            raise OwnerChangeRefusedError(
                f"Invoice {invoice.number} is marked paid ({counted[0].method.value}). "
                f'If that was a mistake, text "mark {invoice.number} unpaid" first.'
            )

    async def prepare(self, business: BusinessRecord, args: Mapping[str, Any]) -> OwnerChangePlan:
        self._invoices.require_invoices(business)
        invoice = await self._invoices.open_invoice(
            business, args.get("invoice"), lambda i: True, "change"
        )
        await self._paid(business, invoice)
        if args.get("amount") in (None, ""):
            raise OwnerChangeRefusedError(
                f"Ask the owner for the new amount of invoice {invoice.number} "
                f"(now {money_text(invoice.total_minor, invoice.currency)})."
            )
        new_minor = _minor(args.get("amount"))
        if new_minor == invoice.total_minor:
            raise OwnerChangeRefusedError(f"Invoice {invoice.number} is already that amount.")
        description = " ".join(str(args.get("description") or "").split())
        if not description:
            description = "; ".join(line.description for line in invoice.line_items)
        if len(description) > _DESCRIPTION_MAX:
            description = description[: _DESCRIPTION_MAX - 1].rstrip() + "…"
        async with self._invoices.unit_of_work_factory() as unit_of_work:
            quote = await unit_of_work.quotes.get(business.business_id, invoice.quote_id)
        if quote is None or quote.customer_id is None:
            raise OwnerChangeRefusedError(
                f"I can't tell which customer invoice {invoice.number} belongs to."
            )
        lines: list[dict[str, JsonValue]] = [
            {"description": description, "amount_minor": new_minor}
        ]
        old = money_text(invoice.total_minor, invoice.currency)
        new = money_text(new_minor, invoice.currency)
        name = invoice.customer_name or invoice.customer_email or "the customer"
        return OwnerChangePlan(
            summary=(
                f"I will void invoice {invoice.number} for {name} (old amount {old}) and "
                f"make a new draft invoice for {new} to replace it:\n"
                f"{_read_back(lines, invoice.currency)}\n"
                "Nothing is sent to them yet."
            ),
            arguments={
                "old_invoice_id": str(invoice.invoice_id),
                "old_number": invoice.number,
                "old_quote_id": str(invoice.quote_id),
                "currency": invoice.currency,
                "customer_id": str(quote.customer_id),
                "name": name,
                "lines": cast(list[JsonValue], lines),
                "quote_id": str(uuid4()),
                "invoice_id": str(uuid4()),
            },
        )

    async def apply(self, business: BusinessRecord, arguments: Mapping[str, JsonValue]) -> str:
        old_number = _str(arguments, "old_number")
        old_invoice_id = UUID(_str(arguments, "old_invoice_id"))
        business_id: BusinessId = business.business_id
        async with self._invoices.unit_of_work_factory() as unit_of_work:
            payments = await unit_of_work.payments.list_for_quote(
                business_id,
                QuoteId(UUID(_str(arguments, "old_quote_id"))),
            )
            if any(payment.counts for payment in payments):
                raise OwnerChangeRefusedError(f"Invoice {old_number} was paid meanwhile.")
            quote = await unit_of_work.quotes.get(
                business_id,
                QuoteId(UUID(_str(arguments, "old_quote_id"))),
            )
            customer = await unit_of_work.customers.get(
                business_id, CustomerId(UUID(_str(arguments, "customer_id")))
            )
            if quote is None or customer is None:
                raise OwnerChangeRefusedError(f"Invoice {old_number} or its customer is gone.")
            checkout = await unit_of_work.quote_payments.find_open(business_id, quote.quote_id)
            if checkout is not None:
                if checkout.status is not QuotePaymentStatus.OPEN:
                    raise OwnerChangeRefusedError(
                        f"A card payment for invoice {old_number} is going through. "
                        "Wait for it before changing the amount."
                    )
                await unit_of_work.quote_payments.save(
                    checkout.mark_expired(self._invoices.now()), expected_from=checkout.status
                )
                if checkout.checkout_session_id:
                    await unit_of_work.outbox.enqueue(
                        checkout_expire_command(business_id, checkout.checkout_session_id)
                    )
            if not await unit_of_work.invoices.void(
                business_id, old_invoice_id, by=TEXTS_RECORDED_BY, at=self._invoices.now()
            ):
                raise OwnerChangeRefusedError(f"Invoice {old_number} was voided already.")
            # The old quote's link must no longer take a payment.
            if quote.is_claimable() and quote.customer_status is not CustomerQuoteStatus.PAID:
                closed = quote.record_customer_decline(self._invoices.now())
                try:
                    await unit_of_work.quotes.save(closed, expected_version=quote.version)
                except QuoteConcurrencyError as error:
                    raise OwnerChangeRefusedError(
                        f"Invoice {old_number} changed meanwhile. Ask me again."
                    ) from error
            invoice = await self._invoices.create(
                unit_of_work, business, customer, arguments, replaces=old_number
            )
            await unit_of_work.commit()
        total = money_text(invoice.total_minor, invoice.currency)
        return (
            f"Done: invoice {old_number} is void, and draft invoice {invoice.number} "
            f"for {customer.name}, {total}, replaces it."
        )

    async def follow_up(
        self, business: BusinessRecord, arguments: Mapping[str, JsonValue]
    ) -> tuple[str, OwnerChangePlan] | None:
        plan = await self._invoices.send_plan(business, UUID(_str(arguments, "invoice_id")))
        return None if plan is None else (SEND_DRAFT_TOOL, plan)


def invoice_change_tools(
    unit_of_work_factory: Callable[[], UnitOfWork],
    *,
    can_text: bool,
    now: Callable[[], datetime],
) -> tuple[OwnerChangeTool, ...]:
    invoices = _Invoices(unit_of_work_factory, now, can_text)
    return (
        CreateInvoiceTool(invoices),
        SendDraftInvoiceTool(invoices),
        ReissueInvoiceTool(invoices),
    )
