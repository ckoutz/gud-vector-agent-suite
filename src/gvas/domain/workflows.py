from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field

from gvas.domain.identifiers import WorkflowIntent, WorkflowRunId
from gvas.domain.messages import InboundOwnerMessage, OutboundOwnerMessage
from gvas.domain.outbox import OutboxCommand


class WorkflowContext(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    run_id: WorkflowRunId
    message: InboundOwnerMessage


class WorkflowResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    status: str
    replies: list[OutboundOwnerMessage] = Field(default_factory=list)
    commands: list[OutboxCommand] = Field(default_factory=list)
    detail: str | None = None


class WorkflowHandler(Protocol):
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
