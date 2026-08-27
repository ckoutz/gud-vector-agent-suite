from datetime import UTC, datetime

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from gvas.domain.enums import OutboxStatus, WorkflowRunStatus
from gvas.domain.identifiers import (
    BusinessId,
    ConversationId,
    MessageId,
    WorkflowIntent,
    WorkflowRunId,
)
from gvas.domain.messages import ConversationRef, InboundOwnerMessage, OutboundOwnerMessage
from gvas.domain.outbox import OutboxCommand, OutboxRecord
from gvas.infrastructure.models import (
    Conversation,
    InboundMessage,
    OutboundMessage,
    OutboxMessage,
    WorkflowRun,
)


class SqlConversationRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_or_create(self, reference: ConversationRef) -> ConversationId:
        result = await self.session.execute(
            select(Conversation).where(
                Conversation.business_id == reference.business_id,
                Conversation.external_conversation_id == reference.external_conversation_id,
            )
        )
        existing = result.scalar_one_or_none()
        if existing is not None:
            return ConversationId(existing.id)
        conversation = Conversation(
            business_id=reference.business_id,
            external_conversation_id=reference.external_conversation_id,
            routing={},
        )
        self.session.add(conversation)
        await self.session.flush()
        return ConversationId(conversation.id)


class SqlInboundMessageRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(
        self, message: InboundOwnerMessage, conversation_id: ConversationId
    ) -> MessageId | None:
        existing = await self.session.scalar(
            select(InboundMessage).where(
                InboundMessage.business_id == message.business_id,
                InboundMessage.message_key == message.message_key,
            )
        )
        if existing is not None:
            return None
        row = InboundMessage(
            business_id=message.business_id,
            conversation_id=conversation_id,
            message_key=message.message_key,
            sender_external_id=message.sender.external_id,
            sender_role=message.sender.role.value,
            intent=message.intent,
            received_at=message.received_at,
            parts=[part.model_dump(mode="json") for part in message.parts],
            reply_to=message.reply_to.model_dump(mode="json") if message.reply_to else None,
            routing=message.routing,
        )
        self.session.add(row)
        try:
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
    ) -> None:
        self.session.add(
            OutboundMessage(
                business_id=message.business_id,
                conversation_id=conversation_id,
                inbound_message_id=inbound_message_id,
                parts=[part.model_dump(mode="json") for part in message.parts],
                reply_to=message.reply_to.model_dump(mode="json") if message.reply_to else None,
                routing=message.routing,
                status="accepted",
                correlation_id=message.correlation_id,
            )
        )
        await self.session.flush()


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
            status=WorkflowRunStatus.PENDING.value,
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
                select(OutboxMessage).where(OutboxMessage.dedup_key == command.dedup_key)
            )
            if command.dedup_key is not None
            else None
        )
        if existing is not None:
            return
        self.session.add(
            OutboxMessage(
                id=command.command_id,
                business_id=command.business_id,
                command_type=command.command_type,
                payload=command.payload,
                status=OutboxStatus.PENDING.value,
                attempts=0,
                max_attempts=3,
                available_at=datetime.now(UTC),
                dedup_key=command.dedup_key,
            )
        )
        await self.session.flush()

    async def claim_batch(self, limit: int, now: datetime) -> list[OutboxRecord]:
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
                )
            ).all()
        )
        result: list[OutboxRecord] = []
        for row in rows:
            row.status = OutboxStatus.IN_PROGRESS.value
            row.attempts += 1
            result.append(
                OutboxRecord(
                    command=OutboxCommand(
                        command_id=row.id,
                        business_id=row.business_id,
                        command_type=row.command_type,
                        payload=row.payload,
                        dedup_key=row.dedup_key,
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
            )
        )
