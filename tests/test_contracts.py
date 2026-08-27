from datetime import UTC, datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError

from gvas.domain.enums import MediaKind, SenderRole
from gvas.domain.identifiers import BusinessId, MessageKey, WorkflowIntent
from gvas.domain.messages import (
    AttachmentPart,
    AttachmentReference,
    InboundOwnerMessage,
    TextPart,
)


def message() -> InboundOwnerMessage:
    business_id = BusinessId(uuid4())
    return InboundOwnerMessage(
        message_key=MessageKey("key-1"),
        business_id=business_id,
        conversation_ref={
            "business_id": business_id,
            "external_conversation_id": "conversation",
        },
        sender={"external_id": "owner", "role": SenderRole.OWNER},
        received_at=datetime.now(UTC),
        parts=[TextPart(text="hello")],
        intent=WorkflowIntent("greeting"),
        routing={"transport": "fixture"},
    )


def test_message_json_round_trip_and_discriminated_parts() -> None:
    original = message()
    restored = InboundOwnerMessage.model_validate_json(original.model_dump_json())
    assert restored == original
    assert isinstance(restored.parts[0], TextPart)


def test_naive_timestamp_rejected() -> None:
    with pytest.raises(ValidationError):
        InboundOwnerMessage.model_validate(
            {**message().model_dump(), "received_at": datetime.now()}
        )


def test_parts_and_attachment_locator_validation() -> None:
    with pytest.raises(ValidationError):
        InboundOwnerMessage.model_validate({**message().model_dump(), "parts": []})
    with pytest.raises(ValidationError):
        TextPart(text="")
    with pytest.raises(ValidationError):
        AttachmentReference(
            attachment_id=uuid4(),
            media_kind=MediaKind.DOCUMENT,
            locator="https://example.test/file",
        )
    attachment = AttachmentReference(
        attachment_id=uuid4(), media_kind=MediaKind.IMAGE, locator="opaque-token"
    )
    parsed = InboundOwnerMessage.model_validate(
        {**message().model_dump(), "parts": [{"kind": "attachment", "attachment": attachment}]}
    )
    assert isinstance(parsed.parts[0], AttachmentPart)
