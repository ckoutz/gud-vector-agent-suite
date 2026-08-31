from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from gvas.application.completeness import CompletenessStatus, FieldNoteCompletenessService
from gvas.application.completeness_review import MarkerCompletenessReviewer
from gvas.application.templates import (
    IndustryTemplateDefinition,
    PublishTemplateSetService,
    TemplateResolver,
)
from gvas.composition.snapshots import BuildFieldNoteCaseSnapshotService
from gvas.domain.completeness import ChecklistItem, ChecklistItemKey, ChecklistKey
from gvas.domain.field_notes import FieldNoteCaseId
from gvas.domain.identifiers import BusinessId, ConversationId, MessageId
from gvas.domain.reporting import ChecklistEvidence, ChecklistEvidenceRequest
from gvas.domain.templates import (
    BusinessTemplateProfile,
    IndustryKey,
    TemplateSetKey,
    TemplateSetRef,
    TemplateSetStatus,
    UnknownTemplateSetError,
)
from gvas.infrastructure.completeness_models import FieldNoteChecklist, FieldNoteReview
from gvas.infrastructure.industry_seeds import (
    UnknownIndustrySeedError,
    load_industry_definition,
    load_industry_definitions,
)
from gvas.infrastructure.models import Business, Conversation, InboundMessage, OwnerChannelEndpoint
from gvas.infrastructure.template_models import FieldNoteTemplateSet
from gvas.infrastructure.unit_of_work import SqlCompletenessUnitOfWorkFactory
from test_architecture_boundaries import find_violations

NOW = datetime(2025, 1, 1, tzinfo=UTC)
ENVIRONMENTAL = IndustryKey("environmental_testing")
LANDSCAPE = IndustryKey("landscape_construction")


class NoEvidence:
    async def attribute(self, request: ChecklistEvidenceRequest) -> tuple[ChecklistEvidence, ...]:
        return ()


def definition(
    industry_key: IndustryKey,
    *,
    version: int = 1,
    report_template_version: int = 1,
    item_key: str = "site",
) -> IndustryTemplateDefinition:
    return IndustryTemplateDefinition(
        industry_key=industry_key,
        template_set_key=TemplateSetKey(f"{industry_key}_notes"),
        checklist_key=ChecklistKey(f"{industry_key}_notes"),
        version=version,
        items=(
            ChecklistItem(
                key=ChecklistItemKey(item_key),
                prompt=f"What about {item_key}?",
                evidence_markers=(f"{item_key}:",),
            ),
        ),
        report_template_key=f"{industry_key}_report",
        report_template_version=report_template_version,
    )


async def seed_business(
    session_factory: async_sessionmaker[AsyncSession], business_id: BusinessId
) -> tuple[ConversationId, MessageId]:
    endpoint_id = uuid4()
    conversation_id = ConversationId(uuid4())
    inbound_id = MessageId(uuid4())
    async with session_factory() as session:
        session.add(
            Business(
                id=business_id,
                slug=f"business-{business_id}",
                name="Business",
                created_at=NOW,
                updated_at=NOW,
            )
        )
        session.add(
            OwnerChannelEndpoint(
                id=endpoint_id,
                business_id=business_id,
                source_namespace="test",
                external_endpoint_id=f"endpoint-{business_id}",
                routing={},
            )
        )
        session.add(
            Conversation(
                id=conversation_id,
                business_id=business_id,
                endpoint_id=endpoint_id,
                external_conversation_id=f"conversation-{business_id}",
                routing={},
            )
        )
        session.add(
            InboundMessage(
                id=inbound_id,
                business_id=business_id,
                endpoint_id=endpoint_id,
                conversation_id=conversation_id,
                message_key="transcript",
                sender_external_id="owner",
                sender_role="owner",
                received_at=NOW,
                parts=[{"kind": "text", "text": "field notes"}],
                reply_to=None,
                routing={},
            )
        )
        await session.commit()
    return conversation_id, inbound_id


