"""Edit a customer's name or phone by text."""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from gvas.composition.owner_change_tools import customer_change_tools
from gvas.domain.customers import CustomerRecord
from gvas.domain.owner_assistant import OwnerChangeRefusedError, OwnerChangeTool
from gvas.domain.repositories import BusinessRecord
from gvas.infrastructure.unit_of_work import SqlUnitOfWorkFactory
from gvas.interfaces import seed_demo
from test_owner_settings_texts import _reload
from test_seed_demo import NOW, _business


async def _larkspur(
    session_factory: async_sessionmaker[AsyncSession],
) -> tuple[BusinessRecord, OwnerChangeTool]:
    business_id = await _business(session_factory)
    await seed_demo.seed_demo(session_factory, business_id, reset=False, now=NOW)
    business = await _reload(session_factory, business_id)
    (tool,) = customer_change_tools(SqlUnitOfWorkFactory(session_factory), now=lambda: NOW)
    return business, tool


async def _customer(
    session_factory: async_sessionmaker[AsyncSession], business: BusinessRecord, name: str
) -> CustomerRecord:
    async with SqlUnitOfWorkFactory(session_factory)() as unit_of_work:
        customers = await unit_of_work.customers.list_for_business(business.business_id)
    (customer,) = [c for c in customers if c.display_name == name]
    return customer


@pytest.mark.asyncio
async def test_new_name_and_phone_after_yes_and_the_new_number_has_no_yes_to_texts(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    business, tool = await _larkspur(session_factory)
    luis = await _customer(session_factory, business, "Luis Romero")
    async with SqlUnitOfWorkFactory(session_factory)() as unit_of_work:
        await unit_of_work.customers.set_sms_consent(
            business.business_id, luis.customer_id, True, NOW
        )
        await unit_of_work.commit()

    plan = await tool.prepare(
        business, {"customer": "luis", "name": " Luis  M. Romero ", "phone": "510-555-0199"}
    )

    assert plan.summary == (
        'I will change Luis Romero\'s name to "Luis M. Romero" and phone to (510) 555-0199. '
        "They agreed to texts at the old number, so Gus won't text the new one until they "
        "agree again."
    )
    assert (await tool.apply(business, plan.arguments)).startswith("Done: Luis M. Romero's")
    saved = await _customer(session_factory, business, "Luis M. Romero")
    assert saved.phone == "+15105550199"
    assert saved.sms_consent is None
    assert saved.email == luis.email


@pytest.mark.asyncio
async def test_finds_the_customer_by_phone_and_changes_only_what_was_asked(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    business, tool = await _larkspur(session_factory)

    plan = await tool.prepare(business, {"customer": "555-0161", "name": "Luis Romero Jr"})

    assert plan.summary == 'I will change Luis Romero\'s name to "Luis Romero Jr".'
    assert plan.arguments["changes"] == {"display_name": "Luis Romero Jr"}
    await tool.apply(business, plan.arguments)
    assert (await _customer(session_factory, business, "Luis Romero Jr")).phone == "+15105550161"


@pytest.mark.asyncio
async def test_a_yes_after_the_details_changed_changes_nothing(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    business, tool = await _larkspur(session_factory)
    luis = await _customer(session_factory, business, "Luis Romero")
    plan = await tool.prepare(business, {"customer": "Luis Romero", "phone": "5105550199"})
    async with SqlUnitOfWorkFactory(session_factory)() as unit_of_work:
        await unit_of_work.customers.update_details(
            business.business_id,
            luis.customer_id,
            display_name="Luis Romero",
            phone="+15105550100",
            now=NOW,
        )
        await unit_of_work.commit()

    with pytest.raises(OwnerChangeRefusedError, match="changed since I asked"):
        await tool.apply(business, plan.arguments)
    assert (await _customer(session_factory, business, "Luis Romero")).phone == "+15105550100"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("args", "refusal"),
    [
        ({"customer": "Luis"}, "what to change"),
        ({"customer": "Luis", "name": "", "phone": None}, "what to change"),
        ({"customer": "Nobody Here", "name": "X Y"}, "No customer"),
        ({"customer": "example.com", "name": "X Y"}, "More than one"),
        ({"customer": "Luis", "phone": "12"}, "doesn't look like a phone"),
        ({"customer": "Luis", "phone": "+1 (510) 555-0161"}, "already what you have"),
        ({"customer": "Luis", "name": "x" * 256}, "too long"),
    ],
)
async def test_refuses(
    session_factory: async_sessionmaker[AsyncSession], args: dict[str, object], refusal: str
) -> None:
    business, tool = await _larkspur(session_factory)

    with pytest.raises(OwnerChangeRefusedError, match=refusal):
        await tool.prepare(business, args)
