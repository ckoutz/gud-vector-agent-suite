"""OpenAI backend for the website booking agent (``IntakeAgentPort``).

One chat-completions call per customer message, answered through a strict
JSON schema: the reply text, whatever new fields were collected, whether the
request is complete enough to schedule, which offered slot the customer chose
(if any) and whether a human must step in. The key and the provider's raw
response never leave this module — failures raise ``IntakeAgentError`` with a
fixed message, and the service layer still scrubs every reply for prices
before it is stored or shown.
"""

import json
import logging
from datetime import UTC, datetime
from typing import Any, Final

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from gvas.config import OpenAISettings
from gvas.domain.intake import (
    IntakeAgentError,
    IntakeCollected,
    IntakeMessageRole,
    IntakeTurn,
    IntakeTurnRequest,
)
from gvas.domain.usage import UsageKind, UsageLedgerPort

logger = logging.getLogger(__name__)

CHAT_COMPLETIONS_PATH: Final = "/chat/completions"
SCHEMA_NAME: Final = "intake_turn"
SEED: Final = 0
MAX_REPLY_CHARS: Final = 600
MAX_SUMMARY_CHARS: Final = 800
TRANSCRIPT_MESSAGE_LIMIT: Final = 40

SYSTEM_PROMPT: Final = """You are the booking assistant on a small service business's website.
Customers message you to request an inspection or estimate and pick a time.

Collect, conversationally: the customer's name, email, phone (optional), the
property address, what the problem or service need is, the property type, and
how urgent it is. The JSON you return carries the fields you have learned so
far in `collected`; leave fields empty when the customer has not answered them
yet.

Rules:
- Ask one question at a time. Keep every reply under 60 words, warm and plain.
- NEVER quote prices, costs, rates or ranges and never promise outcomes. If
  asked, say the owner will review the request and confirm pricing.
- NEVER invent or estimate availability. When `offered_slots` is non-empty,
  only times in that list may be picked; help the customer choose one and set
  `chosen_slot` to its ISO `start` once they commit ("the second one",
  "Tuesday at 9"). Set it to null until they do, and while the list is empty.
- Set `ready_for_slots` to true once you have at least the name, email,
  address and problem — and the customer has indicated they want to book a
  time.
- If the customer is an existing customer (`known_customer` is true), their
  name, email and phone are already collected — do not ask for them again;
  ask about the new service.
- Set `needs_human` when the customer asks for something you cannot answer, is
  upset, or the request is ambiguous or off-topic for this business. Also
  politely decline unrelated requests. When escalating, write a one-sentence
  `summary` of the request for the owner.
- `reply` is the next thing the customer reads. When everything needed is
  collected, say that the owner will review and confirm — never that anything
  is booked."""

RESPONSE_SCHEMA: Final[dict[str, Any]] = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "reply",
        "collected",
        "ready_for_slots",
        "chosen_slot",
        "needs_human",
        "summary",
    ],
    "properties": {
        "reply": {"type": "string"},
        "collected": {
            "type": "object",
            "additionalProperties": False,
            "required": [
                "name",
                "email",
                "phone",
                "address",
                "problem",
                "propertyType",
                "urgency",
            ],
            "properties": {
                "name": {"type": "string"},
                "email": {"type": "string"},
                "phone": {"type": "string"},
                "address": {"type": "string"},
                "problem": {"type": "string"},
                "propertyType": {"type": "string"},
                "urgency": {"type": "string"},
            },
        },
        "ready_for_slots": {"type": "boolean"},
        "chosen_slot": {"type": ["string", "null"]},
        "needs_human": {"type": "boolean"},
        "summary": {"type": "string"},
    },
}


