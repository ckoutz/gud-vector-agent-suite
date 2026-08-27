from enum import StrEnum
from typing import Protocol

from gvas.domain.enums import WorkflowRunStatus
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


class IngestionOutcome:
    def __init__(self, status: IngestionStatus, detail: str | None = None) -> None:
        self.status = status
        self.detail = detail


class UnitOfWorkFactory(Protocol):
    def __call__(self) -> UnitOfWork: ...


class IngestOwnerMessageService:
    def __init__(self, unit_of_work_factory: UnitOfWorkFactory, router: WorkflowRouter) -> None:
        self._unit_of_work_factory = unit_of_work_factory
        self._router = router

    async def ingest(self, message: InboundOwnerMessage) -> IngestionOutcome:
        async with self._unit_of_work_factory() as unit_of_work:
            conversation_id = await unit_of_work.conversations.get_or_create(
                message.conversation_ref
            )
            inbound_message_id = await unit_of_work.inbound_messages.create(
                message, conversation_id
            )
            if inbound_message_id is None:
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
                return IngestionOutcome(IngestionStatus.ACCEPTED, str(error))

            result = await handler.handle(WorkflowContext(run_id=run_id, message=message))
            for reply in result.replies:
                await unit_of_work.outbound_messages.create(
                    reply, conversation_id, inbound_message_id
                )
            for command in result.commands:
                await unit_of_work.outbox.enqueue(command)
            run_status = (
                WorkflowRunStatus.SUCCEEDED
                if result.status == WorkflowRunStatus.SUCCEEDED
                else WorkflowRunStatus.FAILED
            )
            await unit_of_work.workflow_runs.finish(run_id, run_status, result.detail)
            await unit_of_work.commit()
            return IngestionOutcome(IngestionStatus.ACCEPTED, result.detail)
