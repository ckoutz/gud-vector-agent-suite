from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from gvas.application.completeness import FieldNoteCompletenessService
from gvas.application.docx_report import DocxReportRenderer
from gvas.application.field_note_transcription import (
    FieldNoteTranscriptService,
    TranscribeFieldNoteAudioService,
)
from gvas.application.field_notes import (
    CloseFieldNoteCaseHandler,
    FieldNoteIntakeHandler,
    FieldNoteIntentContribution,
)
from gvas.application.ingestion import IngestOwnerMessageService
from gvas.application.outbox_service import OutboxService
from gvas.application.owner_reply_delivery import DeliverOwnerReplyService
from gvas.application.plan_custody import (
    CopyPlanSetIntoCustodyService,
    RegisterPlanSetUploadService,
)
from gvas.application.processing import ProcessOwnerMessageService
from gvas.application.quotes import (
    DeliverApprovedQuoteService,
    QuoteIntentSelector,
    QuoteWorkflowHandler,
)
from gvas.application.report_approval import ApproveFieldNoteReportHandler
from gvas.application.report_generation import GenerateFieldNotesReportService
from gvas.application.templates import PublishTemplateSetService, TemplateResolver
from gvas.application.unmatched_messages import UnmatchedMessageHandler
from gvas.application.workflow_conflicts import WorkflowConflictHandler
from gvas.composition.dispatcher import OutboxCommandDispatcher, OutboxWorker
from gvas.composition.failure_notices import NotifyExhaustedCommandService
from gvas.composition.field_note_workflow import FieldNoteWorkflowHandler
from gvas.composition.intents import DeterministicIntentResolver
from gvas.composition.report_delivery import DeliverFieldNotesReportService
from gvas.composition.report_publication import (
    PublishFieldNotesReportService,
    ReportArtifactAccess,
)
from gvas.composition.review import CoordinateFieldNoteReviewService
from gvas.composition.snapshots import BuildFieldNoteCaseSnapshotService
from gvas.config import Settings
from gvas.domain.completeness import CompletenessReviewPort
from gvas.domain.ports import (
    AttachmentAccessPort,
    ChecklistEvidencePort,
    CustomerQuoteDeliveryPort,
    IntentResolutionPort,
    ObjectStoragePort,
    OwnerReplyPort,
    QuoteDraftingPort,
    TranscriptionPort,
)
from gvas.domain.reporting import ReportGenerationPort
from gvas.domain.workflows import WorkflowRouter
from gvas.infrastructure.db import create_engine, create_session_factory
from gvas.infrastructure.field_note_repositories import SqlFieldNoteUnitOfWorkFactory
from gvas.infrastructure.plan_repositories import SqlPlanCustodyUnitOfWorkFactory
from gvas.infrastructure.reporting_unit_of_work import SqlReportUnitOfWorkFactory
from gvas.infrastructure.unit_of_work import (
    SqlCompletenessUnitOfWorkFactory,
    SqlUnitOfWorkFactory,
)


@dataclass(frozen=True)
class ApplicationPorts:
    """Provider-neutral ports the composition root requires.

    Production adapters are injected here; the repository ships no provider
    implementation, so tests and local runs supply deterministic fakes.
    """

    owner_replies: OwnerReplyPort
    quote_drafting: QuoteDraftingPort
    quote_delivery: CustomerQuoteDeliveryPort
    transcription: TranscriptionPort
    completeness_review: CompletenessReviewPort
    checklist_evidence: ChecklistEvidencePort
    report_generation: ReportGenerationPort
    source_attachments: AttachmentAccessPort | None = None
    object_storage: ObjectStoragePort | None = None


