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
    DispositionStatus,
    OfflineDisposition,
    PresenceSource,
    PresenceState,
    QueueEntryStatus,
    TransferKind,
    TransferStatus,
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
        Index(
            "uq_conversation_assignments_active_conversation",
            "tenant_id",
            "conversation_reference",
            unique=True,
            postgresql_where=sa.text("status = 'ASSIGNED'"),
            sqlite_where=sa.text("status = 'ASSIGNED'"),
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
    # Nullable: a dispatch decision has no human actor, and a sentinel would
    # be a worse lie than an absence. Every actor-initiated command writes it.
    actor_reference: Mapped[str | None] = mapped_column(String(160), nullable=True)


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
        Index(
            "uq_inbox_queue_entries_active_conversation",
            "tenant_id",
            "conversation_reference",
            unique=True,
            postgresql_where=sa.text("status = 'QUEUED'"),
            sqlite_where=sa.text("status = 'QUEUED'"),
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


class InboxRoutingDecision(Base, TimestampMixin):
    """Append-only evidence for the rule that admitted a conversation."""

    __tablename__ = "inbox_routing_decisions"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "id", name="uq_inbox_routing_decisions_tenant_id_id"
        ),
        UniqueConstraint(
            "tenant_id",
            "decision_reference",
            name="uq_inbox_routing_decisions_tenant_reference",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "rule_id"],
            [
                f"{SCHEMA}.inbox_routing_rules.tenant_id",
                f"{SCHEMA}.inbox_routing_rules.id",
            ],
            name="fk_inbox_routing_decisions_tenant_rule",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "queue_id"],
            [f"{SCHEMA}.inbox_queues.tenant_id", f"{SCHEMA}.inbox_queues.id"],
            name="fk_inbox_routing_decisions_tenant_queue",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "queue_entry_id"],
            [
                f"{SCHEMA}.inbox_queue_entries.tenant_id",
                f"{SCHEMA}.inbox_queue_entries.id",
            ],
            name="fk_inbox_routing_decisions_tenant_queue_entry",
        ),
        Index(
            "ix_inbox_routing_decisions_tenant_conversation_time",
            "tenant_id",
            "conversation_reference",
            "decided_at",
        ),
        schema_table_args(SCHEMA),
    )
    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(), ForeignKey(Tenant.__table__.c.id, ondelete="CASCADE"), nullable=False
    )
    decision_reference: Mapped[str] = mapped_column(String(180), nullable=False)
    conversation_reference: Mapped[str] = mapped_column(String(180), nullable=False)
    channel_code: Mapped[str] = mapped_column(String(80), nullable=False)
    rule_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    queue_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    queue_entry_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    priority: Mapped[int] = mapped_column(Integer(), nullable=False)
    decided_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


def _enum(python_type: type, name: str) -> sa.Enum:
    """Portable, constrained, value-stored enum — the shape every column above
    already uses, named once now that ten of them exist."""
    return sa.Enum(
        python_type,
        name=name,
        native_enum=False,
        values_callable=lambda cls: [member.value for member in cls],
        create_constraint=True,
    )


