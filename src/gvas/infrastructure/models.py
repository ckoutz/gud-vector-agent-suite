from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from gvas.domain.identifiers import JsonValue
from gvas.domain.outbox import DEFAULT_MAX_ATTEMPTS
from gvas.infrastructure.db import json_type, metadata


class Base(DeclarativeBase):
    metadata = metadata


class Business(Base):
    __tablename__ = "businesses"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    slug: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class OwnerChannelEndpoint(Base):
    __tablename__ = "owner_channel_endpoints"
    __table_args__ = (
        UniqueConstraint("business_id", "id", name="uq_owner_channel_endpoints_business_id_id"),
        UniqueConstraint(
            "business_id",
            "source_namespace",
            "external_endpoint_id",
            name="uq_owner_channel_endpoints_source_endpoint",
        ),
        Index("ix_owner_channel_endpoints_business_id", "business_id"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    business_id: Mapped[UUID] = mapped_column(
        ForeignKey("businesses.id", ondelete="CASCADE"), nullable=False
    )
    source_namespace: Mapped[str] = mapped_column(String(100), nullable=False)
    external_endpoint_id: Mapped[str] = mapped_column(String(255), nullable=False)
    owner_external_id: Mapped[str | None] = mapped_column(String(255))
    routing: Mapped[dict[str, JsonValue]] = mapped_column(json_type, nullable=False)


class Conversation(Base):
    __tablename__ = "conversations"
    __table_args__ = (
        ForeignKeyConstraint(
            ["business_id", "endpoint_id"],
            ["owner_channel_endpoints.business_id", "owner_channel_endpoints.id"],
            ondelete="CASCADE",
        ),
        UniqueConstraint("business_id", "id"),
        UniqueConstraint("endpoint_id", "external_conversation_id"),
        Index("ix_conversations_endpoint_id", "endpoint_id"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    business_id: Mapped[UUID] = mapped_column(
        ForeignKey("businesses.id", ondelete="CASCADE"), nullable=False
    )
    endpoint_id: Mapped[UUID] = mapped_column(nullable=False)
    external_conversation_id: Mapped[str] = mapped_column(String(255), nullable=False)
    routing: Mapped[dict[str, JsonValue]] = mapped_column(json_type, nullable=False)


class InboundMessage(Base):
    __tablename__ = "inbound_messages"
    __table_args__ = (
        ForeignKeyConstraint(
            ["business_id", "endpoint_id"],
            ["owner_channel_endpoints.business_id", "owner_channel_endpoints.id"],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["business_id", "conversation_id"],
            ["conversations.business_id", "conversations.id"],
            ondelete="CASCADE",
        ),
        UniqueConstraint("business_id", "id"),
        UniqueConstraint("endpoint_id", "message_key"),
        Index("ix_inbound_messages_conversation_id", "conversation_id"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    business_id: Mapped[UUID] = mapped_column(
        ForeignKey("businesses.id", ondelete="CASCADE"), nullable=False
    )
    endpoint_id: Mapped[UUID] = mapped_column(nullable=False)
    conversation_id: Mapped[UUID] = mapped_column(nullable=False)
    message_key: Mapped[str] = mapped_column(String(255), nullable=False)
    sender_external_id: Mapped[str] = mapped_column(String(255), nullable=False)
    sender_role: Mapped[str] = mapped_column(String(50), nullable=False)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    parts: Mapped[list[JsonValue]] = mapped_column(json_type, nullable=False)
    reply_to: Mapped[dict[str, JsonValue] | None] = mapped_column(json_type)
    routing: Mapped[dict[str, JsonValue]] = mapped_column(json_type, nullable=False)


class OutboundMessage(Base):
    __tablename__ = "outbound_messages"
    __table_args__ = (
        ForeignKeyConstraint(
            ["business_id", "conversation_id"],
            ["conversations.business_id", "conversations.id"],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["business_id", "inbound_message_id"],
            ["inbound_messages.business_id", "inbound_messages.id"],
            ondelete="CASCADE",
        ),
        UniqueConstraint("business_id", "id"),
        UniqueConstraint("inbound_message_id", "correlation_id"),
        Index("ix_outbound_messages_conversation_id", "conversation_id"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    business_id: Mapped[UUID] = mapped_column(
        ForeignKey("businesses.id", ondelete="CASCADE"), nullable=False
    )
    conversation_id: Mapped[UUID] = mapped_column(nullable=False)
    inbound_message_id: Mapped[UUID | None] = mapped_column()
    parts: Mapped[list[JsonValue]] = mapped_column(json_type, nullable=False)
    reply_to: Mapped[dict[str, JsonValue] | None] = mapped_column(json_type)
    status: Mapped[str] = mapped_column(String(50), nullable=False)
    provider_message_id: Mapped[str | None] = mapped_column(String(255))
    correlation_id: Mapped[str] = mapped_column(String(255), nullable=False)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    delivery_detail: Mapped[str | None] = mapped_column(Text)


class WorkflowRun(Base):
    __tablename__ = "workflow_runs"
    __table_args__ = (
        ForeignKeyConstraint(
            ["business_id", "inbound_message_id"],
            ["inbound_messages.business_id", "inbound_messages.id"],
            ondelete="CASCADE",
        ),
        UniqueConstraint("inbound_message_id"),
        Index("ix_workflow_runs_business_id", "business_id"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    business_id: Mapped[UUID] = mapped_column(
        ForeignKey("businesses.id", ondelete="CASCADE"), nullable=False
    )
    inbound_message_id: Mapped[UUID] = mapped_column(nullable=False)
    intent: Mapped[str | None] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(50), nullable=False)
    attempts: Mapped[int] = mapped_column(nullable=False, default=0)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    leased_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error: Mapped[str | None] = mapped_column(Text)


class OutboxMessage(Base):
    __tablename__ = "outbox_messages"
    __table_args__ = (
        ForeignKeyConstraint(
            ["business_id", "outbound_message_id"],
            ["outbound_messages.business_id", "outbound_messages.id"],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["business_id", "inbound_message_id"],
            ["inbound_messages.business_id", "inbound_messages.id"],
            ondelete="CASCADE",
        ),
        UniqueConstraint("business_id", "dedup_key"),
        UniqueConstraint("outbound_message_id"),
        UniqueConstraint("inbound_message_id"),
        Index("ix_outbox_messages_claim", "status", "available_at"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    business_id: Mapped[UUID] = mapped_column(
        ForeignKey("businesses.id", ondelete="CASCADE"), nullable=False
    )
    outbound_message_id: Mapped[UUID | None] = mapped_column()
    inbound_message_id: Mapped[UUID | None] = mapped_column()
    command_type: Mapped[str] = mapped_column(String(255), nullable=False)
    payload: Mapped[dict[str, JsonValue]] = mapped_column(json_type, nullable=False)
    status: Mapped[str] = mapped_column(String(50), nullable=False)
    attempts: Mapped[int] = mapped_column(nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(nullable=False, default=DEFAULT_MAX_ATTEMPTS)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_error: Mapped[str | None] = mapped_column(Text)
    dedup_key: Mapped[str | None] = mapped_column(String(255))
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    locked_by: Mapped[str | None] = mapped_column(String(255))
