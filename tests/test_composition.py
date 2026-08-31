from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from composition_fakes import (
    CustomerDeliveryFake,
    OwnerReplyFake,
    QuoteDraftingFake,
    ReportGenerationFake,
    TranscriptionFake,
    application_ports,
)
from gvas.composition import Application, build_application
from gvas.composition.dispatcher import UnknownCommandTypeError
from gvas.domain.completeness import (
    ChecklistItem,
    ChecklistItemKey,
    ChecklistKey,
    CompletenessChecklist,
)
from gvas.domain.enums import MediaKind, OutboxStatus
from gvas.domain.field_notes import FieldNoteCaseId, TranscriptionStatus
from gvas.domain.identifiers import BusinessId, MessageKey, OutboxCommandId
from gvas.domain.messages import (
    AttachmentPart,
    AttachmentReference,
    ChannelEndpointRef,
    ConversationRef,
    InboundOwnerMessage,
    NormalizedOwnerMessage,
    OutboundOwnerMessage,
    SenderRef,
    TextPart,
)
from gvas.domain.outbox import OutboxCommand, OutboxRecord
from gvas.infrastructure.completeness_models import FieldNoteFollowUpQuestion
from gvas.infrastructure.field_note_models import FieldNoteCase as FieldNoteCaseRow
from gvas.infrastructure.field_note_models import FieldNotePartRow
from gvas.infrastructure.models import Business, FieldNoteReport, OutboxMessage

NOW = datetime(2026, 3, 4, 5, 6, tzinfo=UTC)
CHECKLIST_KEY = ChecklistKey("field_notes")


class Clock:
    """Monotonic clock anchored to wall time so enqueued commands are claimable."""

    def __init__(self) -> None:
        self._now = datetime.now(UTC)

    def __call__(self) -> datetime:
        self._now += timedelta(seconds=1)
        return self._now


def inbound(
    business_id: BusinessId,
    text: str,
    *,
    message_key: str,
    conversation: str = "conversation",
    attachments: tuple[AttachmentPart, ...] = (),
) -> InboundOwnerMessage:
    normalized = NormalizedOwnerMessage(
        message_key=MessageKey(message_key),
        business_id=business_id,
        conversation_ref=ConversationRef(
            business_id=business_id, external_conversation_id=conversation
        ),
        sender=SenderRef(external_id="owner", role="owner"),
        received_at=NOW,
        parts=(TextPart(text=text), *attachments),
    )
    return InboundOwnerMessage(
        message=normalized,
        endpoint=ChannelEndpointRef(
            business_id=business_id,
            source_namespace="test",
            external_endpoint_id="endpoint",
        ),
        routing={"thread_root": conversation},
    )


def audio_part(locator: str) -> AttachmentPart:
    return AttachmentPart(
        attachment=AttachmentReference(
            attachment_id=uuid4(), media_kind=MediaKind.AUDIO, locator=locator
        )
    )


async def seed_business(
    session_factory: async_sessionmaker[AsyncSession], business_id: BusinessId
) -> None:
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
        await session.commit()


def checklist(business_id: BusinessId) -> CompletenessChecklist:
    return CompletenessChecklist(
        business_id=business_id,
        checklist_key=CHECKLIST_KEY,
        version=1,
        items=(
            ChecklistItem(
                key=ChecklistItemKey("site"),
                prompt="Which site was visited?",
                evidence_markers=("site:",),
            ),
            ChecklistItem(
                key=ChecklistItemKey("work"),
                prompt="What work was performed?",
                evidence_markers=("work:",),
            ),
        ),
    )


async def configure_checklist(application: Application, business_id: BusinessId) -> None:
    async with application.completeness_unit_of_work_factory() as unit_of_work:
        await unit_of_work.checklists.upsert(checklist(business_id))
        await unit_of_work.commit()


def build(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    owner_replies: OwnerReplyFake,
    quote_drafting: QuoteDraftingFake,
    quote_delivery: CustomerDeliveryFake,
    transcription: TranscriptionFake,
    report_generation: ReportGenerationFake,
) -> Application:
    return build_application(
        application_ports(
            owner_replies=owner_replies,
            quote_drafting=quote_drafting,
            quote_delivery=quote_delivery,
            transcription=transcription,
            report_generation=report_generation,
        ),
        session_factory=session_factory,
        checklist_key=CHECKLIST_KEY,
        now=Clock(),
    )


async def drain(application: Application) -> None:
    await application.worker.drain()


async def outbox_rows(
    session_factory: async_sessionmaker[AsyncSession], command_type: str
) -> list[OutboxMessage]:
    async with session_factory() as session:
        return list(
            (
                await session.scalars(
                    select(OutboxMessage).where(OutboxMessage.command_type == command_type)
                )
            ).all()
        )


