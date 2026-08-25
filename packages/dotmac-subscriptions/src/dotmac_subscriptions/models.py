"""Subscriptions' nine tables on each explicit persistence plane."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import UUID

import sqlalchemy as sa
from dotmac_kernel.models import Base, Tenant, uuid_pk
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
)
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column

SCHEMA = module_schema("subscriptions")
_JSON = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def _created_at() -> Mapped[datetime]:
    return mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=sa.func.now(),
    )


class _OfferColumns:
    code: Mapped[str] = mapped_column(String(120), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    created_at: Mapped[datetime] = _created_at()


class _OfferVersionColumns:
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    charge_model_code: Mapped[str] = mapped_column(String(120), nullable=False)
    pricing_mode: Mapped[str] = mapped_column(String(24), nullable=False)
    state: Mapped[str] = mapped_column(String(24), nullable=False)
    effective_from: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    effective_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    source_code: Mapped[str] = mapped_column(String(120), nullable=False)
    source_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    source_version: Mapped[int] = mapped_column(Integer, nullable=False)
    command_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    content_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    withdrawn_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    withdrawal_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    withdrawal_command_id: Mapped[UUID | None] = mapped_column(Uuid(), nullable=True)
    created_at: Mapped[datetime] = _created_at()


class _PriceColumns:
    price_key: Mapped[str] = mapped_column(String(120), nullable=False)
    charge_model_code: Mapped[str] = mapped_column(String(120), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    scale: Mapped[int] = mapped_column(Integer, nullable=False)
    quantity: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    created_at: Mapped[datetime] = _created_at()


class _ContractColumns:
    source_code: Mapped[str] = mapped_column(String(120), nullable=False)
    source_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    created_at: Mapped[datetime] = _created_at()


class _ContractVersionColumns:
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    state: Mapped[str] = mapped_column(String(24), nullable=False)
    source_code: Mapped[str] = mapped_column(String(120), nullable=False)
    source_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    source_version: Mapped[int] = mapped_column(Integer, nullable=False)
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ends_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    declared_ends_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    rate_basis: Mapped[str] = mapped_column(String(40), nullable=False)
    rate_unit: Mapped[str] = mapped_column(String(12), nullable=False)
    rate_quantity: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    service_interval_unit: Mapped[str] = mapped_column(String(12), nullable=False)
    service_interval_count: Mapped[int] = mapped_column(Integer, nullable=False)
    invoice_interval_unit: Mapped[str] = mapped_column(String(12), nullable=False)
    invoice_interval_count: Mapped[int] = mapped_column(Integer, nullable=False)
    collection_timing: Mapped[str] = mapped_column(String(16), nullable=False)
    alignment: Mapped[str] = mapped_column(String(32), nullable=False)
    anchor_day: Mapped[int | None] = mapped_column(Integer, nullable=True)
    end_of_month_rule: Mapped[str] = mapped_column(String(40), nullable=False)
    timezone_name: Mapped[str] = mapped_column(String(120), nullable=False)
    proration_policy: Mapped[str] = mapped_column(String(40), nullable=False)
    rating_policy_version: Mapped[str] = mapped_column(String(80), nullable=False)
    supersedes_id: Mapped[UUID | None] = mapped_column(Uuid(), nullable=True)
    superseded_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    terminal_actor: Mapped[str | None] = mapped_column(String(160), nullable=True)
    terminal_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    terminal_command_id: Mapped[UUID | None] = mapped_column(Uuid(), nullable=True)
    actor: Mapped[str] = mapped_column(String(160), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    command_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    correlation_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    content_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = _created_at()


class _ContractLineColumns:
    contract_line_key: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    charge_model_code: Mapped[str] = mapped_column(String(120), nullable=False)
    source_code: Mapped[str] = mapped_column(String(120), nullable=False)
    source_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    source_version: Mapped[int] = mapped_column(Integer, nullable=False)
    description: Mapped[str] = mapped_column(String(255), nullable=False)
    product_link_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    quantity: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    unit_price: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    scale: Mapped[int] = mapped_column(Integer, nullable=False)
    offer_version: Mapped[int] = mapped_column(Integer, nullable=False)
    entitlement_codes: Mapped[list[str]] = mapped_column(
        _JSON, nullable=False, default=list
    )
    created_at: Mapped[datetime] = _created_at()


class _OccurrenceColumns:
    contract_line_key: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    charge_model_code: Mapped[str] = mapped_column(String(120), nullable=False)
    source_code: Mapped[str] = mapped_column(String(120), nullable=False)
    source_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    source_version: Mapped[int] = mapped_column(Integer, nullable=False)
    period_start: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    period_end: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    pre_tax_amount: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    amount_scale: Mapped[int] = mapped_column(Integer, nullable=False)
    rating_coverage_start: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    rating_coverage_end: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    rating_unit_price: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    rating_quantity: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    rating_rate_basis: Mapped[str] = mapped_column(String(40), nullable=False)
    rating_rate_unit: Mapped[str] = mapped_column(String(12), nullable=False)
    rating_rate_quantity: Mapped[Decimal] = mapped_column(
        Numeric(20, 6), nullable=False
    )
    rating_rate_units: Mapped[Decimal] = mapped_column(Numeric(38, 28), nullable=False)
    rating_proration_policy: Mapped[str] = mapped_column(String(40), nullable=False)
    rating_proration_factor: Mapped[Decimal] = mapped_column(
        Numeric(38, 28), nullable=False
    )
    rating_timezone_name: Mapped[str] = mapped_column(String(120), nullable=False)
    rating_policy_version: Mapped[str] = mapped_column(String(80), nullable=False)
    offer_version_ref: Mapped[str] = mapped_column(String(180), nullable=False)
    request_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    generation: Mapped[int] = mapped_column(Integer, nullable=False)
    state: Mapped[str] = mapped_column(String(24), nullable=False)
    emitted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    output_acknowledged_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    command_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    correlation_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    created_at: Mapped[datetime] = _created_at()


class _ArrangementColumns:
    """Approval evidence for not collecting a POSITIVE contracted amount.

    Nothing here zeroes a price.  `maximum_recurring_amount` is the approved
    ceiling on what may later be granted, and the contract line keeps its own
    real, strictly positive `unit_price`.
    """

    contract_line_key: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    treatment: Mapped[str] = mapped_column(String(24), nullable=False)
    # An OPEN declared vocabulary (ADR-0008): a plain string with no CHECK, so
    # a product declaring an eighth reason needs no module migration.
    reason_code: Mapped[str] = mapped_column(String(120), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # NOT NULL is the mandatory-end invariant: a permanent exemption cannot be
    # expressed at all, so reapproval is structural rather than procedural.
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    approval_policy_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    approval_policy_version: Mapped[str] = mapped_column(String(80), nullable=False)
    approval_policy_max_days: Mapped[int] = mapped_column(Integer, nullable=False)
    maximum_recurring_amount: Mapped[Decimal] = mapped_column(
        Numeric(20, 6), nullable=False
    )
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    scale: Mapped[int] = mapped_column(Integer, nullable=False)
    service_interval_unit: Mapped[str] = mapped_column(String(12), nullable=False)
    service_interval_count: Mapped[int] = mapped_column(Integer, nullable=False)
    sponsor_reference: Mapped[str | None] = mapped_column(String(255), nullable=True)
    cost_center: Mapped[str | None] = mapped_column(String(120), nullable=True)
    approved_by: Mapped[str] = mapped_column(String(160), nullable=False)
    approved_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    command_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    correlation_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    command_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    revoked_by: Mapped[str | None] = mapped_column(String(160), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    revocation_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    revocation_command_id: Mapped[UUID | None] = mapped_column(Uuid(), nullable=True)
    revocation_correlation_id: Mapped[UUID | None] = mapped_column(
        Uuid(), nullable=True
    )
    revocation_idempotency_key: Mapped[str | None] = mapped_column(
        String(255), nullable=True
    )
    created_at: Mapped[datetime] = _created_at()


class _GrantColumns:
    """Append-only exact non-cash evidence for one approved service period.

    Three amounts, deliberately: `contracted_amount` is what the customer was
    really billed for (proof no price was hidden at zero), `foregone_amount` is
    the non-cash value actually granted, and `approved_maximum_amount` is the
    ceiling snapshotted at approval.  A single CHECK relates all three, so the
    cap cannot be exceeded and a concealed zero cannot be recorded.  There is
    no customer-money column at all: a grant never creates a receivable.
    """

    contract_line_key: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    treatment: Mapped[str] = mapped_column(String(24), nullable=False)
    reason_code: Mapped[str] = mapped_column(String(120), nullable=False)
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    contracted_amount: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    approved_maximum_amount: Mapped[Decimal] = mapped_column(
        Numeric(20, 6), nullable=False
    )
    foregone_amount: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    scale: Mapped[int] = mapped_column(Integer, nullable=False)
    actor: Mapped[str] = mapped_column(String(160), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    command_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    correlation_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    created_at: Mapped[datetime] = _created_at()


class Offer(Base, _OfferColumns):
    __tablename__ = "offers"
    __table_args__ = (
        UniqueConstraint("tenant_id", "code", name="uq_offers_tenant_code"),
        UniqueConstraint("tenant_id", "id", name="uq_offers_tenant_id_id"),
        CheckConstraint(
            "status IN ('draft', 'published', 'withdrawn')",
            name="ck_offers_status",
        ),
        Index("ix_offers_tenant_status", "tenant_id", "status"),
        schema_table_args(SCHEMA),
    )
    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(), ForeignKey(Tenant.__table__.c.id, ondelete="CASCADE"), nullable=False
    )


class OfferVersion(Base, _OfferVersionColumns):
    __tablename__ = "offer_versions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "offer_id"],
            [f"{SCHEMA}.offers.tenant_id", f"{SCHEMA}.offers.id"],
            name="fk_offer_versions_offer",
            ondelete="CASCADE",
        ),
        UniqueConstraint(
            "tenant_id", "offer_id", "version", name="uq_offer_versions_identity"
        ),
        UniqueConstraint("tenant_id", "command_id", name="uq_offer_versions_command"),
        UniqueConstraint("tenant_id", "id", name="uq_offer_versions_tenant_id_id"),
        CheckConstraint("version > 0", name="ck_offer_versions_version"),
        CheckConstraint("source_version > 0", name="ck_offer_versions_source_version"),
        CheckConstraint(
            "state IN ('published', 'withdrawn')",
            name="ck_offer_versions_state",
        ),
        CheckConstraint(
            "pricing_mode IN ('catalog_price', 'contract_price')",
            name="ck_offer_versions_pricing_mode",
        ),
        CheckConstraint(
            "effective_until IS NULL OR effective_until > effective_from",
            name="ck_offer_versions_interval",
        ),
        Index("ix_offer_versions_effective", "tenant_id", "offer_id", "effective_from"),
        schema_table_args(SCHEMA),
    )
    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(), ForeignKey(Tenant.__table__.c.id, ondelete="CASCADE"), nullable=False
    )
    offer_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)


class OfferVersionPrice(Base, _PriceColumns):
    __tablename__ = "offer_version_prices"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "offer_version_id"],
            [f"{SCHEMA}.offer_versions.tenant_id", f"{SCHEMA}.offer_versions.id"],
            name="fk_offer_version_prices_version",
            ondelete="CASCADE",
        ),
        UniqueConstraint(
            "tenant_id",
            "offer_version_id",
            "price_key",
            name="uq_offer_version_prices_key",
        ),
        CheckConstraint(
            "amount > 0 AND quantity > 0", name="ck_offer_version_prices_amounts"
        ),
        CheckConstraint("scale >= 0 AND scale <= 6", name="ck_offer_prices_scale"),
        schema_table_args(SCHEMA),
    )
    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(), ForeignKey(Tenant.__table__.c.id, ondelete="CASCADE"), nullable=False
    )
    offer_version_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)


class SubscriptionContract(Base, _ContractColumns):
    __tablename__ = "subscription_contracts"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "source_code", "source_id", name="uq_contracts_source"
        ),
        UniqueConstraint("tenant_id", "id", name="uq_contracts_tenant_id_id"),
        schema_table_args(SCHEMA),
    )
    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(), ForeignKey(Tenant.__table__.c.id, ondelete="CASCADE"), nullable=False
    )


class SubscriptionContractVersion(Base, _ContractVersionColumns):
    __tablename__ = "subscription_contract_versions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "contract_id"],
            [
                f"{SCHEMA}.subscription_contracts.tenant_id",
                f"{SCHEMA}.subscription_contracts.id",
            ],
            name="fk_contract_versions_contract",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "supersedes_id"],
            [
                f"{SCHEMA}.subscription_contract_versions.tenant_id",
                f"{SCHEMA}.subscription_contract_versions.id",
            ],
            name="fk_contract_versions_supersedes",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "tenant_id", "contract_id", "version", name="uq_contract_versions_number"
        ),
        UniqueConstraint(
            "tenant_id", "idempotency_key", name="uq_contract_versions_idempotency"
        ),
        UniqueConstraint(
            "tenant_id", "command_id", name="uq_contract_versions_command"
        ),
        UniqueConstraint("tenant_id", "id", name="uq_contract_versions_tenant_id_id"),
        CheckConstraint("version > 0", name="ck_contract_versions_version"),
        CheckConstraint(
            "source_version > 0", name="ck_contract_versions_source_version"
        ),
        CheckConstraint(
            "ends_at IS NULL OR ends_at > starts_at",
            name="ck_contract_versions_interval",
        ),
        CheckConstraint(
            "declared_ends_at IS NULL OR declared_ends_at > starts_at",
            name="ck_contract_versions_declared_interval",
        ),
        CheckConstraint(
            "service_interval_count > 0 AND invoice_interval_count > 0",
            name="ck_contract_versions_interval_counts",
        ),
        CheckConstraint("rate_quantity > 0", name="ck_contract_versions_rate_quantity"),
        CheckConstraint(
            "anchor_day IS NULL OR (anchor_day >= 1 AND anchor_day <= 31)",
            name="ck_contract_versions_anchor_day",
        ),
        CheckConstraint(
            "state IN ('draft', 'effective', 'superseded', 'ended', 'cancelled')",
            name="ck_contract_versions_state",
        ),
        Index(
            "ix_contract_versions_effective", "tenant_id", "contract_id", "starts_at"
        ),
        schema_table_args(SCHEMA),
    )
    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(), ForeignKey(Tenant.__table__.c.id, ondelete="CASCADE"), nullable=False
    )
    contract_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)


class SubscriptionContractLine(Base, _ContractLineColumns):
    __tablename__ = "subscription_contract_lines"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "contract_version_id"],
            [
                f"{SCHEMA}.subscription_contract_versions.tenant_id",
                f"{SCHEMA}.subscription_contract_versions.id",
            ],
            name="fk_contract_lines_version",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "offer_version_id"],
            [f"{SCHEMA}.offer_versions.tenant_id", f"{SCHEMA}.offer_versions.id"],
            name="fk_contract_lines_offer_version",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "tenant_id",
            "contract_version_id",
            "contract_line_key",
            name="uq_contract_lines_lineage",
        ),
        UniqueConstraint(
            "tenant_id",
            "contract_version_id",
            "charge_model_code",
            "product_link_ref",
            name="uq_contract_lines_component",
        ),
        CheckConstraint(
            "quantity > 0 AND unit_price > 0", name="ck_contract_lines_amounts"
        ),
        CheckConstraint("scale >= 0 AND scale <= 6", name="ck_contract_lines_scale"),
        CheckConstraint(
            "source_version > 0 AND offer_version > 0",
            name="ck_contract_lines_versions",
        ),
        Index("ix_contract_lines_version", "tenant_id", "contract_version_id"),
        schema_table_args(SCHEMA),
    )
    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(), ForeignKey(Tenant.__table__.c.id, ondelete="CASCADE"), nullable=False
    )
    contract_version_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    offer_version_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)


class RecurringChargeOccurrence(Base, _OccurrenceColumns):
    __tablename__ = "recurring_charge_occurrences"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "contract_version_id"],
            [
                f"{SCHEMA}.subscription_contract_versions.tenant_id",
                f"{SCHEMA}.subscription_contract_versions.id",
            ],
            name="fk_occurrences_contract_version",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "contract_version_id", "contract_line_key"],
            [
                f"{SCHEMA}.subscription_contract_lines.tenant_id",
                f"{SCHEMA}.subscription_contract_lines.contract_version_id",
                f"{SCHEMA}.subscription_contract_lines.contract_line_key",
            ],
            name="fk_occurrences_contract_line",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "corrects_occurrence_id"],
            [
                f"{SCHEMA}.recurring_charge_occurrences.tenant_id",
                f"{SCHEMA}.recurring_charge_occurrences.id",
            ],
            name="fk_occurrences_correction",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "tenant_id",
            "contract_line_key",
            "contract_version_id",
            "charge_model_code",
            "source_code",
            "source_id",
            "source_version",
            "period_start",
            "period_end",
            "currency",
            name="uq_occurrences_natural_identity",
        ),
        UniqueConstraint(
            "tenant_id", "idempotency_key", name="uq_occurrences_idempotency"
        ),
        UniqueConstraint("tenant_id", "command_id", name="uq_occurrences_command"),
        UniqueConstraint("tenant_id", "id", name="uq_occurrences_tenant_id_id"),
        CheckConstraint("period_end > period_start", name="ck_occurrences_period"),
        CheckConstraint(
            "rating_coverage_end > rating_coverage_start "
            "AND rating_coverage_start >= period_start "
            "AND rating_coverage_end <= period_end",
            name="ck_occurrences_coverage",
        ),
        CheckConstraint(
            "pre_tax_amount >= 0 AND rating_unit_price >= 0 "
            "AND rating_quantity > 0 AND rating_rate_quantity > 0 "
            "AND rating_rate_units >= 0",
            name="ck_occurrences_rating_values",
        ),
        CheckConstraint(
            "rating_proration_factor >= 0 AND rating_proration_factor <= 1",
            name="ck_occurrences_proration",
        ),
        CheckConstraint("generation > 0", name="ck_occurrences_generation"),
        CheckConstraint(
            "state IN ('scheduled', 'due', 'emitted', 'cancelled')",
            name="ck_occurrences_state",
        ),
        CheckConstraint(
            "amount_scale >= 0 AND amount_scale <= 6", name="ck_occurrences_scale"
        ),
        Index(
            "ix_occurrences_contract",
            "tenant_id",
            "contract_version_id",
            "period_start",
        ),
        Index("ix_occurrences_unacknowledged", "tenant_id", "output_acknowledged_at"),
        schema_table_args(SCHEMA),
    )
    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(), ForeignKey(Tenant.__table__.c.id, ondelete="CASCADE"), nullable=False
    )
    contract_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    contract_version_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    corrects_occurrence_id: Mapped[UUID | None] = mapped_column(Uuid(), nullable=True)


class SubscriptionBillingArrangement(Base, _ArrangementColumns):
    __tablename__ = "subscription_billing_arrangements"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "contract_id"],
            [
                f"{SCHEMA}.subscription_contracts.tenant_id",
                f"{SCHEMA}.subscription_contracts.id",
            ],
            name="fk_billing_arrangements_contract",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "authorized_contract_version_id", "contract_line_key"],
            [
                f"{SCHEMA}.subscription_contract_lines.tenant_id",
                f"{SCHEMA}.subscription_contract_lines.contract_version_id",
                f"{SCHEMA}.subscription_contract_lines.contract_line_key",
            ],
            name="fk_billing_arrangements_line",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "authorized_offer_version_id"],
            [f"{SCHEMA}.offer_versions.tenant_id", f"{SCHEMA}.offer_versions.id"],
            name="fk_billing_arrangements_offer_version",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "tenant_id",
            "contract_id",
            "contract_line_key",
            "starts_at",
            name="uq_billing_arrangements_start",
        ),
        UniqueConstraint(
            "tenant_id", "idempotency_key", name="uq_billing_arrangements_idempotency"
        ),
        UniqueConstraint(
            "tenant_id", "command_id", name="uq_billing_arrangements_command"
        ),
        UniqueConstraint(
            "tenant_id",
            "revocation_idempotency_key",
            name="uq_billing_arrangements_revocation_idempotency",
        ),
        UniqueConstraint(
            "tenant_id",
            "revocation_command_id",
            name="uq_billing_arrangements_revocation_command",
        ),
        UniqueConstraint(
            "tenant_id", "id", name="uq_billing_arrangements_tenant_id_id"
        ),
        CheckConstraint(
            "treatment IN ('complimentary', 'sponsored')",
            name="ck_billing_arrangements_nonstandard",
        ),
        CheckConstraint(
            "status IN ('active', 'revoked')", name="ck_billing_arrangements_status"
        ),
        CheckConstraint("ends_at > starts_at", name="ck_billing_arrangements_period"),
        CheckConstraint(
            "maximum_recurring_amount > 0",
            name="ck_billing_arrangements_positive_value",
        ),
        CheckConstraint(
            "approval_policy_max_days > 0",
            name="ck_billing_arrangements_approval_policy",
        ),
        CheckConstraint(
            "scale >= 0 AND scale <= 6", name="ck_billing_arrangements_scale"
        ),
        CheckConstraint(
            "service_interval_count > 0",
            name="ck_billing_arrangements_interval_count",
        ),
        CheckConstraint(
            "treatment <> 'sponsored' OR sponsor_reference IS NOT NULL "
            "OR cost_center IS NOT NULL",
            name="ck_billing_arrangements_sponsor_evidence",
        ),
        CheckConstraint(
            "(status = 'active') = (revoked_at IS NULL)",
            name="ck_billing_arrangements_revocation_evidence",
        ),
        Index(
            "ix_billing_arrangements_effective",
            "tenant_id",
            "contract_id",
            "contract_line_key",
            "status",
            "starts_at",
            "ends_at",
        ),
        schema_table_args(SCHEMA),
    )
    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(), ForeignKey(Tenant.__table__.c.id, ondelete="CASCADE"), nullable=False
    )
    contract_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    authorized_contract_version_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    authorized_offer_version_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)


class SubscriptionBillingGrant(Base, _GrantColumns):
    __tablename__ = "subscription_billing_grants"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "arrangement_id"],
            [
                f"{SCHEMA}.subscription_billing_arrangements.tenant_id",
                f"{SCHEMA}.subscription_billing_arrangements.id",
            ],
            name="fk_billing_grants_arrangement",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "recurring_occurrence_id"],
            [
                f"{SCHEMA}.recurring_charge_occurrences.tenant_id",
                f"{SCHEMA}.recurring_charge_occurrences.id",
            ],
            name="fk_billing_grants_occurrence",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "tenant_id",
            "arrangement_id",
            "starts_at",
            "ends_at",
            name="uq_billing_grants_period",
        ),
        UniqueConstraint(
            "tenant_id",
            "recurring_occurrence_id",
            name="uq_billing_grants_occurrence",
        ),
        UniqueConstraint(
            "tenant_id", "idempotency_key", name="uq_billing_grants_idempotency"
        ),
        UniqueConstraint("tenant_id", "command_id", name="uq_billing_grants_command"),
        CheckConstraint(
            "treatment IN ('complimentary', 'sponsored')",
            name="ck_billing_grants_nonstandard",
        ),
        CheckConstraint("ends_at > starts_at", name="ck_billing_grants_period"),
        CheckConstraint(
            "contracted_amount > 0 AND approved_maximum_amount > 0 "
            "AND foregone_amount > 0 "
            "AND foregone_amount <= contracted_amount "
            "AND foregone_amount <= approved_maximum_amount",
            name="ck_billing_grants_bounded_non_cash_value",
        ),
        CheckConstraint("scale >= 0 AND scale <= 6", name="ck_billing_grants_scale"),
        Index(
            "ix_billing_grants_line_period",
            "tenant_id",
            "contract_line_key",
            "starts_at",
            "ends_at",
        ),
        schema_table_args(SCHEMA),
    )
    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(), ForeignKey(Tenant.__table__.c.id, ondelete="CASCADE"), nullable=False
    )
    arrangement_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    recurring_occurrence_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)


class PlatformOffer(Base, _OfferColumns):
    __tablename__ = "platform_offers"
    __table_args__ = (
        UniqueConstraint("code", name="uq_platform_offers_code"),
        CheckConstraint(
            "status IN ('draft', 'published', 'withdrawn')",
            name="ck_platform_offers_status",
        ),
        Index("ix_platform_offers_status", "status"),
        schema_table_args(SCHEMA),
    )
    id: Mapped[UUID] = uuid_pk()


class PlatformOfferVersion(Base, _OfferVersionColumns):
    __tablename__ = "platform_offer_versions"
    __table_args__ = (
        UniqueConstraint(
            "offer_id", "version", name="uq_platform_offer_versions_identity"
        ),
        UniqueConstraint("command_id", name="uq_platform_offer_versions_command"),
        CheckConstraint("version > 0", name="ck_platform_offer_versions_version"),
        CheckConstraint(
            "source_version > 0", name="ck_platform_offer_versions_source_version"
        ),
        CheckConstraint(
            "state IN ('published', 'withdrawn')",
            name="ck_platform_offer_versions_state",
        ),
        CheckConstraint(
            "pricing_mode IN ('catalog_price', 'contract_price')",
            name="ck_platform_offer_versions_pricing_mode",
        ),
        CheckConstraint(
            "effective_until IS NULL OR effective_until > effective_from",
            name="ck_platform_offer_versions_interval",
        ),
        Index("ix_platform_offer_versions_effective", "offer_id", "effective_from"),
        schema_table_args(SCHEMA),
    )
    id: Mapped[UUID] = uuid_pk()
    offer_id: Mapped[UUID] = mapped_column(
        Uuid(),
        ForeignKey(f"{SCHEMA}.platform_offers.id", ondelete="CASCADE"),
        nullable=False,
    )


class PlatformOfferVersionPrice(Base, _PriceColumns):
    __tablename__ = "platform_offer_version_prices"
    __table_args__ = (
        UniqueConstraint(
            "offer_version_id", "price_key", name="uq_platform_offer_prices_key"
        ),
        CheckConstraint(
            "amount > 0 AND quantity > 0", name="ck_platform_offer_prices_amounts"
        ),
        CheckConstraint(
            "scale >= 0 AND scale <= 6", name="ck_platform_offer_prices_scale"
        ),
        schema_table_args(SCHEMA),
    )
    id: Mapped[UUID] = uuid_pk()
    offer_version_id: Mapped[UUID] = mapped_column(
        Uuid(),
        ForeignKey(f"{SCHEMA}.platform_offer_versions.id", ondelete="CASCADE"),
        nullable=False,
    )


class PlatformSubscriptionContract(Base, _ContractColumns):
    __tablename__ = "platform_subscription_contracts"
    __table_args__ = (
        UniqueConstraint(
            "source_code", "source_id", name="uq_platform_contracts_source"
        ),
        schema_table_args(SCHEMA),
    )
    id: Mapped[UUID] = uuid_pk()


class PlatformSubscriptionContractVersion(Base, _ContractVersionColumns):
    __tablename__ = "platform_subscription_contract_versions"
    __table_args__ = (
        UniqueConstraint(
            "contract_id", "version", name="uq_platform_contract_versions_number"
        ),
        UniqueConstraint(
            "idempotency_key", name="uq_platform_contract_versions_idempotency"
        ),
        UniqueConstraint("command_id", name="uq_platform_contract_versions_command"),
        CheckConstraint("version > 0", name="ck_platform_contract_versions_version"),
        CheckConstraint(
            "source_version > 0", name="ck_platform_contract_versions_source_version"
        ),
        CheckConstraint(
            "ends_at IS NULL OR ends_at > starts_at",
            name="ck_platform_contract_versions_interval",
        ),
        CheckConstraint(
            "declared_ends_at IS NULL OR declared_ends_at > starts_at",
            name="ck_platform_contract_versions_declared_interval",
        ),
        CheckConstraint(
            "service_interval_count > 0 AND invoice_interval_count > 0",
            name="ck_platform_contract_versions_interval_counts",
        ),
        CheckConstraint(
            "rate_quantity > 0", name="ck_platform_contract_versions_rate_quantity"
        ),
        CheckConstraint(
            "anchor_day IS NULL OR (anchor_day >= 1 AND anchor_day <= 31)",
            name="ck_platform_contract_versions_anchor_day",
        ),
        CheckConstraint(
            "state IN ('draft', 'effective', 'superseded', 'ended', 'cancelled')",
            name="ck_platform_contract_versions_state",
        ),
        Index("ix_platform_contract_versions_effective", "contract_id", "starts_at"),
        schema_table_args(SCHEMA),
    )
    id: Mapped[UUID] = uuid_pk()
    contract_id: Mapped[UUID] = mapped_column(
        Uuid(),
        ForeignKey(f"{SCHEMA}.platform_subscription_contracts.id", ondelete="CASCADE"),
        nullable=False,
    )
    supersedes_id: Mapped[UUID | None] = mapped_column(
        Uuid(),
        ForeignKey(
            f"{SCHEMA}.platform_subscription_contract_versions.id", ondelete="RESTRICT"
        ),
        nullable=True,
    )


class PlatformSubscriptionContractLine(Base, _ContractLineColumns):
    __tablename__ = "platform_subscription_contract_lines"
    __table_args__ = (
        UniqueConstraint(
            "contract_version_id",
            "contract_line_key",
            name="uq_platform_contract_lines_lineage",
        ),
        UniqueConstraint(
            "contract_version_id",
            "charge_model_code",
            "product_link_ref",
            name="uq_platform_contract_lines_component",
        ),
        CheckConstraint(
            "quantity > 0 AND unit_price > 0",
            name="ck_platform_contract_lines_amounts",
        ),
        CheckConstraint(
            "scale >= 0 AND scale <= 6", name="ck_platform_contract_lines_scale"
        ),
        CheckConstraint(
            "source_version > 0 AND offer_version > 0",
            name="ck_platform_contract_lines_versions",
        ),
        Index("ix_platform_contract_lines_version", "contract_version_id"),
        schema_table_args(SCHEMA),
    )
    id: Mapped[UUID] = uuid_pk()
    contract_version_id: Mapped[UUID] = mapped_column(
        Uuid(),
        ForeignKey(
            f"{SCHEMA}.platform_subscription_contract_versions.id", ondelete="CASCADE"
        ),
        nullable=False,
    )
    offer_version_id: Mapped[UUID] = mapped_column(
        Uuid(),
        ForeignKey(f"{SCHEMA}.platform_offer_versions.id", ondelete="RESTRICT"),
        nullable=False,
    )


class PlatformRecurringChargeOccurrence(Base, _OccurrenceColumns):
    __tablename__ = "platform_recurring_charge_occurrences"
    __table_args__ = (
        ForeignKeyConstraint(
            ["contract_version_id", "contract_line_key"],
            [
                f"{SCHEMA}.platform_subscription_contract_lines.contract_version_id",
                f"{SCHEMA}.platform_subscription_contract_lines.contract_line_key",
            ],
            name="fk_platform_occurrences_contract_line",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "contract_line_key",
            "contract_version_id",
            "charge_model_code",
            "source_code",
            "source_id",
            "source_version",
            "period_start",
            "period_end",
            "currency",
            name="uq_platform_occurrences_natural_identity",
        ),
        UniqueConstraint("idempotency_key", name="uq_platform_occurrences_idempotency"),
        UniqueConstraint("command_id", name="uq_platform_occurrences_command"),
        CheckConstraint(
            "period_end > period_start", name="ck_platform_occurrences_period"
        ),
        CheckConstraint(
            "rating_coverage_end > rating_coverage_start "
            "AND rating_coverage_start >= period_start "
            "AND rating_coverage_end <= period_end",
            name="ck_platform_occurrences_coverage",
        ),
        CheckConstraint(
            "pre_tax_amount >= 0 AND rating_unit_price >= 0 "
            "AND rating_quantity > 0 AND rating_rate_quantity > 0 "
            "AND rating_rate_units >= 0",
            name="ck_platform_occurrences_rating_values",
        ),
        CheckConstraint(
            "rating_proration_factor >= 0 AND rating_proration_factor <= 1",
            name="ck_platform_occurrences_proration",
        ),
        CheckConstraint("generation > 0", name="ck_platform_occurrences_generation"),
        CheckConstraint(
            "state IN ('scheduled', 'due', 'emitted', 'cancelled')",
            name="ck_platform_occurrences_state",
        ),
        CheckConstraint(
            "amount_scale >= 0 AND amount_scale <= 6",
            name="ck_platform_occurrences_scale",
        ),
        Index(
            "ix_platform_occurrences_contract", "contract_version_id", "period_start"
        ),
        Index("ix_platform_occurrences_unacknowledged", "output_acknowledged_at"),
        schema_table_args(SCHEMA),
    )
    id: Mapped[UUID] = uuid_pk()
    contract_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    contract_version_id: Mapped[UUID] = mapped_column(
        Uuid(),
        ForeignKey(
            f"{SCHEMA}.platform_subscription_contract_versions.id", ondelete="RESTRICT"
        ),
        nullable=False,
    )
    corrects_occurrence_id: Mapped[UUID | None] = mapped_column(
        Uuid(),
        ForeignKey(
            f"{SCHEMA}.platform_recurring_charge_occurrences.id", ondelete="RESTRICT"
        ),
        nullable=True,
    )


class PlatformSubscriptionBillingArrangement(Base, _ArrangementColumns):
    __tablename__ = "platform_subscription_billing_arrangements"
    __table_args__ = (
        ForeignKeyConstraint(
            ["authorized_contract_version_id", "contract_line_key"],
            [
                f"{SCHEMA}.platform_subscription_contract_lines.contract_version_id",
                f"{SCHEMA}.platform_subscription_contract_lines.contract_line_key",
            ],
            name="fk_platform_billing_arrangements_line",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "contract_id",
            "contract_line_key",
            "starts_at",
            name="uq_platform_billing_arrangements_start",
        ),
        UniqueConstraint(
            "idempotency_key", name="uq_platform_billing_arrangements_idempotency"
        ),
        UniqueConstraint("command_id", name="uq_platform_billing_arrangements_command"),
        UniqueConstraint(
            "revocation_idempotency_key",
            name="uq_platform_billing_arrangements_revocation_idempotency",
        ),
        UniqueConstraint(
            "revocation_command_id",
            name="uq_platform_billing_arrangements_revocation_command",
        ),
        CheckConstraint(
            "treatment IN ('complimentary', 'sponsored')",
            name="ck_platform_billing_arrangements_nonstandard",
        ),
        CheckConstraint(
            "status IN ('active', 'revoked')",
            name="ck_platform_billing_arrangements_status",
        ),
        CheckConstraint(
            "ends_at > starts_at", name="ck_platform_billing_arrangements_period"
        ),
        CheckConstraint(
            "maximum_recurring_amount > 0",
            name="ck_platform_billing_arrangements_positive_value",
        ),
        CheckConstraint(
            "approval_policy_max_days > 0",
            name="ck_platform_billing_arrangements_approval_policy",
        ),
        CheckConstraint(
            "scale >= 0 AND scale <= 6", name="ck_platform_billing_arrangements_scale"
        ),
        CheckConstraint(
            "service_interval_count > 0",
            name="ck_platform_billing_arrangements_interval_count",
        ),
        CheckConstraint(
            "treatment <> 'sponsored' OR sponsor_reference IS NOT NULL "
            "OR cost_center IS NOT NULL",
            name="ck_platform_billing_arrangements_sponsor_evidence",
        ),
        CheckConstraint(
            "(status = 'active') = (revoked_at IS NULL)",
            name="ck_platform_billing_arrangements_revocation_evidence",
        ),
        Index(
            "ix_platform_billing_arrangements_effective",
            "contract_id",
            "contract_line_key",
            "status",
            "starts_at",
            "ends_at",
        ),
        schema_table_args(SCHEMA),
    )
    id: Mapped[UUID] = uuid_pk()
    contract_id: Mapped[UUID] = mapped_column(
        Uuid(),
        ForeignKey(f"{SCHEMA}.platform_subscription_contracts.id", ondelete="RESTRICT"),
        nullable=False,
    )
    authorized_contract_version_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    authorized_offer_version_id: Mapped[UUID] = mapped_column(
        Uuid(),
        ForeignKey(f"{SCHEMA}.platform_offer_versions.id", ondelete="RESTRICT"),
        nullable=False,
    )


class PlatformSubscriptionBillingGrant(Base, _GrantColumns):
    __tablename__ = "platform_subscription_billing_grants"
    __table_args__ = (
        UniqueConstraint(
            "arrangement_id",
            "starts_at",
            "ends_at",
            name="uq_platform_billing_grants_period",
        ),
        UniqueConstraint(
            "recurring_occurrence_id", name="uq_platform_billing_grants_occurrence"
        ),
        UniqueConstraint(
            "idempotency_key", name="uq_platform_billing_grants_idempotency"
        ),
        UniqueConstraint("command_id", name="uq_platform_billing_grants_command"),
        CheckConstraint(
            "treatment IN ('complimentary', 'sponsored')",
            name="ck_platform_billing_grants_nonstandard",
        ),
        CheckConstraint(
            "ends_at > starts_at", name="ck_platform_billing_grants_period"
        ),
        CheckConstraint(
            "contracted_amount > 0 AND approved_maximum_amount > 0 "
            "AND foregone_amount > 0 "
            "AND foregone_amount <= contracted_amount "
            "AND foregone_amount <= approved_maximum_amount",
            name="ck_platform_billing_grants_bounded_non_cash_value",
        ),
        CheckConstraint(
            "scale >= 0 AND scale <= 6", name="ck_platform_billing_grants_scale"
        ),
        Index(
            "ix_platform_billing_grants_line_period",
            "contract_line_key",
            "starts_at",
            "ends_at",
        ),
        schema_table_args(SCHEMA),
    )
    id: Mapped[UUID] = uuid_pk()
    arrangement_id: Mapped[UUID] = mapped_column(
        Uuid(),
        ForeignKey(
            f"{SCHEMA}.platform_subscription_billing_arrangements.id",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )
    recurring_occurrence_id: Mapped[UUID] = mapped_column(
        Uuid(),
        ForeignKey(
            f"{SCHEMA}.platform_recurring_charge_occurrences.id", ondelete="RESTRICT"
        ),
        nullable=False,
    )


TENANT_TABLES = (
    "offers",
    "offer_versions",
    "offer_version_prices",
    "subscription_contracts",
    "subscription_contract_versions",
    "subscription_contract_lines",
    "recurring_charge_occurrences",
    "subscription_billing_arrangements",
    "subscription_billing_grants",
)
PLATFORM_TABLES = (
    "platform_offers",
    "platform_offer_versions",
    "platform_offer_version_prices",
    "platform_subscription_contracts",
    "platform_subscription_contract_versions",
    "platform_subscription_contract_lines",
    "platform_recurring_charge_occurrences",
    "platform_subscription_billing_arrangements",
    "platform_subscription_billing_grants",
)

__all__ = [
    "PLATFORM_TABLES",
    "SCHEMA",
    "TENANT_TABLES",
    "Offer",
    "OfferVersion",
    "OfferVersionPrice",
    "PlatformOffer",
    "PlatformOfferVersion",
    "PlatformOfferVersionPrice",
    "PlatformRecurringChargeOccurrence",
    "PlatformSubscriptionBillingArrangement",
    "PlatformSubscriptionBillingGrant",
    "PlatformSubscriptionContract",
    "PlatformSubscriptionContractLine",
    "PlatformSubscriptionContractVersion",
    "RecurringChargeOccurrence",
    "SubscriptionBillingArrangement",
    "SubscriptionBillingGrant",
    "SubscriptionContract",
    "SubscriptionContractLine",
    "SubscriptionContractVersion",
]
