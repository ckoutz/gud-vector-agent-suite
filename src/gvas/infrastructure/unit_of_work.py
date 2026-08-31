from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from gvas.domain.completeness_repositories import (
    ChecklistDefinitionRepository,
    CompletenessUnitOfWork,
    FieldNoteReviewRepository,
    FollowUpQuestionRepository,
)
from gvas.domain.quotes import QuoteRepository
from gvas.domain.repositories import (
    BusinessRepository,
    ConversationRepository,
    InboundMessageRepository,
    OutboundMessageRepository,
    OutboxRepository,
    OwnerChannelEndpointRepository,
    UnitOfWork,
    WorkflowRunRepository,
)
from gvas.domain.template_repositories import (
    BusinessTemplateProfileRepository,
    TemplateSetRepository,
)
from gvas.infrastructure.completeness_repositories import (
    SqlChecklistDefinitionRepository,
    SqlFieldNoteReviewRepository,
    SqlFollowUpQuestionRepository,
)
from gvas.infrastructure.repositories import (
    SqlBusinessRepository,
    SqlConversationRepository,
    SqlInboundMessageRepository,
    SqlOutboundMessageRepository,
    SqlOutboxRepository,
    SqlOwnerChannelEndpointRepository,
    SqlQuoteRepository,
    SqlWorkflowRunRepository,
)
from gvas.infrastructure.template_repositories import (
    SqlBusinessTemplateProfileRepository,
    SqlTemplateSetRepository,
)


class SqlUnitOfWork:
    businesses: BusinessRepository
    owner_channel_endpoints: OwnerChannelEndpointRepository
    conversations: ConversationRepository
    inbound_messages: InboundMessageRepository
    outbound_messages: OutboundMessageRepository
    workflow_runs: WorkflowRunRepository
    outbox: OutboxRepository
    quotes: QuoteRepository

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory
        self._session: AsyncSession | None = None

    async def __aenter__(self) -> "SqlUnitOfWork":
        self._session = self._session_factory()
        self.businesses = SqlBusinessRepository(self._session)
        self.owner_channel_endpoints = SqlOwnerChannelEndpointRepository(self._session)
        self.conversations = SqlConversationRepository(self._session)
        self.inbound_messages = SqlInboundMessageRepository(self._session)
        self.outbound_messages = SqlOutboundMessageRepository(self._session)
        self.workflow_runs = SqlWorkflowRunRepository(self._session)
        self.outbox = SqlOutboxRepository(self._session)
        self.quotes = SqlQuoteRepository(self._session)
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
        return SqlUnitOfWork(self._session_factory)


class SqlCompletenessUnitOfWork:
    checklists: ChecklistDefinitionRepository
    template_sets: TemplateSetRepository
    business_template_profiles: BusinessTemplateProfileRepository
    field_note_reviews: FieldNoteReviewRepository
    follow_up_questions: FollowUpQuestionRepository
    outbound_messages: OutboundMessageRepository
    outbox: OutboxRepository

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory
        self._session: AsyncSession | None = None

    async def __aenter__(self) -> "SqlCompletenessUnitOfWork":
        self._session = self._session_factory()
        self.checklists = SqlChecklistDefinitionRepository(self._session)
        self.template_sets = SqlTemplateSetRepository(self._session)
        self.business_template_profiles = SqlBusinessTemplateProfileRepository(self._session)
        self.field_note_reviews = SqlFieldNoteReviewRepository(self._session)
        self.follow_up_questions = SqlFollowUpQuestionRepository(self._session)
        self.outbound_messages = SqlOutboundMessageRepository(self._session)
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


class SqlCompletenessUnitOfWorkFactory:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    def __call__(self) -> CompletenessUnitOfWork:
        return SqlCompletenessUnitOfWork(self._session_factory)
