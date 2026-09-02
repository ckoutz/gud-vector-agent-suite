from gvas.domain.enums import WorkflowRunStatus
from gvas.domain.field_notes import (
    FIELD_NOTE_CLOSE_TRIGGER,
    FIELD_NOTE_REPORT_APPROVE_TRIGGER,
    FIELD_NOTE_TRIGGER_PREFIX,
)
from gvas.domain.intents import UNMATCHED_MESSAGE_INTENT
from gvas.domain.messages import OutboundOwnerMessage, TextPart
from gvas.domain.quotes import QUOTE_TRIGGER_PREFIX
from gvas.domain.workflows import WorkflowContext, WorkflowResult

UNMATCHED_MESSAGE_REPLY = (
    "I did not recognize that message, so nothing was started. "
    f"Begin a quote with `{QUOTE_TRIGGER_PREFIX} ...` or a field-note case with "
    f"`{FIELD_NOTE_TRIGGER_PREFIX} ...`. Inside an open case, "
    f"`{FIELD_NOTE_REPORT_APPROVE_TRIGGER}` publishes the report and "
    f"`{FIELD_NOTE_CLOSE_TRIGGER}` closes the case."
)


class UnmatchedMessageHandler:
    """Answers a message that matches no trigger and no active workflow.

    Not matching is a property of the message, so retrying cannot help; the
    owner gets the available triggers exactly once, through the same persisted
    owner-reply path as every other reply.
    """

    intent = UNMATCHED_MESSAGE_INTENT

    async def handle(self, context: WorkflowContext) -> WorkflowResult:
        message = context.message
        reply = OutboundOwnerMessage(
            business_id=message.business_id,
            conversation_ref=message.conversation_ref,
            parts=(TextPart(text=UNMATCHED_MESSAGE_REPLY),),
            correlation_id=f"message.unmatched:{message.message_key}",
        )
        return WorkflowResult(
            status=WorkflowRunStatus.SUCCEEDED,
            replies=(reply,),
            detail="no workflow trigger: available triggers sent",
        )
