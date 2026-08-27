from datetime import datetime
from typing import Protocol

from pydantic import BaseModel, ConfigDict

from gvas.domain.enums import DeliveryStatus, WorkflowRunStatus
from gvas.domain.identifiers import (
    BusinessId,
    ConversationId,
    EndpointId,
    MessageId,
    RoutingData,
    WorkflowIntent,
    WorkflowRunId,
)
from gvas.domain.messages import (
    ChannelEndpointRef,
    ConversationRef,
    DeliveryReceipt,
    InboundOwnerMessage,
    OutboundOwnerMessage,
)
from gvas.domain.outbox import OutboxCommand, OutboxRecord


class BusinessRecord(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    business_id: BusinessId
    slug: str
    name: str


class OwnerChannelEndpointRecord(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    endpoint_id: EndpointId
    business_id: BusinessId
    source_namespace: str
    external_endpoint_id: str
    owner_external_id: str | None
    routing: RoutingData


class OutboundDeliveryRecord(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    outbound_message_id: MessageId
    message: OutboundOwnerMessage
    endpoint_id: EndpointId
    conversation_routing: RoutingData
    endpoint_routing: RoutingData
    status: DeliveryStatus


class BusinessRepository(Protocol):
    async def get(self, business_id: BusinessId) -> BusinessRecord | None: ...


class OwnerChannelEndpointRepository(Protocol):
    async def get(self, endpoint_id: EndpointId) -> OwnerChannelEndpointRecord | None: ...

    async def get_or_create(
        self, reference: ChannelEndpointRef, routing: RoutingData
    ) -> EndpointId: ...


class ConversationRepository(Protocol):
    async def get_or_create(
        self, reference: ConversationRef, endpoint_id: EndpointId, routing: RoutingData
    ) -> ConversationId: ...


class InboundMessageRepository(Protocol):
    async def create(
        self, message: InboundOwnerMessage, conversation_id: ConversationId, endpoint_id: EndpointId
    ) -> MessageId | None: ...


class OutboundMessageRepository(Protocol):
    async def create(
        self,
        message: OutboundOwnerMessage,
        conversation_id: ConversationId,
        inbound_message_id: MessageId,
    ) -> MessageId: ...

    async def get_for_delivery(
        self, outbound_message_id: MessageId
    ) -> OutboundDeliveryRecord | None: ...

    async def record_delivery(
        self, outbound_message_id: MessageId, receipt: DeliveryReceipt
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
    async def claim_batch(
        self, limit: int, now: datetime, claimed_by: str
    ) -> list[OutboxRecord]: ...
    async def update(self, record: OutboxRecord) -> None: ...


class UnitOfWork(Protocol):
    businesses: BusinessRepository
    owner_channel_endpoints: OwnerChannelEndpointRepository
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
