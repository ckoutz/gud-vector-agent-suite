from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field

from gvas.domain.enums import WorkflowRunStatus
from gvas.domain.identifiers import WorkflowIntent, WorkflowRunId
from gvas.domain.messages import NormalizedOwnerMessage, OutboundOwnerMessage
from gvas.domain.outbox import OutboxCommand


class WorkflowContext(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    run_id: WorkflowRunId
    intent: WorkflowIntent
    message: NormalizedOwnerMessage


class WorkflowResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    status: WorkflowRunStatus
    replies: tuple[OutboundOwnerMessage, ...] = Field(default_factory=tuple)
    commands: tuple[OutboxCommand, ...] = Field(default_factory=tuple)
    detail: str | None = None


class WorkflowHandler(Protocol):
    """Handlers must use deterministic reply correlation IDs for replay safety."""

    intent: WorkflowIntent

    async def handle(self, context: WorkflowContext) -> WorkflowResult: ...


class UnknownWorkflowIntentError(LookupError):
    pass


class WorkflowRouter:
    def __init__(self, handlers: list[WorkflowHandler]) -> None:
        self._handlers = {handler.intent: handler for handler in handlers}

    def route(self, intent: WorkflowIntent) -> WorkflowHandler:
        try:
            return self._handlers[intent]
        except KeyError as error:
            raise UnknownWorkflowIntentError(str(intent)) from error
