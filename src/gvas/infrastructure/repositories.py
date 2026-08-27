from datetime import UTC, datetime

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from gvas.domain.enums import DeliveryStatus, OutboxStatus, WorkflowRunStatus
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
from gvas.domain.outbox import DEFAULT_MAX_ATTEMPTS, OutboxCommand, OutboxRecord
from gvas.domain.repositories import (
    BusinessRecord,
    EndpointBusinessMismatchError,
    OutboundDeliveryRecord,
    OwnerChannelEndpointRecord,
)
from gvas.infrastructure.models import (
    Business,
    Conversation,
    InboundMessage,
    OutboundMessage,
    OutboxMessage,
    OwnerChannelEndpoint,
    WorkflowRun,
)


class SqlBusinessRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, business_id: BusinessId) -> BusinessRecord | None:
        row = await self.session.scalar(select(Business).where(Business.id == business_id))
        if row is None:
            return None
        return BusinessRecord(
            business_id=BusinessId(row.id),
            slug=row.slug,
            name=row.name,
        )


class SqlOwnerChannelEndpointRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    @staticmethod
    def _record(row: OwnerChannelEndpoint) -> OwnerChannelEndpointRecord:
        return OwnerChannelEndpointRecord(
            endpoint_id=EndpointId(row.id),
            business_id=BusinessId(row.business_id),
            source_namespace=row.source_namespace,
            external_endpoint_id=row.external_endpoint_id,
            owner_external_id=row.owner_external_id,
            routing=row.routing,
        )

    async def get(self, endpoint_id: EndpointId) -> OwnerChannelEndpointRecord | None:
        row = await self.session.scalar(
            select(OwnerChannelEndpoint).where(OwnerChannelEndpoint.id == endpoint_id)
        )
        return self._record(row) if row is not None else None

    async def get_or_create(
        self, reference: ChannelEndpointRef, routing: RoutingData
    ) -> EndpointId:
        row = await self.session.scalar(
            select(OwnerChannelEndpoint).where(
                OwnerChannelEndpoint.business_id == reference.business_id,
                OwnerChannelEndpoint.source_namespace == reference.source_namespace,
                OwnerChannelEndpoint.external_endpoint_id == reference.external_endpoint_id,
            )
        )
        if row is not None:
            return EndpointId(row.id)
        row = OwnerChannelEndpoint(
            business_id=reference.business_id,
            source_namespace=reference.source_namespace,
            external_endpoint_id=reference.external_endpoint_id,
            routing=routing,
        )
        try:
            async with self.session.begin_nested():
                self.session.add(row)
                await self.session.flush()
        except IntegrityError:
            existing = await self.session.scalar(
                select(OwnerChannelEndpoint).where(
                    OwnerChannelEndpoint.business_id == reference.business_id,
                    OwnerChannelEndpoint.source_namespace == reference.source_namespace,
                    OwnerChannelEndpoint.external_endpoint_id == reference.external_endpoint_id,
                )
            )
            if existing is None:
                raise
            return EndpointId(existing.id)
        return EndpointId(row.id)


class SqlConversationRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_or_create(
        self, reference: ConversationRef, endpoint_id: EndpointId, routing: RoutingData
    ) -> ConversationId:
        endpoint = await self.session.scalar(
            select(OwnerChannelEndpoint).where(OwnerChannelEndpoint.id == endpoint_id)
        )
        if endpoint is None or endpoint.business_id != reference.business_id:
            raise EndpointBusinessMismatchError(
                "conversation endpoint must belong to the conversation business"
            )
        result = await self.session.execute(
            select(Conversation).where(
                Conversation.endpoint_id == endpoint_id,
                Conversation.external_conversation_id == reference.external_conversation_id,
            )
        )
        existing = result.scalar_one_or_none()
        if existing is not None:
            return ConversationId(existing.id)
        conversation = Conversation(
            business_id=reference.business_id,
            endpoint_id=endpoint_id,
            external_conversation_id=reference.external_conversation_id,
            routing=routing,
        )
        try:
            async with self.session.begin_nested():
                self.session.add(conversation)
                await self.session.flush()
        except IntegrityError:
            existing = await self.session.scalar(
                select(Conversation).where(
                    Conversation.endpoint_id == endpoint_id,
                    Conversation.external_conversation_id == reference.external_conversation_id,
                )
            )
            if existing is None:
                raise
            return ConversationId(existing.id)
        return ConversationId(conversation.id)


class SqlInboundMessageRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(
        self,
        message: InboundOwnerMessage,
        conversation_id: ConversationId,
        endpoint_id: EndpointId,
    ) -> MessageId | None:
        existing = await self.session.scalar(
            select(InboundMessage).where(
                InboundMessage.endpoint_id == endpoint_id,
                InboundMessage.message_key == message.message.message_key,
            )
        )
        if existing is not None:
            return None
        normalized = message.message
        row = InboundMessage(
            business_id=normalized.business_id,
            endpoint_id=endpoint_id,
            conversation_id=conversation_id,
            message_key=normalized.message_key,
            sender_external_id=normalized.sender.external_id,
            sender_role=normalized.sender.role.value,
            received_at=normalized.received_at,
            parts=[part.model_dump(mode="json") for part in normalized.parts],
            reply_to=normalized.reply_to.model_dump(mode="json") if normalized.reply_to else None,
            routing=message.routing,
        )
        try:
            async with self.session.begin_nested():
                self.session.add(row)
                await self.session.flush()
        except IntegrityError:
            return None
        return MessageId(row.id)


class SqlOutboundMessageRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(
        self,
        message: OutboundOwnerMessage,
        conversation_id: ConversationId,
        inbound_message_id: MessageId,
    ) -> MessageId:
        row = OutboundMessage(
            business_id=message.business_id,
            conversation_id=conversation_id,
            inbound_message_id=inbound_message_id,
            parts=[part.model_dump(mode="json") for part in message.parts],
            reply_to=message.reply_to.model_dump(mode="json") if message.reply_to else None,
            status=DeliveryStatus.ACCEPTED.value,
            correlation_id=message.correlation_id,
        )
        self.session.add(row)
        await self.session.flush()
        return MessageId(row.id)

    async def get_for_delivery(
        self, outbound_message_id: MessageId
    ) -> OutboundDeliveryRecord | None:
        result = await self.session.execute(
            select(OutboundMessage, Conversation, OwnerChannelEndpoint)
            .join(Conversation, Conversation.id == OutboundMessage.conversation_id)
            .join(OwnerChannelEndpoint, OwnerChannelEndpoint.id == Conversation.endpoint_id)
            .where(OutboundMessage.id == outbound_message_id)
        )
        row = result.one_or_none()
        if row is None:
            return None
        outbound, conversation, endpoint = row
        return OutboundDeliveryRecord(
            outbound_message_id=MessageId(outbound.id),
            message=OutboundOwnerMessage(
                business_id=BusinessId(outbound.business_id),
                conversation_ref=ConversationRef(
                    business_id=BusinessId(conversation.business_id),
                    external_conversation_id=conversation.external_conversation_id,
                ),
                parts=tuple(outbound.parts),
                correlation_id=outbound.correlation_id,
                reply_to=outbound.reply_to,
            ),
            endpoint_id=EndpointId(endpoint.id),
            conversation_routing=conversation.routing,
            endpoint_routing=endpoint.routing,
            status=DeliveryStatus(outbound.status),
        )

    async def record_delivery(
        self, outbound_message_id: MessageId, receipt: DeliveryReceipt
    ) -> None:
        await self.session.execute(
            update(OutboundMessage)
            .where(OutboundMessage.id == outbound_message_id)
            .values(
                status=receipt.status.value,
                provider_message_id=receipt.provider_message_id,
                delivered_at=receipt.occurred_at,
                delivery_detail=receipt.detail,
            )
        )


class SqlWorkflowRunRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(
        self, business_id: BusinessId, inbound_message_id: MessageId, intent: WorkflowIntent
    ) -> WorkflowRunId:
        row = WorkflowRun(
            business_id=business_id,
            inbound_message_id=inbound_message_id,
            intent=intent,
            status=WorkflowRunStatus.RUNNING.value,
            attempts=1,
            started_at=datetime.now(UTC),
        )
        self.session.add(row)
        await self.session.flush()
        return WorkflowRunId(row.id)

    async def finish(
        self, run_id: WorkflowRunId, status: WorkflowRunStatus, error: str | None = None
    ) -> None:
        await self.session.execute(
            update(WorkflowRun)
            .where(WorkflowRun.id == run_id)
            .values(status=status.value, finished_at=datetime.now(UTC), error=error)
        )


class SqlOutboxRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def enqueue(self, command: OutboxCommand) -> None:
        existing = (
            await self.session.scalar(
                select(OutboxMessage).where(
                    OutboxMessage.business_id == command.business_id,
                    OutboxMessage.dedup_key == command.dedup_key,
                )
            )
            if command.dedup_key is not None
            else None
        )
        if existing is not None:
            return
        row = OutboxMessage(
            id=command.command_id,
            business_id=command.business_id,
            outbound_message_id=command.outbound_message_id,
            command_type=command.command_type,
            payload=command.payload,
            status=OutboxStatus.PENDING.value,
            attempts=0,
            max_attempts=DEFAULT_MAX_ATTEMPTS,
            available_at=datetime.now(UTC),
            dedup_key=command.dedup_key,
        )
        try:
            async with self.session.begin_nested():
                self.session.add(row)
                await self.session.flush()
        except IntegrityError:
            return

    async def claim_batch(self, limit: int, now: datetime, claimed_by: str) -> list[OutboxRecord]:
        rows = list(
            (
                await self.session.scalars(
                    select(OutboxMessage)
                    .where(
                        OutboxMessage.status.in_(
                            [OutboxStatus.PENDING.value, OutboxStatus.FAILED.value]
                        ),
                        OutboxMessage.available_at <= now,
                    )
                    .order_by(OutboxMessage.available_at)
                    .limit(limit)
                    .with_for_update(skip_locked=True)
                )
            ).all()
        )
        result: list[OutboxRecord] = []
        for row in rows:
            row.status = OutboxStatus.IN_PROGRESS.value
            row.attempts += 1
            row.locked_at = now
            row.locked_by = claimed_by
            result.append(
                OutboxRecord(
                    command=OutboxCommand(
                        command_id=row.id,
                        business_id=row.business_id,
                        command_type=row.command_type,
                        payload=row.payload,
                        dedup_key=row.dedup_key,
                        outbound_message_id=row.outbound_message_id,
                    ),
                    status=OutboxStatus.IN_PROGRESS,
                    attempts=row.attempts,
                    max_attempts=row.max_attempts,
                    available_at=row.available_at,
                    last_error=row.last_error,
                )
            )
        await self.session.flush()
        return result

    async def update(self, record: OutboxRecord) -> None:
        await self.session.execute(
            update(OutboxMessage)
            .where(OutboxMessage.id == record.command.command_id)
            .values(
                status=record.status.value,
                attempts=record.attempts,
                max_attempts=record.max_attempts,
                available_at=record.available_at,
                last_error=record.last_error,
                locked_at=None,
                locked_by=None,
            )
        )
