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
    ALREADY_ANSWERED_REPLY,
    CHANGE_FAILED_REPLY,
    CONFIRM_PROMPT,
    NO_CHANGE_SET_UP_NOTE,
    NOTHING_PENDING_REPLY,
    ONE_CHANGE_NOTE,
    UNCLEAR_REPLY,
    cancelled_note,
    still_waiting_note,
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
from gvas.domain.owner_commands import (
    OWNER_COMMAND_TTL,
    OwnerCommand,
    OwnerCommandConflictError,
    OwnerCommandState,
    conversation_key,
    is_no,
    is_unclear,
    is_yes,
    is_yes_and_more,
)
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

    async def follow_up(
        self, business: BusinessRecord, arguments: Mapping[str, JsonValue]
    ) -> tuple[str, OwnerChangePlan] | None:
        """``chain`` offers a next note, as a new invoice offers to send it."""
        if arguments["text"] != "chain":
            return None
        return "change_note", OwnerChangePlan(
            summary='I will add the note "next".', arguments={"text": "next"}
        )


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
        if request.text == "rename it":
            return OwnerAssistantAnswer(
                text="I can rename it to Larkspur Gardens. Reply YES to confirm."
            )
        if request.text == "rename":
            return OwnerAssistantAnswer(text="Please confirm the rename to Larkspur Gardens.")
        if request.text == "what did ana say?":
            return OwnerAssistantAnswer(text='Ana wrote "YES, Tuesday works."')
        if request.text == "is sam booked?":
            return OwnerAssistantAnswer(text="Yes, Sam's visit is Tue at 8:00 AM, booked.")
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
async def test_a_done_change_can_offer_the_next_one_which_waits_for_its_own_yes(
    setup: Setup,
) -> None:
    await setup.text("note chain")

    assert await setup.text("YES") == (
        'Done: added the note "chain".\n\nI will add the note "next".\n\n' + CONFIRM_PROMPT
    )
    assert [arguments for _, arguments in setup.tool.applied] == [{"text": "chain"}]
    assert await setup.text("YES") == 'Done: added the note "next".'
    assert [arguments for _, arguments in setup.tool.applied] == [
        {"text": "chain"},
        {"text": "next"},
    ]
    assert await setup.text("YES") == NOTHING_PENDING_REPLY
    assert len(setup.model.requests) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "other", ["no", "Nope", "cancel", "No thanks", "nah.", "never mind", "no, wait", "Stop"]
)
async def test_a_clear_no_cancels_the_change_and_says_so_first(setup: Setup, other: str) -> None:
    await setup.text("note gate")
    reply = await setup.text(other)

    assert reply.startswith(
        'I cancelled the pending change: add the note "gate". Nothing was changed.'
    )

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
    assert reply.endswith(ONE_CHANGE_NOTE)
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
        cancelled_note('I will add the note "gate".')
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


def test_only_a_plain_yes_confirms() -> None:
    plain = [
        "YES",
        "yes",
        " Yes! ",
        "yes.",
        "y",
        "Yep",
        "yeah",
        "yup",
        "Yes please",
        "go ahead",
        "Do it!",
        *NEW_YES,
    ]
    assert all(is_yes(text) for text in plain)
    more = [
        "Yes and Friday too",
        "yes but make it 2 to 4",
        "y, and Friday",
        "go ahead with friday",
        "sure, and Friday too",
        "ok but make it $95",
        "👍 and add Friday",
    ]
    assert all(is_yes_and_more(text) and not is_yes(text) for text in more)
    others = ["yess", "yesterday", "you there?", "no", "", "yes?", "ok?", "please", "thanks"]
    assert not any(is_yes(text) or is_yes_and_more(text) for text in others)
    assert all(is_no(text) for text in ["no", "No thanks", "nope.", "cancel it", "don't"])


NEW_YES = [
    "Yea",
    "yeh",
    "Sure",
    "OK",
    "okay!",
    "Confirm",
    "send it.",
    "do it",
    "Sounds good!",
    "yes do it",
    "Yes, do it.",
    "yes please, send it",
    "Ok thanks",
    "\N{THUMBS UP SIGN}",
    "\N{THUMBS UP SIGN}\N{EMOJI MODIFIER FITZPATRICK TYPE-4}",
    "\N{THUMBS UP SIGN}\N{VARIATION SELECTOR-16}",
]
UNCLEAR = [
    "hello",
    "hmm",
    "wait",
    "yes?",
    "Friday 2pm",
    "k",
    "send",
    "\N{GRINNING FACE}",
]


