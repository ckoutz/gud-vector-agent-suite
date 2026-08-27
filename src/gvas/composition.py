from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from gvas.application.ingestion import IngestOwnerMessageService
from gvas.application.outbox_service import OutboxService
from gvas.config import Settings
from gvas.domain.workflows import WorkflowRouter
from gvas.infrastructure.db import create_engine, create_session_factory
from gvas.infrastructure.unit_of_work import SqlUnitOfWorkFactory


@dataclass(frozen=True)
class Application:
    engine: AsyncEngine
    session_factory: async_sessionmaker[AsyncSession]
    unit_of_work_factory: SqlUnitOfWorkFactory
    router: WorkflowRouter
    ingestion: IngestOwnerMessageService
    outbox: OutboxService


def build_application(settings: Settings | None = None) -> Application:
    resolved = settings or Settings()
    engine = create_engine(resolved.database_url)
    session_factory = create_session_factory(engine)
    unit_of_work_factory = SqlUnitOfWorkFactory(session_factory)
    router = WorkflowRouter([])
    return Application(
        engine=engine,
        session_factory=session_factory,
        unit_of_work_factory=unit_of_work_factory,
        router=router,
        ingestion=IngestOwnerMessageService(unit_of_work_factory, router),
        outbox=OutboxService(unit_of_work_factory),
    )
