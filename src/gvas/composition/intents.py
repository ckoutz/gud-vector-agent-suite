from datetime import UTC, datetime
from typing import Protocol

from gvas.application.field_notes import FieldNoteIntentContribution, FieldNoteUnitOfWorkFactory
from gvas.application.quotes import QuoteIntentSelector, normalized_text, owner_command
from gvas.domain.calendar_blocks import (
    CALENDAR_BLOCK_INTENT,
    block_confirmation,
    unavailable_request,
    unblock_request,
)
from gvas.domain.enums import QuoteStatus
from gvas.domain.field_note_repositories import AmbiguousFieldNoteMessageError
from gvas.domain.field_notes import (
    FIELD_NOTE_INTENT,
    has_field_note_command_trigger,
    has_field_note_trigger,
)
from gvas.domain.intake import BOOKING_INTENT, booking_decision
from gvas.domain.intents import (
    UNMATCHED_MESSAGE_INTENT,
    WORKFLOW_CONFLICT_INTENT,
    IntentResolution,
    IntentUnresolvedError,
)
from gvas.domain.messages import NormalizedOwnerMessage
from gvas.domain.owner_commands import is_no, is_unclear, is_yes, is_yes_and_more, reaction
from gvas.domain.quotes import Quote, quote_trigger_request_text
from gvas.domain.repositories import UnitOfWork


class MessageUnitOfWorkFactory(Protocol):
    def __call__(self) -> UnitOfWork: ...