@dataclass(frozen=True)
class Application:
    engine: AsyncEngine | None
    session_factory: async_sessionmaker[AsyncSession]
    unit_of_work_factory: SqlUnitOfWorkFactory
    field_note_unit_of_work_factory: SqlFieldNoteUnitOfWorkFactory
    completeness_unit_of_work_factory: SqlCompletenessUnitOfWorkFactory
    report_unit_of_work_factory: SqlReportUnitOfWorkFactory
    plan_custody_unit_of_work_factory: SqlPlanCustodyUnitOfWorkFactory
    router: WorkflowRouter
    intent_resolver: IntentResolutionPort
    ingest_service: IngestOwnerMessageService
    processing_service: ProcessOwnerMessageService
    owner_reply_service: DeliverOwnerReplyService
    quote_delivery_service: DeliverApprovedQuoteService
    transcription_service: TranscribeFieldNoteAudioService
    transcript_service: FieldNoteTranscriptService
    template_resolver: TemplateResolver
    template_publisher: PublishTemplateSetService
    completeness_service: FieldNoteCompletenessService
    review_service: CoordinateFieldNoteReviewService
    snapshot_service: BuildFieldNoteCaseSnapshotService
    report_service: GenerateFieldNotesReportService
    report_delivery_service: DeliverFieldNotesReportService
    report_publication_service: PublishFieldNotesReportService
    report_artifacts: ReportArtifactAccess
    failure_notice_service: NotifyExhaustedCommandService
    plan_set_upload_service: RegisterPlanSetUploadService
    plan_custody_service: CopyPlanSetIntoCustodyService | None
    outbox: OutboxService
    dispatcher: OutboxCommandDispatcher
    worker: OutboxWorker


def _utcnow() -> datetime:
    return datetime.now(UTC)


