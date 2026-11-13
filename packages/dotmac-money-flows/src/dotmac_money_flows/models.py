"""Tenant-only instance state for money flows.

Instance state only: a state, the policy and definition versions it was decided
under, an optional deadline instant, a reason code, and opaque references to the
owners' records. No amount, currency or balance is stored here; the owners
(Billing, Payments) remain the only source of money facts.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from dotmac_kernel.models import Base, Tenant, uuid_pk
from dotmac_kernel.namespaces import module_schema, schema_table_args
from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

SCHEMA = module_schema("mflows")

SETTLE_STATES = ("settled", "refused", "unapplied", "resolved")
SETTLE_TERMINAL_STATES = ("settled", "resolved")

#: Columns every instance table carries (ST-1: instance state only).
COMMON_COLUMNS = (
    "id",
    "tenant_id",
    "state",
    "reason_code",
    "policy_version",
    "definition_version",
    "deadline_at",
    "version",
    "actor_ref",
    "resolution_ref",
    "created_at",
    "updated_at",
)


def _states(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{value}'" for value in values)


class SettleInstance(Base):
    __tablename__ = "settle_instances"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_settle_instances_tenant_id_id"),
        UniqueConstraint(
            "tenant_id", "confirmation_id", name="uq_settle_instances_confirmation"
        ),
        CheckConstraint(
            f"state IN ({_states(SETTLE_STATES)})", name="ck_settle_instances_state"
        ),
        CheckConstraint(
            "(reason_code IS NOT NULL) = (state IN ('refused', 'unapplied'))",
            name="ck_settle_instances_reason_iff_attention",
        ),
        CheckConstraint(
            "(settlement_id IS NOT NULL) = (state IN ('settled', 'unapplied'))",
            name="ck_settle_instances_settlement_iff_accepted",
        ),
        CheckConstraint(
            "(resolution_ref IS NOT NULL) = (state = 'resolved')",
            name="ck_settle_instances_resolution_iff_resolved",
        ),
        CheckConstraint("version >= 0", name="ck_settle_instances_version"),
        Index("ix_settle_instances_tenant_state", "tenant_id", "state"),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(), ForeignKey(Tenant.__table__.c.id, ondelete="CASCADE"), nullable=False
    )
    state: Mapped[str] = mapped_column(String(16), nullable=False)
    reason_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    policy_version: Mapped[str] = mapped_column(String(80), nullable=False)
    definition_version: Mapped[str] = mapped_column(String(80), nullable=False)
    deadline_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    actor_ref: Mapped[str | None] = mapped_column(String(160), nullable=True)
    resolution_ref: Mapped[str | None] = mapped_column(String(160), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    confirmation_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    intent_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    billing_account_id: Mapped[UUID | None] = mapped_column(Uuid(), nullable=True)
    settlement_id: Mapped[UUID | None] = mapped_column(Uuid(), nullable=True)
    withholding_discharge_ref: Mapped[str | None] = mapped_column(
        String(160), nullable=True
    )


TENANT_MODELS = (SettleInstance,)
TENANT_TABLES = tuple(model.__tablename__ for model in TENANT_MODELS)

__all__ = [
    "COMMON_COLUMNS",
    "SCHEMA",
    "SETTLE_STATES",
    "SETTLE_TERMINAL_STATES",
    "TENANT_MODELS",
    "TENANT_TABLES",
    "SettleInstance",
]
