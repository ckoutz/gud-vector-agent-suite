from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from gvas.domain.enums import WorkflowRunStatus
from gvas.domain.identifiers import MessageId, WorkflowRunId
from gvas.domain.messages import InboundOwnerMessage
from gvas.domain.repositories import UnitOfWork
from gvas.domain.workflows import (
    UnknownWorkflowIntentError,
    WorkflowContext,
    WorkflowRouter,
)


class IngestionStatus(StrEnum):
    ACCEPTED = "accepted"
    DUPLICATE = "duplicate"


@dataclass(frozen=True)
class IngestionOutcome:
    status: IngestionStatus
    message_id: MessageId | None = None
    run_id: WorkflowRunId | None = None
    detail: str | None = None


class UnitOfWorkFactory(Protocol):
    def __call__(self) -> UnitOfWork: ...


class IngestOwnerMessageService:
    def __init__(self, unit_of_work_factory: UnitOfWorkFactory, router: WorkflowRouter) -> None:
        self._unit_of_work_factory = unit_of_work_factory
        self._router = router

    async def ingest(self, message: InboundOwnerMessage) -> IngestionOutcome:
        async with self._unit_of_work_factory() as unit_of_work:
            conversation_id = await unit_of_work.conversations.get_or_create(
                message.conversation_ref, message.routing
            )
            inbound_message_id = await unit_of_work.inbound_messages.create(
                message, conversation_id
            )
            if inbound_message_id is None:
                await unit_of_work.rollback()
                return IngestionOutcome(IngestionStatus.DUPLICATE)

            run_id = await unit_of_work.workflow_runs.create(
                message.business_id, inbound_message_id, message.intent
            )
            try:
                handler = self._router.route(message.intent)
            except UnknownWorkflowIntentError as error:
                await unit_of_work.workflow_runs.finish(
                    run_id, WorkflowRunStatus.FAILED, str(error)
                )
                await unit_of_work.commit()
                return IngestionOutcome(
                    IngestionStatus.ACCEPTED,
                    message_id=inbound_message_id,
                    run_id=run_id,
                    detail=str(error),
                )

            result = await handler.handle(WorkflowContext(run_id=run_id, message=message))
            for reply in result.replies:
                await unit_of_work.outbound_messages.create(
                    reply, conversation_id, inbound_message_id
                )
            for command in result.commands:
                await unit_of_work.outbox.enqueue(command)
            await unit_of_work.workflow_runs.finish(run_id, result.status, result.detail)
            await unit_of_work.commit()
            return IngestionOutcome(
                IngestionStatus.ACCEPTED,
                message_id=inbound_message_id,
                run_id=run_id,
                detail=result.detail,
            )
