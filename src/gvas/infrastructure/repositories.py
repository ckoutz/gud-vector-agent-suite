from datetime import UTC, datetime
from typing import Any, cast
from uuid import uuid4

from sqlalchemy import and_, or_, select, update
from sqlalchemy.engine import CursorResult, Result
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from gvas.domain.enums import DeliveryStatus, OutboxStatus, WorkflowRunStatus
from gvas.domain.identifiers import (
    BusinessId,
    ConversationId,
    EndpointId,
    MessageId,
    MessageKey,
    QuoteId,
    RoutingData,
    WorkflowIntent,
    WorkflowRunId,
)
from gvas.domain.messages import (
    ChannelEndpointRef,
    ConversationRef,
    DeliveryReceipt,
    InboundOwnerMessage,
    NormalizedOwnerMessage,
    OutboundOwnerMessage,
)
from gvas.domain.outbox import (
    DEFAULT_MAX_ATTEMPTS,
    LostOutboxLeaseError,
    OutboxCommand,
    OutboxRecord,
)
from gvas.domain.quotes import Quote, QuoteConcurrencyError
from gvas.domain.repositories import (
    BusinessRecord,
    CrossBusinessReferenceError,
    EndpointBusinessMismatchError,
    InboundProcessingRecord,
    LostWorkflowLeaseError,
    OutboundDeliveryRecord,
    OwnerChannelEndpointRecord,
    WorkflowClaimResult,
    WorkflowRunClaim,
)
from gvas.infrastructure.models import (
    Business,
    Conversation,
    InboundMessage,
    OutboundMessage,
    OutboxMessage,
    OwnerChannelEndpoint,
    QuoteRecord,
    WorkflowRun,
)


def _validate_aware(value: datetime, name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")


def _rowcount(result: Result[Any]) -> int:
    return cast(CursorResult[Any], result).rowcount


class SqlBusinessRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, business_id: BusinessId) -> BusinessRecord | None:
        row = await self.session.scalar(select(Business).where(Business.id == business_id))
        if row is None:
            return None
        return BusinessRecord(
            business_id=BusinessId(row.id),
            slug=row.slug,
            name=row.name,
        )

    async def ensure(
        self, business_id: BusinessId, slug: str, name: str, *, now: datetime
    ) -> BusinessRecord:
        row = await self.session.scalar(select(Business).where(Business.id == business_id))
        if row is None:
            row = Business(id=business_id, slug=slug, name=name, created_at=now, updated_at=now)
            self.session.add(row)
        elif row.slug != slug or row.name != name:
            row.slug = slug
            row.name = name
            row.updated_at = now
        await self.session.flush()
        return BusinessRecord(business_id=BusinessId(row.id), slug=row.slug, name=row.name)


class SqlOwnerChannelEndpointRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    @staticmethod
    def _record(row: OwnerChannelEndpoint) -> OwnerChannelEndpointRecord:
        return OwnerChannelEndpointRecord(
            endpoint_id=EndpointId(row.id),
            business_id=BusinessId(row.business_id),
            source_namespace=row.source_namespace,
            external_endpoint_id=row.external_endpoint_id,
            owner_external_id=row.owner_external_id,
            routing=row.routing,
        )

    async def get(self, endpoint_id: EndpointId) -> OwnerChannelEndpointRecord | None:
        row = await self.session.scalar(
            select(OwnerChannelEndpoint).where(OwnerChannelEndpoint.id == endpoint_id)
        )
        return self._record(row) if row is not None else None

    async def get_or_create(
        self, reference: ChannelEndpointRef, routing: RoutingData
    ) -> EndpointId:
        row = await self.session.scalar(
            select(OwnerChannelEndpoint).where(
                OwnerChannelEndpoint.business_id == reference.business_id,
                OwnerChannelEndpoint.source_namespace == reference.source_namespace,
                OwnerChannelEndpoint.external_endpoint_id == reference.external_endpoint_id,
            )
        )
        if row is not None:
            return EndpointId(row.id)
        row = OwnerChannelEndpoint(
            business_id=reference.business_id,
            source_namespace=reference.source_namespace,
            external_endpoint_id=reference.external_endpoint_id,
            routing=routing,
        )
        try:
            async with self.session.begin_nested():
                self.session.add(row)
                await self.session.flush()
        except IntegrityError:
            existing = await self.session.scalar(
                select(OwnerChannelEndpoint).where(
                    OwnerChannelEndpoint.business_id == reference.business_id,
                    OwnerChannelEndpoint.source_namespace == reference.source_namespace,
                    OwnerChannelEndpoint.external_endpoint_id == reference.external_endpoint_id,
                )
            )
            if existing is None:
                raise
            return EndpointId(existing.id)
        return EndpointId(row.id)


class SqlConversationRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_or_create(
        self, reference: ConversationRef, endpoint_id: EndpointId, routing: RoutingData
    ) -> ConversationId:
        endpoint = await self.session.scalar(
            select(OwnerChannelEndpoint).where(OwnerChannelEndpoint.id == endpoint_id)
        )
        if endpoint is None or endpoint.business_id != reference.business_id:
            raise EndpointBusinessMismatchError(
                "conversation endpoint must belong to the conversation business"
            )
        result = await self.session.execute(
            select(Conversation).where(
                Conversation.endpoint_id == endpoint_id,
                Conversation.external_conversation_id == reference.external_conversation_id,
            )
        )
        existing = result.scalar_one_or_none()
        if existing is not None:
            return ConversationId(existing.id)
        conversation = Conversation(
            business_id=reference.business_id,
            endpoint_id=endpoint_id,
            external_conversation_id=reference.external_conversation_id,
            routing=routing,
        )
        try:
            async with self.session.begin_nested():
                self.session.add(conversation)
                await self.session.flush()
        except IntegrityError:
            existing = await self.session.scalar(
                select(Conversation).where(
                    Conversation.endpoint_id == endpoint_id,
                    Conversation.external_conversation_id == reference.external_conversation_id,
                )
            )
            if existing is None:
                raise
            return ConversationId(existing.id)
        return ConversationId(conversation.id)


class SqlInboundMessageRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(
        self,
        message: InboundOwnerMessage,
        conversation_id: ConversationId,
        endpoint_id: EndpointId,
    ) -> MessageId | None:
        conversation = await self.session.scalar(
            select(Conversation).where(Conversation.id == conversation_id)
        )
        endpoint = await self.session.scalar(
            select(OwnerChannelEndpoint).where(OwnerChannelEndpoint.id == endpoint_id)
        )
        if (
            conversation is None
            or endpoint is None
            or conversation.business_id != message.message.business_id
            or endpoint.business_id != message.message.business_id
            or conversation.endpoint_id != endpoint_id
        ):
            raise CrossBusinessReferenceError(
                "inbound message references a conversation or endpoint from another business"
            )
        existing = await self.session.scalar(
            select(InboundMessage).where(
                InboundMessage.endpoint_id == endpoint_id,
                InboundMessage.message_key == message.message.message_key,
            )
        )
        if existing is not None:
            return None
        normalized = message.message
        row = InboundMessage(
            business_id=normalized.business_id,
            endpoint_id=endpoint_id,
            conversation_id=conversation_id,
            message_key=normalized.message_key,
            sender_external_id=normalized.sender.external_id,
            sender_role=normalized.sender.role.value,
            received_at=normalized.received_at,
            parts=[part.model_dump(mode="json") for part in normalized.parts],
            reply_to=normalized.reply_to.model_dump(mode="json") if normalized.reply_to else None,
            routing=message.routing,
        )
        try:
            async with self.session.begin_nested():
                self.session.add(row)
                await self.session.flush()
        except IntegrityError:
            return None
        return MessageId(row.id)

    async def get_for_processing(
        self, inbound_message_id: MessageId
    ) -> InboundProcessingRecord | None:
        result = await self.session.execute(
            select(InboundMessage, Conversation)
            .join(Conversation, Conversation.id == InboundMessage.conversation_id)
            .where(InboundMessage.id == inbound_message_id)
        )
        row = result.one_or_none()
        if row is None:
            return None
        inbound, conversation = row
        return _processing_record(inbound, conversation)

    async def find_by_key(
        self,
        business_id: BusinessId,
        conversation_id: ConversationId,
        message_key: MessageKey,
    ) -> InboundProcessingRecord | None:
        result = await self.session.execute(
            select(InboundMessage, Conversation)
            .join(Conversation, Conversation.id == InboundMessage.conversation_id)
            .where(
                InboundMessage.business_id == business_id,
                InboundMessage.conversation_id == conversation_id,
                InboundMessage.message_key == message_key,
            )
        )
        row = result.one_or_none()
        if row is None:
            return None
        inbound, conversation = row
        return _processing_record(inbound, conversation)


