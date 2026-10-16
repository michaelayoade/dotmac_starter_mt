"""Payment intent persistence contract.

Money is `Numeric`, never float, and every amount column travels with its own
ISO-4217 code and authoritative minor-unit scale — the module refuses to compare
or add amounts across currencies rather than assuming a tenant default (ADR-0003).
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
    Integer,
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
    Conflict,
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
        # The PostgreSQL migration additionally rejects POSIX whitespace-only
        # targets. The portable metadata check serves the SQLite unit adapter;
        # the public command rejects all Python whitespace before persistence.
        CheckConstraint(
            "purpose != 'PURCHASE_FUNDING' OR "
            "(target_reference IS NOT NULL AND length(trim(target_reference)) > 0)",
            name="ck_payment_intents_purchase_target",
        ),
        UniqueConstraint("tenant_id", "id", name="uq_payment_intents_tenant_id_id"),
        UniqueConstraint(
            "tenant_id", "reference", name="uq_payment_intents_tenant_reference"
        ),
        CheckConstraint(
            "length(currency_code) = 3", name="ck_payment_intents_currency"
        ),
        CheckConstraint(
            "minor_units BETWEEN 0 AND 6", name="ck_payment_intents_minor_units"
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
    minor_units: Mapped[int | None] = mapped_column(Integer, nullable=True)
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
            "minor_units BETWEEN 0 AND 6", name="ck_transfer_proofs_minor_units"
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
    minor_units: Mapped[int | None] = mapped_column(Integer, nullable=True)
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
            "minor_units BETWEEN 0 AND 6", name="ck_payment_confirmations_minor_units"
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
    minor_units: Mapped[int | None] = mapped_column(Integer, nullable=True)
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


class MerchantContext(Base):
    """Immutable human-reviewed configuration authority, not qualified cash."""

    __tablename__ = "merchant_contexts"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_merchant_contexts_tenant_id"),
        UniqueConstraint(
            "tenant_id", "command_id", name="uq_merchant_contexts_command"
        ),
        CheckConstraint(
            "effective_until IS NULL OR effective_until > effective_from",
            name="ck_merchant_contexts_interval",
        ),
        Index(
            "ix_merchant_contexts_transport",
            "tenant_id",
            "installation_reference",
            "capability_reference",
        ),
        schema_table_args(SCHEMA),
    )
    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(), ForeignKey(Tenant.__table__.c.id, ondelete="RESTRICT"), nullable=False
    )
    command_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    request_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    provider_type: Mapped[str] = mapped_column(String(40), nullable=False)
    environment: Mapped[str] = mapped_column(String(40), nullable=False)
    physical_merchant_id: Mapped[str] = mapped_column(String(160), nullable=False)
    installation_reference: Mapped[str] = mapped_column(String(160), nullable=False)
    capability_reference: Mapped[str] = mapped_column(String(160), nullable=False)
    config_revision_reference: Mapped[str] = mapped_column(String(160), nullable=False)
    config_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    evidence_reference: Mapped[str] = mapped_column(String(512), nullable=False)
    evidence_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    reviewer_reference: Mapped[str] = mapped_column(String(160), nullable=False)
    reviewed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    effective_from: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    effective_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class MerchantContextEnd(Base):
    """Append-only revocation/supersession; original reviewed facts survive."""

    __tablename__ = "merchant_context_ends"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_merchant_context_ends_tenant_id"),
        UniqueConstraint(
            "tenant_id", "command_id", name="uq_merchant_context_ends_command"
        ),
        UniqueConstraint(
            "tenant_id", "context_id", name="uq_merchant_context_ends_context"
        ),
        CheckConstraint(
            "(operation = 'REVOKE' AND replacement_context_id IS NULL) OR "
            "(operation = 'ACCEPT' AND replacement_context_id IS NOT NULL "
            "AND replacement_context_id <> context_id)",
            name="ck_merchant_context_ends_operation",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "context_id"],
            [f"{SCHEMA}.merchant_contexts.tenant_id", f"{SCHEMA}.merchant_contexts.id"],
            ondelete="RESTRICT",
            name="fk_merchant_context_ends_context",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "replacement_context_id"],
            [f"{SCHEMA}.merchant_contexts.tenant_id", f"{SCHEMA}.merchant_contexts.id"],
            ondelete="RESTRICT",
            deferrable=True,
            initially="DEFERRED",
            name="fk_merchant_context_ends_replacement",
        ),
        schema_table_args(SCHEMA),
    )
    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(), ForeignKey(Tenant.__table__.c.id, ondelete="RESTRICT"), nullable=False
    )
    command_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    context_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    operation: Mapped[str] = mapped_column(String(16), nullable=False)
    request_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    replacement_context_id: Mapped[UUID | None] = mapped_column(Uuid())
    evidence_reference: Mapped[str] = mapped_column(String(512), nullable=False)
    evidence_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    reviewer_reference: Mapped[str] = mapped_column(String(160), nullable=False)
    reviewed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    effective_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


@event.listens_for(MerchantContext, "before_update")
@event.listens_for(MerchantContext, "before_delete")
@event.listens_for(MerchantContextEnd, "before_update")
@event.listens_for(MerchantContextEnd, "before_delete")
def _reject_merchant_context_mutation(*_args: object) -> None:
    raise Conflict("merchant review history is append-only")


class ProviderCashQualification(Base):
    """Append-only original provider cash qualification, never a ledger."""

    __tablename__ = "provider_cash_qualifications"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_provider_cash_tenant_id"),
        UniqueConstraint("tenant_id", "command_id", name="uq_provider_cash_command"),
        UniqueConstraint(
            "tenant_id",
            "provider_type",
            "environment",
            "physical_merchant_id",
            "transaction_kind",
            "transaction_id",
            name="uq_provider_cash_physical_transaction",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "context_id"],
            [f"{SCHEMA}.merchant_contexts.tenant_id", f"{SCHEMA}.merchant_contexts.id"],
            ondelete="RESTRICT",
            name="fk_provider_cash_context",
        ),
        CheckConstraint(
            "amount > 0 AND minor_units BETWEEN 0 AND 6 "
            "AND amount = round(amount,minor_units)",
            name="ck_provider_cash_money",
        ),
        CheckConstraint("status = 'succeeded'", name="ck_provider_cash_status"),
        schema_table_args(SCHEMA),
    )
    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(), ForeignKey(Tenant.__table__.c.id, ondelete="RESTRICT"), nullable=False
    )
    command_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    request_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    evidence_owner: Mapped[str] = mapped_column(String(160), nullable=False)
    evidence_reference: Mapped[str] = mapped_column(String(512), nullable=False)
    evidence_schema_version: Mapped[str] = mapped_column(String(80), nullable=False)
    evidence_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    context_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    provider_type: Mapped[str] = mapped_column(String(40), nullable=False)
    environment: Mapped[str] = mapped_column(String(40), nullable=False)
    physical_merchant_id: Mapped[str] = mapped_column(String(160), nullable=False)
    installation_reference: Mapped[str] = mapped_column(String(160), nullable=False)
    capability_reference: Mapped[str] = mapped_column(String(160), nullable=False)
    config_revision_reference: Mapped[str] = mapped_column(String(160), nullable=False)
    config_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    verified_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    transaction_kind: Mapped[str] = mapped_column(String(80), nullable=False)
    transaction_id: Mapped[str] = mapped_column(String(160), nullable=False)
    checkout_reference: Mapped[str] = mapped_column(String(120), nullable=False)
    status: Mapped[str] = mapped_column(String(40), nullable=False)
    amount: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    currency_code: Mapped[str] = mapped_column(String(3), nullable=False)
    minor_units: Mapped[int] = mapped_column(Integer, nullable=False)
    paid_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    observed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    source_namespace: Mapped[str] = mapped_column(String(80), nullable=False)
    source_key: Mapped[str] = mapped_column(String(160), nullable=False)
    source_version: Mapped[str] = mapped_column(String(80), nullable=False)
    provenance_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)


@event.listens_for(ProviderCashQualification, "before_update")
@event.listens_for(ProviderCashQualification, "before_delete")
def _reject_provider_cash_mutation(*_args: object) -> None:
    raise Conflict("provider cash qualifications are append-only")


TENANT_TABLES = (
    "payment_intents",
    "payment_transfer_proofs",
    "payment_confirmations",
    "merchant_contexts",
    "merchant_context_ends",
    "provider_cash_qualifications",
)
_TABLES: dict[str, sa.Table] = {
    model.__tablename__: cast(sa.Table, model.__table__)
    for model in (
        PaymentIntent,
        PaymentTransferProof,
        PaymentConfirmation,
        MerchantContext,
        MerchantContextEnd,
        ProviderCashQualification,
    )
}


def metadata_table(name: str) -> sa.Table:
    return _TABLES[name]


__all__ = [
    "ProviderCashQualification",
    "MerchantContext",
    "MerchantContextEnd",
    "MONEY",
    "SCHEMA",
    "TENANT_TABLES",
    "PaymentConfirmation",
    "PaymentConfirmationImmutableError",
    "PaymentIntent",
    "PaymentTransferProof",
    "metadata_table",
]
