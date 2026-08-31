"""Deterministic quote drafting.

GVAS structures the owner's own prices; it never estimates one. The owner
therefore states every price explicitly in a small line-oriented format, and
this parser either produces exactly what was written or refuses with an
owner-facing message.

    quote:
    customer: person@example.com
    currency: USD
    item: 2 | Air sampling | 125.00
    item: 1 | Report | 200.00
    note: on-site visit scheduled for Tuesday

``customer``, ``currency`` and at least one ``item`` are required. Keys and
surrounding whitespace are case- and space-insensitive; amounts are not. Amounts
are read as exact minor units, so no floating point value is ever involved.

The pilot prices in USD only. A currency is not a formatting detail: its
minor-unit exponent decides what ``125`` means, so accepting any three-letter
code while assuming two decimals would misprice JPY by a factor of a hundred.
Anything outside :data:`gvas.domain.money.MINOR_UNIT_DIGITS` is refused.
"""

import re
from typing import Final

from gvas.domain.enums import HostedLinkKind, RecipientAddressKind
from gvas.domain.messages import CustomerRecipient
from gvas.domain.money import (
    UnsupportedCurrencyError,
    minor_unit_digits,
    minor_unit_scale,
    supported_currencies,
)
from gvas.domain.quotes import (
    HostedLinkReference,
    QuoteDraftProposal,
    QuoteDraftRejectedError,
    QuoteDraftRequest,
    QuoteLineItem,
)
from gvas.infrastructure.hosted_links import PORTAL_LOGIN_LINK_REFERENCE

#: Every accepted amount is written in this currency; see ``_parse_currency``.
QUOTED_CURRENCY: Final = "USD"
QUOTED_CURRENCY_DIGITS: Final = minor_unit_digits(QUOTED_CURRENCY)
QUOTED_CURRENCY_SCALE: Final = minor_unit_scale(QUOTED_CURRENCY)
AMOUNT_PATTERN: Final = re.compile(rf"^\d{{1,12}}(\.\d{{1,{QUOTED_CURRENCY_DIGITS}}})?$")
EMAIL_PATTERN: Final = re.compile(r"^[^@\s]+@[^@\s.]+(\.[^@\s.]+)+$")
CURRENCY_PATTERN: Final = re.compile(r"^[A-Za-z]{3}$")

FORMAT_HELP: Final = (
    "Use:\nquote:\ncustomer: person@example.com\ncurrency: USD\nitem: 2 | Air sampling | 125.00"
)


def parse_amount_minor(value: str) -> int:
    """Read an exact amount in minor units; prices are never inferred or rounded."""

    text = value.strip()
    if not AMOUNT_PATTERN.match(text):
        raise QuoteDraftRejectedError(
            f"'{text}' is not a valid amount. Write amounts like 125 or 125.00."
        )
    whole, _, fraction = text.partition(".")
    return int(whole) * QUOTED_CURRENCY_SCALE + int(
        fraction.ljust(QUOTED_CURRENCY_DIGITS, "0") or 0
    )


def _parse_item(value: str) -> QuoteLineItem:
    fields = [field.strip() for field in value.split("|")]
    if len(fields) != 3:
        raise QuoteDraftRejectedError(
            "Each item needs 'item: <quantity> | <description> | <unit price>'."
        )
    quantity_text, description, price_text = fields
    if not quantity_text.isdigit() or int(quantity_text) < 1:
        raise QuoteDraftRejectedError(
            f"'{quantity_text}' is not a valid quantity. Use a whole number of 1 or more."
        )
    if not description:
        raise QuoteDraftRejectedError("Each item needs a description.")
    return QuoteLineItem(
        description=description,
        quantity=int(quantity_text),
        unit_price_minor=parse_amount_minor(price_text),
    )


class DeterministicQuoteDrafter:
    """Formats the owner's explicit prices into a proposal, or refuses."""

    def __init__(self, portal_link_reference: str = PORTAL_LOGIN_LINK_REFERENCE) -> None:
        self._portal_link_reference = portal_link_reference

    async def draft(self, request: QuoteDraftRequest) -> QuoteDraftProposal:
        recipient: str | None = None
        currency: str | None = None
        note: str | None = None
        line_items: list[QuoteLineItem] = []
        for line in request.request_text.splitlines():
            entry = line.strip()
            if not entry:
                continue
            key, separator, value = entry.partition(":")
            if not separator:
                raise QuoteDraftRejectedError(
                    f"'{entry}' is not a recognised quote line. {FORMAT_HELP}"
                )
            field = key.strip().casefold()
            content = value.strip()
            if field in {"customer", "email"}:
                recipient = _parse_email(content)
            elif field == "currency":
                currency = _parse_currency(content)
            elif field == "item":
                line_items.append(_parse_item(content))
            elif field == "note":
                note = content or None
            elif field == "quote":
                continue
            else:
                raise QuoteDraftRejectedError(
                    f"'{key.strip()}' is not a supported quote field. {FORMAT_HELP}"
                )
        missing = [
            name
            for name, present in (
                ("customer", recipient is not None),
                ("currency", currency is not None),
                ("item", bool(line_items)),
            )
            if not present
        ]
        if missing or recipient is None or currency is None:
            raise QuoteDraftRejectedError(
                f"The quote is missing: {', '.join(missing)}. {FORMAT_HELP}"
            )
        return QuoteDraftProposal(
            quote_id=request.quote_id,
            business_id=request.business_id,
            recipient=CustomerRecipient(address=recipient, address_kind=RecipientAddressKind.EMAIL),
            currency=currency,
            line_items=tuple(line_items),
            owner_note=note,
            hosted_links=(
                HostedLinkReference(
                    kind=HostedLinkKind.SIGNUP, reference=self._portal_link_reference
                ),
            ),
        )


def _parse_email(value: str) -> str:
    if not EMAIL_PATTERN.match(value):
        raise QuoteDraftRejectedError(
            f"'{value}' is not a valid customer email address."
            if value
            else "The quote needs a customer email address."
        )
    return value


def _parse_currency(value: str) -> str:
    if not CURRENCY_PATTERN.match(value):
        raise QuoteDraftRejectedError(
            f"'{value}' is not a valid currency. Use a three-letter code such as USD."
        )
    code = value.upper()
    try:
        minor_unit_digits(code)
    except UnsupportedCurrencyError as error:
        raise QuoteDraftRejectedError(
            f"Quotes cannot be priced in {code}. "
            f"Supported currencies: {', '.join(supported_currencies())}."
        ) from error
    return code
