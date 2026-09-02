from typing import Protocol

from gvas.domain.identifiers import BusinessId
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
from gvas.domain.object_storage import ObjectCustodyRequest, StoredObject
from gvas.domain.quotes import QuoteDraftProposal, QuoteDraftRequest
from gvas.domain.reporting import (
    ChecklistEvidence,
    ChecklistEvidenceAnnotation,
    ChecklistEvidenceRequest,
)


class IntentResolutionPort(Protocol):
    async def resolve(self, message: NormalizedOwnerMessage) -> IntentResolution: ...


class OwnerReplyPort(Protocol):
    async def send(
        self, conversation_ref: ConversationRef, message: OutboundOwnerMessage
    ) -> DeliveryReceipt: ...


class AttachmentAccessPort(Protocol):
    async def fetch(self, attachment: AttachmentReference) -> AttachmentPayload: ...


class ObjectStoragePort(Protocol):
    """Managed custody of bytes the system must retain (D7b).

    ``put`` is idempotent for a given request: storing the same content under
    the same scope and name twice yields one object and the same reference.
    ``fetch`` is tenant-checked by the adapter: a reference belonging to
    another business is refused even when the caller holds it.
    """

    async def put(self, request: ObjectCustodyRequest) -> StoredObject: ...

    async def fetch(
        self, business_id: BusinessId, artifact: AttachmentReference
    ) -> AttachmentPayload: ...


class TranscriptionPort(Protocol):
    async def transcribe(self, audio: AudioReference) -> TranscriptResult: ...


class CustomerQuoteDeliveryPort(Protocol):
    async def deliver(self, request: CustomerDeliveryRequest) -> DeliveryReceipt: ...


class ChecklistEvidencePort(Protocol):
    """Attributes a completed review's checklist items to transcript evidence.

    Completeness review reports only the items still missing, so evidence for
    the satisfied items is attributed through this port before a report snapshot
    is assembled.
    """

    async def attribute(
        self, request: ChecklistEvidenceRequest
    ) -> tuple[ChecklistEvidence, ...]: ...


class ChecklistEvidenceAnnotatorPort(Protocol):
    """Attaches supporting excerpts to items a primary attributor already satisfied.

    The annotator never decides whether an item is satisfied; it receives the
    attributed evidence and may only return verbatim transcript excerpts for the
    observed items.
    """

    async def annotate(
        self, request: ChecklistEvidenceRequest, attributed: tuple[ChecklistEvidence, ...]
    ) -> tuple[ChecklistEvidenceAnnotation, ...]: ...


class QuoteDraftingPort(Protocol):
    async def draft(self, request: QuoteDraftRequest) -> QuoteDraftProposal: ...
