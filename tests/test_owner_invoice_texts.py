"""Make, send and reissue invoices by text."""

from datetime import UTC, date, datetime, timedelta
from typing import cast

import pytest
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from gvas.application.owner import OwnerContext, OwnerService
from gvas.composition.owner_change_tools import customer_change_tools, message_change_tools
from gvas.composition.owner_invoice_tools import SEND_DRAFT_TOOL, invoice_change_tools
from gvas.domain.enums import CustomerQuoteStatus
from gvas.domain.invoices import Invoice, InvoiceStatus, due_on, invoice_status
from gvas.domain.owner import OwnerSession
from gvas.domain.owner_assistant import OwnerChangeRefusedError, OwnerChangeTool
from gvas.domain.payments import PaymentSource
from gvas.domain.repositories import BusinessRecord
from gvas.infrastructure.models import Business, OutboxMessage
from gvas.infrastructure.unit_of_work import SqlUnitOfWorkFactory
from gvas.interfaces import seed_demo
from gvas.interfaces.http.owner import owner_customer_payload, owner_invoice_payload
from test_owner_settings_texts import _reload
from test_seed_demo import NOW, ZONE, _business


async def _larkspur(
    session_factory: async_sessionmaker[AsyncSession],
) -> tuple[BusinessRecord, OwnerChangeTool, OwnerChangeTool, OwnerChangeTool]:
    business_id = await _business(session_factory)
    async with session_factory() as session:
        await session.execute(
            update(Business).where(Business.id == business_id).values(invoice_prefix="LL")
        )
        await session.commit()
    await seed_demo.seed_demo(session_factory, business_id, reset=False, now=NOW)
    business = await _reload(session_factory, business_id)
    create, send, reissue = invoice_change_tools(
        SqlUnitOfWorkFactory(session_factory), can_text=False, now=lambda: NOW
    )
    return business, create, send, reissue


async def _invoices(
    session_factory: async_sessionmaker[AsyncSession], business: BusinessRecord
) -> dict[str, Invoice]:
    async with SqlUnitOfWorkFactory(session_factory)() as unit_of_work:
        found = await unit_of_work.invoices.list_for_business(business.business_id)
    return {invoice.number: invoice for invoice in found}


def _number(done: str) -> str:
    return done.split("draft invoice ")[1].split(" ")[0]


