"""Change Settings by text: business name, time zone and Gus's wording."""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from gvas.composition.owner_change_tools import settings_change_tools
from gvas.domain.owner_assistant import OwnerChangeRefusedError, OwnerChangeTool
from gvas.domain.repositories import BusinessRecord
from gvas.infrastructure.repositories import SqlBusinessRepository
from gvas.infrastructure.unit_of_work import SqlUnitOfWorkFactory
from gvas.interfaces import seed_demo
from test_seed_demo import NOW, _business, _service


async def _larkspur(
    session_factory: async_sessionmaker[AsyncSession],
) -> tuple[BusinessRecord, OwnerChangeTool]:
    business_id = await _business(session_factory)
    await seed_demo.seed_demo(session_factory, business_id, reset=False, now=NOW)
    business = await _reload(session_factory, business_id)
    (tool,) = settings_change_tools(
        SqlUnitOfWorkFactory(session_factory), _service(session_factory), now=lambda: NOW
    )
    return business, tool


async def _reload(
    session_factory: async_sessionmaker[AsyncSession], business_id: object
) -> BusinessRecord:
    async with session_factory() as session:
        business = await SqlBusinessRepository(session).get(business_id)  # type: ignore[arg-type]
    assert business is not None
    return business


@pytest.mark.asyncio
async def test_rename_and_move_zone_in_one_confirmed_change(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    business, tool = await _larkspur(session_factory)

    plan = await tool.prepare(
        business, {"business_name": "  Larkspur   Gardens ", "time_zone": "America/Denver"}
    )

    assert plan.summary == (
        'I will change your business name to: "Larkspur Gardens"; and set your time zone '
        "to Mountain time (America/Denver)."
    )
    assert (await tool.apply(business, plan.arguments)).startswith("Done: ")
    saved = await _reload(session_factory, business.business_id)
    assert saved.display_name == "Larkspur Gardens"
    assert saved.timezone == "America/Denver"


@pytest.mark.asyncio
async def test_gus_wording_shows_the_full_new_text_and_can_go_back_to_default(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    business, tool = await _larkspur(session_factory)

    plan = await tool.prepare(business, {"greeting": "Hi! Tell me about your yard."})
    assert plan.summary == 'I will change Gus\'s greeting to: "Hi! Tell me about your yard."'
    await tool.apply(business, plan.arguments)
    business = await _reload(session_factory, business.business_id)
    assert business.intake_profile.opening == "Hi! Tell me about your yard."

    clear = await tool.prepare(business, {"greeting": ""})
    assert clear.summary == "I will remove Gus's greeting, so Gus uses his default."
    await tool.apply(business, clear.arguments)
    business = await _reload(session_factory, business.business_id)
    assert business.intake_profile.opening is None


@pytest.mark.asyncio
async def test_refuses_bad_empty_unchanged_and_dashboard_only_settings(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    business, tool = await _larkspur(session_factory)

    with pytest.raises(OwnerChangeRefusedError, match="isn't a known time zone"):
        await tool.prepare(business, {"time_zone": "Pacific"})
    with pytest.raises(OwnerChangeRefusedError, match="can't be left empty"):
        await tool.prepare(business, {"business_name": " "})
    renamed = await tool.prepare(business, {"business_name": "Larkspur"})
    await tool.apply(business, renamed.arguments)
    business = await _reload(session_factory, business.business_id)
    with pytest.raises(OwnerChangeRefusedError, match="already what Settings say"):
        await tool.prepare(business, {"business_name": " Larkspur ", "greeting": None})
    only_zone = await tool.prepare(
        business, {"business_name": "Larkspur", "time_zone": "America/Chicago"}
    )
    assert only_zone.arguments == {"fields": {"timezone": "America/Chicago"}}
    with pytest.raises(OwnerChangeRefusedError, match="which setting"):
        await tool.prepare(business, {"notification_email": "x@example.com"})
    assert "notification_email" not in tool.spec.parameters
