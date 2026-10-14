"""Owner commands by text: Gus proposes one change, only YES applies it."""

from collections.abc import Mapping
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from composition_fakes import (
    CustomerDeliveryFake,
    OwnerReplyFake,
    QuoteDraftingFake,
    ReportGenerationFake,
    TranscriptionFake,
    application_ports,
)
from gvas.application.owner_assistant import (
    CANCELLED_NOTE,
    CHANGE_FAILED_REPLY,
    CONFIRM_PROMPT,
    NOTHING_PENDING_REPLY,
)
from gvas.composition import Application, build_application
from gvas.domain.calendar_blocks import CalendarBlock, CalendarBlockState
from gvas.domain.identifiers import BusinessId, JsonValue
from gvas.domain.messages import TextPart
from gvas.domain.owner_assistant import (
    OwnerAssistantAnswer,
    OwnerAssistantRequest,
    OwnerAssistantTool,
    OwnerChangePlan,
    OwnerChangeRefusedError,
    ToolRunner,
)
from gvas.domain.owner_commands import OwnerCommandState, is_yes
from gvas.domain.repositories import BusinessRecord
from gvas.infrastructure.telnyx.composition import sms_quotes_only_policy
from gvas.infrastructure.unit_of_work import SqlUnitOfWorkFactory
from telnyx_fixtures import message_payload, normalize, seed_business


class Clock:
    def __init__(self) -> None:
        self._now = datetime.now(UTC)

    def __call__(self) -> datetime:
        self._now += timedelta(seconds=1)
        return self._now

    def advance(self, delta: timedelta) -> None:
        self._now += delta


class NoteTool:
    """A stand-in change: adds a note. ``bad`` is refused; ``boom`` fails on apply."""

    spec = OwnerAssistantTool(
        "change_note", "Add a note.", {"text": {"type": "string", "description": "The note."}}
    )
    sensitive = False

    def __init__(self) -> None:
        self.applied: list[tuple[BusinessId, Mapping[str, JsonValue]]] = []

    async def prepare(self, business: BusinessRecord, args: Mapping[str, Any]) -> OwnerChangePlan:
        text = str(args.get("text", ""))
        if text == "bad":
            raise OwnerChangeRefusedError("that note isn't allowed")
        return OwnerChangePlan(summary=f'I will add the note "{text}".', arguments={"text": text})

    async def apply(self, business: BusinessRecord, arguments: Mapping[str, JsonValue]) -> str:
        if arguments["text"] == "boom":
            raise RuntimeError("database down")
        self.applied.append((business.business_id, arguments))
        return f'Done: added the note "{arguments["text"]}".'


class ModelFake:
    """``note X`` proposes note X (``notes X Y`` tries two); anything else is chat.
    Its own text always claims success, which code must never pass on."""

    def __init__(self) -> None:
        self.requests: list[OwnerAssistantRequest] = []
        self.results: list[Any] = []

    async def answer(
        self, request: OwnerAssistantRequest, run_tool: ToolRunner
    ) -> OwnerAssistantAnswer:
        self.requests.append(request)
        words = request.text.split()
        if words[:1] in (["note"], ["notes"]):
            for text in words[1:]:
                self.results.append(await run_tool("change_note", {"text": text}))
            return OwnerAssistantAnswer(text="Done! All changed.", tools_called=("change_note",))
        return OwnerAssistantAnswer(text="Hi there.")


def build(
    session_factory: async_sessionmaker[AsyncSession],
    owner_replies: OwnerReplyFake,
    model: ModelFake,
    tool: NoteTool,
    clock: Clock,
) -> Application:
    ports = replace(
        application_ports(
            owner_replies=owner_replies,
            quote_drafting=QuoteDraftingFake(),
            quote_delivery=CustomerDeliveryFake(),
            transcription=TranscriptionFake({}),
            report_generation=ReportGenerationFake(),
        ),
        channel_policies=(sms_quotes_only_policy("the team chat", owner_assistant=True),),
        owner_assistant=model,
        owner_change_tools=(tool,),
    )
    return build_application(ports, session_factory=session_factory, now=clock)


async def send(
    application: Application, owner_replies: OwnerReplyFake, business_id: BusinessId, text: str
) -> str:
    await application.ingest_service.ingest(
        normalize(message_payload(id=str(uuid4()), text=text), business_id)
    )
    await application.worker.drain()
    await application.worker.drain()
    _, message = owner_replies.sent[-1]
    return "".join(part.text for part in message.parts if isinstance(part, TextPart))


class Setup:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self.session_factory = session_factory
        self.business_id = BusinessId(uuid4())
        self.owner_replies = OwnerReplyFake()
        self.model = ModelFake()
        self.tool = NoteTool()
        self.clock = Clock()
        self.application = build(
            session_factory, self.owner_replies, self.model, self.tool, self.clock
        )

    async def text(self, text: str) -> str:
        return await send(self.application, self.owner_replies, self.business_id, text)

    async def commands(self) -> list[tuple[OwnerCommandState, str | None]]:
        async with SqlUnitOfWorkFactory(self.session_factory)() as unit_of_work:
            rows = await unit_of_work.owner_commands.recent(self.business_id, 20)
        return [(row.state, row.outcome) for row in reversed(rows)]