@pytest.mark.asyncio
async def test_a_draft_reads_back_customer_lines_and_total_and_sends_nothing(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    business, create, _, _ = await _larkspur(session_factory)

    plan = await create.prepare(
        business,
        {
            "customer": "luis",
            "lines": [
                {"description": "Mow lawn", "amount": "$120"},
                {"description": " Haul  clippings ", "amount": "35.50"},
            ],
        },
    )

    assert plan.summary.startswith("I will make a draft invoice for Luis Romero (")
    assert plan.summary.endswith(
        "total $155.50:\n- Mow lawn: $120\n- Haul clippings: $35.50\nNothing is sent to them yet."
    )
    done = await create.apply(business, plan.arguments)
    assert done.startswith("Done: draft invoice LL-") and done.endswith(
        "for Luis Romero, $155.50. It's in the dashboard as a draft."
    )
    invoice = (await _invoices(session_factory, business))[_number(done)]
    assert invoice.sent_at is None and invoice.total_minor == 15_550
    assert invoice_status(invoice, None, today=NOW.date()) is InvoiceStatus.DRAFT
    async with session_factory() as session:
        assert (await session.scalars(select(OutboxMessage))).all() == [] or all(
            "owner-message" not in str(row.payload)
            for row in (await session.scalars(select(OutboxMessage))).all()
        )
    async with SqlUnitOfWorkFactory(session_factory)() as unit_of_work:
        quote = await unit_of_work.quotes.get(business.business_id, invoice.quote_id)
    assert quote is not None and quote.customer_status is CustomerQuoteStatus.ACCEPTED


@pytest.mark.asyncio
async def test_asks_for_what_is_missing_instead_of_refusing_the_invoice(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    business, create, _, _ = await _larkspur(session_factory)

    with pytest.raises(OwnerChangeRefusedError, match="add them as a new customer"):
        await create.prepare(business, {"customer": "Jim", "lines": None})
    with pytest.raises(OwnerChangeRefusedError, match="what the invoice is for"):
        await create.prepare(business, {"customer": "luis", "lines": None})
    with pytest.raises(OwnerChangeRefusedError, match='how much "Mow"'):
        await create.prepare(
            business, {"customer": "luis", "lines": [{"description": "Mow", "amount": ""}]}
        )
    with pytest.raises(OwnerChangeRefusedError, match="isn't an amount"):
        await create.prepare(
            business, {"customer": "luis", "lines": [{"description": "Mow", "amount": "-5"}]}
        )
    with pytest.raises(OwnerChangeRefusedError, match="too large"):
        await create.prepare(
            business, {"customer": "luis", "lines": [{"description": "Mow", "amount": "1e30"}]}
        )


@pytest.mark.asyncio
async def test_the_offer_to_send_reads_back_the_exact_message_and_sends_once(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    business, create, send, _ = await _larkspur(session_factory)
    plan = await create.prepare(
        business, {"customer": "luis", "lines": [{"description": "Mow lawn", "amount": "120"}]}
    )
    number = _number(await create.apply(business, plan.arguments))

    offered = await create.follow_up(business, plan.arguments)  # type: ignore[attr-defined]

    assert offered is not None
    tool, offer = offered
    assert tool == SEND_DRAFT_TOOL
    message = str(offer.arguments["message"])
    assert message.startswith(f"Invoice {number} from ")
    assert ": Mow lawn. Total $120, due Wed Oct 28." in message
    assert offer.summary.startswith(f"I will send invoice {number} ($120, due Wed Oct 28): ")
    assert f'word for word:\n"{message}"' in offer.summary
    assert "e-mail Luis Romero at " in offer.summary
    assert (await send.prepare(business, {"invoice": number})).summary.split(":\n")[1] == (
        offer.summary.split(":\n")[1]
    )

    assert await send.apply(business, offer.arguments) == (
        f"Done: invoice {number} is on its way to Luis Romero by e-mail."
    )
    invoice = (await _invoices(session_factory, business))[number]
    assert invoice.sent_at == NOW
    async with session_factory() as session:
        bodies = [str(row.payload) for row in (await session.scalars(select(OutboxMessage))).all()]
    assert sum(message in body for body in bodies) == 1
    with pytest.raises(OwnerChangeRefusedError, match="isn't a draft any more"):
        await send.apply(business, offer.arguments)


@pytest.mark.asyncio
async def test_reissue_voids_the_old_invoice_and_drafts_the_new_amount(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    business, create, _, reissue = await _larkspur(session_factory)
    plan = await create.prepare(
        business, {"customer": "luis", "lines": [{"description": "Mow lawn", "amount": "120"}]}
    )
    old = _number(await create.apply(business, plan.arguments))

    with pytest.raises(OwnerChangeRefusedError, match="new amount"):
        await reissue.prepare(business, {"invoice": old, "amount": None, "description": None})
    with pytest.raises(OwnerChangeRefusedError, match="already that amount"):
        await reissue.prepare(business, {"invoice": old, "amount": "120", "description": None})
    change = await reissue.prepare(business, {"invoice": old, "amount": "150", "description": None})

    assert change.summary == (
        f"I will void invoice {old} for Luis Romero (old amount $120) and make a new draft "
        "invoice for $150 to replace it:\n- Mow lawn: $150\nNothing is sent to them yet."
    )
    done = await reissue.apply(business, change.arguments)
    new = done.split("draft invoice ")[1].split(" ")[0]
    assert (
        done == f"Done: invoice {old} is void, and draft invoice {new} for Luis Romero, $150, "
        "replaces it."
    )
    invoices = await _invoices(session_factory, business)
    assert invoice_status(invoices[old], None, today=NOW.date()) is InvoiceStatus.VOID
    assert (invoices[new].total_minor, invoices[new].replaces) == (15_000, old)
    async with SqlUnitOfWorkFactory(session_factory)() as unit_of_work:
        quote = await unit_of_work.quotes.get(business.business_id, invoices[old].quote_id)
    assert quote is not None and quote.customer_status is CustomerQuoteStatus.DECLINED
    with pytest.raises(OwnerChangeRefusedError, match="voided already"):
        await reissue.apply(business, change.arguments)
    with pytest.raises(OwnerChangeRefusedError, match="can't find an open invoice"):
        await reissue.prepare(business, {"invoice": old, "amount": "99", "description": None})
    assert await reissue.follow_up(business, change.arguments) is not None  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_an_invoice_paid_by_card_is_never_reissued(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    business, _, _, reissue = await _larkspur(session_factory)
    async with SqlUnitOfWorkFactory(session_factory)() as unit_of_work:
        card = None
        for invoice in await unit_of_work.invoices.list_for_business(business.business_id):
            payments = await unit_of_work.payments.list_for_quote(
                business.business_id, invoice.quote_id
            )
            if any(p.counts and p.source is PaymentSource.STRIPE for p in payments):
                card = invoice
    assert card is not None

    with pytest.raises(OwnerChangeRefusedError, match="paid by card"):
        await reissue.prepare(
            business, {"invoice": card.number, "amount": "1", "description": None}
        )


@pytest.mark.asyncio
async def test_a_phone_only_customer_without_text_consent_is_never_sent_to(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    business, create, send, _ = await _larkspur(session_factory)
    _, add = customer_change_tools(SqlUnitOfWorkFactory(session_factory), now=lambda: NOW)
    added = await add.prepare(business, {"name": "Ruth Park", "email": None, "phone": "9255550188"})
    await add.apply(business, added.arguments)

    plan = await create.prepare(
        business, {"customer": "Ruth Park", "lines": [{"description": "Weeding", "amount": "80"}]}
    )
    assert "draft invoice for Ruth Park ((925) 555-0188)" in plan.summary
    number = _number(await create.apply(business, plan.arguments))

    plain = (
        "no e-mail on file, and they haven't agreed to get texts, so I can't text (925) 555-0188"
    )
    with pytest.raises(OwnerChangeRefusedError, match="Want to add their e-mail") as refused:
        await create.follow_up(business, plan.arguments)  # type: ignore[attr-defined]
    assert plain in str(refused.value)
    with pytest.raises(OwnerChangeRefusedError, match="Want to add their e-mail"):
        await send.prepare(business, {"invoice": number})
    (message,) = message_change_tools(SqlUnitOfWorkFactory(session_factory), can_text=True)
    with pytest.raises(OwnerChangeRefusedError, match="haven't agreed to get texts"):
        await message.prepare(business, {"customer": "Ruth Park", "message": "Hi"})
    async with session_factory() as session:
        bodies = [str(row.payload) for row in (await session.scalars(select(OutboxMessage))).all()]
    assert not any("9255550188" in body or number in body for body in bodies)


@pytest.mark.asyncio
async def test_a_send_read_back_before_midnight_is_asked_again_after_it(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    business, _, _, _ = await _larkspur(session_factory)
    clock = [datetime(2026, 10, 15, 6, 59, tzinfo=UTC)]  # 11:59 pm Wed Oct 14 in Oakland
    create, send, _ = invoice_change_tools(
        SqlUnitOfWorkFactory(session_factory), can_text=False, now=lambda: clock[0]
    )
    plan = await create.prepare(
        business, {"customer": "luis", "lines": [{"description": "Mow lawn", "amount": "120"}]}
    )
    number = _number(await create.apply(business, plan.arguments))
    offered = await create.follow_up(business, plan.arguments)  # type: ignore[attr-defined]
    assert offered is not None
    _, before = offered
    assert "due Wed Oct 28" in before.summary

    clock[0] = datetime(2026, 10, 15, 7, 1, tzinfo=UTC)  # 12:01 am Thu Oct 15: YES comes late
    with pytest.raises(OwnerChangeRefusedError, match="out of date now") as refused:
        await send.apply(business, before.arguments)
    assert f'Text me "send invoice {number}" again.' in str(refused.value)
    assert (await _invoices(session_factory, business))[number].sent_at is None
    async with session_factory() as session:
        bodies = [str(row.payload) for row in (await session.scalars(select(OutboxMessage))).all()]
    assert not any(number in body for body in bodies)

    after = await send.prepare(business, {"invoice": number})
    assert "due Thu Oct 29" in after.summary and "Oct 28" not in after.summary
    await send.apply(business, after.arguments)
    sent = (await _invoices(session_factory, business))[number]
    assert (sent.sent_at, sent.due_on) == (clock[0], due_on(clock[0], ZONE))
    assert sent.due_on == date(2026, 10, 29)
    async with session_factory() as session:
        bodies = [str(row.payload) for row in (await session.scalars(select(OutboxMessage))).all()]
    assert sum(str(after.arguments["message"]) in body for body in bodies) == 1
    assert "due Thu Oct 29" in str(after.arguments["message"])


@pytest.mark.asyncio
async def test_a_phone_only_customers_quotes_and_invoices_are_on_their_record(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    business, create, _, reissue = await _larkspur(session_factory)
    _, add = customer_change_tools(SqlUnitOfWorkFactory(session_factory), now=lambda: NOW)
    added = await add.prepare(business, {"name": "Ruth Park", "email": None, "phone": "9255550188"})
    await add.apply(business, added.arguments)
    plan = await create.prepare(
        business, {"customer": "Ruth Park", "lines": [{"description": "Weeding", "amount": "80"}]}
    )
    old = _number(await create.apply(business, plan.arguments))
    change = await reissue.prepare(business, {"invoice": old, "amount": "95", "description": None})
    new = (await reissue.apply(business, change.arguments)).split("draft invoice ")[1].split()[0]

    owner = OwnerContext(
        session=OwnerSession(
            token_hash="0" * 64,
            business_id=business.business_id,
            email="owner@example.test",
            expires_at=NOW + timedelta(days=1),
            created_at=NOW,
        ),
        business=business,
    )
    summaries = await OwnerService(SqlUnitOfWorkFactory(session_factory)).customers(owner)
    (row,) = [
        owner_customer_payload(summary)
        for summary in summaries
        if summary.customer.display_name == "Ruth Park"
    ]
    assert (row["email"], row["phone"]) == (None, "+19255550188") and not row["smsConsent"]

    # The dashboard's customer page keeps a quote when its id is in quoteIds,
    # and an invoice when its quoteId is one of those quotes.
    invoices = await _invoices(session_factory, business)
    payloads = {
        number: owner_invoice_payload(invoices[number], None, today=NOW.date())
        for number in (old, new)
    }
    linked = set(cast(list[str], row["quoteIds"]))
    assert {str(payload["quoteId"]) for payload in payloads.values()} <= linked
    assert row["quoteCount"] == 2
    assert (payloads[old]["status"], payloads[new]["status"]) == ("void", "draft")