def build_application(
    ports: ApplicationPorts,
    settings: Settings | None = None,
    *,
    intent_resolver: IntentResolutionPort | None = None,
    now: Callable[[], datetime] = _utcnow,
    lease_ttl: timedelta = timedelta(minutes=5),
    engine: AsyncEngine | None = None,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> Application:
    resolved_engine = engine
    if resolved_engine is None and session_factory is None:
        resolved_engine = create_engine((settings or Settings()).database_url)
    if session_factory is not None:
        sessions = session_factory
    elif resolved_engine is not None:
        sessions = create_session_factory(resolved_engine)
    else:
        raise ValueError("an engine or session factory is required")

    unit_of_work_factory = SqlUnitOfWorkFactory(sessions)
    field_note_unit_of_work_factory = SqlFieldNoteUnitOfWorkFactory(sessions)
    completeness_unit_of_work_factory = SqlCompletenessUnitOfWorkFactory(sessions)
    report_unit_of_work_factory = SqlReportUnitOfWorkFactory(sessions)
    plan_custody_unit_of_work_factory = SqlPlanCustodyUnitOfWorkFactory(sessions)

    intake = FieldNoteIntakeHandler(field_note_unit_of_work_factory, now=now)
    closure = CloseFieldNoteCaseHandler(field_note_unit_of_work_factory, now=now)
    approval = ApproveFieldNoteReportHandler(
        field_note_unit_of_work_factory, report_unit_of_work_factory
    )
    plan_set_uploads = RegisterPlanSetUploadService(plan_custody_unit_of_work_factory)
    plan_custody = (
        CopyPlanSetIntoCustodyService(
            plan_custody_unit_of_work_factory, ports.source_attachments, ports.object_storage
        )
        if ports.source_attachments is not None and ports.object_storage is not None
        else None
    )
    field_note_handler = FieldNoteWorkflowHandler(
        intake,
        closure,
        approval,
        field_note_unit_of_work_factory,
        completeness_unit_of_work_factory,
        now=now,
        plan_set_uploads=plan_set_uploads if plan_custody is not None else None,
    )
    quote_handler = QuoteWorkflowHandler(unit_of_work_factory, ports.quote_drafting)
    router = WorkflowRouter(
        [quote_handler, field_note_handler, WorkflowConflictHandler(), UnmatchedMessageHandler()]
    )

    resolver = intent_resolver or DeterministicIntentResolver(
        FieldNoteIntentContribution(field_note_unit_of_work_factory),
        QuoteIntentSelector(),
        field_note_unit_of_work_factory,
        unit_of_work_factory,
    )

    transcripts = FieldNoteTranscriptService(field_note_unit_of_work_factory)
    template_resolver = TemplateResolver(completeness_unit_of_work_factory)
    template_publisher = PublishTemplateSetService(completeness_unit_of_work_factory)
    completeness = FieldNoteCompletenessService(
        completeness_unit_of_work_factory, ports.completeness_review, template_resolver
    )
    review = CoordinateFieldNoteReviewService(
        field_note_unit_of_work_factory,
        unit_of_work_factory,
        transcripts,
        completeness,
    )
    snapshots = BuildFieldNoteCaseSnapshotService(
        completeness_unit_of_work_factory, ports.checklist_evidence, template_resolver
    )
    reports = GenerateFieldNotesReportService(
        report_unit_of_work_factory, ports.report_generation, template_resolver
    )
    processing = ProcessOwnerMessageService(unit_of_work_factory, router, resolver)
    owner_replies = DeliverOwnerReplyService(unit_of_work_factory, ports.owner_replies)
    quote_delivery = DeliverApprovedQuoteService(unit_of_work_factory, ports.quote_delivery)
    transcription = TranscribeFieldNoteAudioService(
        field_note_unit_of_work_factory, ports.transcription
    )
    report_delivery = DeliverFieldNotesReportService(
        field_note_unit_of_work_factory, unit_of_work_factory
    )
    report_renderer = DocxReportRenderer()
    report_publication = PublishFieldNotesReportService(
        report_renderer,
        report_unit_of_work_factory,
        field_note_unit_of_work_factory,
        unit_of_work_factory,
        ports.object_storage,
    )
    report_artifacts = ReportArtifactAccess(report_renderer, report_unit_of_work_factory)
    failure_notices = NotifyExhaustedCommandService(
        unit_of_work_factory, field_note_unit_of_work_factory
    )
    outbox = OutboxService(unit_of_work_factory)
    dispatcher = OutboxCommandDispatcher(
        processing=processing,
        owner_replies=owner_replies,
        quote_delivery=quote_delivery,
        transcription=transcription,
        review=review,
        snapshots=snapshots,
        reports=reports,
        report_delivery=report_delivery,
        report_publication=report_publication,
        outbox=outbox,
        now=now,
        plan_custody=plan_custody,
        lease_ttl=lease_ttl,
    )
    return Application(
        engine=resolved_engine,
        session_factory=sessions,
        unit_of_work_factory=unit_of_work_factory,
        field_note_unit_of_work_factory=field_note_unit_of_work_factory,
        completeness_unit_of_work_factory=completeness_unit_of_work_factory,
        report_unit_of_work_factory=report_unit_of_work_factory,
        plan_custody_unit_of_work_factory=plan_custody_unit_of_work_factory,
        router=router,
        intent_resolver=resolver,
        ingest_service=IngestOwnerMessageService(unit_of_work_factory),
        processing_service=processing,
        owner_reply_service=owner_replies,
        quote_delivery_service=quote_delivery,
        transcription_service=transcription,
        transcript_service=transcripts,
        template_resolver=template_resolver,
        template_publisher=template_publisher,
        completeness_service=completeness,
        review_service=review,
        snapshot_service=snapshots,
        report_service=reports,
        report_delivery_service=report_delivery,
        report_publication_service=report_publication,
        report_artifacts=report_artifacts,
        failure_notice_service=failure_notices,
        plan_set_upload_service=plan_set_uploads,
        plan_custody_service=plan_custody,
        outbox=outbox,
        dispatcher=dispatcher,
        worker=OutboxWorker(
            outbox,
            dispatcher,
            now=now,
            lease_ttl=lease_ttl,
            failure_notices=failure_notices,
        ),
    )