class InboxPresenceEvent(Base, TimestampMixin):
    """Append-only evidence of every presence TRANSITION.

    Transitions only, never heartbeats. A browser beating every thirty seconds
    would bury the handful of rows that answer "who put this agent on break, and
    why" under thousands that say nothing changed. The manager-override
    columns are nullable because an agent's own choice has no actor other than
    themselves, but `override_agent_presence` refuses to write without both.
    """

    __tablename__ = "inbox_presence_events"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "id", name="uq_inbox_presence_events_tenant_id_id"
        ),
        Index(
            "ix_inbox_presence_events_tenant_agent_time",
            "tenant_id",
            "agent_reference",
            "occurred_at",
        ),
        sa.CheckConstraint(
            "(source <> 'MANAGER') OR "
            "(actor_reference IS NOT NULL AND reason IS NOT NULL)",
            name="ck_inbox_presence_events_manager_evidence",
        ),
        schema_table_args(SCHEMA),
    )
    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(), ForeignKey(Tenant.__table__.c.id, ondelete="CASCADE"), nullable=False
    )
    agent_reference: Mapped[str] = mapped_column(String(160), nullable=False)
    previous_state: Mapped[PresenceState | None] = mapped_column(
        _enum(PresenceState, "inbox_presence_event_previous_state"), nullable=True
    )
    state: Mapped[PresenceState] = mapped_column(
        _enum(PresenceState, "inbox_presence_event_state"), nullable=False
    )
    previous_capacity: Mapped[int | None] = mapped_column(Integer(), nullable=True)
    assignment_capacity: Mapped[int] = mapped_column(Integer(), nullable=False)
    source: Mapped[PresenceSource] = mapped_column(
        _enum(PresenceSource, "inbox_presence_source"), nullable=False
    )
    actor_reference: Mapped[str | None] = mapped_column(String(160), nullable=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


class InboxTransferRequest(Base, TimestampMixin):
    """One record for every ownership move, warm or cold.

    A cold transfer lands here already `ACCEPTED`; a warm one arrives
    `REQUESTED` and settles later. Giving both the same row is what makes
    "who moved this conversation, from whom, to whom, and why" one query rather
    than two half-answers, and it is where the supervisor override is recorded
    as an override rather than disappearing into a normal-looking move.
    """

    __tablename__ = "inbox_transfer_requests"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "id", name="uq_inbox_transfer_requests_tenant_id_id"
        ),
        ForeignKeyConstraint(
            ["tenant_id", "source_assignment_id"],
            [
                f"{SCHEMA}.conversation_assignments.tenant_id",
                f"{SCHEMA}.conversation_assignments.id",
            ],
            name="fk_inbox_transfer_requests_tenant_source_assignment",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "resulting_assignment_id"],
            [
                f"{SCHEMA}.conversation_assignments.tenant_id",
                f"{SCHEMA}.conversation_assignments.id",
            ],
            name="fk_inbox_transfer_requests_tenant_resulting_assignment",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "from_queue_id"],
            [f"{SCHEMA}.inbox_queues.tenant_id", f"{SCHEMA}.inbox_queues.id"],
            name="fk_inbox_transfer_requests_tenant_from_queue",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "to_queue_id"],
            [f"{SCHEMA}.inbox_queues.tenant_id", f"{SCHEMA}.inbox_queues.id"],
            name="fk_inbox_transfer_requests_tenant_to_queue",
        ),
        # At most one open warm request per conversation. Two people cannot
        # both be deciding whether to take the same conversation.
        Index(
            "uq_inbox_transfer_requests_open_conversation",
            "tenant_id",
            "conversation_reference",
            unique=True,
            postgresql_where=sa.text("status = 'REQUESTED'"),
            sqlite_where=sa.text("status = 'REQUESTED'"),
        ),
        Index(
            "ix_inbox_transfer_requests_tenant_status_expiry",
            "tenant_id",
            "status",
            "expires_at",
        ),
        Index(
            "ix_inbox_transfer_requests_tenant_target_status",
            "tenant_id",
            "to_agent_reference",
            "status",
        ),
        sa.CheckConstraint(
            "(supervisor_override = false) OR (override_reason IS NOT NULL)",
            name="ck_inbox_transfer_requests_override_reason",
        ),
        sa.CheckConstraint(
            "(kind <> 'WARM') OR (expires_at IS NOT NULL)",
            name="ck_inbox_transfer_requests_warm_has_sla",
        ),
        sa.CheckConstraint(
            "(status = 'REQUESTED') = (settled_at IS NULL)",
            name="ck_inbox_transfer_requests_settled_coherence",
        ),
        sa.CheckConstraint(
            "(resulting_assignment_id IS NULL) OR (status = 'ACCEPTED')",
            name="ck_inbox_transfer_requests_result_only_when_accepted",
        ),
        schema_table_args(SCHEMA),
    )
    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(), ForeignKey(Tenant.__table__.c.id, ondelete="CASCADE"), nullable=False
    )
    conversation_reference: Mapped[str] = mapped_column(String(180), nullable=False)
    kind: Mapped[TransferKind] = mapped_column(
        _enum(TransferKind, "inbox_transfer_kind"), nullable=False
    )
    status: Mapped[TransferStatus] = mapped_column(
        _enum(TransferStatus, "inbox_transfer_status"), nullable=False
    )
    source_assignment_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    resulting_assignment_id: Mapped[UUID | None] = mapped_column(Uuid(), nullable=True)
    from_agent_reference: Mapped[str] = mapped_column(String(160), nullable=False)
    to_agent_reference: Mapped[str] = mapped_column(String(160), nullable=False)
    from_queue_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    to_queue_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    requested_by_reference: Mapped[str] = mapped_column(String(160), nullable=False)
    requested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    settled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    settled_by_reference: Mapped[str | None] = mapped_column(String(160), nullable=True)
    settlement_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    supervisor_override: Mapped[bool] = mapped_column(
        Boolean(), nullable=False, default=False
    )
    override_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    notify_reference: Mapped[str | None] = mapped_column(String(160), nullable=True)


