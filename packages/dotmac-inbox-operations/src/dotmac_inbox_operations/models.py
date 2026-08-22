"""Staffed inbox-operations persistence contract."""

from __future__ import annotations

from datetime import datetime
from typing import cast
from uuid import UUID

import sqlalchemy as sa
from dotmac_kernel.models import Base, Tenant, TimestampMixin, uuid_pk
from dotmac_kernel.namespaces import module_schema, schema_table_args
from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column

from dotmac_inbox_operations.contracts import (
    AssignmentStatus,
    PresenceState,
    QueueEntryStatus,
)

SCHEMA = module_schema("inbox_ops")


class InboxQueue(Base, TimestampMixin):
    __tablename__ = "inbox_queues"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_inbox_queues_tenant_id_id"),
        UniqueConstraint("tenant_id", "code", name="uq_inbox_queues_tenant_code"),
        schema_table_args(SCHEMA),
    )
    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(), ForeignKey(Tenant.__table__.c.id, ondelete="CASCADE"), nullable=False
    )
    code: Mapped[str] = mapped_column(String(80), nullable=False)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    active: Mapped[bool] = mapped_column(Boolean(), nullable=False, default=True)


class InboxRoutingRule(Base, TimestampMixin):
    __tablename__ = "inbox_routing_rules"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_inbox_routing_rules_tenant_id_id"),
        UniqueConstraint(
            "tenant_id",
            "queue_id",
            "channel_code",
            name="uq_inbox_routing_rules_tenant_queue_channel",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "queue_id"],
            [f"{SCHEMA}.inbox_queues.tenant_id", f"{SCHEMA}.inbox_queues.id"],
            ondelete="CASCADE",
            name="fk_inbox_routing_rules_tenant_queue",
        ),
        Index("ix_inbox_routing_rules_tenant_channel", "tenant_id", "channel_code"),
        schema_table_args(SCHEMA),
    )
    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(), ForeignKey(Tenant.__table__.c.id, ondelete="CASCADE"), nullable=False
    )
    queue_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    channel_code: Mapped[str] = mapped_column(String(80), nullable=False)
    priority: Mapped[int] = mapped_column(Integer(), nullable=False)
    active: Mapped[bool] = mapped_column(Boolean(), nullable=False, default=True)


class InboxAgentPresence(Base, TimestampMixin):
    __tablename__ = "inbox_agent_presence"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "id", name="uq_inbox_agent_presence_tenant_id_id"
        ),
        UniqueConstraint(
            "tenant_id",
            "agent_reference",
            name="uq_inbox_agent_presence_tenant_agent",
        ),
        schema_table_args(SCHEMA),
    )
    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(), ForeignKey(Tenant.__table__.c.id, ondelete="CASCADE"), nullable=False
    )
    agent_reference: Mapped[str] = mapped_column(String(160), nullable=False)
    state: Mapped[PresenceState] = mapped_column(
        sa.Enum(
            PresenceState,
            name="inbox_presence_state",
            native_enum=False,
            values_callable=lambda cls: [member.value for member in cls],
            create_constraint=True,
        ),
        nullable=False,
    )
    assignment_capacity: Mapped[int] = mapped_column(Integer(), nullable=False)
    observed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


