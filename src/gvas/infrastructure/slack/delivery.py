from collections.abc import Callable
from datetime import UTC, datetime
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field

from gvas.domain.enums import DeliveryStatus
from gvas.domain.identifiers import RoutingData
from gvas.domain.messages import (
    AttachmentPart,
    ConversationRef,
    DeliveryReceipt,
    OutboundOwnerMessage,
    TextPart,
)


class SlackDeliveryError(RuntimeError):
    """Raised when a Slack delivery attempt should be retried by the dispatcher."""


class SlackRoutingError(SlackDeliveryError):
    pass


class SlackModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class SlackConversationRouting(SlackModel):
    channel: str = Field(min_length=1)
    thread_ts: str | None = None

    @classmethod
    def from_routing(cls, routing: RoutingData) -> "SlackConversationRouting":
        channel = routing.get("channel")
        thread_ts = routing.get("thread_ts")
        if not isinstance(channel, str) or not channel:
            raise SlackRoutingError("persisted routing has no slack channel")
        if thread_ts is not None and not isinstance(thread_ts, str):
            raise SlackRoutingError("persisted routing has an invalid slack thread timestamp")
        return cls(channel=channel, thread_ts=thread_ts)


class SlackChatPostRequest(SlackModel):
    channel: str = Field(min_length=1)
    text: str = Field(min_length=1)
    thread_ts: str | None = None
    idempotency_key: str = Field(min_length=1)


class SlackChatPostResult(SlackModel):
    message_ts: str | None = None
    detail: str | None = None


class SlackChatPoster(Protocol):
    """Outbound Slack transport; no provider client is selected in this round."""

    async def post_message(self, request: SlackChatPostRequest) -> SlackChatPostResult: ...


class SlackRoutingResolver(Protocol):
    async def resolve(
        self, conversation_ref: ConversationRef
    ) -> SlackConversationRouting | None: ...


class SlackDeliveryLedger(Protocol):
    """Records completed deliveries so a retried command posts at most once."""

    async def find(self, key: str) -> DeliveryReceipt | None: ...

    async def record(self, key: str, receipt: DeliveryReceipt) -> None: ...


class InMemorySlackDeliveryLedger:
    def __init__(self) -> None:
        self._receipts: dict[str, DeliveryReceipt] = {}

    async def find(self, key: str) -> DeliveryReceipt | None:
        return self._receipts.get(key)

    async def record(self, key: str, receipt: DeliveryReceipt) -> None:
        self._receipts.setdefault(key, receipt)


def _utc_now() -> datetime:
    return datetime.now(tz=UTC)


def render_text(message: OutboundOwnerMessage) -> str:
    lines: list[str] = []
    for part in message.parts:
        if isinstance(part, TextPart):
            lines.append(part.text)
        elif isinstance(part, AttachmentPart):
            attachment = part.attachment
            label = attachment.filename or attachment.media_kind.value
            lines.append(f"[{attachment.media_kind.value}: {label}]")
    return "\n".join(lines)


def delivery_key(conversation_ref: ConversationRef, message: OutboundOwnerMessage) -> str:
    return (
        f"{conversation_ref.business_id}:"
        f"{conversation_ref.external_conversation_id}:{message.correlation_id}"
    )


class SlackOwnerReplyAdapter:
    """Delivers owner replies to Slack using persisted routing only."""

    def __init__(
        self,
        poster: SlackChatPoster,
        routing_resolver: SlackRoutingResolver,
        ledger: SlackDeliveryLedger,
        *,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        self._poster = poster
        self._routing_resolver = routing_resolver
        self._ledger = ledger
        self._clock = clock

    async def send(
        self, conversation_ref: ConversationRef, message: OutboundOwnerMessage
    ) -> DeliveryReceipt:
        key = delivery_key(conversation_ref, message)
        recorded = await self._ledger.find(key)
        if recorded is not None:
            return recorded
        routing = await self._routing_resolver.resolve(conversation_ref)
        if routing is None:
            raise SlackRoutingError(
                f"no slack routing for conversation {conversation_ref.external_conversation_id}"
            )
        text = render_text(message)
        if not text:
            raise SlackDeliveryError("owner reply has no deliverable slack content")
        result = await self._poster.post_message(
            SlackChatPostRequest(
                channel=routing.channel,
                text=text,
                thread_ts=_thread_ts(routing, message),
                idempotency_key=key,
            )
        )
        if result.message_ts is None:
            raise SlackDeliveryError(result.detail or "slack rejected the message")
        receipt = DeliveryReceipt(
            status=DeliveryStatus.ACCEPTED,
            provider_message_id=result.message_ts,
            occurred_at=self._clock(),
            detail=result.detail,
        )
        await self._ledger.record(key, receipt)
        return receipt


def _thread_ts(routing: SlackConversationRouting, message: OutboundOwnerMessage) -> str | None:
    if message.reply_to is not None and message.reply_to.external_message_id:
        return message.reply_to.external_message_id
    return routing.thread_ts
