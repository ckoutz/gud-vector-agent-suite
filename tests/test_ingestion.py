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
    OwnerChannelEndpoint,
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
        conversation = await session.scalar(select(Conversation))
        assert conversation is not None
        assert conversation.routing == {"transport": "opaque"}


@pytest.mark.asyncio
async def test_typed_business_endpoint_reads_and_conversation_routing(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    business_id = BusinessId(uuid4())
    endpoint_id = uuid4()
    now = datetime.now(UTC)
    async with session_factory() as session:
        session.add(
            Business(
                id=business_id,
                slug="typed-repositories",
                name="Typed Repositories",
                created_at=now,
                updated_at=now,
            )
        )
        session.add(
            OwnerChannelEndpoint(
                id=endpoint_id,
                business_id=business_id,
                transport="fixture",
                external_endpoint_id="endpoint",
                owner_external_id="owner",
                routing={"opaque": True},
            )
        )
        await session.commit()
    async with SqlUnitOfWorkFactory(session_factory)() as unit_of_work:
        business = await unit_of_work.businesses.get(business_id)
        endpoint = await unit_of_work.owner_channel_endpoints.get_for_conversation(
            business_id, "endpoint"
        )
        conversation_id = await unit_of_work.conversations.get_or_create(
            ConversationRef(
                business_id=business_id,
                external_conversation_id="typed-conversation",
            ),
            {"opaque": True},
            endpoint_id,
        )
        await unit_of_work.commit()
    assert business is not None
    assert business.slug == "typed-repositories"
    assert endpoint is not None
    assert endpoint.endpoint_id == endpoint_id
    assert endpoint.business_id == business_id
    assert endpoint.routing == {"opaque": True}
    async with session_factory() as session:
        conversation = await session.scalar(
            select(Conversation).where(Conversation.id == conversation_id)
        )
        assert conversation is not None
        assert conversation.endpoint_id == endpoint_id
        assert conversation.routing == {"opaque": True}


@pytest.mark.asyncio
async def test_duplicate_insert_leaves_unit_of_work_usable(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    business_id = BusinessId(uuid4())
    async with session_factory() as session:
        session.add(
            Business(
                id=business_id,
                slug="duplicate-savepoint",
                name="Duplicate Savepoint",
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
            )
        )
        await session.commit()
    message = make_message(business_id)
    async with SqlUnitOfWorkFactory(session_factory)() as unit_of_work:
        conversation_id = await unit_of_work.conversations.get_or_create(
            message.conversation_ref, message.routing
        )
        first_id = await unit_of_work.inbound_messages.create(message, conversation_id)
        duplicate_id = await unit_of_work.inbound_messages.create(message, conversation_id)
        second_conversation_id = await unit_of_work.conversations.get_or_create(
            ConversationRef(
                business_id=business_id,
                external_conversation_id="second-conversation",
            ),
            {},
        )
        await unit_of_work.commit()
    assert first_id is not None
    assert duplicate_id is None
    assert second_conversation_id != conversation_id


@pytest.mark.asyncio
async def test_outbox_duplicate_insert_savepoint_keeps_unit_of_work_usable(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    business_id = BusinessId(uuid4())
    now = datetime.now(UTC)
    async with session_factory() as session:
        session.add(
            Business(
                id=business_id,
                slug="outbox-savepoint",
                name="Outbox Savepoint",
                created_at=now,
                updated_at=now,
            )
        )
        await session.commit()
    command_id = OutboxCommandId(uuid4())
    command = OutboxCommand(
        command_id=command_id,
        business_id=business_id,
        command_type="savepoint",
        payload={"value": "first"},
    )
    second_command = command.model_copy(update={"command_id": OutboxCommandId(uuid4())})
    async with SqlUnitOfWorkFactory(session_factory)() as unit_of_work:
        await unit_of_work.outbox.enqueue(command)
        await unit_of_work.outbox.enqueue(command)
        await unit_of_work.outbox.enqueue(second_command)
        await unit_of_work.commit()
    async with session_factory() as session:
        assert await session.scalar(select(func.count()).select_from(OutboxMessage)) == 2


@pytest.mark.asyncio
async def test_unknown_intent_is_recorded_as_failed(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    business_id = BusinessId(uuid4())
    async with session_factory() as session:
        session.add(
            Business(
                id=business_id,
                slug="unknown-intent",
                name="Unknown Intent",
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
            )
        )
        await session.commit()
    message = make_message(business_id).model_copy(
        update={"intent": WorkflowIntent("not-registered")}
    )
    service = IngestOwnerMessageService(SqlUnitOfWorkFactory(session_factory), WorkflowRouter([]))
    outcome = await service.ingest(message)
    assert outcome.status is IngestionStatus.ACCEPTED
    assert outcome.run_id is not None
    async with session_factory() as session:
        run = await session.scalar(select(WorkflowRun))
        assert run is not None
        assert run.status == WorkflowRunStatus.FAILED.value
        assert await session.scalar(select(func.count()).select_from(OutboundMessage)) == 0
        assert await session.scalar(select(func.count()).select_from(OutboxMessage)) == 0


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
                ConversationRef(business_id=business_id, external_conversation_id="rolled-back"),
                {},
            )
            raise RuntimeError("fail")
    async with factory() as unit_of_work:
        await unit_of_work.conversations.get_or_create(
            ConversationRef(business_id=business_id, external_conversation_id="not-committed"),
            {},
        )
    async with session_factory() as session:
        assert await session.scalar(select(func.count()).select_from(Conversation)) == 0
