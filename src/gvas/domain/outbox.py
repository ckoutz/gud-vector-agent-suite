from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, model_validator

from gvas.domain.enums import OutboxStatus
from gvas.domain.identifiers import BusinessId, JsonValue, OutboxCommandId

DEFAULT_MAX_ATTEMPTS = 3


class OutboxCommand(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    command_id: OutboxCommandId
    business_id: BusinessId
    command_type: str = Field(min_length=1)
    payload: dict[str, JsonValue]
    dedup_key: str | None = None


class InvalidOutboxTransitionError(ValueError):
    pass


class OutboxRecord(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    command: OutboxCommand
    status: OutboxStatus = OutboxStatus.PENDING
    attempts: int = Field(default=0, ge=0)
    max_attempts: int = Field(default=DEFAULT_MAX_ATTEMPTS, ge=1)
    available_at: datetime
    last_error: str | None = None

    @model_validator(mode="after")
    def validate_state(self) -> "OutboxRecord":
        if self.status is OutboxStatus.DEAD and self.attempts < self.max_attempts:
            raise ValueError("dead outbox record must have exhausted attempts")
        return self

    def transition(self, status: OutboxStatus, *, error: str | None = None) -> "OutboxRecord":
        allowed = {
            OutboxStatus.PENDING: {OutboxStatus.IN_PROGRESS},
            OutboxStatus.IN_PROGRESS: {
                OutboxStatus.SUCCEEDED,
                OutboxStatus.FAILED,
                OutboxStatus.DEAD,
            },
            OutboxStatus.FAILED: {OutboxStatus.IN_PROGRESS, OutboxStatus.DEAD},
            OutboxStatus.SUCCEEDED: set(),
            OutboxStatus.DEAD: set(),
        }
        if status not in allowed[self.status]:
            raise InvalidOutboxTransitionError(f"{self.status} -> {status}")
        return self.model_copy(update={"status": status, "last_error": error})
