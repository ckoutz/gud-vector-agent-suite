from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from gvas.domain.enums import OutboxStatus
from gvas.domain.identifiers import BusinessId, OutboxCommandId
from gvas.domain.outbox import InvalidOutboxTransitionError, OutboxCommand, OutboxRecord
from gvas.infrastructure.models import Business, OutboxMessage
from gvas.infrastructure.repositories import SqlOutboxRepository


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
        assert await repository.claim_batch(10, now - timedelta(seconds=1)) == []
        claimed = await repository.claim_batch(10, now + timedelta(seconds=1))
        assert len(claimed) == 1
        assert claimed[0].status is OutboxStatus.IN_PROGRESS
        await session.commit()
    async with session_factory() as session:
        assert (
            await session.scalar(select(OutboxMessage).where(OutboxMessage.dedup_key == "dedup"))
            is not None
        )