@pytest.mark.asyncio
@pytest.mark.parametrize("yes", NEW_YES)
async def test_a_natural_yes_confirms_the_change(setup: Setup, yes: str) -> None:
    await setup.text("note gate")
    requests = len(setup.model.requests)

    assert await setup.text(yes) == 'Done: added the note "gate".'
    assert setup.tool.applied == [(setup.business_id, {"text": "gate"})]
    assert len(setup.model.requests) == requests


@pytest.mark.asyncio
@pytest.mark.parametrize("unclear", UNCLEAR)
async def test_a_short_unclear_reply_keeps_the_change_waiting(setup: Setup, unclear: str) -> None:
    assert is_unclear(unclear)
    await setup.text("note gate")
    requests = len(setup.model.requests)

    assert await setup.text(unclear) == UNCLEAR_REPLY
    assert len(setup.model.requests) == requests  # the model never saw it
    assert await setup.commands() == [(OwnerCommandState.PROPOSED, None)]
    assert await setup.text("yea") == 'Done: added the note "gate".'


@pytest.mark.parametrize(
    "text",
    [
        "note dog",
        "who owes me",
        "schedule?",
        "what about next week",
        "no thanks",
        "do not",
        "is Sam booked?",
        "quote: Sam",
    ],
)
def test_a_short_request_or_no_is_not_unclear(text: str) -> None:
    assert not is_unclear(text)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "request_text", ["who owes me", "what about next week then", "is Sam booked?"]
)
async def test_a_different_request_cancels_and_is_answered(setup: Setup, request_text: str) -> None:
    await setup.text("note gate")

    reply = await setup.text(request_text)

    assert reply == cancelled_note('I will add the note "gate".') + "\n\nHi there."
    assert await setup.commands() == [(OwnerCommandState.CANCELLED, None)]


@pytest.mark.asyncio
async def test_do_not_cancels(setup: Setup) -> None:
    await setup.text("note gate")

    assert await setup.text("do not") == cancelled_note('I will add the note "gate".')
    assert await setup.commands() == [(OwnerCommandState.CANCELLED, None)]


@pytest.mark.asyncio
async def test_a_thumbs_up_tapback_on_the_read_back_confirms(setup: Setup) -> None:
    await setup.text("note gate")

    assert (
        await setup.text(f'Liked \u201cI will add the note "gate". {CONFIRM_PROMPT}\u201d')
        == 'Done: added the note "gate".'
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "tapback",
    [
        'Liked \u201cDone: added the note "fence" yesterday afternoon.\u201d',
        'Laughed at \u201cI will add the note "gate".\u201d',
        'Reacted \N{THUMBS DOWN SIGN} to \u201cI will add the note "gate".\u201d',
    ],
)
async def test_another_tapback_keeps_the_change_waiting(setup: Setup, tapback: str) -> None:
    await setup.text("note gate")

    assert await setup.text(tapback) == UNCLEAR_REPLY
    assert await setup.commands() == [(OwnerCommandState.PROPOSED, None)]


@pytest.mark.asyncio
async def test_the_demo_copy_takes_the_same_yes_and_unclear_replies(setup: Setup) -> None:
    assistant = setup.application.owner_assistant
    assert assistant is not None
    await assistant.answer(setup.business_id, "note gate", conversation_ref="demo")

    assert await assistant.answer(setup.business_id, "hmm", conversation_ref="demo") == (
        UNCLEAR_REPLY
    )
    assert await assistant.answer(setup.business_id, "Yea", conversation_ref="demo") == (
        'Done: added the note "gate".'
    )


@pytest.mark.asyncio
async def test_an_unclear_reply_from_another_thread_still_cancels(setup: Setup) -> None:
    assistant = setup.application.owner_assistant
    assert assistant is not None
    await assistant.answer(setup.business_id, "note gate", conversation_ref="sms:a")

    reply = await assistant.answer(setup.business_id, "hmm", conversation_ref="sms:b")

    assert reply.startswith(cancelled_note('I will add the note "gate".'))
    assert await setup.commands() == [(OwnerCommandState.CANCELLED, None)]


@pytest.mark.asyncio
async def test_a_yes_with_more_on_it_keeps_the_change_waiting(setup: Setup) -> None:
    await setup.text("note gate")
    requests = len(setup.model.requests)

    reply = await setup.text("Yes and Friday too")

    assert reply == still_waiting_note('I will add the note "gate".')
    assert reply.startswith("Nothing is done yet.")
    assert len(setup.model.requests) == requests  # the model never saw it
    assert setup.tool.applied == []
    assert await setup.text("yep") == 'Done: added the note "gate".'


