"""Message a customer by text: the full message and recipient before YES."""

from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from gvas.composition.failure_notices import (
    owner_message_failure_notice,
    owner_message_recipient,
)
from gvas.composition.owner_change_tools import message_change_tools
from gvas.domain.identifiers import CustomerId
from gvas.domain.intake import (
    INTAKE_CUSTOMER_EMAIL_COMMAND_TYPE,
    INTAKE_CUSTOMER_TEXT_COMMAND_TYPE,
    intake_customer_text_command,
)
from gvas.domain.owner_assistant import OwnerChangeRefusedError, OwnerChangeTool
from gvas.domain.repositories import BusinessRecord
from gvas.infrastructure.customer_repositories import SqlCustomerRepository
from gvas.infrastructure.models import OutboxMessage
from gvas.infrastructure.repositories import SqlBusinessRepository
from gvas.infrastructure.unit_of_work import SqlUnitOfWorkFactory
from gvas.interfaces import seed_demo
from test_seed_demo import NOW, _business


async def _larkspur(
    session_factory: async_sessionmaker[AsyncSession], *, can_text: bool = True
) -> tuple[BusinessRecord, OwnerChangeTool]:
    business_id = await _business(session_factory)
    await seed_demo.seed_demo(session_factory, business_id, reset=False, now=NOW)
    async with session_factory() as session:
        business = await SqlBusinessRepository(session).get(business_id)
    assert business is not None
    (tool,) = message_change_tools(SqlUnitOfWorkFactory(session_factory), can_text=can_text)
    return business, tool


async def _consent(
    session_factory: async_sessionmaker[AsyncSession], business: BusinessRecord, consent: bool
) -> None:
    async with session_factory() as session:
        customers = SqlCustomerRepository(session)
        luis = await customers.find_by_email(business.business_id, "luis.romero@example.com")
        assert luis is not None
        await customers.set_sms_consent(business.business_id, luis.customer_id, consent, NOW)
        await session.commit()


async def _queued(session_factory: async_sessionmaker[AsyncSession]) -> list[tuple[str, Any]]:
    async with session_factory() as session:
        rows = await session.scalars(
            select(OutboxMessage).where(
                OutboxMessage.command_type.in_(
                    (INTAKE_CUSTOMER_EMAIL_COMMAND_TYPE, INTAKE_CUSTOMER_TEXT_COMMAND_TYPE)
                )
            )
        )
        return [(row.command_type, row.payload) for row in rows]


@pytest.mark.asyncio
async def test_emails_the_exact_message_after_showing_it_and_the_address(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    business, tool = await _larkspur(session_factory)
    before = await _queued(session_factory)
    sender = business.display_name or business.name

    plan = await tool.prepare(business, {"customer": "luis", "message": " Running 15 min late. "})

    assert plan.summary == (
        "I will e-mail Luis Romero at luis.romero@example.com, subject "
        f'"Message from {sender}", this message, word for word:\n"Running 15 min late."'
    )
    assert await _queued(session_factory) == before  # nothing goes out before YES
    assert await tool.apply(business, plan.arguments) == (
        "Done: your message to Luis Romero is on its way by e-mail."
    )
    (sent,) = [item for item in await _queued(session_factory) if item not in before]
    assert sent[0] == INTAKE_CUSTOMER_EMAIL_COMMAND_TYPE
    assert sent[1]["to"] == "luis.romero@example.com"
    assert sent[1]["subject"] == f"Message from {sender}"
    assert sent[1]["body"] == "Running 15 min late."


@pytest.mark.asyncio
async def test_texts_only_a_customer_who_said_yes_to_texts(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    business, tool = await _larkspur(session_factory)
    await _consent(session_factory, business, True)
    before = await _queued(session_factory)

    plan = await tool.prepare(business, {"customer": "Luis Romero", "message": "See you at 10."})

    assert plan.summary == (
        'I will text Luis Romero at (510) 555-0161, this message, word for word:\n"See you at 10."'
    )
    await tool.apply(business, plan.arguments)
    (sent,) = [item for item in await _queued(session_factory) if item not in before]
    assert sent[0] == INTAKE_CUSTOMER_TEXT_COMMAND_TYPE
    assert (sent[1]["phone"], sent[1]["text"]) == ("+15105550161", "See you at 10.")


@pytest.mark.asyncio
async def test_a_contact_change_between_read_back_and_yes_sends_nothing(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    business, tool = await _larkspur(session_factory)
    await _consent(session_factory, business, True)
    plan = await tool.prepare(business, {"customer": "Luis", "message": "See you at 10."})
    before = await _queued(session_factory)
    await _consent(session_factory, business, False)

    with pytest.raises(OwnerChangeRefusedError, match="contact details changed"):
        await tool.apply(business, plan.arguments)
    assert await _queued(session_factory) == before


@pytest.mark.asyncio
async def test_refuses_unknown_ambiguous_empty_and_too_long(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    business, tool = await _larkspur(session_factory)

    with pytest.raises(OwnerChangeRefusedError, match="can't find a customer"):
        await tool.prepare(business, {"customer": "Zed", "message": "Hi"})
    with pytest.raises(OwnerChangeRefusedError, match="More than one matches"):
        await tool.prepare(business, {"customer": "an", "message": "Hi"})
    with pytest.raises(OwnerChangeRefusedError, match="what the message should say"):
        await tool.prepare(business, {"customer": "Luis", "message": "  "})
    with pytest.raises(OwnerChangeRefusedError, match="at most 900"):
        await tool.prepare(business, {"customer": "Luis", "message": "x" * 901})
    assert tool.sensitive


@pytest.mark.asyncio
async def test_without_texting_set_up_a_consented_customer_gets_e_mail(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    business, tool = await _larkspur(session_factory, can_text=False)
    await _consent(session_factory, business, True)

    plan = await tool.prepare(business, {"customer": "Luis", "message": "See you at 10."})

    assert plan.summary.startswith("I will e-mail Luis Romero at luis.romero@example.com")
    assert plan.arguments["channel"] == "e-mail"


def test_a_failed_owner_message_gets_its_own_notice_not_the_booking_one() -> None:
    def text(key: str, words: str = "See you at 10.") -> Any:
        return intake_customer_text_command(
            business_id=uuid4(),  # type: ignore[arg-type]
            customer_id=CustomerId(uuid4()),
            phone="+15105550161",
            text=words,
            idempotency_key=key,
        )

    assert owner_message_recipient(text("owner-message:b:k")) == "+15105550161"
    assert owner_message_failure_notice(text("owner-message:b:k")) == (
        'Your message to +15105550161 could not be sent: "See you at 10."\n'
        "Send it to them yourself, or ask me to send it again."
    )
    assert owner_message_failure_notice(text("intake:booking:k")) is None
    long = "x" * 300
    assert f'"{long}"' in str(owner_message_failure_notice(text("owner-message:b:k", long)))
    assert owner_message_recipient(text("intake:booking:k")) is None
