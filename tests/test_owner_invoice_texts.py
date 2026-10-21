"""Make, send and reissue invoices by text."""

import pytest
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from gvas.composition.owner_invoice_tools import SEND_DRAFT_TOOL, invoice_change_tools
from gvas.domain.enums import CustomerQuoteStatus
from gvas.domain.invoices import Invoice, InvoiceStatus, invoice_status
from gvas.domain.owner_assistant import OwnerChangeRefusedError, OwnerChangeTool
from gvas.domain.payments import PaymentSource
from gvas.domain.repositories import BusinessRecord
from gvas.infrastructure.models import Business, OutboxMessage
from gvas.infrastructure.unit_of_work import SqlUnitOfWorkFactory
from gvas.interfaces import seed_demo
from test_owner_settings_texts import _reload
from test_seed_demo import NOW, _business


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
