"""Approve or decline by text, in plain words: the dashboard's decision buttons."""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from gvas.composition.owner_change_tools import decision_change_tools
from gvas.domain.enums import QuoteStatus
from gvas.domain.intake import IntakeState
from gvas.domain.owner_assistant import OwnerChangeRefusedError, OwnerChangeTool
from gvas.domain.repositories import BusinessRecord
from gvas.infrastructure.repositories import SqlBusinessRepository
from gvas.infrastructure.unit_of_work import SqlUnitOfWorkFactory
from gvas.interfaces import seed_demo
from test_seed_demo import NOW, _business, _service


async def _larkspur(
    session_factory: async_sessionmaker[AsyncSession],
) -> tuple[BusinessRecord, dict[str, OwnerChangeTool]]:
    business_id = await _business(session_factory)
    await seed_demo.seed_demo(session_factory, business_id, reset=False, now=NOW)
    async with session_factory() as session:
        business = await SqlBusinessRepository(session).get(business_id)
    assert business is not None
    tools = decision_change_tools(
        SqlUnitOfWorkFactory(session_factory), _service(session_factory), now=lambda: NOW
    )
    return business, {tool.spec.name: tool for tool in tools}


async def _olivia_state(
    session_factory: async_sessionmaker[AsyncSession], business: BusinessRecord, reference: str
) -> IntakeState:
    async with SqlUnitOfWorkFactory(session_factory)() as unit_of_work:
        conversation = await unit_of_work.intake_conversations.lock_by_reference(
            business.business_id, reference
        )
    assert conversation is not None
    return conversation.state


@pytest.mark.asyncio
async def test_approve_a_visit_request_by_the_customers_name(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    business, tools = await _larkspur(session_factory)
    approve = tools["change_approve_booking"]

    plan = await approve.prepare(business, {"customer": "olivia"})

    assert plan.summary.startswith("I will approve Olivia Grant's visit request for ")
    assert plan.summary.endswith("They'll be told it's booked.")
    assert (await approve.apply(business, plan.arguments)).startswith("Done: ")
    reference = str(plan.arguments["reference"])
    assert await _olivia_state(session_factory, business, reference) is IntakeState.APPROVED
    with pytest.raises(OwnerChangeRefusedError, match="is waiting for your OK"):
        await approve.prepare(business, {"customer": "Olivia"})
    with pytest.raises(OwnerChangeRefusedError, match="already approved"):
        await approve.apply(business, plan.arguments)


@pytest.mark.asyncio
async def test_decline_shows_the_note_the_customer_will_see(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    business, tools = await _larkspur(session_factory)
    decline = tools["change_decline_booking"]

    plan = await decline.prepare(
        business, {"customer": "Olivia Grant", "reason": "  Fully booked\nthat week. "}
    )

    assert plan.summary.endswith(
        "They'll be told it's declined, with your note: \"Fully booked that week.\""
    )
    await decline.apply(business, plan.arguments)
    reference = str(plan.arguments["reference"])
    assert await _olivia_state(session_factory, business, reference) is IntakeState.DECLINED


@pytest.mark.asyncio
async def test_approve_and_reject_quotes_waiting_for_the_owner(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    business, tools = await _larkspur(session_factory)

    approve = await tools["change_approve_quote"].prepare(business, {"customer": "Hannah"})
    assert approve.summary == (
        "I will approve the $1,240 quote for Hannah Brooks (Backyard cleanup and "
        "haul-away, Mulch, 6 cu yd, spread) and send it to them."
    )
    reject = await tools["change_reject_quote"].prepare(business, {"customer": "Hannah"})
    assert reject.summary.endswith("It won't be sent.")

    assert await tools["change_approve_quote"].apply(business, approve.arguments) == (
        "Done: the quote for Hannah Brooks is approved and on its way to them."
    )
    async with SqlUnitOfWorkFactory(session_factory)() as unit_of_work:
        waiting = await unit_of_work.quotes.list_for_business(
            business.business_id, limit=50, status=QuoteStatus.AWAITING_APPROVAL
        )
    assert waiting == ()
    with pytest.raises(OwnerChangeRefusedError, match="waiting for your OK"):
        await tools["change_reject_quote"].apply(business, reject.arguments)


@pytest.mark.asyncio
async def test_only_requests_waiting_in_the_texting_business_match(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    _, tools = await _larkspur(session_factory)
    other_id = await _business(session_factory)
    async with session_factory() as session:
        other = await SqlBusinessRepository(session).get(other_id)
    assert other is not None

    for name in tools:
        with pytest.raises(OwnerChangeRefusedError, match="is waiting for your OK"):
            await tools[name].prepare(other, {"customer": "Olivia"})
    with pytest.raises(OwnerChangeRefusedError, match="Say which one"):
        await tools["change_approve_booking"].prepare(other, {"customer": " "})