@pytest.mark.asyncio
async def test_a_change_another_yes_claimed_is_not_called_cancelled(setup: Setup) -> None:
    await setup.text("note gate")
    async with SqlUnitOfWorkFactory(setup.session_factory)() as unit_of_work:
        command = await unit_of_work.owner_commands.latest_proposed(setup.business_id)
        assert command is not None
    assistant = setup.application.owner_assistant
    assert assistant is not None
    # A YES claims it between this "no" reading the proposal and closing it.
    pending = assistant._pending

    async def claimed_meanwhile(business_id: BusinessId, now: datetime) -> OwnerCommand | None:
        found = await pending(business_id, now)
        async with SqlUnitOfWorkFactory(setup.session_factory)() as unit_of_work:
            assert await unit_of_work.owner_commands.claim(command, datetime.now(UTC))
            await unit_of_work.commit()
        return found

    assistant._pending = claimed_meanwhile  # type: ignore[method-assign]

    assert await setup.text("no") == ALREADY_ANSWERED_REPLY


@pytest.mark.asyncio
async def test_other_words_after_a_claimed_change_say_it_was_answered(setup: Setup) -> None:
    await setup.text("note gate")
    async with SqlUnitOfWorkFactory(setup.session_factory)() as unit_of_work:
        command = await unit_of_work.owner_commands.latest_proposed(setup.business_id)
        assert command is not None
    assistant = setup.application.owner_assistant
    assert assistant is not None
    pending = assistant._pending

    async def claimed_meanwhile(business_id: BusinessId, now: datetime) -> OwnerCommand | None:
        found = await pending(business_id, now)
        async with SqlUnitOfWorkFactory(setup.session_factory)() as unit_of_work:
            assert await unit_of_work.owner_commands.claim(command, datetime.now(UTC))
            await unit_of_work.commit()
        return found

    assistant._pending = claimed_meanwhile  # type: ignore[method-assign]

    assert await setup.text("hello, what is on today") == f"{ALREADY_ANSWERED_REPLY}\n\nHi there."


def _command(business_id: BusinessId, now: datetime) -> OwnerCommand:
    return OwnerCommand(
        command_id=uuid4(),
        business_id=business_id,
        conversation_ref=conversation_key("x" * 400),
        tool="change_note",
        arguments={"text": "a"},
        summary="I will add the note.",
        requested_text="note a",
        created_at=now,
        expires_at=now + OWNER_COMMAND_TTL,
    )


@pytest.mark.asyncio
async def test_the_database_keeps_one_proposal_and_a_claimed_one_is_never_cancelled(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    business_id = BusinessId(uuid4())
    await seed_business(session_factory, business_id)
    factory = SqlUnitOfWorkFactory(session_factory)
    now = datetime.now(UTC)
    first = _command(business_id, now)
    async with factory() as unit_of_work:
        await unit_of_work.owner_commands.add(first)
        await unit_of_work.commit()
    async with factory() as unit_of_work:
        with pytest.raises(OwnerCommandConflictError):
            await unit_of_work.owner_commands.add(_command(business_id, now))
    async with factory() as unit_of_work:
        assert await unit_of_work.owner_commands.claim(first, now)
        assert not await unit_of_work.owner_commands.close(first, OwnerCommandState.CANCELLED, now)
        await unit_of_work.commit()
    async with factory() as unit_of_work:
        (stored,) = await unit_of_work.owner_commands.recent(business_id, 5)
    assert stored.state is OwnerCommandState.APPLYING
    assert len(stored.conversation_ref) == 64


@pytest.mark.asyncio
async def test_a_model_asking_for_yes_without_a_proposal_gets_a_note(setup: Setup) -> None:
    note = f"\n\n{NO_CHANGE_SET_UP_NOTE}"
    assert await setup.text("rename it") == (
        f"I can rename it to Larkspur Gardens. Reply YES to confirm.{note}"
    )
    assert await setup.text("rename") == f"Please confirm the rename to Larkspur Gardens.{note}"
    # The answer is never lost: a quoted customer YES only adds the note.
    assert await setup.text("what did ana say?") == f'Ana wrote "YES, Tuesday works."{note}'
    assert await setup.text("is sam booked?") == "Yes, Sam's visit is Tue at 8:00 AM, booked."
    assert await setup.commands() == []