class DeterministicIntentResolver:
    """Resolves intents from message triggers and persisted conversation state.

    A new conversation must carry an explicit trigger; replies follow the active
    quote or field-note case. One conversation runs one workflow: a trigger for
    the other workflow, or a conversation that already carries both, resolves to
    the conflict intent so the owner is told to use another thread instead of a
    precedence being guessed. ``close notes`` and ``approve report`` are
    field-note commands, so they only reach the field-note workflow when this
    conversation is not a quote-only conversation; with both workflows active
    they are allowed through to repair the field-note state. A message that
    matches nothing resolves to the unmatched intent, whose handler replies with
    the triggers; ``IntentUnresolvedError`` is kept for resolver faults
    (unpersisted or ambiguous rows) that a retry can fix.
    """

    def __init__(
        self,
        field_notes: FieldNoteIntentContribution,
        quotes: QuoteIntentSelector,
        field_note_unit_of_work_factory: FieldNoteUnitOfWorkFactory,
        message_unit_of_work_factory: MessageUnitOfWorkFactory,
    ) -> None:
        self._field_notes = field_notes
        self._quotes = quotes
        self._field_note_unit_of_work_factory = field_note_unit_of_work_factory
        self._message_unit_of_work_factory = message_unit_of_work_factory

    async def resolve(self, message: NormalizedOwnerMessage) -> IntentResolution:
        # While a change Gus proposed is waiting, every message goes to Gus: a
        # plain yes applies it, a yes with more on it or a short unclear reply
        # keeps it waiting, and a no or a new request cancels it and says so
        # before the rest of the answer.
        # Gus has tools for booking decisions and calendar blocks, so those go
        # to him too; quote and field-note commands keep their own workflows
        # and leave the change waiting, untouched.
        text = normalized_text(message)
        pending = await self._owner_command_pending(message)
        if pending and (is_yes(text) or is_yes_and_more(text) or is_no(text)):
            return IntentResolution(intent=UNMATCHED_MESSAGE_INTENT, confidence=1)
        # An unclear reply or a tapback goes to Gus even in a thread with an
        # active quote or field-note case, unless it is one of their commands.
        if (
            pending
            and (is_unclear(text) or reaction(text) is not None)
            and not _explicit_workflow_command(message)
            and not await self._is_customer_choice(message, text)
        ):
            return IntentResolution(intent=UNMATCHED_MESSAGE_INTENT, confidence=1)
        # Booking decisions are checked before everything else: the quote
        # selector claims every message in a conversation with an active
        # quote, and a booking command must still reach its own handler.
        if not pending and booking_decision(text) is not None:
            return IntentResolution(intent=BOOKING_INTENT, confidence=1)
        if not pending and await self._is_calendar_block(message):
            return IntentResolution(intent=CALENDAR_BLOCK_INTENT, confidence=1)
        quote_resolution, active_quote = await self._resolve_quote(message)
        workflow_command = (
            has_field_note_trigger(message)
            or has_field_note_command_trigger(message)
            or (
                quote_resolution is not None
                and not _plain_words_while_awaiting_ok(message, active_quote)
            )
        )
        if pending and not workflow_command:
            return IntentResolution(intent=UNMATCHED_MESSAGE_INTENT, confidence=1)
        if has_field_note_trigger(message):
            if quote_resolution is not None:
                return IntentResolution(intent=WORKFLOW_CONFLICT_INTENT, confidence=1)
            return IntentResolution(intent=FIELD_NOTE_INTENT, confidence=1)
        field_note_intent = await self._field_notes.contribute(message)
        if has_field_note_command_trigger(message):
            if field_note_intent is None and quote_resolution is not None:
                return IntentResolution(intent=WORKFLOW_CONFLICT_INTENT, confidence=1)
            return IntentResolution(intent=FIELD_NOTE_INTENT, confidence=1)
        if field_note_intent is not None and quote_resolution is not None:
            return IntentResolution(intent=WORKFLOW_CONFLICT_INTENT, confidence=1)
        if quote_resolution is not None:
            if _plain_words_while_awaiting_ok(message, active_quote):
                return IntentResolution(intent=UNMATCHED_MESSAGE_INTENT, confidence=1)
            return quote_resolution
        if field_note_intent is not None:
            return IntentResolution(intent=field_note_intent, confidence=1)
        return IntentResolution(intent=UNMATCHED_MESSAGE_INTENT, confidence=1)

    async def _owner_command_pending(self, message: NormalizedOwnerMessage) -> bool:
        now = datetime.now(UTC)
        async with self._message_unit_of_work_factory() as unit_of_work:
            command = await unit_of_work.owner_commands.latest_proposed(message.business_id)
            return command is not None and command.is_live(now)

    async def _is_customer_choice(self, message: NormalizedOwnerMessage, text: str) -> bool:
        """A bare number answers a quote's "which customer?" list."""

        if not text.strip().rstrip(".").isdigit():
            return False
        _, active_quote = await self._resolve_quote(message)
        return (
            active_quote is not None
            and active_quote.status is QuoteStatus.AWAITING_CUSTOMER_SELECTION
        )

    async def _is_calendar_block(self, message: NormalizedOwnerMessage) -> bool:
        """``unavailable 8-12`` / ``unblock tue`` always; ``yes``/``no`` only while
        a block is waiting on the owner, so other workflows keep their replies."""

        text = normalized_text(message)
        if unavailable_request(text) is not None or unblock_request(text) is not None:
            return True
        if block_confirmation(text) is None:
            return False
        async with self._message_unit_of_work_factory() as unit_of_work:
            waiting = await unit_of_work.calendar_blocks.latest_proposed(message.business_id)
        return waiting is not None

    async def _resolve_quote(
        self, message: NormalizedOwnerMessage
    ) -> tuple[IntentResolution | None, Quote | None]:
        async with self._field_note_unit_of_work_factory() as unit_of_work:
            try:
                location = await unit_of_work.field_note_messages.locate(
                    message.business_id, message.conversation_ref, message.message_key
                )
            except AmbiguousFieldNoteMessageError as error:
                await unit_of_work.rollback()
                raise IntentUnresolvedError(str(error)) from error
            await unit_of_work.commit()
        if location is None:
            raise IntentUnresolvedError("normalized message is not persisted")
        async with self._message_unit_of_work_factory() as unit_of_work:
            active_quote = await unit_of_work.quotes.get_active(
                location.business_id, location.conversation_id
            )
            await unit_of_work.commit()
        return self._quotes.select(message, location.conversation_id, active_quote), active_quote


def _explicit_workflow_command(message: NormalizedOwnerMessage) -> bool:
    return (
        has_field_note_trigger(message)
        or has_field_note_command_trigger(message)
        or owner_command(message) is not None
        or quote_trigger_request_text(normalized_text(message)) is not None
    )


def _plain_words_while_awaiting_ok(
    message: NormalizedOwnerMessage, active_quote: Quote | None
) -> bool:
    """A quote waiting for the owner's OK keeps only its exact replies
    (approve / reject / correct:); plain words ("approve Hannah's quote",
    "what's tomorrow?") go to Gus like in any other thread."""

    return (
        active_quote is not None
        and active_quote.status is QuoteStatus.AWAITING_APPROVAL
        and owner_command(message) is None
        and quote_trigger_request_text(normalized_text(message)) is None
    )
