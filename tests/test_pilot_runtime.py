"""Deterministic pilot runtime: report delivery, failure notices, bootstrap."""

from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from composition_fakes import (
    CustomerDeliveryFake,
    OwnerReplyFake,
    QuoteDraftingFake,
    TranscriptionFake,
)
from gvas.application.checklist_evidence import MarkerChecklistEvidenceAttributor
from gvas.application.completeness_review import MarkerCompletenessReviewer
from gvas.application.deterministic_report import DeterministicReportGenerator
from gvas.composition import Application, ApplicationPorts, build_application
from gvas.composition.dispatcher import OutboxWorker
from gvas.domain.enums import OutboxStatus
from gvas.domain.identifiers import BusinessId
from gvas.domain.messages import AudioReference, TextPart, TranscriptResult
from gvas.domain.ports import TranscriptionPort
from gvas.domain.templates import IndustryKey
from gvas.infrastructure.models import Business, OutboxMessage
from gvas.interfaces.bootstrap import BootstrapRequest, run_bootstrap
from test_composition import (
    Clock,
    audio_part,
    configure_checklist,
    inbound,
    seed_business,
)


class FailingTranscription:
    """Stands in for a provider outage that outlives every retry."""

    async def transcribe(self, audio: AudioReference) -> TranscriptResult:
        raise RuntimeError("provider token 'xoxb-secret' rejected")


def deterministic_ports(
    owner_replies: OwnerReplyFake, transcription: TranscriptionPort
) -> ApplicationPorts:
    """The pilot wiring: deterministic review and reporting, no inference model."""

    return ApplicationPorts(
        owner_replies=owner_replies,
        quote_drafting=QuoteDraftingFake(),
        quote_delivery=CustomerDeliveryFake(),
        transcription=transcription,
        completeness_review=MarkerCompletenessReviewer(),
        checklist_evidence=MarkerChecklistEvidenceAttributor(),
        report_generation=DeterministicReportGenerator(),
    )


def build(
    session_factory: async_sessionmaker[AsyncSession],
    owner_replies: OwnerReplyFake,
    transcription: TranscriptionPort,
) -> Application:
    return build_application(
        deterministic_ports(owner_replies, transcription),
        session_factory=session_factory,
        now=Clock(),
    )


def immediate_worker(application: Application) -> OutboxWorker:
    """Retries without delay so a test can reach the terminal state."""

    return OutboxWorker(
        application.outbox,
        application.dispatcher,
        now=Clock(),
        retry_in=timedelta(seconds=0),
        failure_notices=application.failure_notice_service,
    )


def texts_of(owner_replies: OwnerReplyFake, prefix: str) -> list[str]:
    return [
        part.text
        for _, message in owner_replies.sent
        for part in message.parts
        if isinstance(part, TextPart) and part.text.startswith(prefix)
    ]


@pytest.mark.asyncio
async def test_completed_report_is_delivered_to_the_case_thread_once(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    business_id = BusinessId(uuid4())
    await seed_business(session_factory, business_id)
    owner_replies = OwnerReplyFake()
    application = build(session_factory, owner_replies, TranscriptionFake({}))
    await configure_checklist(application, business_id)

    await application.ingest_service.ingest(
        inbound(business_id, "field notes: site: north tower", message_key="notes-1")
    )
    await application.worker.drain()
    await application.ingest_service.ingest(
        inbound(business_id, "Replaced the gutter run", message_key="notes-1-answer")
    )
    await application.worker.drain()
    await application.worker.drain()

    reports = texts_of(owner_replies, "Field Notes Report")
    assert len(reports) == 1
    assert reports[0].splitlines()[:4] == [
        "Field Notes Report",
        "Report version 1",
        "",
        "Site and Work",
    ]
    assert "Which site was visited? — observed." in reports[0]
    assert "Replaced the gutter run" in reports[0]
    assert {ref.external_conversation_id for ref, _ in owner_replies.sent} == {"conversation"}


@pytest.mark.asyncio
async def test_dead_command_notifies_the_owner_once_without_leaking_provider_detail(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    business_id = BusinessId(uuid4())
    await seed_business(session_factory, business_id)
    owner_replies = OwnerReplyFake()
    application = build(session_factory, owner_replies, FailingTranscription())
    await configure_checklist(application, business_id)
    worker = immediate_worker(application)

    await application.ingest_service.ingest(
        inbound(
            business_id,
            "field notes: site visit",
            message_key="notes-audio",
            attachments=(audio_part("channel-file:F1"),),
        )
    )
    await worker.drain()
    await worker.drain()

    async with session_factory() as session:
        dead = await session.scalar(
            select(func.count())
            .select_from(OutboxMessage)
            .where(OutboxMessage.status == OutboxStatus.DEAD.value)
        )
    assert dead == 1

    notices = texts_of(owner_replies, "A voice note could not be transcribed.")
    assert len(notices) == 1
    assert "xoxb" not in notices[0]
    assert "Send the request again in this thread." in notices[0]


@pytest.mark.asyncio
async def test_bootstrap_is_idempotent_and_updates_identity_in_place(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    business_id = BusinessId(uuid4())
    request = BootstrapRequest(
        business_id=business_id,
        slug="protech",
        name="ProTech",
        industry=IndustryKey("environmental_testing"),
    )

    first = await run_bootstrap(request, session_factory)
    second = await run_bootstrap(request, session_factory)
    renamed = await run_bootstrap(
        BootstrapRequest(
            business_id=business_id,
            slug="protech",
            name="ProTech Environmental",
            industry=IndustryKey("environmental_testing"),
        ),
        session_factory,
    )

    assert first.template_set == second.template_set == renamed.template_set
    async with session_factory() as session:
        rows = list((await session.scalars(select(Business))).all())
    assert [(row.slug, row.name) for row in rows] == [("protech", "ProTech Environmental")]
