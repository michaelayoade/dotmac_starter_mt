"""Tenant-scoped persistence for the reusable conversation aggregate."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

import sqlalchemy as sa
from dotmac_kernel.models import Base, Tenant, TimestampMixin, uuid_pk
from dotmac_kernel.namespaces import module_schema, schema_table_args
from sqlalchemy import (
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column

SCHEMA = module_schema("inbox")
_JSON = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


class Conversation(Base, TimestampMixin):
    """One durable exchange with an external party through one channel."""

    __tablename__ = "conversations"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_conversations_tenant_id_id"),
        UniqueConstraint(
            "tenant_id", "thread_key", name="uq_conversations_tenant_thread"
        ),
        Index(
            "ix_conversations_tenant_status_activity",
            "tenant_id",
            "status",
            "last_message_at",
        ),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(), ForeignKey(Tenant.__table__.c.id, ondelete="CASCADE"), nullable=False
    )
    channel: Mapped[str] = mapped_column(String(40), nullable=False)
    account_scope: Mapped[str] = mapped_column(String(160), nullable=False)
    contact: Mapped[str] = mapped_column(String(320), nullable=False)
    thread_key: Mapped[str] = mapped_column(String(512), nullable=False)
    transport_thread_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    status_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)
    subject: Mapped[str | None] = mapped_column(String(255), nullable=True)
    tags: Mapped[list[str] | None] = mapped_column(_JSON, nullable=True)
    first_message_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_message_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    resolved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    snoozed_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class Message(Base, TimestampMixin):
    """One ordered message in a conversation."""

    __tablename__ = "messages"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "conversation_id"],
            [f"{SCHEMA}.conversations.tenant_id", f"{SCHEMA}.conversations.id"],
            name="fk_messages_conversation",
            ondelete="CASCADE",
        ),
        UniqueConstraint("tenant_id", "message_key", name="uq_messages_tenant_key"),
        UniqueConstraint(
            "tenant_id",
            "conversation_id",
            "id",
            name="uq_messages_conversation_id_id",
        ),
        Index(
            "ix_messages_tenant_conversation_time",
            "tenant_id",
            "conversation_id",
            "occurred_at",
            "id",
        ),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(), ForeignKey(Tenant.__table__.c.id, ondelete="CASCADE"), nullable=False
    )
    conversation_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    channel: Mapped[str] = mapped_column(String(40), nullable=False)
    direction: Mapped[str] = mapped_column(String(16), nullable=False)
    message_key: Mapped[str] = mapped_column(String(512), nullable=False)
    subject: Mapped[str | None] = mapped_column(String(255), nullable=True)
    body: Mapped[str | None] = mapped_column(Text, nullable=True)
    transport_message_ref: Mapped[str | None] = mapped_column(
        String(255), nullable=True
    )
    transport_observation_ref: Mapped[str | None] = mapped_column(
        String(255), nullable=True
    )
    author_id: Mapped[UUID | None] = mapped_column(Uuid(), nullable=True)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


class ConversationReadState(Base, TimestampMixin):
    """One operator's monotonic read cursor on one conversation."""

    __tablename__ = "conversation_read_states"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "conversation_id"],
            [f"{SCHEMA}.conversations.tenant_id", f"{SCHEMA}.conversations.id"],
            name="fk_conversation_read_states_conversation",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "conversation_id", "last_read_message_id"],
            [
                f"{SCHEMA}.messages.tenant_id",
                f"{SCHEMA}.messages.conversation_id",
                f"{SCHEMA}.messages.id",
            ],
            name="fk_conversation_read_states_message",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "tenant_id",
            "conversation_id",
            "actor_id",
            name="uq_conversation_read_states_actor",
        ),
        Index(
            "ix_conversation_read_states_actor",
            "tenant_id",
            "actor_id",
            "last_read_at",
        ),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(), ForeignKey(Tenant.__table__.c.id, ondelete="CASCADE"), nullable=False
    )
    conversation_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    actor_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    last_read_message_id: Mapped[UUID | None] = mapped_column(Uuid(), nullable=True)
    last_read_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


TENANT_MODELS = (Conversation, Message, ConversationReadState)
TENANT_TABLES = tuple(model.__tablename__ for model in TENANT_MODELS)

__all__ = [
    "SCHEMA",
    "TENANT_MODELS",
    "TENANT_TABLES",
    "Conversation",
    "ConversationReadState",
    "Message",
]
