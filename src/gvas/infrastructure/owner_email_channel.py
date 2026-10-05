"""The e-mail owner channel's reply side.

Replies to an e-mailed owner message (confirmations, questions) go back on
the same e-mail thread: recipient, ``Re:`` subject, ``In-Reply-To``/
``References`` and the signed reply address were persisted as conversation
routing when the reply was ingested.
"""

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from gvas.domain.messages import (
    ConversationRef,
    DeliveryReceipt,
    OutboundOwnerMessage,
    TextPart,
)
from gvas.domain.owner_email import (
    OWNER_EMAIL_SOURCE_NAMESPACE,
    OwnerEmailRequest,
    owner_notice_content,
    render_owner_email_html,
)
from gvas.domain.ports import OwnerEmailPort
from gvas.infrastructure.models import Conversation, OwnerChannelEndpoint


class OwnerEmailRoutingError(RuntimeError):
    """No usable e-mail routing persisted for the conversation; retried."""


class OwnerEmailDeliveryLedger(Protocol):
    async def find(self, key: str) -> DeliveryReceipt | None: ...

    async def record(self, key: str, receipt: DeliveryReceipt) -> None: ...


def _utc_now() -> datetime:
    return datetime.now(tz=UTC)


class EmailOwnerReplyAdapter:
    def __init__(
        self,
        sender: OwnerEmailPort,
        session_factory: async_sessionmaker[AsyncSession],
        ledger: OwnerEmailDeliveryLedger,
        *,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        self._sender = sender
        self._session_factory = session_factory
        self._ledger = ledger
        self._clock = clock

    async def send(
        self, conversation_ref: ConversationRef, message: OutboundOwnerMessage
    ) -> DeliveryReceipt:
        key = (
            f"{conversation_ref.business_id}:"
            f"{conversation_ref.external_conversation_id}:{message.correlation_id}"
        )
        recorded = await self._ledger.find(key)
        if recorded is not None:
            return recorded
        routing = await self._routing(conversation_ref)
        text = "\n".join(part.text for part in message.parts if isinstance(part, TextPart))
        if not text.strip():
            raise OwnerEmailRoutingError("owner reply has no deliverable e-mail content")
        to = routing.get("to")
        subject = routing.get("subject")
        if not isinstance(to, str) or not to or not isinstance(subject, str) or not subject:
            raise OwnerEmailRoutingError("persisted routing has no e-mail recipient")
        in_reply_to = routing.get("in_reply_to")
        reply_to = routing.get("reply_to")
        references = routing.get("references")
        receipt = await self._sender.send(
            OwnerEmailRequest(
                business_id=conversation_ref.business_id,
                to=to,
                subject=subject,
                text=text,
                html=render_owner_email_html(owner_notice_content(text)),
                reply_to=reply_to if isinstance(reply_to, str) and reply_to else None,
                in_reply_to=in_reply_to if isinstance(in_reply_to, str) and in_reply_to else None,
                references=(
                    tuple(item for item in references if isinstance(item, str) and item)
                    if isinstance(references, list)
                    else ()
                ),
                idempotency_key=f"owner_email_reply:{key}"[:256],
            )
        )
        await self._ledger.record(key, receipt)
        return receipt

    async def _routing(self, conversation_ref: ConversationRef) -> dict[str, object]:
        async with self._session_factory() as session:
            row = await session.scalar(
                select(Conversation)
                .join(OwnerChannelEndpoint, OwnerChannelEndpoint.id == Conversation.endpoint_id)
                .where(
                    Conversation.business_id == conversation_ref.business_id,
                    Conversation.external_conversation_id
                    == conversation_ref.external_conversation_id,
                    OwnerChannelEndpoint.source_namespace == OWNER_EMAIL_SOURCE_NAMESPACE,
                )
            )
            routing: dict[str, object] | None = None if row is None else dict(row.routing)
        if routing is None:
            raise OwnerEmailRoutingError(
                f"no e-mail routing for conversation {conversation_ref.external_conversation_id}"
            )
        return routing
