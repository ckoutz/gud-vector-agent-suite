from typing import cast

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from gvas.domain.repositories import UnitOfWork
from gvas.infrastructure.repositories import (
    SqlConversationRepository,
    SqlInboundMessageRepository,
    SqlOutboundMessageRepository,
    SqlOutboxRepository,
    SqlWorkflowRunRepository,
)


class SqlUnitOfWork:
    conversations: SqlConversationRepository
    inbound_messages: SqlInboundMessageRepository
    outbound_messages: SqlOutboundMessageRepository
    workflow_runs: SqlWorkflowRunRepository
    outbox: SqlOutboxRepository

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory
        self._session: AsyncSession | None = None

    async def __aenter__(self) -> "SqlUnitOfWork":
        self._session = self._session_factory()
        self.conversations = SqlConversationRepository(self._session)
        self.inbound_messages = SqlInboundMessageRepository(self._session)
        self.outbound_messages = SqlOutboundMessageRepository(self._session)
        self.workflow_runs = SqlWorkflowRunRepository(self._session)
        self.outbox = SqlOutboxRepository(self._session)
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: object | None,
    ) -> None:
        if self._session is None:
            return
        if exc_type is not None:
            await self._session.rollback()
        await self._session.close()

    async def commit(self) -> None:
        if self._session is None:
            raise RuntimeError("unit of work is not active")
        await self._session.commit()

    async def rollback(self) -> None:
        if self._session is None:
            raise RuntimeError("unit of work is not active")
        await self._session.rollback()


class SqlUnitOfWorkFactory:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    def __call__(self) -> UnitOfWork:
        return cast(UnitOfWork, SqlUnitOfWork(self._session_factory))
