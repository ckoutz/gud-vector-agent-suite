"""Owner commands by text: Gus proposes one change, the owner replies YES.

A command is only a proposal until the owner's next message in the same
thread is exactly ``YES`` within ``OWNER_COMMAND_TTL``. Any other message
cancels it. Every command, whatever its outcome, stays as a row: that is the
command log the dashboard shows.
"""

import hashlib
import re
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Protocol
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from gvas.domain.identifiers import BusinessId, JsonValue

OWNER_COMMAND_TTL = timedelta(minutes=10)
OWNER_COMMAND_TEXT_MAX_CHARS = 500


class OwnerCommandState(StrEnum):
    PROPOSED = "proposed"
    APPLYING = "applying"
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"
    EXPIRED = "expired"


class OwnerCommand(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    command_id: UUID
    business_id: BusinessId
    # The owner thread it was proposed in; only a YES there confirms it.
    conversation_ref: str = Field(max_length=255)
    tool: str = Field(max_length=64)
    # Resolved by code when proposed (ids, not names), applied exactly on YES.
    arguments: dict[str, JsonValue]
    # The exact sentence the owner confirmed, written by code, not the model.
    summary: str
    # Money or a customer-facing message: where a PIN can be required later.
    sensitive: bool = False
    # The owner's message that led to the proposal.
    requested_text: str = Field(max_length=OWNER_COMMAND_TEXT_MAX_CHARS)
    state: OwnerCommandState = OwnerCommandState.PROPOSED
    outcome: str | None = None
    created_at: datetime
    expires_at: datetime
    decided_at: datetime | None = None

    def is_live(self, now: datetime) -> bool:
        return self.state is OwnerCommandState.PROPOSED and now < self.expires_at

    def with_updates(self, **changes: object) -> "OwnerCommand":
        return self.model_copy(update=changes)


def is_yes(text: str) -> bool:
    """Only ``YES`` (any case, trailing punctuation allowed) confirms a change;
    "ok", "sure" or "yes but…" do not."""

    return re.sub(r"[\s.!]+$", "", text.strip()).casefold() == "yes"


class OwnerCommandConflictError(Exception):
    """Another change is already waiting for the owner's YES."""


def conversation_key(conversation_ref: str) -> str:
    """A fixed-length key for the thread a command was proposed in."""

    return hashlib.sha256(conversation_ref.encode()).hexdigest()


class OwnerCommandRepository(Protocol):
    async def add(self, command: OwnerCommand) -> None: ...

    async def save(self, command: OwnerCommand) -> None: ...

    async def latest_proposed(self, business_id: BusinessId) -> OwnerCommand | None:
        """The newest command still in ``proposed``, live or not."""
        ...

    async def claim(self, command: OwnerCommand, now: datetime) -> bool:
        """Moves a live proposal to ``applying``; False if it was already
        decided, expired, or claimed by a concurrent YES."""
        ...

    async def close(self, command: OwnerCommand, state: OwnerCommandState, now: datetime) -> bool:
        """Cancels or expires a proposal only while it is still one; False if
        a YES already claimed it."""
        ...

    async def recent(self, business_id: BusinessId, limit: int) -> tuple[OwnerCommand, ...]: ...
