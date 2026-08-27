from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from gvas.application.ingestion import IngestionStatus, IngestOwnerMessageService
from gvas.domain.enums import WorkflowRunStatus
from gvas.domain.identifiers import BusinessId, MessageKey, OutboxCommandId, WorkflowIntent
from gvas.domain.messages import (
    ConversationRef,
    InboundOwnerMessage,
    OutboundOwnerMessage,
    TextPart,
)
from gvas.domain.outbox import OutboxCommand
from gvas.domain.workflows import WorkflowContext, WorkflowResult, WorkflowRouter
from gvas.infrastructure.models import (
    Business,
    Conversation,
    InboundMessage,
    OutboundMessage,
    OutboxMessage,
    WorkflowRun,
)
from gvas.infrastructure.unit_of_work import SqlUnitOfWorkFactory


class EchoHandler:
    intent = WorkflowIntent("echo")

    async def handle(self, context: WorkflowContext) -> WorkflowResult:
        return WorkflowResult(
            status=WorkflowRunStatus.SUCCEEDED,
            replies=[
                OutboundOwnerMessage(
                    business_id=context.message.business_id,
                    conversation_ref=context.message.conversation_ref,
                    parts=[TextPart(text="response")],
                    correlation_id="response-1",
                    routing=context.message.routing,
                )
            ],
            commands=[
                OutboxCommand(
                    command_id=OutboxCommandId(uuid4()),
                    business_id=context.message.business_id,
                    command_type="reply",
                    payload={"correlation_id": "response-1"},
                    dedup_key="response-1",
                )
            ],
        )


def make_message(business_id: BusinessId) -> InboundOwnerMessage:
    return InboundOwnerMessage(
        message_key=MessageKey("same-message"),
        business_id=business_id,
        conversation_ref={
            "business_id": business_id,
            "external_conversation_id": "conversation-1",
        },
        sender={"external_id": "owner-1", "role": "owner"},
        received_at=datetime.now(UTC),
        parts=[{"kind": "text", "text": "hello"}],
        intent=WorkflowIntent("echo"),
        routing={"transport": "opaque"},
    )


@pytest.mark.asyncio
async def test_duplicate_ingestion_is_idempotent(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    business_id = BusinessId(uuid4())
    async with session_factory() as session:
        session.add(
            Business(
                id=business_id,
                slug="test",
                name="Test",
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
            )
        )
        await session.commit()
    factory = SqlUnitOfWorkFactory(session_factory)
    service = IngestOwnerMessageService(factory, WorkflowRouter([EchoHandler()]))
    first = await service.ingest(make_message(business_id))
    second = await service.ingest(make_message(business_id))
    assert first.status is IngestionStatus.ACCEPTED
    assert second.status is IngestionStatus.DUPLICATE
    async with session_factory() as session:
        for model in (InboundMessage, WorkflowRun, OutboxMessage, OutboundMessage):
            assert await session.scalar(select(func.count()).select_from(model)) == 1


@pytest.mark.asyncio
async def test_unit_of_work_rolls_back_and_never_implicitly_commits(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    business_id = BusinessId(uuid4())
    async with session_factory() as session:
        session.add(
            Business(
                id=business_id,
                slug="rollback",
                name="Rollback",
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
            )
        )
        await session.commit()
    factory = SqlUnitOfWorkFactory(session_factory)
    with pytest.raises(RuntimeError):
        async with factory() as unit_of_work:
            await unit_of_work.conversations.get_or_create(
                ConversationRef(business_id=business_id, external_conversation_id="rolled-back")
            )
            raise RuntimeError("fail")
    async with factory() as unit_of_work:
        await unit_of_work.conversations.get_or_create(
            ConversationRef(business_id=business_id, external_conversation_id="not-committed")
        )
    async with session_factory() as session:
        assert await session.scalar(select(func.count()).select_from(Conversation)) == 0
