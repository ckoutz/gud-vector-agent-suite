from datetime import datetime
from typing import Protocol

from gvas.domain.enums import WorkflowRunStatus
from gvas.domain.identifiers import (
    BusinessId,
    ConversationId,
    MessageId,
    WorkflowIntent,
    WorkflowRunId,
)
from gvas.domain.messages import ConversationRef, InboundOwnerMessage, OutboundOwnerMessage
from gvas.domain.outbox import OutboxCommand, OutboxRecord


class BusinessRepository(Protocol):
    async def get(self, business_id: BusinessId) -> object | None: ...


class OwnerChannelEndpointRepository(Protocol):
    async def get_for_conversation(
        self, business_id: BusinessId, external_id: str
    ) -> object | None: ...


class ConversationRepository(Protocol):
    async def get_or_create(self, reference: ConversationRef) -> ConversationId: ...


class InboundMessageRepository(Protocol):
    async def create(
        self, message: InboundOwnerMessage, conversation_id: ConversationId
    ) -> MessageId | None: ...


class OutboundMessageRepository(Protocol):
    async def create(
        self,
        message: OutboundOwnerMessage,
        conversation_id: ConversationId,
        inbound_message_id: MessageId,
    ) -> None: ...


class WorkflowRunRepository(Protocol):
    async def create(
        self, business_id: BusinessId, inbound_message_id: MessageId, intent: WorkflowIntent
    ) -> WorkflowRunId: ...

    async def finish(
        self, run_id: WorkflowRunId, status: WorkflowRunStatus, error: str | None = None
    ) -> None: ...


class OutboxRepository(Protocol):
    async def enqueue(self, command: OutboxCommand) -> None: ...
    async def claim_batch(self, limit: int, now: datetime) -> list[OutboxRecord]: ...
    async def update(self, record: OutboxRecord) -> None: ...


class UnitOfWork(Protocol):
    conversations: ConversationRepository
    inbound_messages: InboundMessageRepository
    outbound_messages: OutboundMessageRepository
    workflow_runs: WorkflowRunRepository
    outbox: OutboxRepository

    async def __aenter__(self) -> "UnitOfWork": ...

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: object | None,
    ) -> None: ...

    async def commit(self) -> None: ...
    async def rollback(self) -> None: ...