@pytest.mark.asyncio
async def test_quote_path_delivers_once_under_replay(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    business_id = BusinessId(uuid4())
    await seed_business(session_factory, business_id)
    owner_replies = OwnerReplyFake()
    quote_drafting = QuoteDraftingFake()
    quote_delivery = CustomerDeliveryFake()
    application = build(
        session_factory,
        owner_replies=owner_replies,
        quote_drafting=quote_drafting,
        quote_delivery=quote_delivery,
        transcription=TranscriptionFake({}),
        report_generation=ReportGenerationFake(),
    )

    request = inbound(business_id, "quote: replace two gutters", message_key="quote-1")
    await application.ingest_service.ingest(request)
    await application.ingest_service.ingest(request)
    await drain(application)

    assert len(quote_drafting.requests) == 1
    approval = [
        message
        for _, message in owner_replies.sent
        if any(isinstance(part, TextPart) and "approve" in part.text for part in message.parts)
    ]
    assert len(approval) == 1

    approve = inbound(business_id, "approve", message_key="quote-1-approve")
    await application.ingest_service.ingest(approve)
    await application.ingest_service.ingest(approve)
    await drain(application)

    assert len(quote_delivery.requests) == 1
    assert quote_delivery.requests[0].idempotency_key.startswith("quote-delivery:")
    delivery_commands = await outbox_rows(session_factory, "customer_quote.deliver")
    assert len(delivery_commands) == 1
    assert delivery_commands[0].status == OutboxStatus.SUCCEEDED.value
    async with session_factory() as session:
        statuses = list(
            (await session.scalars(select(func.count()).select_from(FieldNoteCaseRow))).all()
        )
    assert statuses == [0]


@pytest.mark.asyncio
async def test_text_field_note_path_reaches_report_with_one_follow_up(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    business_id = BusinessId(uuid4())
    await seed_business(session_factory, business_id)
    owner_replies = OwnerReplyFake()
    reports = ReportGenerationFake()
    application = build(
        session_factory,
        owner_replies=owner_replies,
        quote_drafting=QuoteDraftingFake(),
        quote_delivery=CustomerDeliveryFake(),
        transcription=TranscriptionFake({}),
        report_generation=reports,
    )
    await configure_checklist(application, business_id)

    intake = inbound(business_id, "field notes: site: north tower", message_key="notes-1")
    await application.ingest_service.ingest(intake)
    await application.ingest_service.ingest(intake)
    await drain(application)

    async with session_factory() as session:
        questions = (
            await session.scalars(
                select(FieldNoteFollowUpQuestion).order_by(FieldNoteFollowUpQuestion.asked_at)
            )
        ).all()
    assert [question.item_key for question in questions] == ["work"]
    assert reports.requests == []

    answer_key = questions[0].correlation_id
    answer = inbound(business_id, "Replaced the gutter run", message_key="notes-1-answer")
    await application.ingest_service.ingest(answer)
    await application.ingest_service.ingest(answer)
    await drain(application)

    assert len(reports.requests) == 1
    snapshot = reports.requests[0].source
    assert snapshot.canonical_transcript.startswith("site: north tower")
    assert {answer.question_key for answer in snapshot.correlated_answers} == {"work"}
    assert {item.item_key for item in snapshot.checklist_evidence} == {"site", "work"}
    async with session_factory() as session:
        assert await session.scalar(select(func.count()).select_from(FieldNoteReport)) == 1
        assert (
            await session.scalar(select(func.count()).select_from(FieldNoteFollowUpQuestion)) == 1
        )
    assert answer_key is not None


@pytest.mark.asyncio
async def test_audio_field_note_transcribes_outside_the_claim_transaction(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    business_id = BusinessId(uuid4())
    await seed_business(session_factory, business_id)

    async def observe() -> str | None:
        async with session_factory() as session:
            part = await session.scalar(
                select(FieldNotePartRow).where(
                    FieldNotePartRow.transcription_status != TranscriptionStatus.NOT_REQUIRED.value
                )
            )
            return None if part is None else part.transcription_status

    transcription = TranscriptionFake({"audio-1": "site: north work: inspection"}, observe)
    application = build(
        session_factory,
        owner_replies=OwnerReplyFake(),
        quote_drafting=QuoteDraftingFake(),
        quote_delivery=CustomerDeliveryFake(),
        transcription=transcription,
        report_generation=ReportGenerationFake(),
    )
    await configure_checklist(application, business_id)

    intake = inbound(
        business_id,
        "field notes: voice memo",
        message_key="audio-notes",
        attachments=(audio_part("audio-1"),),
    )
    await application.ingest_service.ingest(intake)
    await drain(application)

    assert transcription.calls == ["audio-1"]
    assert transcription.observed_states == [TranscriptionStatus.IN_PROGRESS.value]
    transcribe_commands = await outbox_rows(session_factory, "field_note.transcribe")
    assert [row.status for row in transcribe_commands] == [OutboxStatus.SUCCEEDED.value]
    async with session_factory() as session:
        case = await session.scalar(select(FieldNoteCaseRow))
    assert case is not None
    transcript = await application.transcript_service.canonical_transcript(
        business_id, FieldNoteCaseId(case.id)
    )
    assert transcript.is_complete
    assert "site: north work: inspection" in transcript.text


@pytest.mark.asyncio
async def test_tenant_isolation_and_thread_root_correlation(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    first = BusinessId(uuid4())
    second = BusinessId(uuid4())
    await seed_business(session_factory, first)
    await seed_business(session_factory, second)
    owner_replies = OwnerReplyFake()
    application = build(
        session_factory,
        owner_replies=owner_replies,
        quote_drafting=QuoteDraftingFake(),
        quote_delivery=CustomerDeliveryFake(),
        transcription=TranscriptionFake({}),
        report_generation=ReportGenerationFake(),
    )
    await configure_checklist(application, first)
    await configure_checklist(application, second)

    await application.ingest_service.ingest(
        inbound(first, "field notes: site: north", message_key="a", conversation="thread-a")
    )
    await application.ingest_service.ingest(
        inbound(second, "field notes: site: south", message_key="b", conversation="thread-b")
    )
    await drain(application)

    async with session_factory() as session:
        cases = (await session.scalars(select(FieldNoteCaseRow))).all()
    assert {case.business_id for case in cases} == {first, second}
    conversations = {
        conversation_ref.external_conversation_id for conversation_ref, _ in owner_replies.sent
    }
    assert conversations == {"thread-a", "thread-b"}
    for conversation_ref, message in owner_replies.sent:
        assert isinstance(message, OutboundOwnerMessage)
        assert message.business_id == conversation_ref.business_id


@pytest.mark.asyncio
async def test_unknown_command_fails_explicitly_and_stays_retryable(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    business_id = BusinessId(uuid4())
    await seed_business(session_factory, business_id)
    application = build(
        session_factory,
        owner_replies=OwnerReplyFake(),
        quote_drafting=QuoteDraftingFake(),
        quote_delivery=CustomerDeliveryFake(),
        transcription=TranscriptionFake({}),
        report_generation=ReportGenerationFake(),
    )
    command = OutboxCommand(
        command_id=OutboxCommandId(uuid4()),
        business_id=business_id,
        command_type="unsupported.command",
        payload={},
        dedup_key="unsupported",
    )
    await application.outbox.enqueue(command)
    with pytest.raises(UnknownCommandTypeError):
        await application.dispatcher.dispatch(
            OutboxRecord(command=command, status=OutboxStatus.PENDING, available_at=NOW)
        )

    report = await application.worker.run_once()
    assert report.failed == 1
    rows = await outbox_rows(session_factory, "unsupported.command")
    assert [row.status for row in rows] == [OutboxStatus.FAILED.value]
    assert rows[0].attempts == 1
    assert rows[0].last_error is not None


@pytest.mark.asyncio
async def test_postgres_backed_quote_and_field_note_paths(
    postgres_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    business_id = BusinessId(uuid4())
    await seed_business(postgres_session_factory, business_id)
    quote_delivery = CustomerDeliveryFake()
    reports = ReportGenerationFake()
    application = build(
        postgres_session_factory,
        owner_replies=OwnerReplyFake(),
        quote_drafting=QuoteDraftingFake(),
        quote_delivery=quote_delivery,
        transcription=TranscriptionFake({"audio-pg": "site: north work: inspection"}),
        report_generation=reports,
    )
    await configure_checklist(application, business_id)

    await application.ingest_service.ingest(
        inbound(business_id, "quote: install a gate", message_key="pg-quote", conversation="pg-q")
    )
    await drain(application)
    await application.ingest_service.ingest(
        inbound(business_id, "approve", message_key="pg-quote-approve", conversation="pg-q")
    )
    await drain(application)
    assert len(quote_delivery.requests) == 1

    await application.ingest_service.ingest(
        inbound(
            business_id,
            "field notes: voice memo",
            message_key="pg-notes",
            conversation="pg-n",
            attachments=(audio_part("audio-pg"),),
        )
    )
    await drain(application)
    assert len(reports.requests) == 1

    async with postgres_session_factory() as session:
        pending = await session.scalar(
            select(func.count())
            .select_from(OutboxMessage)
            .where(OutboxMessage.status != OutboxStatus.SUCCEEDED.value)
        )
        quotes = (await session.scalars(select(func.count()).select_from(FieldNoteCaseRow))).all()
    assert pending == 0
    assert quotes == [1]
