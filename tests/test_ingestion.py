from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from gvas.application.ingestion import IngestionStatus, IngestOwnerMessageService
from gvas.application.intents import UnconfiguredIntentResolver
from gvas.domain.enums import DeliveryStatus, WorkflowRunStatus
from gvas.domain.identifiers import (
    BusinessId,
    MessageId,
    MessageKey,
    OutboxCommandId,
    WorkflowIntent,
)
from gvas.domain.intents import IntentResolution
from gvas.domain.messages import (
    ChannelEndpointRef,
    ConversationRef,
    DeliveryReceipt,
    InboundOwnerMessage,
    NormalizedOwnerMessage,
    OutboundOwnerMessage,
    TextPart,
)
from gvas.domain.outbox import (
    OWNER_REPLY_COMMAND_TYPE,
    OutboxCommand,
    ReservedOutboxCommandTypeError,
)
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


class FakeResolver:
    async def resolve(self, message: NormalizedOwnerMessage) -> IntentResolution:
        return IntentResolution(intent=WorkflowIntent("echo"))


class ReservedResolver:
    async def resolve(self, message: NormalizedOwnerMessage) -> IntentResolution:
        return IntentResolution(intent=WorkflowIntent("reserved"))


class ReplyHandler:
    intent = WorkflowIntent("echo")

    async def handle(self, context: WorkflowContext) -> WorkflowResult:
        return WorkflowResult(
            status=WorkflowRunStatus.SUCCEEDED,
            replies=(
                OutboundOwnerMessage(
                    business_id=context.message.business_id,
                    conversation_ref=context.message.conversation_ref,
                    parts=(TextPart(text="response"),),
                    correlation_id="response",
                ),
            ),
        )


class UnknownCommandHandler:
    intent = WorkflowIntent("reserved")

    async def handle(self, context: WorkflowContext) -> WorkflowResult:
        return WorkflowResult(
            status=WorkflowRunStatus.SUCCEEDED,
            commands=(
                OutboxCommand(
                    command_id=OutboxCommandId(UUID("00000000-0000-0000-0000-000000000010")),
                    business_id=context.message.business_id,
                    command_type=OWNER_REPLY_COMMAND_TYPE,
                    payload={"outbound_message_id": "wrong"},
                    outbound_message_id=MessageId(UUID("00000000-0000-0000-0000-000000000011")),
                ),
            ),
        )


def make_message(
    business_id: BusinessId,
    *,
    endpoint: str = "endpoint-a",
    source_namespace: str = "source-a",
    conversation: str = "conversation",
    message_key: str = "message",
) -> InboundOwnerMessage:
    normalized = NormalizedOwnerMessage(
        message_key=MessageKey(message_key),
        business_id=business_id,
        conversation_ref=ConversationRef(
            business_id=business_id, external_conversation_id=conversation
        ),
        sender={"external_id": "owner", "role": "owner"},
        received_at=datetime.now(UTC),
        parts=(TextPart(text="hello"),),
    )
    return InboundOwnerMessage(
        message=normalized,
        endpoint=ChannelEndpointRef(
            business_id=business_id,
            source_namespace=source_namespace,
            external_endpoint_id=endpoint,
        ),
        routing={"source": source_namespace},
    )


async def seed_business(
    session_factory: async_sessionmaker[AsyncSession], business_id: BusinessId
) -> None:
    now = datetime.now(UTC)
    async with session_factory() as session:
        session.add(
            Business(
                id=business_id,
                slug=f"business-{business_id}",
                name="Business",
                created_at=now,
                updated_at=now,
            )
        )
        await session.commit()


