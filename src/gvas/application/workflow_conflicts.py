from gvas.domain.enums import WorkflowRunStatus
from gvas.domain.field_notes import has_field_note_trigger
from gvas.domain.intents import WORKFLOW_CONFLICT_INTENT
from gvas.domain.messages import NormalizedOwnerMessage, OutboundOwnerMessage, TextPart
from gvas.domain.quotes import has_quote_trigger
from gvas.domain.workflows import WorkflowContext, WorkflowResult

FIELD_NOTE_CONFLICT_REPLY = (
    "This conversation already has an active quote. Finish or close it first, and start "
    "field notes in a separate thread or conversation."
)
QUOTE_CONFLICT_REPLY = (
    "This conversation already has an open field notes case. Close it with 'close notes' "
    "first, and start the quote in a separate thread or conversation."
)
BOTH_ACTIVE_CONFLICT_REPLY = (
    "This conversation has both an active quote and an open field notes case. Continue each "
    "one in a separate thread or conversation."
)


class WorkflowConflictHandler:
    """Rejects a second workflow in one conversation instead of guessing precedence.

    The reply is a normal owner reply, so it is persisted and delivered through the
    owner-reply port with a deterministic correlation ID.
    """

    intent = WORKFLOW_CONFLICT_INTENT

    async def handle(self, context: WorkflowContext) -> WorkflowResult:
        message = context.message
        reply = OutboundOwnerMessage(
            business_id=message.business_id,
            conversation_ref=message.conversation_ref,
            parts=(TextPart(text=self._body(message)),),
            correlation_id=f"workflow.conflict:{message.message_key}",
        )
        return WorkflowResult(
            status=WorkflowRunStatus.SUCCEEDED,
            replies=(reply,),
            detail="one workflow per conversation: message rejected",
        )

    @staticmethod
    def _body(message: NormalizedOwnerMessage) -> str:
        if has_field_note_trigger(message):
            return FIELD_NOTE_CONFLICT_REPLY
        if has_quote_trigger(message):
            return QUOTE_CONFLICT_REPLY
        return BOTH_ACTIVE_CONFLICT_REPLY
