"""Payment intent persistence contract.

Money is `Numeric`, never float, and every amount column travels with its own
ISO-4217 code — the module refuses to compare or add amounts across currencies
rather than assuming a tenant default (ADR-0003).
"""

from __future__ import annotations

import enum
from datetime import datetime
from decimal import Decimal
from typing import cast
from uuid import UUID

import sqlalchemy as sa
from dotmac_kernel.models import Base, Tenant, TimestampMixin, uuid_pk
from dotmac_kernel.namespaces import module_schema, schema_table_args
from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    event,
)
from sqlalchemy.orm import Mapped, mapped_column

from dotmac_payments.contracts import (
    ConfirmationSource,
    PaymentIntentStatus,
    PaymentPurpose,
    TransferProofState,
)

SCHEMA = module_schema("payments")

MONEY = Numeric(20, 6)


def _enum(python_type: type[enum.StrEnum], name: str) -> sa.Enum:
    return sa.Enum(
        python_type,
        name=name,
        native_enum=False,
        values_callable=lambda cls: [member.value for member in cls],
        create_constraint=True,
    )


class PaymentIntent(Base, TimestampMixin):
    __tablename__ = "payment_intents"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_payment_intents_tenant_id_id"),
        UniqueConstraint(
            "tenant_id", "reference", name="uq_payment_intents_tenant_reference"
        ),
        CheckConstraint(
            "length(currency_code) = 3", name="ck_payment_intents_currency"
        ),
        CheckConstraint(
            "requested_amount > 0", name="ck_payment_intents_requested_positive"
        ),
        Index("ix_payment_intents_tenant_payer", "tenant_id", "payer_reference"),
        Index("ix_payment_intents_tenant_status", "tenant_id", "status"),
        schema_table_args(SCHEMA),
    )
    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(), ForeignKey(Tenant.__table__.c.id, ondelete="CASCADE"), nullable=False
    )
    reference: Mapped[str] = mapped_column(String(120), nullable=False)
    payer_reference: Mapped[str] = mapped_column(String(160), nullable=False)
    target_reference: Mapped[str | None] = mapped_column(String(160), nullable=True)
    purpose: Mapped[PaymentPurpose] = mapped_column(
        _enum(PaymentPurpose, "payment_purpose"), nullable=False
    )
    provider_type: Mapped[str] = mapped_column(String(40), nullable=False)
    channel: Mapped[str] = mapped_column(String(40), nullable=False)
    currency_code: Mapped[str] = mapped_column(String(3), nullable=False)
    requested_amount: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    confirmed_amount: Mapped[Decimal | None] = mapped_column(MONEY, nullable=True)
    status: Mapped[PaymentIntentStatus] = mapped_column(
        _enum(PaymentIntentStatus, "payment_intent_status"), nullable=False
    )
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    settled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class PaymentTransferProof(Base, TimestampMixin):
    __tablename__ = "payment_transfer_proofs"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_transfer_proofs_tenant_id_id"),
        UniqueConstraint(
            "tenant_id",
            "submitted_reference",
            name="uq_transfer_proofs_tenant_submitted_reference",
        ),
        CheckConstraint(
            "length(currency_code) = 3", name="ck_transfer_proofs_currency"
        ),
        CheckConstraint(
            "declared_amount > 0", name="ck_transfer_proofs_declared_positive"
        ),
        ForeignKeyConstraint(
            ["tenant_id", "intent_id"],
            [f"{SCHEMA}.payment_intents.tenant_id", f"{SCHEMA}.payment_intents.id"],
            ondelete="CASCADE",
            name="fk_transfer_proofs_tenant_intent",
        ),
        Index("ix_transfer_proofs_tenant_intent", "tenant_id", "intent_id"),
        schema_table_args(SCHEMA),
    )
    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(), ForeignKey(Tenant.__table__.c.id, ondelete="CASCADE"), nullable=False
    )
    intent_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    submitted_reference: Mapped[str] = mapped_column(String(120), nullable=False)
    document_reference: Mapped[str] = mapped_column(String(160), nullable=False)
    currency_code: Mapped[str] = mapped_column(String(3), nullable=False)
    declared_amount: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    state: Mapped[TransferProofState] = mapped_column(
        _enum(TransferProofState, "transfer_proof_state"), nullable=False
    )
    declared_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    reviewer: Mapped[str | None] = mapped_column(String(160), nullable=True)
    rejection_reason: Mapped[str | None] = mapped_column(Text, nullable=True)


class PaymentConfirmation(Base, TimestampMixin):
    """Append-only correlation between an intent and an external settlement fact."""

    __tablename__ = "payment_confirmations"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "id", name="uq_payment_confirmations_tenant_id_id"
        ),
        # Unconditional, unlike Sub's partial index on `provider_id IS NOT NULL`
        # — which left CRM-originated payments outside it and needed a second
        # index to close the double-record window. One rule, no gap class.
        UniqueConstraint(
            "tenant_id",
            "provider_type",
            "external_reference",
            name="uq_payment_confirmations_tenant_provider_external",
        ),
        CheckConstraint(
            "length(currency_code) = 3", name="ck_payment_confirmations_currency"
        ),
        CheckConstraint(
            "confirmed_amount > 0", name="ck_payment_confirmations_amount_positive"
        ),
        ForeignKeyConstraint(
            ["tenant_id", "intent_id"],
            [f"{SCHEMA}.payment_intents.tenant_id", f"{SCHEMA}.payment_intents.id"],
            ondelete="CASCADE",
            name="fk_payment_confirmations_tenant_intent",
        ),
        Index(
            "ix_payment_confirmations_tenant_intent_observed",
            "tenant_id",
            "intent_id",
            "observed_at",
        ),
        schema_table_args(SCHEMA),
    )
    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(), ForeignKey(Tenant.__table__.c.id, ondelete="CASCADE"), nullable=False
    )
    intent_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    source: Mapped[ConfirmationSource] = mapped_column(
        _enum(ConfirmationSource, "payment_confirmation_source"), nullable=False
    )
    provider_type: Mapped[str] = mapped_column(String(40), nullable=False)
    external_reference: Mapped[str] = mapped_column(String(160), nullable=False)
    currency_code: Mapped[str] = mapped_column(String(3), nullable=False)
    confirmed_amount: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    observed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


class PaymentConfirmationImmutableError(RuntimeError):
    """Raised when append-only confirmation correlation is updated or deleted."""


@event.listens_for(PaymentConfirmation, "before_update")
@event.listens_for(PaymentConfirmation, "before_delete")
def _reject_confirmation_mutation(*_args: object) -> None:
    raise PaymentConfirmationImmutableError("payment confirmations are append-only")


TENANT_TABLES = (
    "payment_intents",
    "payment_transfer_proofs",
    "payment_confirmations",
)
_TABLES: dict[str, sa.Table] = {
    model.__tablename__: cast(sa.Table, model.__table__)
    for model in (PaymentIntent, PaymentTransferProof, PaymentConfirmation)
}


def metadata_table(name: str) -> sa.Table:
    return _TABLES[name]


__all__ = [
    "MONEY",
    "SCHEMA",
    "TENANT_TABLES",
    "PaymentConfirmation",
    "PaymentConfirmationImmutableError",
    "PaymentIntent",
    "PaymentTransferProof",
    "metadata_table",
]
