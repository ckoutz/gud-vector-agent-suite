"""Whole command chains through the dispatcher with the real provider adapters.

The adapters are exercised over ``httpx.MockTransport`` rather than replaced by
port fakes, so these cover what isolated adapter tests cannot: that the payload
the workflow builds is one the adapter accepts, and that what comes back reaches
the owner.
"""

from uuid import uuid4

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from composition_fakes import CustomerDeliveryFake, OwnerReplyFake, TranscriptionFake
from gvas.application.checklist_evidence import MarkerChecklistEvidenceAttributor
from gvas.application.completeness_review import MarkerCompletenessReviewer
from gvas.application.deterministic_report import DeterministicReportGenerator
from gvas.composition import Application, ApplicationPorts, build_application
from gvas.config import OpenAISettings, ResendSettings
from gvas.domain.identifiers import BusinessId
from gvas.domain.messages import TextPart
from gvas.domain.ports import CustomerQuoteDeliveryPort, TranscriptionPort
from gvas.infrastructure.openai_transcription import OpenAITranscriber
from gvas.infrastructure.quote_drafting import DeterministicQuoteDrafter
from gvas.infrastructure.resend import ResendQuoteDeliveryAdapter
from gvas.infrastructure.slack.api import SlackFileAttachmentAccess
from gvas.infrastructure.slack.config import SlackSettings
from test_composition import (
    Clock,
    audio_part,
    configure_checklist,
    inbound,
    seed_business,
)

QUOTE_REQUEST = "\n".join(
    (
        "quote:",
        "customer: person@example.com",
        "currency: USD",
        "item: 2 | Air sampling | 125.00",
        "item: 1 | Report | 200.00",
    )
)
DICTATED_NOTE = "site: 123 Main Street. work: replaced the gutter run."


def ports(
    owner_replies: OwnerReplyFake,
    *,
    transcription: TranscriptionPort,
    quote_delivery: CustomerQuoteDeliveryPort,
) -> ApplicationPorts:
    return ApplicationPorts(
        owner_replies=owner_replies,
        quote_drafting=DeterministicQuoteDrafter(),
        quote_delivery=quote_delivery,
        transcription=transcription,
        completeness_review=MarkerCompletenessReviewer(),
        checklist_evidence=MarkerChecklistEvidenceAttributor(),
        report_generation=DeterministicReportGenerator(),
    )


def build(
    session_factory: async_sessionmaker[AsyncSession],
    owner_replies: OwnerReplyFake,
    *,
    transcription: TranscriptionPort,
    quote_delivery: CustomerQuoteDeliveryPort,
) -> Application:
    return build_application(
        ports(owner_replies, transcription=transcription, quote_delivery=quote_delivery),
        session_factory=session_factory,
        now=Clock(),
    )


def owner_texts(owner_replies: OwnerReplyFake) -> list[str]:
    return [
        part.text
        for _, message in owner_replies.sent
        for part in message.parts
        if isinstance(part, TextPart)
    ]


def slack_settings() -> SlackSettings:
    return SlackSettings(
        bot_token="xoxb-test",  # noqa: S106
        signing_secret="secret",  # noqa: S106
    )


def slack_audio_transport(audio: bytes, transcript: str) -> httpx.MockTransport:
    """Serves Slack file metadata, the private download, and the transcription."""

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/files.info"):
            return httpx.Response(
                200,
                json={
                    "ok": True,
                    "file": {
                        "id": request.url.params["file"],
                        "name": "note.m4a",
                        "mimetype": "audio/mp4",
                        "size": len(audio),
                        "url_private_download": "https://files.slack.com/note.m4a",
                    },
                },
            )
        if request.url.host == "files.slack.com":
            return httpx.Response(200, content=audio)
        if request.url.path.endswith("/audio/transcriptions"):
            assert audio in request.read()
            return httpx.Response(200, json={"text": transcript})
        raise AssertionError(f"unexpected request to {request.url}")

    return httpx.MockTransport(handle)


@pytest.mark.asyncio
async def test_approved_quote_reaches_resend_with_the_parsed_amounts(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    business_id = BusinessId(uuid4())
    await seed_business(session_factory, business_id)
    emails: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        emails.append(request)
        return httpx.Response(200, json={"id": "email-1"})

    owner_replies = OwnerReplyFake()
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        settings = ResendSettings(api_key="re-test", from_address="quotes@gudvector.com")
        application = build(
            session_factory,
            owner_replies,
            transcription=TranscriptionFake({}),
            quote_delivery=ResendQuoteDeliveryAdapter(settings, client),
        )
        await application.ingest_service.ingest(
            inbound(business_id, QUOTE_REQUEST, message_key="quote-1")
        )
        await application.worker.drain()
        await application.ingest_service.ingest(
            inbound(business_id, "approve", message_key="quote-1-approve")
        )
        await application.worker.drain()

    assert len(emails) == 1
    body = emails[0].read().decode()
    assert "person@example.com" in body
    assert "250.00" in body
    assert "https://gudvector.com/portal/login" in body
    assert any("Quote approved" in text for text in owner_texts(owner_replies))


@pytest.mark.asyncio
async def test_dictated_note_is_transcribed_and_reported_with_its_own_words(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    business_id = BusinessId(uuid4())
    await seed_business(session_factory, business_id)
    owner_replies = OwnerReplyFake()
    transport = slack_audio_transport(b"audio-bytes", DICTATED_NOTE)

    async with httpx.AsyncClient(transport=transport) as client:
        attachments = SlackFileAttachmentAccess(slack_settings(), client)
        application = build(
            session_factory,
            owner_replies,
            transcription=OpenAITranscriber(OpenAISettings(api_key="sk-test"), client, attachments),
            quote_delivery=CustomerDeliveryFake(),
        )
        await configure_checklist(application, business_id)
        await application.ingest_service.ingest(
            inbound(
                business_id,
                "field notes:",
                message_key="notes-audio",
                attachments=(audio_part("slack-file:F1"),),
            )
        )
        for _ in range(4):
            await application.worker.drain()

    reports = [text for text in owner_texts(owner_replies) if text.startswith("Field Notes Report")]
    assert len(reports) == 1
    report = reports[0]
    assert report.splitlines()[:4] == [
        "Field Notes Report",
        "Report version 1",
        "",
        "Site and Work",
    ]
    # The dictated values, not the configured marker labels, are what the owner
    # needs to read back.
    assert "site: 123 Main Street." in report
    assert "work: replaced the gutter run." in report
