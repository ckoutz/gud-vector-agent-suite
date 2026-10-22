"""Owner commands by text: Gus proposes one change, the owner replies YES.

A command is only a proposal until the owner's next message in the same
thread is a plain yes (``YES``, ``yea``, ``sounds good``, a thumbs-up…) within
``OWNER_COMMAND_TTL``. A clear no or a different request cancels it; a short
reply that is neither keeps it waiting. Every command, whatever its outcome,
stays as a row: that is the command log the dashboard shows.
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


# A yes on its own is one or more of these, then any of ``_THANKS``.
_YES_PHRASES = (
    ("yes",),
    ("y",),
    ("yep",),
    ("yeah",),
    ("yea",),
    ("yeh",),
    ("yup",),
    ("sure",),
    ("ok",),
    ("okay",),
    ("confirm",),
    ("confirmed",),
    ("go", "ahead"),
    ("do", "it"),
    ("send", "it"),
    ("sounds", "good"),
    ("👍",),
)
_NO_PHRASES = (
    ("no",),
    ("n",),
    ("nope",),
    ("nah",),
    ("cancel",),
    ("cancel", "it"),
    ("never", "mind"),
    ("nevermind",),
    ("dont",),
    ("don't",),
    ("do", "not"),
)
_THANKS = (("please",), ("thanks",), ("thank", "you"))
# A short reply starting with one of these is a no, even with more on it.
_NO_FIRST = frozenset({"no", "nope", "nah", "cancel", "dont", "don't", "stop", "never"})
# A short reply starting with one of these (two words or more, or a question)
# is a new request: "note dog", "who owes me", "schedule?".
_REQUEST_FIRST = frozenset(
    {
        "what", "what's", "whats", "who", "who's", "whos", "when", "where", "which",
        "how", "why", "show", "list", "send", "text", "email", "e-mail", "message",
        "tell", "remind", "block", "unblock", "unavailable", "mark", "add", "create",
        "creat", "make", "new", "note", "notes", "approve", "accept", "decline",
        "reject", "invoice", "invoices", "quote", "quotes", "schedule", "calendar",
        "move", "reschedule", "change", "edit", "update", "void", "reissue", "record",
        "set", "turn", "undo", "find", "look", "check", "give", "book", "bookings",
        "customer", "customers", "money", "owe", "owes",
    }
)  # fmt: skip
UNCLEAR_MAX_WORDS = 3
# iMessage and Android send a tapback on a text as words: Liked “…”.
_REACTION = re.compile(
    r"^(?P<kind>liked|loved|disliked|laughed at|emphasized|questioned|"
    r"reacted \S+ to|[^\w\s]+ to)\s+[“\"](?P<quoted>.*?)[”\"]?$",
    re.IGNORECASE | re.DOTALL,
)
_THUMBS_UP = ("liked", "reacted 👍 to", "👍 to")


def _words(text: str) -> list[str]:
    """Lowercase words: skin tones folded into 👍, commas and trailing
    ``.``/``!`` gone; a ``?`` stays, since a question is not a yes."""

    text = re.sub(r"[\U0001F3FB-\U0001F3FF\uFE0F]", "", text.replace("’", "'"))
    text = text.replace("👍", " 👍 ").replace(",", " ")
    return re.sub(r"[\s.!]+$", "", text.strip()).casefold().split()


def _plain(text: str) -> str:
    return " ".join(_words(text))


def _leading(words: list[str], phrases: tuple[tuple[str, ...], ...]) -> int:
    """How many words at the start are a run of ``phrases`` (and thanks)."""

    at, matched = 0, False
    while at < len(words):
        for phrase in sorted(phrases + (_THANKS if matched else ()), key=len, reverse=True):
            if tuple(words[at : at + len(phrase)]) == phrase:
                at += len(phrase)
                matched = matched or phrase not in _THANKS
                break
        else:
            break
    return at if matched else 0


def is_yes(text: str) -> bool:
    """A yes on its own confirms a change: ``YES``, ``yea``, ``ok``, ``sure``,
    ``sounds good``, ``yes, do it``, ``👍``…, any case, trailing "." or "!".
    "yes?" or "yes but…" do not."""

    words = _words(text)
    return bool(words) and _leading(words, _YES_PHRASES) == len(words)


def is_no(text: str) -> bool:
    words = _words(text)
    return bool(words) and _leading(words, _NO_PHRASES) == len(words)


def is_yes_and_more(text: str) -> bool:
    """``Yes and Friday too``: a yes with more on it, which confirms nothing,
    since the rest may change what the yes meant."""

    words = _words(text)
    return 0 < _leading(words, _YES_PHRASES) < len(words)


def is_unclear(text: str) -> bool:
    """A short reply that is neither a yes, a no nor a new request ("hmm",
    "yes?", "wait", "is it done?"): the change keeps waiting and code asks
    again, rather than cancelling something the owner may have meant."""

    words = _words(text)
    if len(words) > UNCLEAR_MAX_WORDS or is_yes(text) or is_yes_and_more(text):
        return False
    first = words[0].rstrip("?") if words else ""
    if first in _NO_FIRST:
        return False
    return not (first in _REQUEST_FIRST and (len(words) > 1 or "?" in text))


def reaction(text: str) -> tuple[bool, str] | None:
    """A tapback sent as words: (a thumbs-up or like, the quoted text)."""

    match = _REACTION.match(text.strip())
    if match is None:
        return None
    kind = " ".join(match.group("kind").casefold().split())
    kind = re.sub(r"[\U0001F3FB-\U0001F3FF\uFE0F]", "", kind)
    return kind in _THUMBS_UP, " ".join(match.group("quoted").split()).rstrip(".…")


def is_thumbs_up_on(text: str, sent: str) -> bool:
    """A like or thumbs-up on ``sent`` itself, quoted at least in part."""

    found = reaction(text)
    if found is None or not found[0]:
        return False
    quoted, sent = found[1], " ".join(sent.split())
    return len(quoted) >= 20 and (quoted in sent or sent.split(". ", 1)[0] in quoted)


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
