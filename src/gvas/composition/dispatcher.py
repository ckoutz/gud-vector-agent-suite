from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID

from gvas.application.field_note_transcription import (
    TranscribeFieldNoteAudioService,
    TranscriptionOutcome,
)
from gvas.application.outbox_service import OutboxService
from gvas.application.owner_reply_delivery import (
    DeliverOwnerReplyService,
    OwnerReplyDeliveryStatus,
)
from gvas.application.processing import ProcessingStatus, ProcessOwnerMessageService
from gvas.application.quotes import DeliverApprovedQuoteService, QuoteDeliveryStatus
from gvas.application.report_generation import GenerateFieldNotesReportService
from gvas.composition.review import CoordinateFieldNoteReviewService
from gvas.composition.snapshots import BuildFieldNoteCaseSnapshotService
from gvas.domain.completeness import FieldNoteReviewId
from gvas.domain.field_notes import (
    FIELD_NOTE_REVIEW_COMMAND_TYPE,
    FIELD_NOTE_TRANSCRIBE_COMMAND_TYPE,
    FieldNoteCaseId,
    FieldNotePartId,
    FieldNoteReviewTrigger,
    field_note_review_command,
)
from gvas.domain.identifiers import MessageId, QuoteId
from gvas.domain.outbox import (
    OWNER_MESSAGE_PROCESS_COMMAND_TYPE,
    OWNER_REPLY_COMMAND_TYPE,
    OutboxCommand,
    OutboxRecord,
)
from gvas.domain.quotes import QUOTE_DELIVERY_COMMAND_TYPE
from gvas.domain.reporting import FIELD_NOTES_REPORT_COMMAND_TYPE


class UnknownCommandTypeError(RuntimeError):
    """Raised for commands with no registered handler; the record stays retryable."""


class TransientCommandError(RuntimeError):
    """Raised when a command cannot make progress yet and must be retried."""


class MalformedCommandPayloadError(ValueError):
    pass


@dataclass(frozen=True)
class DispatchOutcome:
    command_type: str
    detail: str


def _text(command: OutboxCommand, key: str) -> str:
    value = command.payload.get(key)
    if not isinstance(value, str) or not value:
        raise MalformedCommandPayloadError(f"{command.command_type} requires payload {key!r}")
    return value


def _uuid(command: OutboxCommand, key: str) -> UUID:
    try:
        return UUID(_text(command, key))
    except ValueError as error:
        raise MalformedCommandPayloadError(
            f"{command.command_type} payload {key!r} is not a UUID"
        ) from error


