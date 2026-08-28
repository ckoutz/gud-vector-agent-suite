from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from gvas.application.field_note_transcription import (
    FieldNoteMediaHandoff,
    TranscribeFieldNoteAudioService,
    TranscriptionOutcome,
)
from gvas.application.field_notes import FieldNoteIntakeHandler
from gvas.application.ingestion import IngestOwnerMessageService
from gvas.domain.enums import MediaKind
from gvas.domain.field_notes import (
    FIELD_NOTE_INTENT,
    FieldNotePartId,
    FieldNotePartKind,
)
from gvas.domain.identifiers import BusinessId, MessageKey
from gvas.domain.messages import (
    AttachmentPart,
    AttachmentPayload,
    AttachmentReference,
    ChannelEndpointRef,
    ConversationRef,
    InboundOwnerMessage,
    NormalizedOwnerMessage,
    TextPart,
    TranscriptResult,
)
from gvas.domain.workflows import WorkflowContext
from gvas.infrastructure.field_note_models import FieldNotePartRow
from gvas.infrastructure.field_note_repositories import SqlFieldNoteUnitOfWorkFactory
from gvas.infrastructure.models import Business
from gvas.infrastructure.unit_of_work import SqlUnitOfWorkFactory


async def seed_business(
    session_factory: async_sessionmaker[AsyncSession], business_id: BusinessId
) -> None:
    now = datetime.now(UTC)
    async with session_factory() as session:
        session.add(
            Business(
                id=business_id,
                slug=f"business-{business_id}",
                name="Business",
                created_at=now,
                updated_at=now,
            )
        )
        await session.commit()


@pytest.mark.asyncio
async def test_transcription_lifecycle_and_retry(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    business_id = BusinessId(uuid4())
    await seed_business(session_factory, business_id)
    attachment = AttachmentReference(
        attachment_id=uuid4(), media_kind=MediaKind.AUDIO, locator="opaque-audio"
    )
    message = NormalizedOwnerMessage(
        message_key=MessageKey("audio"),
        business_id=business_id,
        conversation_ref=ConversationRef(
            business_id=business_id, external_conversation_id="conversation"
        ),
        sender={"external_id": "owner", "role": "owner"},
        received_at=datetime.now(UTC),
        parts=(TextPart(text="field notes: voice"), AttachmentPart(attachment=attachment)),
    )
    envelope = InboundOwnerMessage(
        message=message,
        endpoint=ChannelEndpointRef(
            business_id=business_id, source_namespace="test", external_endpoint_id="endpoint"
        ),
        routing={},
    )
    accepted = await IngestOwnerMessageService(SqlUnitOfWorkFactory(session_factory)).ingest(
        envelope
    )
    assert accepted.message_id is not None
    await FieldNoteIntakeHandler(
        SqlFieldNoteUnitOfWorkFactory(session_factory),
        now=lambda: datetime.now(UTC),
    ).handle(WorkflowContext(run_id=uuid4(), intent=FIELD_NOTE_INTENT, message=message))
    async with session_factory() as session:
        row = await session.scalar(
            select(FieldNotePartRow).where(FieldNotePartRow.kind == FieldNotePartKind.AUDIO.value)
        )
        assert row is not None
        part_id = FieldNotePartId(row.id)

    class FailingPort:
        async def transcribe(self, audio: object) -> TranscriptResult:
            raise RuntimeError("failed")

    service = TranscribeFieldNoteAudioService(
        SqlFieldNoteUnitOfWorkFactory(session_factory), FailingPort()
    )
    failed = await service.transcribe(
        part_id, now=datetime.now(UTC), stale_before=datetime.now(UTC) - timedelta(hours=1)
    )
    assert failed.outcome is TranscriptionOutcome.FAILED

    class SuccessfulPort:
        calls = 0

        async def transcribe(self, audio: object) -> TranscriptResult:
            self.calls += 1
            return TranscriptResult(text="voice transcript")

    port = SuccessfulPort()
    service = TranscribeFieldNoteAudioService(SqlFieldNoteUnitOfWorkFactory(session_factory), port)
    succeeded = await service.transcribe(
        part_id, now=datetime.now(UTC), stale_before=datetime.now(UTC) - timedelta(hours=1)
    )
    assert succeeded.outcome is TranscriptionOutcome.TRANSCRIBED
    duplicate = await service.transcribe(
        part_id, now=datetime.now(UTC), stale_before=datetime.now(UTC) - timedelta(hours=1)
    )
    assert duplicate.outcome is TranscriptionOutcome.ALREADY_TRANSCRIBED
    assert port.calls == 1


@pytest.mark.asyncio
async def test_media_handoff_rejects_non_audio() -> None:
    class Port:
        async def fetch(self, attachment: AttachmentReference) -> AttachmentPayload:
            return AttachmentPayload(content=b"")

    image = AttachmentReference(
        attachment_id=uuid4(), media_kind=MediaKind.IMAGE, locator="opaque-image"
    )
    part = type("Part", (), {"kind": FieldNotePartKind.UNSUPPORTED, "attachment": image})()
    with pytest.raises(ValueError) as error:
        await FieldNoteMediaHandoff(Port()).open_audio(part)
    assert "opaque-image" not in str(error.value)
    assert "attachment" in str(error.value)