class ConversationAssignment(Base, TimestampMixin):
    __tablename__ = "conversation_assignments"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "id", name="uq_conversation_assignments_tenant_id_id"
        ),
        UniqueConstraint(
            "tenant_id",
            "conversation_reference",
            name="uq_conversation_assignments_tenant_conversation",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "queue_id"],
            [f"{SCHEMA}.inbox_queues.tenant_id", f"{SCHEMA}.inbox_queues.id"],
            name="fk_conversation_assignments_tenant_queue",
        ),
        Index(
            "ix_conversation_assignments_tenant_agent_status",
            "tenant_id",
            "agent_reference",
            "status",
        ),
        schema_table_args(SCHEMA),
    )
    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(), ForeignKey(Tenant.__table__.c.id, ondelete="CASCADE"), nullable=False
    )
    conversation_reference: Mapped[str] = mapped_column(String(180), nullable=False)
    queue_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    agent_reference: Mapped[str] = mapped_column(String(160), nullable=False)
    status: Mapped[AssignmentStatus] = mapped_column(
        sa.Enum(
            AssignmentStatus,
            name="inbox_assignment_status",
            native_enum=False,
            values_callable=lambda cls: [member.value for member in cls],
            create_constraint=True,
        ),
        nullable=False,
    )
    assigned_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    released_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class InboxWorkflowEvent(Base, TimestampMixin):
    __tablename__ = "inbox_workflow_events"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "id", name="uq_inbox_workflow_events_tenant_id_id"
        ),
        ForeignKeyConstraint(
            ["tenant_id", "assignment_id"],
            [
                f"{SCHEMA}.conversation_assignments.tenant_id",
                f"{SCHEMA}.conversation_assignments.id",
            ],
            ondelete="CASCADE",
            name="fk_inbox_workflow_events_tenant_assignment",
        ),
        Index(
            "ix_inbox_workflow_events_tenant_assignment_time",
            "tenant_id",
            "assignment_id",
            "occurred_at",
        ),
        schema_table_args(SCHEMA),
    )
    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(), ForeignKey(Tenant.__table__.c.id, ondelete="CASCADE"), nullable=False
    )
    assignment_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    event_type: Mapped[str] = mapped_column(String(60), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    reason: Mapped[str] = mapped_column(Text, nullable=False)


class InboxQueueEntry(Base, TimestampMixin):
    """Durable FIFO admission evidence for one conversation in one queue.

    Ported from Sub's `inbox_conversation_queue_entries`. The position is a
    real column with a unique constraint per queue rather than an ordering
    derived at read time, which is what makes the customer-visible answer to
    "where am I in the line" stable across restarts and readers.
    """

    __tablename__ = "inbox_queue_entries"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_inbox_queue_entries_tenant_id_id"),
        UniqueConstraint(
            "tenant_id",
            "conversation_reference",
            name="uq_inbox_queue_entries_tenant_conversation",
        ),
        UniqueConstraint(
            "tenant_id",
            "queue_id",
            "queue_position",
            name="uq_inbox_queue_entries_tenant_queue_position",
        ),
        sa.CheckConstraint(
            "queue_position > 0", name="ck_inbox_queue_entries_position_positive"
        ),
        ForeignKeyConstraint(
            ["tenant_id", "queue_id"],
            [f"{SCHEMA}.inbox_queues.tenant_id", f"{SCHEMA}.inbox_queues.id"],
            ondelete="CASCADE",
            name="fk_inbox_queue_entries_tenant_queue",
        ),
        Index(
            "ix_inbox_queue_entries_tenant_queue_status_position",
            "tenant_id",
            "queue_id",
            "status",
            "queue_position",
        ),
        schema_table_args(SCHEMA),
    )
    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(), ForeignKey(Tenant.__table__.c.id, ondelete="CASCADE"), nullable=False
    )
    queue_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    conversation_reference: Mapped[str] = mapped_column(String(160), nullable=False)
    queue_position: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[QueueEntryStatus] = mapped_column(
        sa.Enum(
            QueueEntryStatus,
            name="inbox_queue_entry_status",
            native_enum=False,
            values_callable=lambda cls: [member.value for member in cls],
            create_constraint=True,
        ),
        nullable=False,
    )
    entered_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    settled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class InboxRoundRobinCursor(Base, TimestampMixin):
    """Durable per-queue rotation state.

    Ported from Sub's `inbox_team_round_robin_cursors`. Rotation that lives in
    memory restarts at the same agent after every deploy, which is the fairness
    bug the source made durable to fix.
    """

    __tablename__ = "inbox_round_robin_cursors"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "id", name="uq_inbox_round_robin_cursors_tenant_id_id"
        ),
        UniqueConstraint(
            "tenant_id", "queue_id", name="uq_inbox_round_robin_cursors_tenant_queue"
        ),
        ForeignKeyConstraint(
            ["tenant_id", "queue_id"],
            [f"{SCHEMA}.inbox_queues.tenant_id", f"{SCHEMA}.inbox_queues.id"],
            ondelete="CASCADE",
            name="fk_inbox_round_robin_cursors_tenant_queue",
        ),
        schema_table_args(SCHEMA),
    )
    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(), ForeignKey(Tenant.__table__.c.id, ondelete="CASCADE"), nullable=False
    )
    queue_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    last_assigned_agent_reference: Mapped[str | None] = mapped_column(
        String(160), nullable=True
    )
    rotation_count: Mapped[int] = mapped_column(Integer, nullable=False)


TENANT_TABLES = (
    "inbox_queues",
    "inbox_routing_rules",
    "inbox_agent_presence",
    "conversation_assignments",
    "inbox_workflow_events",
    "inbox_queue_entries",
    "inbox_round_robin_cursors",
)
_TABLES: dict[str, sa.Table] = {
    model.__tablename__: cast(sa.Table, model.__table__)
    for model in (
        InboxQueue,
        InboxRoutingRule,
        InboxAgentPresence,
        ConversationAssignment,
        InboxWorkflowEvent,
        InboxQueueEntry,
        InboxRoundRobinCursor,
    )
}


def metadata_table(name: str) -> sa.Table:
    return _TABLES[name]


__all__ = [
    "SCHEMA",
    "TENANT_TABLES",
    "ConversationAssignment",
    "InboxAgentPresence",
    "InboxQueue",
    "InboxQueueEntry",
    "InboxRoundRobinCursor",
    "InboxRoutingRule",
    "InboxWorkflowEvent",
    "metadata_table",
]
