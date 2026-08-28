from datetime import datetime
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field

from gvas.domain.completeness import (
    ChecklistItemKey,
    ChecklistKey,
    CompletenessChecklist,
    CorrelatedAnswer,
    FieldNoteReviewId,
    FieldNoteReviewStatus,
    FollowUpQuestionId,
    FollowUpQuestionStatus,
    MissingChecklistItem,
)
from gvas.domain.identifiers import BusinessId, ConversationId, MessageId
from gvas.domain.repositories import OutboundMessageRepository, OutboxRepository


class CompletenessRecord(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class FieldNoteReviewRecord(CompletenessRecord):
    review_id: FieldNoteReviewId
    business_id: BusinessId
    conversation_id: ConversationId
    external_conversation_id: str = Field(min_length=1)
    inbound_message_id: MessageId
    checklist_key: ChecklistKey
    checklist_version: int = Field(ge=1)
    transcript_text: str
    thread_correlation_id: str = Field(min_length=1)
    status: FieldNoteReviewStatus
    round_index: int = Field(ge=0)


class FollowUpQuestionRecord(CompletenessRecord):
    question_id: FollowUpQuestionId
    business_id: BusinessId
    review_id: FieldNoteReviewId
    item_key: ChecklistItemKey
    round_index: int = Field(ge=1)
    prompt: str = Field(min_length=1)
    correlation_id: str = Field(min_length=1)
    status: FollowUpQuestionStatus


class ChecklistDefinitionRepository(Protocol):
    """Versioned checklist definitions; content is configuration, not policy."""

    async def upsert(self, checklist: CompletenessChecklist) -> None: ...

    async def get(
        self,
        business_id: BusinessId,
        checklist_key: ChecklistKey,
        version: int | None = None,
    ) -> CompletenessChecklist | None: ...


class FieldNoteReviewRepository(Protocol):
    """Review references must stay inside one business."""

    async def get_or_create(
        self,
        business_id: BusinessId,
        conversation_id: ConversationId,
        external_conversation_id: str,
        inbound_message_id: MessageId,
        checklist_key: ChecklistKey,
        checklist_version: int,
        transcript_text: str,
        thread_correlation_id: str,
    ) -> FieldNoteReviewRecord: ...

    async def get_active_for_conversation(
        self, business_id: BusinessId, conversation_id: ConversationId
    ) -> FieldNoteReviewRecord | None: ...

    async def begin_round(self, review: FieldNoteReviewRecord) -> FieldNoteReviewRecord: ...

    async def complete(self, review: FieldNoteReviewRecord) -> FieldNoteReviewRecord: ...


class FollowUpQuestionRepository(Protocol):
    """Question creation and answer correlation are idempotent per review."""

    async def get_or_create_many(
        self, review: FieldNoteReviewRecord, missing_items: tuple[MissingChecklistItem, ...]
    ) -> tuple[FollowUpQuestionRecord, ...]: ...

    async def list_for_review(
        self, business_id: BusinessId, review_id: FieldNoteReviewId
    ) -> tuple[FollowUpQuestionRecord, ...]: ...

    async def get_by_correlation(
        self, business_id: BusinessId, review_id: FieldNoteReviewId, correlation_id: str
    ) -> FollowUpQuestionRecord | None: ...

    async def mark_asked(self, question: FollowUpQuestionRecord) -> None: ...

    async def record_answer(
        self,
        question: FollowUpQuestionRecord,
        inbound_message_id: MessageId,
        text: str,
        received_at: datetime,
    ) -> bool: ...

    async def answer_exists_for_inbound(
        self,
        business_id: BusinessId,
        review_id: FieldNoteReviewId,
        inbound_message_id: MessageId,
    ) -> bool: ...

    async def answers_for_review(
        self, business_id: BusinessId, review_id: FieldNoteReviewId
    ) -> tuple[CorrelatedAnswer, ...]: ...


class CompletenessUnitOfWork(Protocol):
    checklists: ChecklistDefinitionRepository
    field_note_reviews: FieldNoteReviewRepository
    follow_up_questions: FollowUpQuestionRepository
    outbound_messages: OutboundMessageRepository
    outbox: OutboxRepository

    async def __aenter__(self) -> "CompletenessUnitOfWork": ...

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: object | None,
    ) -> None: ...

    async def commit(self) -> None: ...
    async def rollback(self) -> None: ...
