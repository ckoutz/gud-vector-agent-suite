"""Add a new customer to the business by text."""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from gvas.composition.owner_change_tools import customer_change_tools
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
    _, tool = customer_change_tools(SqlUnitOfWorkFactory(session_factory), now=lambda: NOW)
    return business, tool


@pytest.mark.asyncio
async def test_reads_back_the_full_record_and_saves_on_yes(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    business, tool = await _larkspur(session_factory)

    plan = await tool.prepare(
        business, {"name": " Jim  Park ", "email": " Jim@Example.com ", "phone": "510 555 0142"}
    )

    assert plan.summary == (
        "I will add this new customer to your customers:\n"
        "Name: Jim Park\nE-mail: jim@example.com\nPhone: (510) 555-0142\n"
        "Nothing is sent to them."
    )
    assert await tool.apply(business, plan.arguments) == (
        "Done: Jim Park is in your customers, and the dashboard shows them."
    )
    async with SqlUnitOfWorkFactory(session_factory)() as unit_of_work:
        saved = await unit_of_work.customers.find_by_email(business.business_id, "jim@example.com")
    assert saved is not None
    assert (saved.display_name, saved.phone, saved.sms_consent) == (
        "Jim Park",
        "+15105550142",
        None,
    )


@pytest.mark.asyncio
async def test_asks_for_the_email_and_phone_when_only_a_name_is_given(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    business, tool = await _larkspur(session_factory)

    with pytest.raises(OwnerChangeRefusedError, match="Jim's e-mail or phone number"):
        await tool.prepare(business, {"name": "Jim", "email": None, "phone": None})
    with pytest.raises(OwnerChangeRefusedError, match="name"):
        await tool.prepare(business, {"name": None, "email": "jim@example.com", "phone": None})


@pytest.mark.asyncio
async def test_phone_is_optional_but_must_be_a_number(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    business, tool = await _larkspur(session_factory)

    plan = await tool.prepare(business, {"name": "Jim", "email": "jim@example.com", "phone": None})
    assert "Phone: no phone" in plan.summary
    with pytest.raises(OwnerChangeRefusedError, match="phone number"):
        await tool.prepare(business, {"name": "Jim", "email": "jim@example.com", "phone": "ext 5"})
    with pytest.raises(OwnerChangeRefusedError, match="e-mail address"):
        await tool.prepare(business, {"name": "Jim", "email": "jim at home", "phone": None})


@pytest.mark.asyncio
async def test_an_email_already_in_the_customers_is_refused_before_and_after_yes(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    business, tool = await _larkspur(session_factory)
    async with SqlUnitOfWorkFactory(session_factory)() as unit_of_work:
        existing = (await unit_of_work.customers.list_for_business(business.business_id))[0]

    with pytest.raises(OwnerChangeRefusedError, match="already has the e-mail"):
        await tool.prepare(business, {"name": "Jim", "email": existing.email, "phone": None})

    plan = await tool.prepare(business, {"name": "Jim", "email": "jim@example.com", "phone": None})
    await tool.apply(business, plan.arguments)
    with pytest.raises(OwnerChangeRefusedError, match="added meanwhile"):
        await tool.apply(business, plan.arguments)


@pytest.mark.asyncio
async def test_a_namesake_is_named_in_the_read_back(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    business, tool = await _larkspur(session_factory)

    plan = await tool.prepare(
        business, {"name": "luis romero", "email": "luis2@example.com", "phone": None}
    )

    assert "You already have a luis romero (" in plan.summary
    assert "this adds a separate one." in plan.summary


@pytest.mark.asyncio
async def test_a_phone_is_enough_and_an_email_can_be_added_later_by_text(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    business, tool = await _larkspur(session_factory)
    change, _ = customer_change_tools(SqlUnitOfWorkFactory(session_factory), now=lambda: NOW)

    plan = await tool.prepare(business, {"name": "Jim Park", "email": None, "phone": "5105550142"})

    assert plan.summary.startswith(
        "I will add this new customer to your customers:\n"
        "Name: Jim Park\nE-mail: none\nPhone: (510) 555-0142\nNothing is sent to them."
    )
    assert "can't sign in to the customer portal" in plan.summary
    assert "text me their e-mail instead of YES" in plan.summary
    await tool.apply(business, plan.arguments)
    again = await tool.prepare(business, {"name": "Jim Park", "email": None, "phone": "5105550142"})
    assert "You already have a Jim Park ((510) 555-0142)" in again.summary

    email_plan = await change.prepare(
        business, {"customer": "(510) 555-0142", "name": None, "phone": None, "email": "Jim@x.com"}
    )
    assert "e-mail to jim@x.com" in email_plan.summary
    await change.apply(business, email_plan.arguments)
    async with SqlUnitOfWorkFactory(session_factory)() as unit_of_work:
        saved = await unit_of_work.customers.find_by_email(business.business_id, "jim@x.com")
    assert saved is not None and saved.phone == "+15105550142"
    with pytest.raises(OwnerChangeRefusedError, match="already has the e-mail jim@x.com"):
        await change.prepare(
            business, {"customer": "jim@x.com", "name": None, "phone": None, "email": "j@y.com"}
        )
