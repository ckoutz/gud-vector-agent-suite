"""Owner replies by e-mail: the channel adapter between a received e-mail
and the channel-agnostic owner understanding step.

``receive`` runs in the webhook request: it authorizes the sender, matches
the signed reply token to a booking request, records the event id (first
writer wins) and queues the reply. ``process`` runs on the worker: it asks
``OwnerMessageUnderstanding`` what the owner wants and either ingests the
reply as an ordinary owner message carrying the resolved command — so the
router, the channel policy and ``decide_booking`` handle it exactly like a
chat command — or answers with a question. Nothing acts on ``unclear``.
"""

import logging
from collections.abc import Callable
from datetime import UTC, datetime
from enum import StrEnum
from uuid import uuid5

from pydantic import BaseModel, ConfigDict, Field

from gvas.domain.enums import SenderRole
from gvas.domain.identifiers import (
    BusinessId,
    IntakeConversationId,
    JsonValue,
    MessageKey,
    OutboxCommandId,
)
from gvas.domain.intake import IntakeConversation
from gvas.domain.messages import (
    ChannelEndpointRef,
    ConversationRef,
    InboundOwnerMessage,
    NormalizedOwnerMessage,
    OutboundOwnerMessage,
    SenderRef,
    TextPart,
)
from gvas.domain.outbox import OutboxCommand, owner_message_process_command, owner_reply_command
from gvas.domain.owner_email import (
    OWNER_EMAIL_REPLY_COMMAND_NAMESPACE,
    OWNER_EMAIL_REPLY_COMMAND_TYPE,
    OWNER_EMAIL_SOURCE_NAMESPACE,
    InboundOwnerEmail,
    OwnerReplyToken,
    owner_reply_thread,
    owner_reply_token,
    owner_reply_tokens,
    reply_subject,
    strip_quoted_reply,
)
from gvas.domain.owner_understanding import OwnerMessageContext, OwnerMessageUnderstandingPort
from gvas.domain.repositories import BusinessRecord, UnitOfWork

logger = logging.getLogger(__name__)

UnitOfWorkFactory = Callable[[], UnitOfWork]

STALE_REPLY = (
    "Booking #{reference} changed since that e-mail, so nothing was changed. "
    "Reply to the newest e-mail about it."
)


class OwnerEmailReplyStatus(StrEnum):
    ACCEPTED = "accepted"
    DUPLICATE = "duplicate"
    REJECTED = "rejected"
    IGNORED = "ignored"


class OwnerEmailProcessStatus(StrEnum):
    UNDERSTOOD = "understood"
    CLARIFIED = "clarified"
    STALE = "stale"
    DUPLICATE = "duplicate"


