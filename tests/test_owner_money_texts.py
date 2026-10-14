"""Money by text: the dashboard's money buttons as YES-confirmed change tools."""

from uuid import uuid4

import pytest
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from gvas.composition.owner_change_tools import money_change_tools
from gvas.domain.identifiers import BusinessId
from gvas.domain.owner_assistant import OwnerChangeRefusedError, OwnerChangeTool
from gvas.domain.repositories import BusinessRecord
from gvas.infrastructure.models import Business
from gvas.infrastructure.repositories import SqlBusinessRepository
from gvas.infrastructure.unit_of_work import SqlUnitOfWorkFactory
from gvas.interfaces import seed_demo
from test_seed_demo import NOW, _business, _service


async def _larkspur(
    session_factory: async_sessionmaker[AsyncSession],
) -> tuple[BusinessRecord, dict[str, OwnerChangeTool]]:
    business_id = await _business(session_factory)
    async with session_factory() as session:
        await session.execute(
            update(Business).where(Business.id == business_id).values(invoice_prefix="LL")
        )
        await session.commit()
    await seed_demo.seed_demo(session_factory, business_id, reset=False, now=NOW)
    async with session_factory() as session:
        business = await SqlBusinessRepository(session).get(business_id)
    assert business is not None
    tools = money_change_tools(
        SqlUnitOfWorkFactory(session_factory), _service(session_factory), now=lambda: NOW
    )
    return business, {tool.spec.name: tool for tool in tools}


async def _invoice_number(
    session_factory: async_sessionmaker[AsyncSession], business_id: BusinessId, name: str
) -> str:
    async with SqlUnitOfWorkFactory(session_factory)() as unit_of_work:
        invoices = await unit_of_work.invoices.list_for_business(business_id)
    return next(invoice.number for invoice in invoices if invoice.customer_name == name)


@pytest.mark.asyncio
async def test_send_invoice_names_the_customer_and_total_and_runs_once(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    business, tools = await _larkspur(session_factory)
    tool = tools["change_send_invoice"]

    plan = await tool.prepare(business, {"target": "ellen"})

    assert plan.summary.startswith("I will send Ellen Park an invoice for")
    assert "$1,455" in plan.summary
    assert "isn't e-mailed" in plan.summary
    assert await tool.apply(business, plan.arguments) == (
        "Done: invoice LL-1042 for Ellen Park, $1,455, due Wed Oct 28."
    )
    with pytest.raises(OwnerChangeRefusedError, match="nothing to invoice"):
        await tool.prepare(business, {"target": "Ellen Park"})


@pytest.mark.asyncio
async def test_mark_paid_then_unpaid_by_invoice_number(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    business, tools = await _larkspur(session_factory)
    number = await _invoice_number(session_factory, business.business_id, "Luis Romero")

    plan = await tools["change_mark_paid"].prepare(
        business, {"target": number.lower(), "method": "check"}
    )
    assert plan.summary == (
        f"I will mark invoice {number} (Luis Romero, $310) paid by check, on Wed Oct 14."
    )
    assert await tools["change_mark_paid"].apply(business, plan.arguments) == (
        f"Done: invoice {number} (Luis Romero, $310) is marked paid."
    )
    with pytest.raises(OwnerChangeRefusedError, match="nothing to mark paid"):
        await tools["change_mark_paid"].prepare(business, {"target": number, "method": "cash"})

    undo = await tools["change_mark_unpaid"].prepare(business, {"target": "Luis"})
    assert undo.summary == (
        f"I will undo the $310 check payment on invoice {number} (Luis Romero, $310), "
        "paid Wed Oct 14, so it shows unpaid again."
    )
    assert "shows unpaid again" in await tools["change_mark_unpaid"].apply(business, undo.arguments)


@pytest.mark.asyncio
async def test_card_payments_wrong_methods_future_dates_and_unknown_names_are_refused(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    business, tools = await _larkspur(session_factory)

    with pytest.raises(OwnerChangeRefusedError, match="card"):
        await tools["change_mark_unpaid"].prepare(business, {"target": "Marcus Bell"})
    with pytest.raises(OwnerChangeRefusedError, match="check, cash or other"):
        await tools["change_mark_paid"].prepare(business, {"target": "Ellen", "method": "card"})
    with pytest.raises(OwnerChangeRefusedError, match="future"):
        await tools["change_mark_paid"].prepare(
            business, {"target": "Ellen", "method": "cash", "paid_on": "2026-10-15"}
        )
    with pytest.raises(OwnerChangeRefusedError, match="can't find"):
        await tools["change_mark_paid"].prepare(business, {"target": "Zed", "method": "cash"})


@pytest.mark.asyncio
async def test_another_business_cannot_reach_larkspurs_quotes(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    _, tools = await _larkspur(session_factory)
    other_id = await _business(session_factory)
    async with session_factory() as session:
        other = await SqlBusinessRepository(session).get(other_id)
    assert other is not None and other.business_id != BusinessId(uuid4())

    with pytest.raises(OwnerChangeRefusedError, match="can't find"):
        await tools["change_mark_paid"].prepare(other, {"target": "Ellen", "method": "cash"})
    with pytest.raises(OwnerChangeRefusedError, match="off"):
        await tools["change_send_invoice"].prepare(other, {"target": "Ellen"})


@pytest.mark.asyncio
async def test_plan_payment_recorded_then_the_latest_one_undone(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    business, tools = await _larkspur(session_factory)
    record = tools["change_record_plan_payment"]

    plan = await record.prepare(
        business, {"customer": "Ana", "amount": 180, "months": 1, "method": "cash"}
    )
    assert plan.summary == (
        "I will record a $180 cash payment from Ana Delgado covering 1 month of their "
        "$180 every month plan, paid Wed Oct 14."
    )
    assert (await record.apply(business, plan.arguments)).startswith(
        "Done: payment recorded for Ana Delgado, paid through"
    )

    undo = await tools["change_undo_plan_payment"].prepare(business, {"customer": "Ana"})
    assert undo.summary.startswith("I will undo the $180 cash payment from Ana Delgado")
    assert (await tools["change_undo_plan_payment"].apply(business, undo.arguments)).startswith(
        "Done: the payment from Ana Delgado is undone. They're now paid through"
    )
    with pytest.raises(OwnerChangeRefusedError, match="1-24 months"):
        await record.prepare(
            business, {"customer": "Ana", "amount": 180, "months": 30, "method": "cash"}
        )
