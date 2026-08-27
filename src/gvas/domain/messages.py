from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from gvas.domain.enums import DeliveryStatus, MediaKind, RecipientAddressKind, SenderRole
from gvas.domain.identifiers import (
    BusinessId,
    MessageKey,
    RoutingData,
    WorkflowIntent,
)


class DomainModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class AttachmentReference(DomainModel):
    attachment_id: UUID
    media_kind: MediaKind
    locator: str
    mime_type: str | None = None
    filename: str | None = None
    byte_size: int | None = Field(default=None, ge=0)

    @field_validator("locator")
    @classmethod
    def locator_is_opaque(cls, value: str) -> str:
        if "://" in value or value.lower().startswith(("http:", "https:", "www.")):
            raise ValueError("attachment locator must be an opaque adapter token")
        if not value.strip():
            raise ValueError("attachment locator must not be empty")
        return value


class TextPart(DomainModel):
    kind: Literal["text"] = "text"
    text: str = Field(min_length=1)


class AttachmentPart(DomainModel):
    kind: Literal["attachment"] = "attachment"
    attachment: AttachmentReference


ContentPart = Annotated[TextPart | AttachmentPart, Field(discriminator="kind")]


class SenderRef(DomainModel):
    external_id: str = Field(min_length=1)
    role: SenderRole


class ConversationRef(DomainModel):
    business_id: BusinessId
    external_conversation_id: str = Field(min_length=1)


class ReplyRef(DomainModel):
    correlation_id: str = Field(min_length=1)
    external_message_id: str | None = None


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must be timezone-aware")
    return value


class InboundOwnerMessage(DomainModel):
    message_key: MessageKey
    business_id: BusinessId
    conversation_ref: ConversationRef
    sender: SenderRef
    received_at: datetime
    parts: tuple[ContentPart, ...] = Field(min_length=1)
    intent: WorkflowIntent
    reply_to: ReplyRef | None = None
    routing: RoutingData

    _received_at_aware = field_validator("received_at")(_aware)

    @model_validator(mode="after")
    def business_matches_conversation(self) -> "InboundOwnerMessage":
        if self.conversation_ref.business_id != self.business_id:
            raise ValueError("conversation business must match message business")
        return self


class OutboundOwnerMessage(DomainModel):
    business_id: BusinessId
    conversation_ref: ConversationRef
    parts: tuple[ContentPart, ...] = Field(min_length=1)
    correlation_id: str = Field(min_length=1)
    reply_to: ReplyRef | None = None
    routing: RoutingData

    @model_validator(mode="after")
    def business_matches_conversation(self) -> "OutboundOwnerMessage":
        if self.conversation_ref.business_id != self.business_id:
            raise ValueError("conversation business must match message business")
        return self


class DeliveryReceipt(DomainModel):
    status: DeliveryStatus
    provider_message_id: str | None = None
    occurred_at: datetime
    detail: str | None = None

    _occurred_at_aware = field_validator("occurred_at")(_aware)


class AudioReference(DomainModel):
    attachment: AttachmentReference

    @field_validator("attachment")
    @classmethod
    def must_be_audio(cls, value: AttachmentReference) -> AttachmentReference:
        if value.media_kind is not MediaKind.AUDIO:
            raise ValueError("audio reference requires an audio attachment")
        return value


class TranscriptResult(DomainModel):
    text: str
    language: str | None = None
    confidence: float | None = Field(default=None, ge=0, le=1)
    duration_seconds: float | None = Field(default=None, ge=0)
    provider_ref: str | None = None


class AttachmentPayload(DomainModel):
    content: bytes
    mime_type: str | None = None
    filename: str | None = None


class CustomerRecipient(DomainModel):
    address: str = Field(min_length=1)
    address_kind: RecipientAddressKind
    display_name: str | None = None


class CustomerDeliveryRequest(DomainModel):
    business_id: BusinessId
    recipient: CustomerRecipient
    subject: str | None = None
    body_text: str = Field(min_length=1)
    links: tuple[str, ...] = Field(default_factory=tuple)
    attachments: tuple[AttachmentReference, ...] = Field(default_factory=tuple)
