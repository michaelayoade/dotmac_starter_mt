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
    ARRAY,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    Uuid,
    func,
    inspect,
)
from sqlalchemy.orm import Mapped, mapped_column

SCHEMA = module_schema("mflows")
_TENANT_ID = inspect(Tenant).local_table.c.id

SETTLE_TABLE = "settle_instances"
SETTLE_STATES = ("settled", "refused", "unapplied", "resolved")
SETTLE_TERMINAL_STATES = ("settled", "resolved")

FUNDED_CHARGE_TABLE = "funded_charge_instances"
FUNDED_CHARGE_STATES = ("pending", "discharged", "attention_required", "withdrawn")
FUNDED_CHARGE_TERMINAL_STATES = ("discharged", "withdrawn")

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
    __tablename__ = SETTLE_TABLE
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_settle_instances_tenant_id_id"),
        UniqueConstraint(
            "tenant_id", "confirmation_id", name="uq_settle_instances_confirmation"
        ),
        CheckConstraint(
            f"state IN ({_states(SETTLE_STATES)})", name="ck_settle_instances_state"
        ),
        CheckConstraint(
            "(reason_code IS NOT NULL) = "
            "(state IN ('refused', 'unapplied', 'resolved'))",
            name="ck_settle_instances_reason_unless_settled",
        ),
        CheckConstraint(
            "(state = 'refused' AND settlement_id IS NULL) "
            "OR (state IN ('settled', 'unapplied') AND settlement_id IS NOT NULL) "
            "OR state = 'resolved'",
            name="ck_settle_instances_settlement_kept",
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
        Uuid(), ForeignKey(_TENANT_ID, ondelete="CASCADE"), nullable=False
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
    #: Array of discharge references in acceptance order (mf_0003). The
    #: legacy column above keeps the first one.
    withholding_discharge_list_ref: Mapped[list[str] | None] = mapped_column(
        ARRAY(String(160)), nullable=True
    )


class FundedChargeInstance(Base):
    __tablename__ = FUNDED_CHARGE_TABLE
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "id", name="uq_funded_charge_instances_tenant_id_id"
        ),
        UniqueConstraint(
            "tenant_id", "charge_ref", name="uq_funded_charge_instances_charge"
        ),
        CheckConstraint(
            f"state IN ({_states(FUNDED_CHARGE_STATES)})",
            name="ck_funded_charge_instances_state",
        ),
        CheckConstraint(
            "(reason_code IS NOT NULL) = "
            "(state IN ('attention_required', 'withdrawn'))",
            name="ck_funded_charge_instances_reason_iff_attention_or_withdrawn",
        ),
        CheckConstraint(
            "deadline_at IS NULL", name="ck_funded_charge_instances_no_deadline"
        ),
        CheckConstraint(
            "resolution_ref IS NULL", name="ck_funded_charge_instances_no_resolution"
        ),
        CheckConstraint(
            "(state = 'discharged' AND obligation_id IS NOT NULL "
            "AND document_id IS NOT NULL AND credit_draw_id IS NOT NULL) "
            "OR (state <> 'discharged' AND obligation_id IS NULL "
            "AND document_id IS NULL AND credit_draw_id IS NULL)",
            name="ck_funded_charge_instances_discharge_refs",
        ),
        CheckConstraint(
            "octet_length(charge_ref) <= 1024",
            name="ck_funded_charge_instances_charge_ref_bytes",
        ),
        CheckConstraint("version >= 0", name="ck_funded_charge_instances_version"),
        Index(
            "ix_funded_charge_instances_account_order",
            "tenant_id",
            "billing_account_id",
            "state",
            "created_at",
            "id",
        ),
        Index("ix_funded_charge_instances_service", "tenant_id", "service_ref"),
        Index(
            "ix_funded_charge_instances_state_order",
            "tenant_id",
            "state",
            "created_at",
            "id",
        ),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(), ForeignKey(_TENANT_ID, ondelete="CASCADE"), nullable=False
    )
    state: Mapped[str] = mapped_column(String(24), nullable=False)
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
    charge_ref: Mapped[str] = mapped_column(String(700), nullable=False)
    billing_account_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    service_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    obligation_id: Mapped[UUID | None] = mapped_column(Uuid(), nullable=True)
    document_id: Mapped[UUID | None] = mapped_column(Uuid(), nullable=True)
    credit_draw_id: Mapped[UUID | None] = mapped_column(Uuid(), nullable=True)
    funding_settlement_id: Mapped[UUID | None] = mapped_column(Uuid(), nullable=True)


TENANT_MODELS = (SettleInstance, FundedChargeInstance)
TENANT_TABLES = (SETTLE_TABLE, FUNDED_CHARGE_TABLE)

__all__ = [
    "COMMON_COLUMNS",
    "FUNDED_CHARGE_STATES",
    "FUNDED_CHARGE_TABLE",
    "FUNDED_CHARGE_TERMINAL_STATES",
    "SCHEMA",
    "SETTLE_TABLE",
    "SETTLE_STATES",
    "SETTLE_TERMINAL_STATES",
    "TENANT_MODELS",
    "TENANT_TABLES",
    "FundedChargeInstance",
    "SettleInstance",
]