class OwnerEmailReply(BaseModel):
    """The queued reply: the owner's own words plus the thread it belongs to."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    email_id: str = Field(min_length=1)
    sender: str = Field(min_length=3)
    subject: str = ""
    message_id: str | None = None
    references: tuple[str, ...] = ()
    text: str = Field(min_length=1)
    received_at: datetime
    booking_reference: str | None = None
    conversation_id: IntakeConversationId | None = None
    request_epoch: int | None = None


def owner_email_reply_command(business_id: BusinessId, reply: OwnerEmailReply) -> OutboxCommand:
    payload: dict[str, JsonValue] = reply.model_dump(mode="json")
    return OutboxCommand(
        command_id=OutboxCommandId(
            uuid5(OWNER_EMAIL_REPLY_COMMAND_NAMESPACE, f"{business_id}:{reply.email_id}")
        ),
        business_id=business_id,
        command_type=OWNER_EMAIL_REPLY_COMMAND_TYPE,
        payload=payload,
        dedup_key=f"owner_email_reply:{business_id}:{reply.email_id}",
    )


class OwnerEmailReplyService:
    def __init__(
        self,
        unit_of_work_factory: UnitOfWorkFactory,
        understanding: OwnerMessageUnderstandingPort,
        *,
        secret: str,
        reply_domain: str,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._unit_of_work_factory = unit_of_work_factory
        self._understanding = understanding
        self._secret = secret
        self._reply_domain = reply_domain.strip().lower()
        self._now = now

    async def receive(self, email: InboundOwnerEmail) -> OwnerEmailReplyStatus:
        """Authorize and queue one received e-mail. Unauthorized senders are
        dropped without a reply so the address cannot be probed."""

        if not email.sender_authenticated:
            logger.info("owner e-mail dropped: sender failed authentication")
            return OwnerEmailReplyStatus.REJECTED
        tokens = owner_reply_tokens(email.recipients, self._reply_domain) or owner_reply_tokens(
            email.references, self._reply_domain
        )
        text = strip_quoted_reply(email.text)
        async with self._unit_of_work_factory() as unit_of_work:
            businesses = await unit_of_work.businesses.find_by_owner_address(email.sender)
            if not businesses:
                logger.info("owner e-mail dropped: sender is not an owner address")
                await unit_of_work.rollback()
                return OwnerEmailReplyStatus.REJECTED
            # Only replies bound to a signed notice are heard: a tokenless
            # mail, even from the owner address, cannot act.
            match = await self._match(unit_of_work, businesses, tokens)
            if match is None:
                logger.info("owner e-mail dropped: no verified reply token")
                await unit_of_work.rollback()
                return OwnerEmailReplyStatus.REJECTED
            business, conversation, token = match
            if not text:
                await unit_of_work.rollback()
                return OwnerEmailReplyStatus.IGNORED
            recorded = await unit_of_work.payment_events.try_record(
                email.event_source, email.event_id, self._now()
            )
            if not recorded:
                await unit_of_work.rollback()
                return OwnerEmailReplyStatus.DUPLICATE
            reply = OwnerEmailReply(
                email_id=email.email_id,
                sender=email.sender,
                subject=email.subject,
                message_id=email.message_id,
                references=email.references,
                text=text,
                received_at=email.received_at,
                booking_reference=conversation.reference,
                conversation_id=conversation.conversation_id,
                request_epoch=token.request_epoch,
            )
            await unit_of_work.outbox.enqueue(
                owner_email_reply_command(business.business_id, reply)
            )
            await unit_of_work.commit()
        return OwnerEmailReplyStatus.ACCEPTED

    async def _match(
        self,
        unit_of_work: UnitOfWork,
        businesses: tuple[BusinessRecord, ...],
        tokens: tuple[OwnerReplyToken, ...],
    ) -> tuple[BusinessRecord, IntakeConversation, OwnerReplyToken] | None:
        for token in tokens:
            for business in businesses:
                conversation = await unit_of_work.intake_conversations.find_by_reference(
                    business.business_id, token.reference
                )
                if conversation is not None and token.verifies(
                    self._secret,
                    business_id=business.business_id,
                    conversation_id=conversation.conversation_id,
                ):
                    return business, conversation, token
        return None

    async def process(
        self, business_id: BusinessId, payload: dict[str, JsonValue] | object
    ) -> OwnerEmailProcessStatus:
        reply = OwnerEmailReply.model_validate(payload)
        context = OwnerMessageContext(booking_reference=reply.booking_reference)
        if await self._is_stale(business_id, reply):
            return await self._ingest(
                business_id,
                reply,
                text=reply.text,
                answer=STALE_REPLY.format(reference=reply.booking_reference),
                status=OwnerEmailProcessStatus.STALE,
            )
        understanding = await self._understanding.understand(business_id, reply.text, context)
        if understanding.resolved and understanding.command_text is not None:
            logger.info(
                "owner e-mail understood as %s via %s",
                understanding.intent,
                understanding.source.value,
            )
            return await self._ingest(
                business_id,
                reply,
                text=understanding.command_text,
                answer=None,
                status=OwnerEmailProcessStatus.UNDERSTOOD,
            )
        return await self._ingest(
            business_id,
            reply,
            text=reply.text,
            answer=understanding.question,
            status=OwnerEmailProcessStatus.CLARIFIED,
        )

    async def _is_stale(self, business_id: BusinessId, reply: OwnerEmailReply) -> bool:
        """Like a stale decision link: the request was re-sent (rescheduled,
        re-routed) after the e-mail this reply answers."""

        if reply.booking_reference is None or reply.request_epoch is None:
            return False
        async with self._unit_of_work_factory() as unit_of_work:
            conversation = await unit_of_work.intake_conversations.find_by_reference(
                business_id, reply.booking_reference
            )
        if conversation is None or conversation.owner_notified_at is None:
            return True
        return int(conversation.owner_notified_at.timestamp()) != reply.request_epoch

    async def _ingest(
        self,
        business_id: BusinessId,
        reply: OwnerEmailReply,
        *,
        text: str,
        answer: str | None,
        status: OwnerEmailProcessStatus,
    ) -> OwnerEmailProcessStatus:
        """Persist the reply as an owner message on its own e-mail thread;
        resolved replies go to the normal processing pipeline, the rest get
        ``answer`` back by e-mail."""

        inbound = self._inbound(business_id, reply, text)
        async with self._unit_of_work_factory() as unit_of_work:
            endpoint_id = await unit_of_work.owner_channel_endpoints.get_or_create(
                inbound.endpoint, {"address": reply.sender}
            )
            conversation_id = await unit_of_work.conversations.get_or_create(
                inbound.message.conversation_ref, endpoint_id, inbound.routing
            )
            inbound_message_id = await unit_of_work.inbound_messages.create(
                inbound, conversation_id, endpoint_id
            )
            if inbound_message_id is None:
                await unit_of_work.rollback()
                return OwnerEmailProcessStatus.DUPLICATE
            if answer is None:
                await unit_of_work.outbox.enqueue(
                    owner_message_process_command(business_id, inbound_message_id)
                )
            else:
                outbound = OutboundOwnerMessage(
                    business_id=business_id,
                    conversation_ref=inbound.message.conversation_ref,
                    parts=(TextPart(text=answer),),
                    correlation_id=f"owner_email_clarify:{reply.email_id}",
                )
                outbound_id = await unit_of_work.outbound_messages.create(
                    outbound, conversation_id, inbound_message_id
                )
                await unit_of_work.outbox.enqueue(owner_reply_command(business_id, outbound_id))
            await unit_of_work.commit()
        return status

    def _inbound(
        self, business_id: BusinessId, reply: OwnerEmailReply, text: str
    ) -> InboundOwnerMessage:
        conversation_ref = ConversationRef(
            business_id=business_id, external_conversation_id=f"email:{reply.email_id}"
        )
        references = [*reply.references]
        if reply.message_id and reply.message_id not in references:
            references.append(reply.message_id)
        reply_to: str | None = None
        if (
            reply.booking_reference is not None
            and reply.conversation_id is not None
            and reply.request_epoch is not None
        ):
            # Answers keep the signed reply address so the owner can simply
            # reply again ("approve") on the same thread.
            thread = owner_reply_thread(
                owner_reply_token(
                    self._secret,
                    business_id=business_id,
                    conversation_id=reply.conversation_id,
                    reference=reply.booking_reference,
                    request_epoch=reply.request_epoch,
                ),
                self._reply_domain,
            )
            reply_to = thread.reply_to
            if thread.anchor not in references:
                references.insert(0, thread.anchor)
        routing: dict[str, JsonValue] = {
            "to": reply.sender,
            "subject": reply_subject(reply.subject),
            "in_reply_to": reply.message_id,
            "references": list(references),
            "reply_to": reply_to,
            "request_epoch": reply.request_epoch,
        }
        return InboundOwnerMessage(
            message=NormalizedOwnerMessage(
                message_key=MessageKey(f"email:{reply.email_id}"),
                business_id=business_id,
                conversation_ref=conversation_ref,
                sender=SenderRef(external_id=reply.sender, role=SenderRole.OWNER),
                received_at=reply.received_at,
                parts=(TextPart(text=text),),
            ),
            endpoint=ChannelEndpointRef(
                business_id=business_id,
                source_namespace=OWNER_EMAIL_SOURCE_NAMESPACE,
                external_endpoint_id=reply.sender,
            ),
            routing=routing,
        )


__all__ = [
    "OwnerEmailProcessStatus",
    "OwnerEmailReply",
    "OwnerEmailReplyService",
    "OwnerEmailReplyStatus",
    "owner_email_reply_command",
]
