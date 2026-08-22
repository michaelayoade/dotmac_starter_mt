"""Provider-neutral command lifecycle persistence in ``mod_netctrl``."""

from __future__ import annotations

import uuid
from datetime import datetime

from dotmac_kernel.models import Base, Tenant
from dotmac_kernel.namespaces import module_schema
from sqlalchemy import (
    JSON,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

SCHEMA = module_schema("netctrl")


class Command(Base):
    __tablename__ = "commands"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_netctrl_commands_tenant_id_id"),
        UniqueConstraint(
            "tenant_id", "correlation_ref", name="uq_netctrl_command_correlation"
        ),
        Index("ix_netctrl_commands_state", "tenant_id", "state", "requested_at"),
        {"schema": SCHEMA},
    )
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(Tenant.__table__.c.id, ondelete="CASCADE"), nullable=False
    )
    operation_code: Mapped[str] = mapped_column(String(120), nullable=False)
    target_ref: Mapped[str] = mapped_column(String(200), nullable=False)
    capability_code: Mapped[str] = mapped_column(String(160), nullable=False)
    parameters: Mapped[list[list[str]]] = mapped_column(JSON, nullable=False)
    request_fingerprint: Mapped[str] = mapped_column(String(128), nullable=False)
    correlation_ref: Mapped[str] = mapped_column(String(200), nullable=False)
    requested_by_ref: Mapped[str] = mapped_column(String(200), nullable=False)
    state: Mapped[str] = mapped_column(String(24), nullable=False)
    requested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    terminal_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class CommandEvent(Base):
    __tablename__ = "command_events"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_netctrl_events_tenant_id_id"),
        ForeignKeyConstraint(
            ["tenant_id", "command_id"],
            [f"{SCHEMA}.commands.tenant_id", f"{SCHEMA}.commands.id"],
            name="fk_netctrl_events_command",
            ondelete="CASCADE",
        ),
        Index("ix_netctrl_events_command", "tenant_id", "command_id", "occurred_at"),
        {"schema": SCHEMA},
    )
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(Tenant.__table__.c.id, ondelete="CASCADE"), nullable=False
    )
    command_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    event_type: Mapped[str] = mapped_column(String(80), nullable=False)
    evidence_ref: Mapped[str] = mapped_column(String(240), nullable=False)
    payload: Mapped[dict[str, str]] = mapped_column(JSON, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


class Dispatch(Base):
    __tablename__ = "dispatches"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_netctrl_dispatches_tenant_id_id"),
        UniqueConstraint("tenant_id", "dispatch_ref", name="uq_netctrl_dispatch_ref"),
        ForeignKeyConstraint(
            ["tenant_id", "command_id"],
            [f"{SCHEMA}.commands.tenant_id", f"{SCHEMA}.commands.id"],
            name="fk_netctrl_dispatches_command",
            ondelete="CASCADE",
        ),
        {"schema": SCHEMA},
    )
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(Tenant.__table__.c.id, ondelete="CASCADE"), nullable=False
    )
    command_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    dispatch_ref: Mapped[str] = mapped_column(String(200), nullable=False)
    plugin_capability: Mapped[str] = mapped_column(String(160), nullable=False)
    dispatched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


class ExecutionEvidenceRow(Base):
    __tablename__ = "execution_evidence"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_netctrl_execution_tenant_id_id"),
        UniqueConstraint(
            "tenant_id",
            "dispatch_ref",
            "result_fingerprint",
            name="uq_netctrl_execution_result",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "command_id"],
            [f"{SCHEMA}.commands.tenant_id", f"{SCHEMA}.commands.id"],
            name="fk_netctrl_execution_command",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "dispatch_ref"],
            [f"{SCHEMA}.dispatches.tenant_id", f"{SCHEMA}.dispatches.dispatch_ref"],
            name="fk_netctrl_execution_dispatch",
            ondelete="RESTRICT",
        ),
        {"schema": SCHEMA},
    )
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(Tenant.__table__.c.id, ondelete="CASCADE"), nullable=False
    )
    command_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    dispatch_ref: Mapped[str] = mapped_column(String(200), nullable=False)
    outcome: Mapped[str] = mapped_column(String(24), nullable=False)
    observed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    evidence_ref: Mapped[str] = mapped_column(String(240), nullable=False)
    result_fingerprint: Mapped[str] = mapped_column(String(128), nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(120))


class ReconciliationRun(Base):
    __tablename__ = "reconciliation_runs"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "id", name="uq_netctrl_reconciliation_tenant_id_id"
        ),
        ForeignKeyConstraint(
            ["tenant_id", "command_id"],
            [f"{SCHEMA}.commands.tenant_id", f"{SCHEMA}.commands.id"],
            name="fk_netctrl_reconciliation_command",
            ondelete="CASCADE",
        ),
        {"schema": SCHEMA},
    )
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(Tenant.__table__.c.id, ondelete="CASCADE"), nullable=False
    )
    command_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    missing_dispatch_refs: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    unexpected_dispatch_refs: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    changed: Mapped[bool] = mapped_column(nullable=False)
    reconciled_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


ALL_MODELS = (Command, CommandEvent, Dispatch, ExecutionEvidenceRow, ReconciliationRun)
TENANT_TABLES = tuple(model.__tablename__ for model in ALL_MODELS)
__all__ = [
    "ALL_MODELS",
    "Command",
    "CommandEvent",
    "Dispatch",
    "ExecutionEvidenceRow",
    "ReconciliationRun",
    "SCHEMA",
    "TENANT_TABLES",
]
