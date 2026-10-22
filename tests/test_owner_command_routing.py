from datetime import UTC, datetime
from uuid import uuid4

import pytest

from gvas.composition.intents import DeterministicIntentResolver
from gvas.domain.identifiers import BusinessId, MessageKey
from gvas.domain.intents import UNMATCHED_MESSAGE_INTENT
from gvas.domain.messages import ConversationRef, NormalizedOwnerMessage, SenderRef, TextPart


def _message(text: str) -> NormalizedOwnerMessage:
    business_id = BusinessId(uuid4())
    return NormalizedOwnerMessage(
        message_key=MessageKey("m"),
        business_id=business_id,
        conversation_ref=ConversationRef(business_id=business_id, external_conversation_id="c"),
        sender=SenderRef(external_id="owner", role="owner"),
        received_at=datetime.now(UTC),
        parts=(TextPart(text=text),),
    )


class _WorkflowError(Exception):
    pass


def _resolver() -> DeterministicIntentResolver:
    resolver = object.__new__(DeterministicIntentResolver)

    async def pending(_message: NormalizedOwnerMessage) -> bool:
        return True

    async def quote(_message: NormalizedOwnerMessage) -> None:
        raise _WorkflowError  # an active quote or field-note case would claim the reply here

    resolver._owner_command_pending = pending  # type: ignore[method-assign]
    resolver._resolve_quote = quote  # type: ignore[method-assign,assignment]
    return resolver


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "text", ["wait", "hmm", "yes?", "Liked \u201cI will add the note \u201cgate\u201d.\u201d"]
)
async def test_an_unclear_reply_reaches_gus_before_any_active_workflow(text: str) -> None:
    resolution = await _resolver().resolve(_message(text))

    assert resolution.intent == UNMATCHED_MESSAGE_INTENT


@pytest.mark.asyncio
@pytest.mark.parametrize("text", ["close notes", "correct: price 300", "approve"])
async def test_short_workflow_commands_still_reach_their_workflows(text: str) -> None:
    with pytest.raises(_WorkflowError):
        await _resolver().resolve(_message(text))