def publisher(session_factory: async_sessionmaker[AsyncSession]) -> PublishTemplateSetService:
    return PublishTemplateSetService(SqlCompletenessUnitOfWorkFactory(session_factory))


def resolver(session_factory: async_sessionmaker[AsyncSession]) -> TemplateResolver:
    return TemplateResolver(SqlCompletenessUnitOfWorkFactory(session_factory))


def completeness(session_factory: async_sessionmaker[AsyncSession]) -> FieldNoteCompletenessService:
    return FieldNoteCompletenessService(
        SqlCompletenessUnitOfWorkFactory(session_factory),
        MarkerCompletenessReviewer(),
        resolver(session_factory),
    )


async def review_row(session_factory: async_sessionmaker[AsyncSession]) -> FieldNoteReview:
    async with session_factory() as session:
        row = await session.scalar(select(FieldNoteReview))
    assert row is not None
    return row


@pytest.mark.asyncio
async def test_seeding_twice_is_a_no_op(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    business_id = BusinessId(uuid4())
    await seed_business(session_factory, business_id)
    first = await publisher(session_factory).seed_industry(business_id, definition(ENVIRONMENTAL))
    second = await publisher(session_factory).seed_industry(business_id, definition(ENVIRONMENTAL))

    assert first == second
    async with session_factory() as session:
        assert await session.scalar(select(func.count(FieldNoteTemplateSet.id))) == 1
        assert await session.scalar(select(func.count(FieldNoteChecklist.id))) == 1


@pytest.mark.asyncio
async def test_businesses_in_different_industries_resolve_different_checklists(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    first = BusinessId(uuid4())
    second = BusinessId(uuid4())
    first_conversation, _ = await seed_business(session_factory, first)
    second_conversation, _ = await seed_business(session_factory, second)
    await publisher(session_factory).seed_industry(first, definition(ENVIRONMENTAL))
    await publisher(session_factory).seed_industry(second, definition(LANDSCAPE, item_key="crew"))

    resolution = resolver(session_factory)
    first_set = await resolution.load(
        await resolution.resolve_for_new_case(first, first_conversation)
    )
    second_set = await resolution.load(
        await resolution.resolve_for_new_case(second, second_conversation)
    )

    assert first_set.industry_key == ENVIRONMENTAL
    assert second_set.industry_key == LANDSCAPE
    assert first_set.checklist_key != second_set.checklist_key


@pytest.mark.asyncio
async def test_business_without_custom_set_falls_back_to_industry_default(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    business_id = BusinessId(uuid4())
    conversation_id, _ = await seed_business(session_factory, business_id)
    seeded = definition(ENVIRONMENTAL)
    await publisher(session_factory).seed_industry(business_id, seeded)
    unit_of_work_factory = SqlCompletenessUnitOfWorkFactory(session_factory)
    async with unit_of_work_factory() as unit_of_work:
        await unit_of_work.business_template_profiles.upsert(
            BusinessTemplateProfile(
                business_id=business_id,
                industry_key=ENVIRONMENTAL,
                template_set_key=TemplateSetKey("custom_unpublished"),
                default_template_set_key=seeded.template_set_key,
            )
        )
        await unit_of_work.commit()

    resolved = await resolver(session_factory).resolve_for_new_case(business_id, conversation_id)
    assert resolved.template_set_key == seeded.template_set_key


@pytest.mark.asyncio
async def test_resolution_is_tenant_scoped(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    owner = BusinessId(uuid4())
    intruder = BusinessId(uuid4())
    await seed_business(session_factory, owner)
    intruder_conversation, _ = await seed_business(session_factory, intruder)
    seeded = await publisher(session_factory).seed_industry(owner, definition(ENVIRONMENTAL))

    resolution = resolver(session_factory)
    with pytest.raises(UnknownTemplateSetError):
        await resolution.resolve_for_new_case(intruder, intruder_conversation)
    with pytest.raises(UnknownTemplateSetError):
        await resolution.load(
            TemplateSetRef(
                business_id=intruder,
                template_set_key=seeded.template_set_key,
                version=seeded.version,
            )
        )


@pytest.mark.asyncio
async def test_publishing_a_new_version_does_not_change_an_in_flight_case(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    business_id = BusinessId(uuid4())
    conversation_id, inbound_id = await seed_business(session_factory, business_id)
    await publisher(session_factory).seed_industry(business_id, definition(ENVIRONMENTAL))
    service = completeness(session_factory)
    started = await service.start_review(
        business_id,
        conversation_id,
        f"conversation-{business_id}",
        inbound_id,
        "no markers here",
    )
    assert started.status is CompletenessStatus.QUESTIONS_SENT

    await publisher(session_factory).seed_industry(
        business_id, definition(ENVIRONMENTAL, version=2, item_key="site")
    )
    await service.start_review(
        business_id,
        conversation_id,
        f"conversation-{business_id}",
        inbound_id,
        "no markers here",
    )

    row = await review_row(session_factory)
    assert (row.template_set_version, row.checklist_version) == (1, 1)
    active = await resolver(session_factory).resolve_for_new_case(business_id, conversation_id)
    assert active.version == 2


@pytest.mark.asyncio
async def test_completed_case_snapshot_uses_its_original_template_set(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    business_id = BusinessId(uuid4())
    conversation_id, inbound_id = await seed_business(session_factory, business_id)
    await publisher(session_factory).seed_industry(business_id, definition(ENVIRONMENTAL))
    completed = await completeness(session_factory).start_review(
        business_id,
        conversation_id,
        f"conversation-{business_id}",
        inbound_id,
        "site: north tower",
    )
    assert completed.status is CompletenessStatus.COMPLETE
    assert completed.review_id is not None
    await publisher(session_factory).seed_industry(
        business_id, definition(ENVIRONMENTAL, version=2, report_template_version=7)
    )

    snapshot = await BuildFieldNoteCaseSnapshotService(
        SqlCompletenessUnitOfWorkFactory(session_factory),
        NoEvidence(),
        resolver(session_factory),
    ).build(
        business_id,
        FieldNoteCaseId(uuid4()),
        completed.review_id,
        completed_at=NOW,
    )

    assert snapshot.report_template_key == f"{ENVIRONMENTAL}_report"
    assert snapshot.report_template_version == 1


@pytest.mark.asyncio
async def test_publishing_a_new_version_deprecates_the_previous_one(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    business_id = BusinessId(uuid4())
    await seed_business(session_factory, business_id)
    first = await publisher(session_factory).seed_industry(business_id, definition(ENVIRONMENTAL))
    await publisher(session_factory).seed_industry(
        business_id, definition(ENVIRONMENTAL, version=2)
    )

    previous = await resolver(session_factory).load(first)
    assert previous.status is TemplateSetStatus.DEPRECATED


def test_checked_in_industry_seeds_cover_more_than_inspection_work() -> None:
    definitions = load_industry_definitions()
    assert {ENVIRONMENTAL, LANDSCAPE} <= set(definitions)
    assert definitions[ENVIRONMENTAL].checklist_key != definitions[LANDSCAPE].checklist_key
    assert load_industry_definition(ENVIRONMENTAL) == definitions[ENVIRONMENTAL]
    with pytest.raises(UnknownIndustrySeedError):
        load_industry_definition(IndustryKey("does_not_exist"))


def test_template_modules_respect_layer_boundaries() -> None:
    root = Path(__file__).parents[1] / "src" / "gvas"
    for layer, names in {
        "domain": ("templates.py", "template_repositories.py"),
        "application": ("templates.py",),
    }.items():
        for name in names:
            path = root / layer / name
            assert not find_violations(layer, path, path.read_text())