@pytest.fixture
async def setup(session_factory: async_sessionmaker[AsyncSession]) -> Setup:
    built = Setup(session_factory)
    await seed_business(session_factory, built.business_id)
    return built


@pytest.mark.asyncio
async def test_a_change_is_only_proposed_in_words_written_by_code(setup: Setup) -> None:
    reply = await setup.text("note gate")

    assert reply == f'I will add the note "gate".\n\n{CONFIRM_PROMPT}'
    assert "Done!" not in reply
    assert setup.tool.applied == []
    assert await setup.commands() == [(OwnerCommandState.PROPOSED, None)]
    assert "change_note" in {tool.name for tool in setup.model.requests[0].tools}


@pytest.mark.asyncio
async def test_yes_applies_the_stored_change_once_without_the_model(setup: Setup) -> None:
    await setup.text("note gate")

    assert await setup.text("YES") == 'Done: added the note "gate".'
    assert await setup.text("yes") == NOTHING_PENDING_REPLY

    assert setup.tool.applied == [(setup.business_id, {"text": "gate"})]
    assert len(setup.model.requests) == 1
    assert await setup.commands() == [(OwnerCommandState.DONE, 'Done: added the note "gate".')]


@pytest.mark.asyncio
@pytest.mark.parametrize("other", ["ok", "sure", "yes but tomorrow", "hello"])
async def test_any_other_reply_cancels_the_change(setup: Setup, other: str) -> None:
    await setup.text("note gate")
    await setup.text(other)

    assert await setup.text("YES") == NOTHING_PENDING_REPLY
    assert setup.tool.applied == []
    assert await setup.commands() == [(OwnerCommandState.CANCELLED, None)]


@pytest.mark.asyncio
async def test_a_change_expires_after_ten_minutes(setup: Setup) -> None:
    await setup.text("note gate")
    setup.clock.advance(timedelta(minutes=10))

    assert await setup.text("YES") == NOTHING_PENDING_REPLY
    assert setup.tool.applied == []
    assert await setup.commands() == [(OwnerCommandState.EXPIRED, None)]


@pytest.mark.asyncio
async def test_a_new_request_replaces_the_pending_one(setup: Setup) -> None:
    await setup.text("note gate")
    await setup.text("note dog")
    await setup.text("YES")

    assert setup.tool.applied == [(setup.business_id, {"text": "dog"})]
    assert [state for state, _ in await setup.commands()] == [
        OwnerCommandState.CANCELLED,
        OwnerCommandState.DONE,
    ]


@pytest.mark.asyncio
async def test_only_one_change_per_message(setup: Setup) -> None:
    reply = await setup.text("notes gate dog")

    assert reply.startswith('I will add the note "gate".')
    assert setup.model.results[1] == {
        "error": "Only one change at a time. Propose just the first one."
    }
    assert len(await setup.commands()) == 1


@pytest.mark.asyncio
async def test_a_refused_change_is_not_stored_and_the_model_explains(setup: Setup) -> None:
    reply = await setup.text("note bad")

    assert setup.model.results == [{"error": "that note isn't allowed"}]
    assert reply == "Done! All changed."  # the model's own words; nothing was proposed
    assert await setup.commands() == []


@pytest.mark.asyncio
async def test_a_change_that_fails_says_so_and_is_logged(setup: Setup) -> None:
    await setup.text("note boom")

    assert await setup.text("YES") == CHANGE_FAILED_REPLY
    assert await setup.commands() == [(OwnerCommandState.FAILED, CHANGE_FAILED_REPLY)]


@pytest.mark.asyncio
async def test_yes_from_another_thread_cancels_instead_of_applying(setup: Setup) -> None:
    assistant = setup.application.owner_assistant
    assert assistant is not None
    await assistant.answer(setup.business_id, "note gate", conversation_ref="sms:a")

    assert await assistant.answer(setup.business_id, "YES", conversation_ref="sms:b") == (
        CANCELLED_NOTE
    )
    assert setup.tool.applied == []
    assert await setup.commands() == [(OwnerCommandState.CANCELLED, None)]


@pytest.mark.asyncio
async def test_proposing_a_change_cancels_a_pending_calendar_block(setup: Setup) -> None:
    now = datetime.now(UTC)
    block = CalendarBlock(
        block_id=uuid4(),
        business_id=setup.business_id,
        day=date.today() + timedelta(days=1),
        start_minute=8 * 60,
        end_minute=12 * 60,
        created_at=now,
        expires_at=now + timedelta(minutes=30),
    )
    async with SqlUnitOfWorkFactory(setup.session_factory)() as unit_of_work:
        await unit_of_work.calendar_blocks.add(block)
        await unit_of_work.commit()

    await setup.text("note gate")

    async with SqlUnitOfWorkFactory(setup.session_factory)() as unit_of_work:
        assert await unit_of_work.calendar_blocks.latest_proposed(setup.business_id) is None
    assert await setup.text("YES") == 'Done: added the note "gate".'
    assert block.state is CalendarBlockState.PROPOSED  # the stored row changed, not this copy


def test_only_yes_confirms() -> None:
    assert all(is_yes(text) for text in ["YES", "yes", " Yes! ", "yes."])
    assert not any(is_yes(text) for text in ["ok", "y", "yes please", "yess", "no", ""])
