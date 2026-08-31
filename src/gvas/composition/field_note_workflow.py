from typing import Protocol

from gvas.application.field_notes import (
    CloseFieldNoteCaseHandler,
    FieldNoteIntakeHandler,
    FieldNoteUnitOfWorkFactory,
)
from gvas.domain.completeness import FollowUpQuestionStatus
from gvas.domain.completeness_repositories import CompletenessUnitOfWork
from gvas.domain.enums import WorkflowRunStatus
from gvas.domain.field_note_repositories import AmbiguousFieldNoteMessageError
from gvas.domain.field_notes import (
    FIELD_NOTE_INTENT,
    FieldNoteCaseId,
    FieldNoteReviewTrigger,
    field_note_review_command,
    has_field_note_close_trigger,
    match_field_note_trigger,
)
from gvas.domain.identifiers import BusinessId, ConversationId, MessageId
from gvas.domain.workflows import WorkflowContext, WorkflowResult


class CompletenessUnitOfWorkFactory(Protocol):
    def __call__(self) -> CompletenessUnitOfWork: ...


class FieldNoteWorkflowHandler:
    """Field-note workflow entry point for intake and follow-up answers.

    Intake stays in its accepted handler; this handler only decides whether an
    owner message closes the case, continues the note or answers the single
    outstanding follow-up question, and hands review work to the outbox.
    """

    intent = FIELD_NOTE_INTENT

    def __init__(
        self,
        intake: FieldNoteIntakeHandler,
        closure: CloseFieldNoteCaseHandler,
        field_note_unit_of_work_factory: FieldNoteUnitOfWorkFactory,
        completeness_unit_of_work_factory: CompletenessUnitOfWorkFactory,
    ) -> None:
        self._intake = intake
        self._closure = closure
        self._field_notes = field_note_unit_of_work_factory
        self._completeness = completeness_unit_of_work_factory

    async def handle(self, context: WorkflowContext) -> WorkflowResult:
        message = context.message
        conversation_id = context.conversation_id
        if conversation_id is None:
            raise ValueError("field-note workflow requires a persisted conversation identity")
        if has_field_note_close_trigger(message):
            return await self._closure.close(message, conversation_id)
        if match_field_note_trigger(message) is None:
            answer = await self._answer_result(message.business_id, conversation_id, context)
            if answer is not None:
                return answer
        result = await self._intake.handle(context)
        if result.status is not WorkflowRunStatus.SUCCEEDED:
            return result
        case_id = await self._active_case_id(message.business_id, conversation_id)
        if case_id is None:
            return result
        review = field_note_review_command(
            message.business_id,
            case_id,
            FieldNoteReviewTrigger.INTAKE,
            str(message.message_key),
        )
        return result.model_copy(update={"commands": (*result.commands, review)})

    async def _answer_result(
        self,
        business_id: BusinessId,
        conversation_id: ConversationId,
        context: WorkflowContext,
    ) -> WorkflowResult | None:
        async with self._completeness() as unit_of_work:
            review = await unit_of_work.field_note_reviews.get_active_for_conversation(
                business_id, conversation_id
            )
            questions = (
                ()
                if review is None
                else await unit_of_work.follow_up_questions.list_for_review(
                    business_id, review.review_id
                )
            )
            await unit_of_work.commit()
        asked = [
            question for question in questions if question.status is FollowUpQuestionStatus.ASKED
        ]
        if review is None or len(asked) != 1:
            return None
        case_id = await self._active_case_id(business_id, conversation_id)
        inbound_message_id = await self._inbound_message_id(business_id, context)
        if case_id is None or inbound_message_id is None:
            return None
        return WorkflowResult(
            status=WorkflowRunStatus.SUCCEEDED,
            commands=(
                field_note_review_command(
                    business_id,
                    case_id,
                    FieldNoteReviewTrigger.REPLY,
                    str(inbound_message_id),
                    owner_reply_message_id=inbound_message_id,
                ),
            ),
            detail="follow-up answer routed to completeness review",
        )

    async def _active_case_id(
        self, business_id: BusinessId, conversation_id: ConversationId
    ) -> FieldNoteCaseId | None:
        async with self._field_notes() as unit_of_work:
            case_id = await unit_of_work.field_note_conversation_states.get_active_case_id(
                business_id, conversation_id
            )
            await unit_of_work.commit()
        return case_id

    async def _inbound_message_id(
        self, business_id: BusinessId, context: WorkflowContext
    ) -> MessageId | None:
        message = context.message
        async with self._field_notes() as unit_of_work:
            try:
                location = await unit_of_work.field_note_messages.locate(
                    business_id, message.conversation_ref, message.message_key
                )
            except AmbiguousFieldNoteMessageError:
                await unit_of_work.rollback()
                return None
            await unit_of_work.commit()
        return None if location is None else location.inbound_message_id
