from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Protocol

from gvas.domain.enums import WorkflowRunStatus
from gvas.domain.identifiers import MessageId, WorkflowIntent, WorkflowRunId
from gvas.domain.intents import IntentUnresolvedError
from gvas.domain.outbox import (
    RESERVED_COMMAND_TYPES,
    ReservedOutboxCommandTypeError,
    owner_reply_command,
)
from gvas.domain.ports import IntentResolutionPort
from gvas.domain.repositories import UnitOfWork, WorkflowClaimResult
from gvas.domain.workflows import UnknownWorkflowIntentError, WorkflowContext, WorkflowRouter


class ProcessingStatus(StrEnum):
    COMPLETED = "completed"
    ALREADY_PROCESSED = "already_processed"
    MISSING = "missing"
    BUSY = "busy"
    INTENT_UNRESOLVED = "intent_unresolved"
    UNKNOWN_INTENT = "unknown_intent"
    HANDLER_FAILED = "handler_failed"


@dataclass(frozen=True)
class ProcessingOutcome:
    status: ProcessingStatus
    run_id: WorkflowRunId | None = None
    intent: WorkflowIntent | None = None
    detail: str | None = None


class ProcessingUnitOfWorkFactory(Protocol):
    def __call__(self) -> UnitOfWork: ...


class ProcessOwnerMessageService:
    def __init__(
        self,
        unit_of_work_factory: ProcessingUnitOfWorkFactory,
        router: WorkflowRouter,
        intent_resolver: IntentResolutionPort,
    ) -> None:
        self._unit_of_work_factory = unit_of_work_factory
        self._router = router
        self._intent_resolver = intent_resolver

    async def process(
        self, inbound_message_id: MessageId, *, now: datetime, stale_before: datetime
    ) -> ProcessingOutcome:
        async with self._unit_of_work_factory() as unit_of_work:
            record = await unit_of_work.inbound_messages.get_for_processing(inbound_message_id)
            if record is None:
                await unit_of_work.commit()
                return ProcessingOutcome(ProcessingStatus.MISSING)
            claim = await unit_of_work.workflow_runs.claim(
                record.business_id,
                record.inbound_message_id,
                now=now,
                stale_before=stale_before,
            )
            await unit_of_work.commit()

        if claim.result is WorkflowClaimResult.TERMINAL:
            return ProcessingOutcome(
                ProcessingStatus.ALREADY_PROCESSED,
                run_id=claim.run_id,
                intent=claim.intent,
            )
        if claim.result is WorkflowClaimResult.BUSY:
            return ProcessingOutcome(
                ProcessingStatus.BUSY,
                run_id=claim.run_id,
                intent=claim.intent,
            )

        intent = claim.intent
        if intent is None:
            try:
                intent = (await self._intent_resolver.resolve(record.message)).intent
            except IntentUnresolvedError as error:
                async with self._unit_of_work_factory() as unit_of_work:
                    await unit_of_work.workflow_runs.set_error(claim.run_id, str(error))
                    await unit_of_work.commit()
                return ProcessingOutcome(
                    ProcessingStatus.INTENT_UNRESOLVED,
                    run_id=claim.run_id,
                    detail=str(error),
                )

        try:
            handler = self._router.route(intent)
        except UnknownWorkflowIntentError as error:
            await self._finish_failed(claim.run_id, intent, str(error))
            return ProcessingOutcome(
                ProcessingStatus.UNKNOWN_INTENT,
                run_id=claim.run_id,
                intent=intent,
                detail=str(error),
            )

        try:
            result = await handler.handle(
                WorkflowContext(run_id=claim.run_id, intent=intent, message=record.message)
            )
        except Exception as error:
            detail = repr(error)
            await self._finish_failed(claim.run_id, intent, detail)
            return ProcessingOutcome(
                ProcessingStatus.HANDLER_FAILED,
                run_id=claim.run_id,
                intent=intent,
                detail=detail,
            )

        async with self._unit_of_work_factory() as unit_of_work:
            await unit_of_work.workflow_runs.set_intent(claim.run_id, intent)
            for reply in result.replies:
                outbound_message_id = await unit_of_work.outbound_messages.create(
                    reply, record.conversation_id, record.inbound_message_id
                )
                await unit_of_work.outbox.enqueue(
                    owner_reply_command(reply.business_id, outbound_message_id)
                )
            for command in result.commands:
                if command.command_type in RESERVED_COMMAND_TYPES:
                    raise ReservedOutboxCommandTypeError(
                        f"{command.command_type} is reserved for framework processing"
                    )
                await unit_of_work.outbox.enqueue(command)
            await unit_of_work.workflow_runs.finish(claim.run_id, result.status, result.detail)
            await unit_of_work.commit()
        return ProcessingOutcome(
            ProcessingStatus.COMPLETED,
            run_id=claim.run_id,
            intent=intent,
            detail=result.detail,
        )

    async def _finish_failed(
        self, run_id: WorkflowRunId, intent: WorkflowIntent, detail: str
    ) -> None:
        async with self._unit_of_work_factory() as unit_of_work:
            await unit_of_work.workflow_runs.set_intent(run_id, intent)
            await unit_of_work.workflow_runs.finish(run_id, WorkflowRunStatus.FAILED, detail)
            await unit_of_work.commit()