def _processing_record(
    inbound: InboundMessage, conversation: Conversation
) -> InboundProcessingRecord:
    message = NormalizedOwnerMessage.model_validate(
        {
            "message_key": inbound.message_key,
            "business_id": inbound.business_id,
            "conversation_ref": {
                "business_id": conversation.business_id,
                "external_conversation_id": conversation.external_conversation_id,
            },
            "sender": {
                "external_id": inbound.sender_external_id,
                "role": inbound.sender_role,
            },
            "received_at": (
                inbound.received_at
                if inbound.received_at.tzinfo is not None
                else inbound.received_at.replace(tzinfo=UTC)
            ),
            "parts": inbound.parts,
            "reply_to": inbound.reply_to,
        }
    )
    return InboundProcessingRecord(
        inbound_message_id=MessageId(inbound.id),
        business_id=BusinessId(inbound.business_id),
        conversation_id=ConversationId(inbound.conversation_id),
        endpoint_id=EndpointId(inbound.endpoint_id),
        message=message,
    )


class SqlOutboundMessageRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(
        self,
        message: OutboundOwnerMessage,
        conversation_id: ConversationId,
        inbound_message_id: MessageId,
    ) -> MessageId:
        conversation = await self.session.scalar(
            select(Conversation).where(Conversation.id == conversation_id)
        )
        inbound = await self.session.scalar(
            select(InboundMessage).where(InboundMessage.id == inbound_message_id)
        )
        if (
            conversation is None
            or inbound is None
            or conversation.business_id != message.business_id
            or inbound.business_id != message.business_id
            or inbound.conversation_id != conversation_id
        ):
            raise CrossBusinessReferenceError(
                "outbound message references a conversation or inbound message "
                "from another business"
            )
        existing = await self.session.scalar(
            select(OutboundMessage).where(
                OutboundMessage.inbound_message_id == inbound_message_id,
                OutboundMessage.correlation_id == message.correlation_id,
            )
        )
        if existing is not None:
            return MessageId(existing.id)
        row = OutboundMessage(
            business_id=message.business_id,
            conversation_id=conversation_id,
            inbound_message_id=inbound_message_id,
            parts=[part.model_dump(mode="json") for part in message.parts],
            reply_to=message.reply_to.model_dump(mode="json") if message.reply_to else None,
            status=DeliveryStatus.ACCEPTED.value,
            correlation_id=message.correlation_id,
        )
        try:
            async with self.session.begin_nested():
                self.session.add(row)
                await self.session.flush()
        except IntegrityError:
            existing = await self.session.scalar(
                select(OutboundMessage).where(
                    OutboundMessage.inbound_message_id == inbound_message_id,
                    OutboundMessage.correlation_id == message.correlation_id,
                )
            )
            if existing is None:
                raise
            return MessageId(existing.id)
        return MessageId(row.id)

    async def get_for_delivery(
        self, outbound_message_id: MessageId
    ) -> OutboundDeliveryRecord | None:
        result = await self.session.execute(
            select(OutboundMessage, Conversation, OwnerChannelEndpoint)
            .join(Conversation, Conversation.id == OutboundMessage.conversation_id)
            .join(OwnerChannelEndpoint, OwnerChannelEndpoint.id == Conversation.endpoint_id)
            .where(OutboundMessage.id == outbound_message_id)
        )
        row = result.one_or_none()
        if row is None:
            return None
        outbound, conversation, endpoint = row
        return OutboundDeliveryRecord(
            outbound_message_id=MessageId(outbound.id),
            message=OutboundOwnerMessage(
                business_id=BusinessId(outbound.business_id),
                conversation_ref=ConversationRef(
                    business_id=BusinessId(conversation.business_id),
                    external_conversation_id=conversation.external_conversation_id,
                ),
                parts=tuple(outbound.parts),
                correlation_id=outbound.correlation_id,
                reply_to=outbound.reply_to,
            ),
            endpoint_id=EndpointId(endpoint.id),
            conversation_routing=conversation.routing,
            endpoint_routing=endpoint.routing,
            status=DeliveryStatus(outbound.status),
        )

    async def record_delivery(
        self, outbound_message_id: MessageId, receipt: DeliveryReceipt
    ) -> None:
        await self.session.execute(
            update(OutboundMessage)
            .where(OutboundMessage.id == outbound_message_id)
            .values(
                status=receipt.status.value,
                provider_message_id=receipt.provider_message_id,
                delivered_at=receipt.occurred_at,
                delivery_detail=receipt.detail,
            )
        )


class SqlWorkflowRunRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    @staticmethod
    def _claim(row: WorkflowRun) -> WorkflowRunClaim:
        return WorkflowRunClaim(
            result=WorkflowClaimResult.ACQUIRED,
            run_id=WorkflowRunId(row.id),
            status=WorkflowRunStatus(row.status),
            intent=None if row.intent is None else WorkflowIntent(row.intent),
            attempts=row.attempts,
            lease_token=row.lease_token,
        )

    async def claim(
        self,
        business_id: BusinessId,
        inbound_message_id: MessageId,
        *,
        now: datetime,
        stale_before: datetime,
    ) -> WorkflowRunClaim:
        _validate_aware(now, "now")
        _validate_aware(stale_before, "stale_before")
        row = await self.session.scalar(
            select(WorkflowRun)
            .where(
                WorkflowRun.business_id == business_id,
                WorkflowRun.inbound_message_id == inbound_message_id,
            )
            .with_for_update()
        )
        if row is None:
            row = WorkflowRun(
                business_id=business_id,
                inbound_message_id=inbound_message_id,
                intent=None,
                status=WorkflowRunStatus.RUNNING.value,
                attempts=1,
                started_at=now,
                leased_at=now,
                lease_token=uuid4(),
            )
            try:
                async with self.session.begin_nested():
                    self.session.add(row)
                    await self.session.flush()
            except IntegrityError:
                row = await self.session.scalar(
                    select(WorkflowRun)
                    .where(
                        WorkflowRun.business_id == business_id,
                        WorkflowRun.inbound_message_id == inbound_message_id,
                    )
                    .with_for_update()
                )
                if row is None:
                    raise
                return self._claim_existing(row, now, stale_before)
            return self._claim(row)
        return self._claim_existing(row, now, stale_before)

    def _claim_existing(
        self, row: WorkflowRun, now: datetime, stale_before: datetime
    ) -> WorkflowRunClaim:
        if row.status == WorkflowRunStatus.SUCCEEDED.value:
            return WorkflowRunClaim(
                result=WorkflowClaimResult.TERMINAL,
                run_id=WorkflowRunId(row.id),
                status=WorkflowRunStatus(row.status),
                intent=None if row.intent is None else WorkflowIntent(row.intent),
                attempts=row.attempts,
            )
        leased_at = row.leased_at
        if leased_at is not None and leased_at.tzinfo is None:
            leased_at = leased_at.replace(tzinfo=UTC)
        if row.status == WorkflowRunStatus.RUNNING.value and (
            leased_at is not None and leased_at > stale_before
        ):
            return WorkflowRunClaim(
                result=WorkflowClaimResult.BUSY,
                run_id=WorkflowRunId(row.id),
                status=WorkflowRunStatus(row.status),
                intent=None if row.intent is None else WorkflowIntent(row.intent),
                attempts=row.attempts,
            )
        row.status = WorkflowRunStatus.RUNNING.value
        row.attempts += 1
        row.leased_at = now
        row.lease_token = uuid4()
        row.finished_at = None
        row.error = None
        return self._claim(row)

    @staticmethod
    def _lease_filter(claim: WorkflowRunClaim) -> tuple[ColumnElement[bool], ...]:
        if claim.result is not WorkflowClaimResult.ACQUIRED or claim.lease_token is None:
            raise LostWorkflowLeaseError("workflow claim does not hold an active lease")
        return (
            WorkflowRun.id == claim.run_id,
            WorkflowRun.lease_token == claim.lease_token,
            WorkflowRun.status == WorkflowRunStatus.RUNNING.value,
        )

    async def set_intent(self, claim: WorkflowRunClaim, intent: WorkflowIntent) -> None:
        result = await self.session.execute(
            update(WorkflowRun).where(*self._lease_filter(claim)).values(intent=intent)
        )
        if _rowcount(result) != 1:
            raise LostWorkflowLeaseError("workflow claim is no longer active")

    async def set_error(self, claim: WorkflowRunClaim, error: str) -> None:
        result = await self.session.execute(
            update(WorkflowRun).where(*self._lease_filter(claim)).values(error=error)
        )
        if _rowcount(result) != 1:
            raise LostWorkflowLeaseError("workflow claim is no longer active")

    async def finish(
        self,
        claim: WorkflowRunClaim,
        status: WorkflowRunStatus,
        error: str | None = None,
    ) -> None:
        result = await self.session.execute(
            update(WorkflowRun)
            .where(*self._lease_filter(claim))
            .values(status=status.value, finished_at=datetime.now(UTC), error=error)
        )
        if _rowcount(result) != 1:
            raise LostWorkflowLeaseError("workflow claim is no longer active")


class SqlQuoteRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    @staticmethod
    def _quote(row: QuoteRecord) -> Quote:
        created_at = row.created_at
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=UTC)
        updated_at = row.updated_at
        if updated_at.tzinfo is None:
            updated_at = updated_at.replace(tzinfo=UTC)
        return Quote.model_validate(
            {
                "quote_id": row.id,
                "business_id": row.business_id,
                "conversation_id": row.conversation_id,
                "conversation_ref": {
                    "business_id": row.business_id,
                    "external_conversation_id": row.external_conversation_id,
                },
                "status": row.status,
                "revision": row.revision,
                "source_message_key": row.source_message_key,
                "last_message_key": row.last_message_key,
                "pending_request_text": row.pending_request_text,
                "draft": row.draft,
                "approval_correlation_id": row.approval_correlation_id,
                "delivery_receipt": row.delivery_receipt,
                "version": row.version,
                "created_at": created_at,
                "updated_at": updated_at,
            }
        )

    async def get(self, business_id: BusinessId, quote_id: QuoteId) -> Quote | None:
        row = await self.session.scalar(
            select(QuoteRecord).where(
                QuoteRecord.business_id == business_id,
                QuoteRecord.id == quote_id,
            )
        )
        return None if row is None else self._quote(row)

    async def get_active(
        self, business_id: BusinessId, conversation_id: ConversationId
    ) -> Quote | None:
        row = await self.session.scalar(
            select(QuoteRecord).where(
                QuoteRecord.business_id == business_id,
                QuoteRecord.active_conversation_id == conversation_id,
            )
        )
        return None if row is None else self._quote(row)

    async def get_by_message(
        self,
        business_id: BusinessId,
        conversation_id: ConversationId,
        message_key: MessageKey,
    ) -> Quote | None:
        row = await self.session.scalar(
            select(QuoteRecord).where(
                QuoteRecord.business_id == business_id,
                QuoteRecord.conversation_id == conversation_id,
                QuoteRecord.last_message_key == message_key,
            )
        )
        return None if row is None else self._quote(row)

    async def add(self, quote: Quote) -> None:
        conversation = await self.session.scalar(
            select(Conversation).where(Conversation.id == quote.conversation_id)
        )
        if (
            conversation is None
            or conversation.business_id != quote.business_id
            or conversation.external_conversation_id
            != quote.conversation_ref.external_conversation_id
        ):
            raise CrossBusinessReferenceError(
                "quote references a conversation from another business"
            )
        row = QuoteRecord(
            id=quote.quote_id,
            business_id=quote.business_id,
            conversation_id=quote.conversation_id,
            active_conversation_id=(quote.conversation_id if quote.is_active else None),
            external_conversation_id=quote.conversation_ref.external_conversation_id,
            status=quote.status.value,
            revision=quote.revision,
            source_message_key=quote.source_message_key,
            last_message_key=quote.last_message_key,
            pending_request_text=quote.pending_request_text,
            draft=(quote.draft.model_dump(mode="json") if quote.draft is not None else None),
            approval_correlation_id=quote.approval_correlation_id,
            delivery_receipt=(
                quote.delivery_receipt.model_dump(mode="json")
                if quote.delivery_receipt is not None
                else None
            ),
            version=quote.version,
            created_at=quote.created_at,
            updated_at=quote.updated_at,
        )
        try:
            async with self.session.begin_nested():
                self.session.add(row)
                await self.session.flush()
        except IntegrityError as error:
            raise QuoteConcurrencyError("quote already exists or conversation is active") from error

    async def save(self, quote: Quote, *, expected_version: int) -> None:
        result = await self.session.execute(
            update(QuoteRecord)
            .where(
                QuoteRecord.id == quote.quote_id,
                QuoteRecord.business_id == quote.business_id,
                QuoteRecord.version == expected_version,
            )
            .values(
                active_conversation_id=(quote.conversation_id if quote.is_active else None),
                status=quote.status.value,
                revision=quote.revision,
                last_message_key=quote.last_message_key,
                pending_request_text=quote.pending_request_text,
                draft=(quote.draft.model_dump(mode="json") if quote.draft is not None else None),
                approval_correlation_id=quote.approval_correlation_id,
                delivery_receipt=(
                    quote.delivery_receipt.model_dump(mode="json")
                    if quote.delivery_receipt is not None
                    else None
                ),
                version=quote.version,
                updated_at=quote.updated_at,
            )
        )
        if _rowcount(result) != 1:
            raise QuoteConcurrencyError("quote version is no longer current")


class SqlOutboxRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def enqueue(self, command: OutboxCommand) -> None:
        if command.outbound_message_id is not None:
            outbound = await self.session.scalar(
                select(OutboundMessage).where(OutboundMessage.id == command.outbound_message_id)
            )
            if outbound is None or outbound.business_id != command.business_id:
                raise CrossBusinessReferenceError(
                    "outbox command references an outbound message from another business"
                )
        if command.inbound_message_id is not None:
            inbound = await self.session.scalar(
                select(InboundMessage).where(InboundMessage.id == command.inbound_message_id)
            )
            if inbound is None or inbound.business_id != command.business_id:
                raise CrossBusinessReferenceError(
                    "outbox command references an inbound message from another business"
                )
        existing = (
            await self.session.scalar(
                select(OutboxMessage).where(
                    OutboxMessage.business_id == command.business_id,
                    OutboxMessage.dedup_key == command.dedup_key,
                )
            )
            if command.dedup_key is not None
            else None
        )
        if existing is not None:
            return
        row = OutboxMessage(
            id=command.command_id,
            business_id=command.business_id,
            outbound_message_id=command.outbound_message_id,
            inbound_message_id=command.inbound_message_id,
            command_type=command.command_type,
            payload=command.payload,
            status=OutboxStatus.PENDING.value,
            attempts=0,
            max_attempts=DEFAULT_MAX_ATTEMPTS,
            available_at=datetime.now(UTC),
            dedup_key=command.dedup_key,
        )
        try:
            async with self.session.begin_nested():
                self.session.add(row)
                await self.session.flush()
        except IntegrityError:
            return

    async def claim_batch(
        self, limit: int, now: datetime, claimed_by: str, *, stale_before: datetime
    ) -> list[OutboxRecord]:
        _validate_aware(now, "now")
        _validate_aware(stale_before, "stale_before")
        rows = list(
            (
                await self.session.scalars(
                    select(OutboxMessage)
                    .where(
                        or_(
                            and_(
                                OutboxMessage.status.in_(
                                    [OutboxStatus.PENDING.value, OutboxStatus.FAILED.value]
                                ),
                                OutboxMessage.available_at <= now,
                            ),
                            and_(
                                OutboxMessage.status == OutboxStatus.IN_PROGRESS.value,
                                OutboxMessage.locked_at.is_not(None),
                                OutboxMessage.locked_at <= stale_before,
                            ),
                        ),
                    )
                    .order_by(OutboxMessage.available_at)
                    .limit(limit)
                    .with_for_update(skip_locked=True)
                )
            ).all()
        )
        result: list[OutboxRecord] = []
        for row in rows:
            row.status = OutboxStatus.IN_PROGRESS.value
            row.attempts += 1
            row.locked_at = now
            row.locked_by = claimed_by
            result.append(
                OutboxRecord(
                    command=OutboxCommand(
                        command_id=row.id,
                        business_id=row.business_id,
                        command_type=row.command_type,
                        payload=row.payload,
                        dedup_key=row.dedup_key,
                        outbound_message_id=row.outbound_message_id,
                        inbound_message_id=row.inbound_message_id,
                    ),
                    status=OutboxStatus.IN_PROGRESS,
                    attempts=row.attempts,
                    max_attempts=row.max_attempts,
                    available_at=row.available_at,
                    last_error=row.last_error,
                    locked_by=row.locked_by,
                    claim_attempts=row.attempts,
                )
            )
        await self.session.flush()
        return result

    async def update(self, record: OutboxRecord) -> None:
        if (
            record.locked_by is None
            or record.claim_attempts is None
            or record.status is OutboxStatus.PENDING
        ):
            raise LostOutboxLeaseError("outbox record does not hold an active lease")
        result = await self.session.execute(
            update(OutboxMessage)
            .where(
                OutboxMessage.id == record.command.command_id,
                OutboxMessage.status == OutboxStatus.IN_PROGRESS.value,
                OutboxMessage.attempts == record.claim_attempts,
                OutboxMessage.locked_by == record.locked_by,
            )
            .values(
                status=record.status.value,
                attempts=record.attempts,
                max_attempts=record.max_attempts,
                available_at=record.available_at,
                last_error=record.last_error,
                locked_at=None,
                locked_by=None,
            )
        )
        if _rowcount(result) != 1:
            raise LostOutboxLeaseError("outbox record lease is no longer active")
