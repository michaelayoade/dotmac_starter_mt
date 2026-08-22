"""Tenant-scoped persistence for physical work execution."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from dotmac_kernel.models import Base, Tenant, TimestampMixin, uuid_pk
from dotmac_kernel.namespaces import module_schema, schema_table_args
from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

SCHEMA = module_schema("workorders")


class WorkOrder(Base, TimestampMixin):
    __tablename__ = "work_orders"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_work_orders_tenant_id_id"),
        UniqueConstraint(
            "tenant_id", "public_id", name="uq_work_orders_tenant_public_id"
        ),
        Index("ix_work_orders_tenant_status", "tenant_id", "status"),
        Index("ix_work_orders_tenant_assignee", "tenant_id", "current_assignee_id"),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(), ForeignKey(Tenant.__table__.c.id, ondelete="CASCADE"), nullable=False
    )
    public_id: Mapped[str] = mapped_column(String(80), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    priority: Mapped[str] = mapped_column(String(20), nullable=False)
    work_type: Mapped[str | None] = mapped_column(String(80))
    current_assignee_id: Mapped[UUID | None] = mapped_column(Uuid())
    current_assignee_kind: Mapped[str | None] = mapped_column(String(40))
    scheduled_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    scheduled_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    estimated_duration_minutes: Mapped[int | None] = mapped_column(Integer)
    address: Mapped[str | None] = mapped_column(String(255))
    access_notes: Mapped[str | None] = mapped_column(Text)
    required_skills: Mapped[list[str]] = mapped_column(
        JSON, default=list, nullable=False
    )
    tags: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    minimum_photo_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    customer_signoff_required: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )
    signature_unavailable_reason_allowed: Mapped[bool] = mapped_column(
        Boolean, default=True, nullable=False
    )
    required_evidence_kinds: Mapped[list[str]] = mapped_column(
        JSON, default=list, nullable=False
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    paused_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    total_active_seconds: Mapped[int] = mapped_column(
        Integer, default=0, nullable=False
    )
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class _WorkOrderChild:
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(), ForeignKey(Tenant.__table__.c.id, ondelete="CASCADE"), nullable=False
    )
    work_order_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)


class WorkOrderAssignment(Base, _WorkOrderChild, TimestampMixin):
    __tablename__ = "work_order_assignments"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "id", name="uq_work_order_assignments_tenant_id_id"
        ),
        UniqueConstraint(
            "tenant_id",
            "client_assignment_id",
            name="uq_work_order_assignments_tenant_client",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "work_order_id"],
            [f"{SCHEMA}.work_orders.tenant_id", f"{SCHEMA}.work_orders.id"],
            ondelete="CASCADE",
            name="fk_work_order_assignments_work_order",
        ),
        Index(
            "ix_work_order_assignments_tenant_work_order",
            "tenant_id",
            "work_order_id",
        ),
        Index(
            "uq_work_order_assignments_one_active",
            "tenant_id",
            "work_order_id",
            unique=True,
            postgresql_where=text("active"),
            sqlite_where=text("active = 1"),
        ),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    assignee_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    assignee_kind: Mapped[str] = mapped_column(String(40), nullable=False)
    assigned_by_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    client_assignment_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    reason: Mapped[str | None] = mapped_column(Text)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    assigned_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    unassigned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    unassigned_by_id: Mapped[UUID | None] = mapped_column(Uuid())
    unassignment_reason: Mapped[str | None] = mapped_column(Text)


class WorkOrderEvent(Base, _WorkOrderChild):
    __tablename__ = "work_order_events"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_work_order_events_tenant_id_id"),
        UniqueConstraint(
            "tenant_id",
            "client_event_id",
            name="uq_work_order_events_tenant_client",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "work_order_id"],
            [f"{SCHEMA}.work_orders.tenant_id", f"{SCHEMA}.work_orders.id"],
            ondelete="CASCADE",
            name="fk_work_order_events_work_order",
        ),
        Index(
            "ix_work_order_events_tenant_work_order_occurred",
            "tenant_id",
            "work_order_id",
            "occurred_at",
        ),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    actor_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    event: Mapped[str] = mapped_column(String(40), nullable=False)
    previous_status: Mapped[str] = mapped_column(String(20), nullable=False)
    new_status: Mapped[str] = mapped_column(String(20), nullable=False)
    latitude: Mapped[float | None] = mapped_column(Float)
    longitude: Mapped[float | None] = mapped_column(Float)
    note: Mapped[str | None] = mapped_column(Text)
    payload: Mapped[dict[str, object]] = mapped_column(
        JSON, default=dict, nullable=False
    )
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    client_event_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)


class WorkOrderWorkLog(Base, _WorkOrderChild, TimestampMixin):
    __tablename__ = "work_order_worklogs"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_work_order_worklogs_tenant_id_id"),
        UniqueConstraint(
            "tenant_id",
            "client_worklog_id",
            name="uq_work_order_worklogs_tenant_client",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "work_order_id"],
            [f"{SCHEMA}.work_orders.tenant_id", f"{SCHEMA}.work_orders.id"],
            ondelete="CASCADE",
            name="fk_work_order_worklogs_work_order",
        ),
        Index(
            "ix_work_order_worklogs_tenant_actor_start",
            "tenant_id",
            "actor_id",
            "started_at",
        ),
        Index(
            "uq_work_order_worklogs_one_open_actor",
            "tenant_id",
            "actor_id",
            unique=True,
            postgresql_where=text("ended_at IS NULL AND active"),
            sqlite_where=text("ended_at IS NULL AND active = 1"),
        ),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    actor_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    minutes: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    notes: Mapped[str | None] = mapped_column(Text)
    client_worklog_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class WorkOrderNote(Base, _WorkOrderChild):
    __tablename__ = "work_order_notes"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_work_order_notes_tenant_id_id"),
        UniqueConstraint(
            "tenant_id", "client_note_id", name="uq_work_order_notes_tenant_client"
        ),
        ForeignKeyConstraint(
            ["tenant_id", "work_order_id"],
            [f"{SCHEMA}.work_orders.tenant_id", f"{SCHEMA}.work_orders.id"],
            ondelete="CASCADE",
            name="fk_work_order_notes_work_order",
        ),
        Index("ix_work_order_notes_tenant_work_order", "tenant_id", "work_order_id"),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    author_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    client_note_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    internal: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    metadata_: Mapped[dict[str, object]] = mapped_column(
        "metadata", JSON, default=dict, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


class WorkOrderEvidence(Base, _WorkOrderChild, TimestampMixin):
    __tablename__ = "work_order_evidence"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_work_order_evidence_tenant_id_id"),
        UniqueConstraint(
            "tenant_id",
            "client_evidence_id",
            name="uq_work_order_evidence_tenant_client",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "work_order_id"],
            [f"{SCHEMA}.work_orders.tenant_id", f"{SCHEMA}.work_orders.id"],
            ondelete="CASCADE",
            name="fk_work_order_evidence_work_order",
        ),
        Index(
            "ix_work_order_evidence_tenant_work_order_kind",
            "tenant_id",
            "work_order_id",
            "kind",
        ),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    kind: Mapped[str] = mapped_column(String(60), nullable=False)
    artifact_reference: Mapped[str] = mapped_column(String(255), nullable=False)
    recorded_by_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    client_evidence_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    captured_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    latitude: Mapped[float | None] = mapped_column(Float)
    longitude: Mapped[float | None] = mapped_column(Float)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    metadata_: Mapped[dict[str, object]] = mapped_column(
        "metadata", JSON, default=dict, nullable=False
    )


TENANT_TABLES: tuple[str, ...] = (
    "work_orders",
    "work_order_assignments",
    "work_order_events",
    "work_order_worklogs",
    "work_order_notes",
    "work_order_evidence",
)

__all__ = [
    "SCHEMA",
    "TENANT_TABLES",
    "WorkOrder",
    "WorkOrderAssignment",
    "WorkOrderEvidence",
    "WorkOrderEvent",
    "WorkOrderNote",
    "WorkOrderWorkLog",
]
