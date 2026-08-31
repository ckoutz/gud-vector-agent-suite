from typing import Protocol

from gvas.domain.intents import IntentResolution
from gvas.domain.messages import (
    AttachmentPayload,
    AttachmentReference,
    AudioReference,
    ConversationRef,
    CustomerDeliveryRequest,
    DeliveryReceipt,
    NormalizedOwnerMessage,
    OutboundOwnerMessage,
    TranscriptResult,
)
from gvas.domain.quotes import QuoteDraftProposal, QuoteDraftRequest


class IntentResolutionPort(Protocol):
    async def resolve(self, message: NormalizedOwnerMessage) -> IntentResolution: ...


class OwnerReplyPort(Protocol):
    async def send(
        self, conversation_ref: ConversationRef, message: OutboundOwnerMessage
    ) -> DeliveryReceipt: ...


class AttachmentAccessPort(Protocol):
    async def fetch(self, attachment: AttachmentReference) -> AttachmentPayload: ...


class TranscriptionPort(Protocol):
    async def transcribe(self, audio: AudioReference) -> TranscriptResult: ...


class CustomerQuoteDeliveryPort(Protocol):
    async def deliver(self, request: CustomerDeliveryRequest) -> DeliveryReceipt: ...


class QuoteDraftingPort(Protocol):
    async def draft(self, request: QuoteDraftRequest) -> QuoteDraftProposal: ...