class InboxEscalationRequest(Base, TimestampMixin):
    """Append-only evidence that an agent ASKED for an escalation.

    Not an escalation. `dotmac-operational-escalations` owns whether one should
    exist, under which policy version, at what level, and who answered it —
    across tickets, outages and inboxes alike. Two tables answering "is this
    escalated?" is precisely the drift the one-writer rule exists to prevent.

    So there is no status column here, and no acknowledge or resolve command:
    nothing about this row can be settled, because settling is not this
    module's decision. `severity` is an opaque string handed straight to the
    escalation owner rather than a vocabulary declared twice. `dedup_key` is
    unique per tenant and is the same key that owner dedupes on, so a retried
    request cannot become two escalations on either side.

    What it does own is the conversation's timeline: this conversation, at this
    time, by this agent, for this reason. There is deliberately no
    target-agent column, so an escalation that reassigns is not expressible.
    """

    __tablename__ = "inbox_escalation_requests"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "id", name="uq_inbox_escalation_requests_tenant_id_id"
        ),
        UniqueConstraint(
            "tenant_id",
            "dedup_key",
            name="uq_inbox_escalation_requests_tenant_dedup_key",
        ),
        Index(
            "ix_inbox_escalation_requests_tenant_conversation_time",
            "tenant_id",
            "conversation_reference",
            "requested_at",
        ),
        schema_table_args(SCHEMA),
    )
    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(), ForeignKey(Tenant.__table__.c.id, ondelete="CASCADE"), nullable=False
    )
    conversation_reference: Mapped[str] = mapped_column(String(180), nullable=False)
    dedup_key: Mapped[str] = mapped_column(String(180), nullable=False)
    severity: Mapped[str] = mapped_column(String(40), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    requested_by_reference: Mapped[str] = mapped_column(String(160), nullable=False)
    notify_reference: Mapped[str] = mapped_column(String(160), nullable=False)
    assignment_id: Mapped[UUID | None] = mapped_column(Uuid(), nullable=True)
    requested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    due_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class InboxOfflineDisposition(Base, TimestampMixin):
    """One held conversation waiting out an absent agent's grace period.

    The row is the durable answer to "what is supposed to happen to this
    conversation, and when". Holding it in a scheduler's memory would lose every
    pending decision on the next deploy — the same failure the round-robin
    cursor was made durable to fix.
    """

    __tablename__ = "inbox_offline_dispositions"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "id", name="uq_inbox_offline_dispositions_tenant_id_id"
        ),
        ForeignKeyConstraint(
            ["tenant_id", "assignment_id"],
            [
                f"{SCHEMA}.conversation_assignments.tenant_id",
                f"{SCHEMA}.conversation_assignments.id",
            ],
            ondelete="CASCADE",
            name="fk_inbox_offline_dispositions_tenant_assignment",
        ),
        # One pending disposition per assignment: a double sign-out must not
        # queue the same conversation for requeue twice.
        Index(
            "uq_inbox_offline_dispositions_pending_assignment",
            "tenant_id",
            "assignment_id",
            unique=True,
            postgresql_where=sa.text("status = 'PENDING'"),
            sqlite_where=sa.text("status = 'PENDING'"),
        ),
        Index(
            "ix_inbox_offline_dispositions_tenant_status_due",
            "tenant_id",
            "status",
            "due_at",
        ),
        sa.CheckConstraint(
            "(status = 'PENDING') = (settled_at IS NULL)",
            name="ck_inbox_offline_dispositions_settled_coherence",
        ),
        schema_table_args(SCHEMA),
    )
    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(), ForeignKey(Tenant.__table__.c.id, ondelete="CASCADE"), nullable=False
    )
    agent_reference: Mapped[str] = mapped_column(String(160), nullable=False)
    assignment_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    conversation_reference: Mapped[str] = mapped_column(String(180), nullable=False)
    disposition: Mapped[OfflineDisposition] = mapped_column(
        _enum(OfflineDisposition, "inbox_offline_disposition"), nullable=False
    )
    status: Mapped[DispositionStatus] = mapped_column(
        _enum(DispositionStatus, "inbox_disposition_status"), nullable=False
    )
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    notify_reference: Mapped[str | None] = mapped_column(String(160), nullable=True)
    escalation_severity: Mapped[str | None] = mapped_column(String(40), nullable=True)
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    settled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    settlement_note: Mapped[str | None] = mapped_column(Text, nullable=True)


TENANT_TABLES = (
    "inbox_queues",
    "inbox_routing_rules",
    "inbox_agent_presence",
    "conversation_assignments",
    "inbox_workflow_events",
    "inbox_queue_entries",
    "inbox_round_robin_cursors",
    "inbox_routing_decisions",
    "inbox_presence_events",
    "inbox_transfer_requests",
    "inbox_escalation_requests",
    "inbox_offline_dispositions",
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
        InboxRoutingDecision,
        InboxPresenceEvent,
        InboxTransferRequest,
        InboxEscalationRequest,
        InboxOfflineDisposition,
    )
}


def metadata_table(name: str) -> sa.Table:
    return _TABLES[name]


__all__ = [
    "SCHEMA",
    "TENANT_TABLES",
    "ConversationAssignment",
    "InboxAgentPresence",
    "InboxEscalationRequest",
    "InboxOfflineDisposition",
    "InboxPresenceEvent",
    "InboxQueue",
    "InboxQueueEntry",
    "InboxRoundRobinCursor",
    "InboxRoutingDecision",
    "InboxRoutingRule",
    "InboxTransferRequest",
    "InboxWorkflowEvent",
    "metadata_table",
]