@pytest.mark.asyncio
async def test_reply_is_atomically_coupled_to_one_outbox_command(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    business_id = BusinessId(uuid4())
    await seed_business(session_factory, business_id)
    service = IngestOwnerMessageService(
        SqlUnitOfWorkFactory(session_factory), WorkflowRouter([ReplyHandler()]), FakeResolver()
    )
    message = make_message(business_id)
    outcome = await service.ingest(message)
    duplicate = await service.ingest(message)
    assert outcome.status is IngestionStatus.ACCEPTED
    assert duplicate.status is IngestionStatus.DUPLICATE
    async with session_factory() as session:
        outbound = await session.scalars(select(OutboundMessage))
        outbound_rows = list(outbound)
        commands = list(await session.scalars(select(OutboxMessage)))
        assert len(outbound_rows) == 1
        assert len(commands) == 1
        assert commands[0].command_type == OWNER_REPLY_COMMAND_TYPE
        assert commands[0].outbound_message_id == outbound_rows[0].id
        outbound_id = MessageId(outbound_rows[0].id)
    async with SqlUnitOfWorkFactory(session_factory)() as unit_of_work:
        delivery = await unit_of_work.outbound_messages.get_for_delivery(outbound_id)
        assert delivery is not None
        endpoint = await unit_of_work.owner_channel_endpoints.get(delivery.endpoint_id)
        assert endpoint is not None
        assert endpoint.source_namespace == "source-a"
        assert endpoint.external_endpoint_id == "endpoint-a"
        assert delivery.endpoint_routing == {"source": "source-a"}
        assert delivery.conversation_routing == {"source": "source-a"}
        await unit_of_work.outbound_messages.record_delivery(
            outbound_id,
            DeliveryReceipt(
                status=DeliveryStatus.DELIVERED,
                provider_message_id="provider-1",
                occurred_at=datetime.now(UTC),
                detail="delivered",
            ),
        )
        await unit_of_work.commit()
    async with session_factory() as session:
        row = await session.scalar(select(OutboundMessage))
        assert row is not None
        assert row.status == DeliveryStatus.DELIVERED.value
        assert row.provider_message_id == "provider-1"
        assert row.delivery_detail == "delivered"
        assert row.delivered_at is not None


@pytest.mark.asyncio
async def test_reserved_reply_command_rolls_back_all_rows(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    business_id = BusinessId(uuid4())
    await seed_business(session_factory, business_id)
    service = IngestOwnerMessageService(
        SqlUnitOfWorkFactory(session_factory),
        WorkflowRouter([UnknownCommandHandler()]),
        ReservedResolver(),
    )
    message = make_message(business_id)
    message = message.model_copy(
        update={
            "message": message.message.model_copy(update={"message_key": MessageKey("reserved")})
        }
    )
    with pytest.raises(ReservedOutboxCommandTypeError, match="reserved"):
        await service.ingest(message)
    async with session_factory() as session:
        assert await session.scalar(select(func.count()).select_from(InboundMessage)) == 0
        assert await session.scalar(select(func.count()).select_from(OutboundMessage)) == 0
        assert await session.scalar(select(func.count()).select_from(OutboxMessage)) == 0


@pytest.mark.asyncio
async def test_duplicate_inbound_savepoint_does_not_poison_unit_of_work(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    business_id = BusinessId(uuid4())
    await seed_business(session_factory, business_id)
    first = make_message(business_id, message_key="first")
    second = make_message(business_id, message_key="second")
    async with SqlUnitOfWorkFactory(session_factory)() as unit_of_work:
        endpoint_id = await unit_of_work.owner_channel_endpoints.get_or_create(
            first.endpoint, first.routing
        )
        conversation_id = await unit_of_work.conversations.get_or_create(
            first.message.conversation_ref, endpoint_id, first.routing
        )
        assert (
            await unit_of_work.inbound_messages.create(first, conversation_id, endpoint_id)
        ) is not None
        assert (
            await unit_of_work.inbound_messages.create(first, conversation_id, endpoint_id)
        ) is None
        assert (
            await unit_of_work.inbound_messages.create(second, conversation_id, endpoint_id)
        ) is not None
        await unit_of_work.commit()
    async with session_factory() as session:
        assert await session.scalar(select(func.count()).select_from(InboundMessage)) == 2


@pytest.mark.asyncio
async def test_unknown_intent_records_failed_run_without_outputs(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    business_id = BusinessId(uuid4())
    await seed_business(session_factory, business_id)

    class UnknownResolver:
        async def resolve(self, message: NormalizedOwnerMessage) -> IntentResolution:
            return IntentResolution(intent=WorkflowIntent("missing"))

    service = IngestOwnerMessageService(
        SqlUnitOfWorkFactory(session_factory), WorkflowRouter([]), UnknownResolver()
    )
    outcome = await service.ingest(make_message(business_id))
    assert outcome.status is IngestionStatus.ACCEPTED
    async with session_factory() as session:
        run = await session.scalar(select(WorkflowRun))
        assert run is not None
        assert run.status == WorkflowRunStatus.FAILED.value
        assert await session.scalar(select(func.count()).select_from(OutboundMessage)) == 0
        assert await session.scalar(select(func.count()).select_from(OutboxMessage)) == 0


@pytest.mark.asyncio
async def test_unresolved_intent_commits_inbound_without_run(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    business_id = BusinessId(uuid4())
    await seed_business(session_factory, business_id)
    service = IngestOwnerMessageService(
        SqlUnitOfWorkFactory(session_factory), WorkflowRouter([]), UnconfiguredIntentResolver()
    )
    outcome = await service.ingest(make_message(business_id))
    assert outcome.status is IngestionStatus.ACCEPTED
    assert outcome.run_id is None
    assert outcome.detail == "no intent resolver is configured"
    async with session_factory() as session:
        assert await session.scalar(select(func.count()).select_from(InboundMessage)) == 1
        assert await session.scalar(select(func.count()).select_from(WorkflowRun)) == 0


@pytest.mark.asyncio
async def test_identity_is_scoped_to_endpoint(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    business_id = BusinessId(uuid4())
    await seed_business(session_factory, business_id)
    service = IngestOwnerMessageService(
        SqlUnitOfWorkFactory(session_factory), WorkflowRouter([ReplyHandler()]), FakeResolver()
    )
    first = await service.ingest(make_message(business_id, endpoint="one", source_namespace="a"))
    second = await service.ingest(make_message(business_id, endpoint="two", source_namespace="b"))
    repeat = await service.ingest(make_message(business_id, endpoint="one", source_namespace="a"))
    assert first.status is IngestionStatus.ACCEPTED
    assert second.status is IngestionStatus.ACCEPTED
    assert repeat.status is IngestionStatus.DUPLICATE
    async with session_factory() as session:
        assert await session.scalar(select(func.count()).select_from(Conversation)) == 2
        assert await session.scalar(select(func.count()).select_from(InboundMessage)) == 2
        assert await session.scalar(select(func.count()).select_from(WorkflowRun)) == 2
        assert await session.scalar(select(func.count()).select_from(OutboundMessage)) == 2
