from datetime import datetime
from enum import StrEnum
from typing import Protocol
from uuid import UUID

from pydantic import BaseModel, ConfigDict, model_validator

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
    NormalizedOwnerMessage,
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


class WorkflowClaimResult(StrEnum):
    ACQUIRED = "acquired"
    TERMINAL = "terminal"
    BUSY = "busy"


class InboundProcessingRecord(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    inbound_message_id: MessageId
    business_id: BusinessId
    conversation_id: ConversationId
    endpoint_id: EndpointId
    message: NormalizedOwnerMessage


class WorkflowRunClaim(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    result: WorkflowClaimResult
    run_id: WorkflowRunId
    status: WorkflowRunStatus
    intent: WorkflowIntent | None
    attempts: int
    lease_token: UUID | None = None

    @model_validator(mode="after")
    def validate_lease_token(self) -> "WorkflowRunClaim":
        if (self.result is WorkflowClaimResult.ACQUIRED) != (self.lease_token is not None):
            raise ValueError("only acquired workflow claims may carry a lease token")
        return self


class LostWorkflowLeaseError(ValueError):
    pass


class EndpointBusinessMismatchError(ValueError):
    pass


class CrossBusinessReferenceError(ValueError):
    pass


class BusinessRepository(Protocol):
    async def get(self, business_id: BusinessId) -> BusinessRecord | None: ...


class OwnerChannelEndpointRepository(Protocol):
    async def get(self, endpoint_id: EndpointId) -> OwnerChannelEndpointRecord | None: ...

    async def get_or_create(
        self, reference: ChannelEndpointRef, routing: RoutingData
    ) -> EndpointId: ...


class ConversationRepository(Protocol):
    """Endpoint and conversation references must belong to the same business."""

    async def get_or_create(
        self, reference: ConversationRef, endpoint_id: EndpointId, routing: RoutingData
    ) -> ConversationId: ...


class InboundMessageRepository(Protocol):
    """Inbound links must reference the message business and endpoint."""

    async def create(
        self, message: InboundOwnerMessage, conversation_id: ConversationId, endpoint_id: EndpointId
    ) -> MessageId | None: ...

    async def get_for_processing(
        self, inbound_message_id: MessageId
    ) -> InboundProcessingRecord | None: ...


class OutboundMessageRepository(Protocol):
    """Outbound links must reference the same business as the reply."""

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
    async def claim(
        self,
        business_id: BusinessId,
        inbound_message_id: MessageId,
        *,
        now: datetime,
        stale_before: datetime,
    ) -> WorkflowRunClaim: ...

    async def set_intent(self, claim: WorkflowRunClaim, intent: WorkflowIntent) -> None: ...

    async def set_error(self, claim: WorkflowRunClaim, error: str) -> None: ...

    async def finish(
        self,
        claim: WorkflowRunClaim,
        status: WorkflowRunStatus,
        error: str | None = None,
    ) -> None: ...


class OutboxRepository(Protocol):
    async def enqueue(self, command: OutboxCommand) -> None: ...
    async def claim_batch(
        self, limit: int, now: datetime, claimed_by: str, *, stale_before: datetime
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