class OutboxCommandDispatcher:
    """Routes claimed outbox commands to channel-neutral application services.

    Every provider call happens inside the dispatched service, outside the
    ingress transaction. Commands without a handler fail explicitly so the
    existing outbox retry and dead-letter behaviour applies unchanged.
    """

    def __init__(
        self,
        *,
        processing: ProcessOwnerMessageService,
        owner_replies: DeliverOwnerReplyService,
        quote_delivery: DeliverApprovedQuoteService,
        transcription: TranscribeFieldNoteAudioService,
        review: CoordinateFieldNoteReviewService,
        snapshots: BuildFieldNoteCaseSnapshotService,
        reports: GenerateFieldNotesReportService,
        outbox: OutboxService,
        now: Callable[[], datetime],
        lease_ttl: timedelta = timedelta(minutes=5),
    ) -> None:
        self._processing = processing
        self._owner_replies = owner_replies
        self._quote_delivery = quote_delivery
        self._transcription = transcription
        self._review = review
        self._snapshots = snapshots
        self._reports = reports
        self._outbox = outbox
        self._now = now
        self._lease_ttl = lease_ttl

    async def dispatch(self, record: OutboxRecord) -> DispatchOutcome:
        command = record.command
        if command.command_type == OWNER_MESSAGE_PROCESS_COMMAND_TYPE:
            return await self._process_owner_message(command)
        if command.command_type == OWNER_REPLY_COMMAND_TYPE:
            return await self._deliver_owner_reply(command)
        if command.command_type == QUOTE_DELIVERY_COMMAND_TYPE:
            return await self._deliver_quote(command)
        if command.command_type == FIELD_NOTE_TRANSCRIBE_COMMAND_TYPE:
            return await self._transcribe(command)
        if command.command_type == FIELD_NOTE_REVIEW_COMMAND_TYPE:
            return await self._coordinate_review(command)
        if command.command_type == FIELD_NOTES_REPORT_COMMAND_TYPE:
            return await self._generate_report(command)
        raise UnknownCommandTypeError(f"no handler is registered for {command.command_type}")

    def _window(self) -> tuple[datetime, datetime]:
        now = self._now()
        return now, now - self._lease_ttl

    async def _process_owner_message(self, command: OutboxCommand) -> DispatchOutcome:
        inbound_message_id = command.inbound_message_id
        if inbound_message_id is None:
            raise MalformedCommandPayloadError("process command is missing its inbound message")
        now, stale_before = self._window()
        outcome = await self._processing.process(
            inbound_message_id, now=now, stale_before=stale_before
        )
        if outcome.status in {ProcessingStatus.BUSY, ProcessingStatus.LEASE_LOST}:
            raise TransientCommandError(f"owner message processing is {outcome.status.value}")
        if outcome.status in {
            ProcessingStatus.MISSING,
            ProcessingStatus.INTENT_UNRESOLVED,
            ProcessingStatus.UNKNOWN_INTENT,
            ProcessingStatus.HANDLER_FAILED,
        }:
            raise RuntimeError(f"owner message processing failed: {outcome.status.value}")
        return DispatchOutcome(command.command_type, outcome.status.value)

    async def _deliver_owner_reply(self, command: OutboxCommand) -> DispatchOutcome:
        outbound_message_id = command.outbound_message_id
        if outbound_message_id is None:
            raise MalformedCommandPayloadError("reply command is missing its outbound message")
        outcome = await self._owner_replies.deliver(outbound_message_id)
        if outcome.status is OwnerReplyDeliveryStatus.MISSING:
            raise RuntimeError("owner reply message is not persisted")
        return DispatchOutcome(command.command_type, outcome.status.value)

    async def _deliver_quote(self, command: OutboxCommand) -> DispatchOutcome:
        quote_id = QuoteId(_uuid(command, "quote_id"))
        outcome = await self._quote_delivery.deliver(command.business_id, quote_id)
        if outcome.status in {QuoteDeliveryStatus.MISSING, QuoteDeliveryStatus.NOT_APPROVED}:
            raise RuntimeError(f"quote delivery is {outcome.status.value}")
        return DispatchOutcome(command.command_type, outcome.status.value)

    async def _transcribe(self, command: OutboxCommand) -> DispatchOutcome:
        part_id = FieldNotePartId(_uuid(command, "field_note_part_id"))
        case_id = FieldNoteCaseId(_uuid(command, "field_note_case_id"))
        now, stale_before = self._window()
        report = await self._transcription.transcribe(
            command.business_id, part_id, now=now, stale_before=stale_before
        )
        if report.outcome in {
            TranscriptionOutcome.BUSY,
            TranscriptionOutcome.LEASE_LOST,
            TranscriptionOutcome.MISSING,
        }:
            raise TransientCommandError(f"field-note transcription is {report.outcome.value}")
        if report.outcome is TranscriptionOutcome.FAILED:
            raise RuntimeError(f"field-note transcription failed: {report.detail}")
        await self._outbox.enqueue(
            field_note_review_command(
                command.business_id,
                case_id,
                FieldNoteReviewTrigger.TRANSCRIPTION,
                str(part_id),
            )
        )
        return DispatchOutcome(command.command_type, report.outcome.value)

    async def _coordinate_review(self, command: OutboxCommand) -> DispatchOutcome:
        case_id = FieldNoteCaseId(_uuid(command, "field_note_case_id"))
        trigger = FieldNoteReviewTrigger(_text(command, "trigger"))
        owner_reply_message_id = (
            MessageId(_uuid(command, "owner_reply_message_id"))
            if "owner_reply_message_id" in command.payload
            else None
        )
        outcome = await self._review.coordinate(
            command.business_id,
            case_id,
            trigger,
            now=self._now(),
            owner_reply_message_id=owner_reply_message_id,
        )
        return DispatchOutcome(command.command_type, outcome.status.value)

    async def _generate_report(self, command: OutboxCommand) -> DispatchOutcome:
        case_id = FieldNoteCaseId(_uuid(command, "field_note_case_id"))
        review_id = FieldNoteReviewId(_uuid(command, "field_note_review_id"))
        completed_at = datetime.fromisoformat(_text(command, "completed_at"))
        snapshot = await self._snapshots.build(
            command.business_id, case_id, review_id, completed_at=completed_at
        )
        now, stale_before = self._window()
        version = await self._reports.generate(snapshot, now=now, stale_before=stale_before)
        return DispatchOutcome(command.command_type, f"report version {version.version}")


@dataclass(frozen=True)
class WorkerBatchReport:
    claimed: int
    succeeded: int
    failed: int
    details: tuple[str, ...] = ()


class OutboxWorker:
    """Claims outbox batches and dispatches them outside the ingress request."""

    def __init__(
        self,
        outbox: OutboxService,
        dispatcher: OutboxCommandDispatcher,
        *,
        now: Callable[[], datetime],
        worker_id: str = "outbox-worker",
        batch_size: int = 10,
        lease_ttl: timedelta = timedelta(minutes=5),
        retry_in: timedelta = timedelta(seconds=30),
    ) -> None:
        self._outbox = outbox
        self._dispatcher = dispatcher
        self._now = now
        self._worker_id = worker_id
        self._batch_size = batch_size
        self._lease_ttl = lease_ttl
        self._retry_in = retry_in

    async def run_once(self) -> WorkerBatchReport:
        now = self._now()
        records = await self._outbox.claim_batch(
            self._batch_size, now, self._worker_id, stale_before=now - self._lease_ttl
        )
        succeeded = 0
        failed = 0
        details: list[str] = []
        for record in records:
            try:
                outcome = await self._dispatcher.dispatch(record)
            except Exception as error:
                failed += 1
                details.append(f"{record.command.command_type}: {error!r}")
                await self._outbox.mark_failed(record, self._retry_in, repr(error), self._now())
                continue
            succeeded += 1
            details.append(f"{outcome.command_type}: {outcome.detail}")
            await self._outbox.mark_succeeded(record)
        return WorkerBatchReport(
            claimed=len(records),
            succeeded=succeeded,
            failed=failed,
            details=tuple(details),
        )

    async def drain(self, max_batches: int = 50) -> tuple[WorkerBatchReport, ...]:
        reports: list[WorkerBatchReport] = []
        for _ in range(max_batches):
            report = await self.run_once()
            reports.append(report)
            if report.claimed == 0:
                break
        return tuple(reports)
