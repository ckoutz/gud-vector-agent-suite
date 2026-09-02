from typing import Protocol

from gvas.application.field_notes import FieldNoteIntentContribution, FieldNoteUnitOfWorkFactory
from gvas.application.quotes import QuoteIntentSelector
from gvas.domain.field_note_repositories import AmbiguousFieldNoteMessageError
from gvas.domain.field_notes import (
    FIELD_NOTE_INTENT,
    has_field_note_command_trigger,
    has_field_note_trigger,
)
from gvas.domain.intents import (
    UNMATCHED_MESSAGE_INTENT,
    WORKFLOW_CONFLICT_INTENT,
    IntentResolution,
    IntentUnresolvedError,
)
from gvas.domain.messages import NormalizedOwnerMessage
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
        quote_resolution = await self._resolve_quote(message)
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
            return quote_resolution
        if field_note_intent is not None:
            return IntentResolution(intent=field_note_intent, confidence=1)
        return IntentResolution(intent=UNMATCHED_MESSAGE_INTENT, confidence=1)

    async def _resolve_quote(self, message: NormalizedOwnerMessage) -> IntentResolution | None:
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
        return self._quotes.select(message, location.conversation_id, active_quote)
