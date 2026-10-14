from datetime import UTC, datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from gvas.domain.identifiers import BusinessId
from gvas.domain.owner_commands import OwnerCommand, OwnerCommandState
from gvas.infrastructure.owner_command_models import OwnerCommandRecord


class SqlOwnerCommandRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def add(self, command: OwnerCommand) -> None:
        row = OwnerCommandRecord(id=command.command_id, business_id=command.business_id)
        _write(row, command)
        self.session.add(row)
        await self.session.flush()

    async def save(self, command: OwnerCommand) -> None:
        row = await self.session.get(OwnerCommandRecord, command.command_id)
        if row is None or row.business_id != command.business_id:
            raise LookupError("owner command is not persisted")
        _write(row, command)
        await self.session.flush()

    async def latest_proposed(self, business_id: BusinessId) -> OwnerCommand | None:
        row = await self.session.scalar(
            select(OwnerCommandRecord)
            .where(
                OwnerCommandRecord.business_id == business_id,
                OwnerCommandRecord.state == OwnerCommandState.PROPOSED.value,
            )
            .order_by(OwnerCommandRecord.created_at.desc())
            .limit(1)
        )
        return None if row is None else _read(row)

    async def claim(self, command: OwnerCommand, now: datetime) -> bool:
        result = await self.session.execute(
            update(OwnerCommandRecord)
            .where(
                OwnerCommandRecord.id == command.command_id,
                OwnerCommandRecord.business_id == command.business_id,
                OwnerCommandRecord.state == OwnerCommandState.PROPOSED.value,
                OwnerCommandRecord.expires_at > now,
            )
            .values(state=OwnerCommandState.APPLYING.value, decided_at=now)
            .execution_options(synchronize_session=False)
        )
        await self.session.flush()
        return bool(getattr(result, "rowcount", 0))

    async def recent(self, business_id: BusinessId, limit: int) -> tuple[OwnerCommand, ...]:
        rows = await self.session.scalars(
            select(OwnerCommandRecord)
            .where(OwnerCommandRecord.business_id == business_id)
            .order_by(OwnerCommandRecord.created_at.desc(), OwnerCommandRecord.id)
            .limit(limit)
        )
        return tuple(_read(row) for row in rows.all())


def _write(row: OwnerCommandRecord, command: OwnerCommand) -> None:
    row.conversation_ref = command.conversation_ref
    row.tool = command.tool
    row.arguments = dict(command.arguments)
    row.summary = command.summary
    row.sensitive = command.sensitive
    row.requested_text = command.requested_text
    row.state = command.state.value
    row.outcome = command.outcome
    row.created_at = command.created_at
    row.expires_at = command.expires_at
    row.decided_at = command.decided_at


def _read(row: OwnerCommandRecord) -> OwnerCommand:
    return OwnerCommand(
        command_id=row.id,
        business_id=BusinessId(row.business_id),
        conversation_ref=row.conversation_ref,
        tool=row.tool,
        arguments=dict(row.arguments),
        summary=row.summary,
        sensitive=row.sensitive,
        requested_text=row.requested_text,
        state=OwnerCommandState(row.state),
        outcome=row.outcome,
        created_at=_aware(row.created_at),
        expires_at=_aware(row.expires_at),
        decided_at=None if row.decided_at is None else _aware(row.decided_at),
    )


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)