class _ReportedCollected(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    name: str = ""
    email: str = ""
    phone: str = ""
    address: str = ""
    problem: str = ""
    property_type: str = Field(default="", alias="propertyType")
    urgency: str = ""


class _ReportedTurn(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    reply: str = ""
    collected: _ReportedCollected = Field(default_factory=_ReportedCollected)
    ready_for_slots: bool = False
    chosen_slot: str | None = None
    needs_human: bool = False
    summary: str = ""


class OpenAIIntakeAgent:
    """Implements ``IntakeAgentPort`` over the chat-completions endpoint."""

    def __init__(
        self,
        settings: OpenAISettings,
        client: httpx.AsyncClient,
        usage_ledger: UsageLedgerPort | None = None,
    ) -> None:
        if not settings.is_configured:
            raise IntakeAgentError("openai api key is not configured")
        self._settings = settings
        self._client = client
        self._usage_ledger = usage_ledger

    async def turn(self, request: IntakeTurnRequest) -> IntakeTurn:
        try:
            response = await self._client.post(
                f"{self._settings.api_base_url}{CHAT_COMPLETIONS_PATH}",
                json=_request_body(self._settings.review_model, request),
                headers={"Authorization": f"Bearer {self._settings.api_key}"},
                timeout=self._settings.timeout_seconds,
            )
        except httpx.HTTPError as error:
            logger.warning("intake agent request failed: %s", type(error).__name__)
            raise IntakeAgentError("openai was unreachable") from error
        turn = _turn(response)
        if self._usage_ledger is not None:
            await self._usage_ledger.record(
                request.business_id,
                UsageKind.REVIEW_TOKENS,
                _total_tokens(response),
                at=datetime.now(UTC),
            )
        return turn


def _total_tokens(response: httpx.Response) -> int:
    try:
        usage = response.json().get("usage")
    except ValueError:
        return 0
    if not isinstance(usage, dict):
        return 0
    total = 0
    for key in ("prompt_tokens", "completion_tokens"):
        value = usage.get(key)
        if isinstance(value, int) and not isinstance(value, bool) and value > 0:
            total += value
    return total


def _request_body(model: str, request: IntakeTurnRequest) -> dict[str, Any]:
    return {
        "model": model,
        "seed": SEED,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": _user_content(request)},
        ],
        "response_format": {
            "type": "json_schema",
            "json_schema": {"name": SCHEMA_NAME, "strict": True, "schema": RESPONSE_SCHEMA},
        },
    }


def _user_content(request: IntakeTurnRequest) -> str:
    transcript = [
        {
            "role": "customer" if m.role is IntakeMessageRole.USER else m.role.value,
            "content": m.content,
        }
        for m in request.transcript[-TRANSCRIPT_MESSAGE_LIMIT:]
    ]
    return json.dumps(
        {
            "business_name": request.business_name,
            "known_customer": request.known_customer,
            "collected_so_far": request.collected.as_stored(),
            "offered_slots": [
                {"start": slot.start.isoformat(), "end": slot.end.isoformat()}
                for slot in request.offered_slots
            ],
            "transcript": transcript,
        },
        ensure_ascii=False,
    )


def _turn(response: httpx.Response) -> IntakeTurn:
    if response.status_code >= 400:
        logger.warning("intake agent returned http %s", response.status_code)
        raise IntakeAgentError(f"openai returned http {response.status_code}")
    try:
        payload = response.json()
        content = payload["choices"][0]["message"]["content"]
        if not isinstance(content, str):
            raise TypeError("message content is not text")
        reported = _ReportedTurn.model_validate_json(content)
    except (KeyError, IndexError, TypeError, ValueError, ValidationError) as error:
        logger.warning("intake agent returned an unreadable response: %s", type(error).__name__)
        raise IntakeAgentError("openai returned an unreadable intake turn") from error

    chosen_slot = None
    if reported.chosen_slot:
        try:
            parsed = datetime.fromisoformat(reported.chosen_slot.strip())
            if parsed.tzinfo is None or parsed.utcoffset() is None:
                parsed = parsed.replace(tzinfo=UTC)
            chosen_slot = parsed
        except ValueError:
            chosen_slot = None
    collected = reported.collected
    return IntakeTurn(
        reply=_squash(reported.reply)[:MAX_REPLY_CHARS] or "Could you tell me more?",
        collected=IntakeCollected(
            name=_blank_none(collected.name),
            email=_blank_none(collected.email),
            phone=_blank_none(collected.phone),
            address=_blank_none(collected.address),
            problem=_blank_none(collected.problem),
            property_type=_blank_none(collected.property_type),
            urgency=_blank_none(collected.urgency),
        ),
        ready_for_slots=reported.ready_for_slots,
        chosen_slot=chosen_slot,
        needs_human=reported.needs_human,
        summary=_squash(reported.summary)[:MAX_SUMMARY_CHARS],
    )


def _blank_none(value: str) -> str | None:
    return _squash(value) or None


def _squash(value: str) -> str:
    return " ".join(value.split())
