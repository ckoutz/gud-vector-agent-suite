from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from gvas.application.outbox_service import OutboxService
from gvas.domain.enums import OutboxStatus
from gvas.domain.identifiers import BusinessId, OutboxCommandId
from gvas.domain.outbox import InvalidOutboxTransitionError, OutboxCommand, OutboxRecord
from gvas.infrastructure.models import Business, OutboxMessage
from gvas.infrastructure.repositories import SqlOutboxRepository
from gvas.infrastructure.unit_of_work import SqlUnitOfWorkFactory


def record() -> OutboxRecord:
    return OutboxRecord(
        command=OutboxCommand(
            command_id=OutboxCommandId(uuid4()),
            business_id=BusinessId(uuid4()),
            command_type="notify",
            payload={"value": "x"},
            dedup_key="dedup",
        ),
        available_at=datetime.now(UTC),
    )


def test_outbox_transitions_and_retry_metadata() -> None:
    current = record().transition(OutboxStatus.IN_PROGRESS)
    failed = current.model_copy(
        update={"attempts": 1, "available_at": current.available_at + timedelta(seconds=5)}
    ).transition(OutboxStatus.FAILED, error="temporary")
    assert failed.last_error == "temporary"
    assert failed.available_at > current.available_at
    assert failed.transition(OutboxStatus.IN_PROGRESS).status is OutboxStatus.IN_PROGRESS
    with pytest.raises(InvalidOutboxTransitionError):
        record().transition(OutboxStatus.SUCCEEDED)


@pytest.mark.asyncio
async def test_claim_batch_gates_by_available_at_and_deduplicates(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    business_id = BusinessId(uuid4())
    now = datetime.now(UTC)
    async with session_factory() as session:
        session.add(
            Business(
                id=business_id,
                slug="outbox",
                name="Outbox",
                created_at=now,
                updated_at=now,
            )
        )
        repository = SqlOutboxRepository(session)
        command = record().command.model_copy(update={"business_id": business_id})
        await repository.enqueue(command)
        await repository.enqueue(command)
        await session.commit()
    async with session_factory() as session:
        repository = SqlOutboxRepository(session)
        assert await repository.claim_batch(10, now - timedelta(seconds=1), "worker-a") == []
        claimed = await repository.claim_batch(10, now + timedelta(seconds=1), "worker-a")
        assert len(claimed) == 1
        assert claimed[0].status is OutboxStatus.IN_PROGRESS
        locked = await session.scalar(select(OutboxMessage))
        assert locked is not None
        assert locked.locked_by == "worker-a"
        await session.commit()
    async with session_factory() as session:
        assert (
            await session.scalar(select(OutboxMessage).where(OutboxMessage.dedup_key == "dedup"))
            is not None
        )


@pytest.mark.asyncio
async def test_outbox_service_lifecycle_and_retry_backoff(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    now = datetime.now(UTC)
    business_id = BusinessId(uuid4())
    async with session_factory() as session:
        session.add(
            Business(
                id=business_id,
                slug="outbox-service",
                name="Outbox Service",
                created_at=now,
                updated_at=now,
            )
        )
        await session.commit()
    service = OutboxService(SqlUnitOfWorkFactory(session_factory))
    command = record().command.model_copy(
        update={"business_id": business_id, "dedup_key": "service-dedup"}
    )
    await service.enqueue(command)
    claimed = await service.claim_batch(1, now + timedelta(seconds=1), "worker-service")
    assert len(claimed) == 1
    first = await service.mark_failed(claimed[0], timedelta(seconds=10), "temporary", now=now)
    assert first.status is OutboxStatus.FAILED
    assert first.attempts == 1
    assert first.available_at == now + timedelta(seconds=10)
    second_claim = await service.claim_batch(1, now + timedelta(seconds=11), "worker-service")
    second = await service.mark_failed(
        second_claim[0], timedelta(seconds=10), "temporary", now=now + timedelta(seconds=11)
    )
    assert second.status is OutboxStatus.FAILED
    assert second.attempts == 2
    third_claim = await service.claim_batch(1, now + timedelta(seconds=22), "worker-service")
    third = await service.mark_failed(
        third_claim[0], timedelta(seconds=10), "permanent", now=now + timedelta(seconds=22)
    )
    assert third.status is OutboxStatus.DEAD
    assert third.attempts == 3


@pytest.mark.asyncio
async def test_outbox_service_success_dead_and_invalid_transitions(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    now = datetime.now(UTC)
    business_id = BusinessId(uuid4())
    async with session_factory() as session:
        session.add(
            Business(
                id=business_id,
                slug="outbox-terminal",
                name="Outbox Terminal",
                created_at=now,
                updated_at=now,
            )
        )
        await session.commit()
    service = OutboxService(SqlUnitOfWorkFactory(session_factory))
    success_command = record().command.model_copy(
        update={"business_id": business_id, "dedup_key": "success"}
    )
    await service.enqueue(success_command)
    success_claim = (await service.claim_batch(1, now + timedelta(seconds=1), "worker"))[0]
    succeeded = await service.mark_succeeded(success_claim)
    assert succeeded.status is OutboxStatus.SUCCEEDED
    with pytest.raises(InvalidOutboxTransitionError):
        await service.mark_succeeded(succeeded)

    dead_command = record().command.model_copy(
        update={"business_id": business_id, "dedup_key": "dead"}
    )
    await service.enqueue(dead_command)
    dead_claim = (await service.claim_batch(1, now + timedelta(seconds=1), "worker"))[0]
    dead = await service.mark_dead(dead_claim, "cancelled")
    assert dead.status is OutboxStatus.DEAD
