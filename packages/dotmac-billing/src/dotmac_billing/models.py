"""Billing evidence on separate tenant and platform persistence planes.

Balances are absent by design. Posting effects are immutable source facts;
position facts are immutable rebuild attestations over those effects.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from dotmac_kernel.models import Base, Tenant, uuid_pk
from dotmac_kernel.namespaces import module_schema, schema_table_args
from sqlalchemy import (
    JSON,
    BigInteger,
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
    func,
    text,
)
from sqlalchemy.orm import Mapped, MappedColumn, declared_attr, mapped_column

SCHEMA = module_schema("billing")
MONEY = Numeric(20, 6)


def money_column(
    *, nullable: bool = False, default: Decimal | None = None
) -> MappedColumn[Decimal]:
    return mapped_column(
        MONEY,
        nullable=nullable,
        default=default,
        info={"billing_money": True},
    )


class _EvidenceTime:
    @declared_attr
    def created_at(cls) -> Mapped[datetime]:
        return mapped_column(
            DateTime(timezone=True), nullable=False, server_default=func.now()
        )


class _TenantRow:
    @declared_attr
    def tenant_id(cls) -> Mapped[UUID]:
        return mapped_column(
            Uuid(),
            ForeignKey(Tenant.__table__.c.id, ondelete="CASCADE"),
            nullable=False,
        )


class _AccountColumns(_EvidenceTime):
    id: Mapped[UUID] = uuid_pk()
    external_account_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    minor_units: Mapped[int] = mapped_column(Integer, nullable=False)


class BillingAccount(Base, _TenantRow, _AccountColumns):
    __tablename__ = "billing_accounts"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_billing_accounts_tenant_id_id"),
        UniqueConstraint(
            "tenant_id",
            "external_account_ref",
            "currency",
            name="uq_billing_accounts_tenant_external_currency",
        ),
        schema_table_args(SCHEMA),
    )


class PlatformBillingAccount(Base, _AccountColumns):
    __tablename__ = "platform_billing_accounts"
    __table_args__ = (
        UniqueConstraint(
            "external_account_ref",
            "currency",
            name="uq_platform_billing_accounts_external_currency",
        ),
        schema_table_args(SCHEMA),
    )


class _ObligationColumns(_EvidenceTime):
    id: Mapped[UUID] = uuid_pk()
    billing_account_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    natural_key_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    request_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    contract_line_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    contract_version: Mapped[str] = mapped_column(String(120), nullable=False)
    charge_component: Mapped[str] = mapped_column(String(120), nullable=False)
    source_system: Mapped[str] = mapped_column(String(120), nullable=False)
    source_kind: Mapped[str] = mapped_column(String(120), nullable=False)
    source_fact_id: Mapped[str] = mapped_column(String(255), nullable=False)
    source_fact_version: Mapped[str] = mapped_column(String(120), nullable=False)
    subject_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    service_ref: Mapped[str | None] = mapped_column(String(255))
    service_period_status: Mapped[str] = mapped_column(String(32), nullable=False)
    period_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    period_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    collection_timing: Mapped[str] = mapped_column(String(80), nullable=False)
    pre_tax_amount: Mapped[Decimal] = money_column()
    tax_amount: Mapped[Decimal] = money_column()
    total_amount: Mapped[Decimal] = money_column()
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    minor_units: Mapped[int] = mapped_column(Integer, nullable=False)
    rated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    price_version_id: Mapped[str] = mapped_column(String(255), nullable=False)
    supersedes_obligation_id: Mapped[UUID | None] = mapped_column(Uuid())


class RatedObligation(Base, _TenantRow, _ObligationColumns):
    __tablename__ = "rated_obligations"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "billing_account_id"],
            [f"{SCHEMA}.billing_accounts.tenant_id", f"{SCHEMA}.billing_accounts.id"],
            name="fk_rated_obligations_account",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "supersedes_obligation_id"],
            [f"{SCHEMA}.rated_obligations.tenant_id", f"{SCHEMA}.rated_obligations.id"],
            name="fk_rated_obligations_supersedes",
            ondelete="RESTRICT",
        ),
        UniqueConstraint("tenant_id", "id", name="uq_rated_obligations_tenant_id_id"),
        UniqueConstraint(
            "tenant_id",
            "id",
            "billing_account_id",
            name="uq_rated_obligations_tenant_id_account",
        ),
        UniqueConstraint(
            "tenant_id",
            "supersedes_obligation_id",
            name="uq_rated_obligations_tenant_superseded_once",
        ),
        UniqueConstraint(
            "tenant_id",
            "natural_key_digest",
            name="uq_rated_obligations_tenant_natural_key",
        ),
        schema_table_args(SCHEMA),
    )


class PlatformRatedObligation(Base, _ObligationColumns):
    __tablename__ = "platform_rated_obligations"
    __table_args__ = (
        ForeignKeyConstraint(
            ["billing_account_id"],
            [f"{SCHEMA}.platform_billing_accounts.id"],
            name="fk_platform_rated_obligations_account",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["supersedes_obligation_id"],
            [f"{SCHEMA}.platform_rated_obligations.id"],
            name="fk_platform_rated_obligations_supersedes",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "id",
            "billing_account_id",
            name="uq_platform_rated_obligations_id_account",
        ),
        UniqueConstraint(
            "supersedes_obligation_id",
            name="uq_platform_rated_obligations_superseded_once",
        ),
        UniqueConstraint(
            "natural_key_digest", name="uq_platform_rated_obligations_natural_key"
        ),
        schema_table_args(SCHEMA),
    )


class _DocumentColumns(_EvidenceTime):
    id: Mapped[UUID] = uuid_pk()
    billing_account_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    obligation_id: Mapped[UUID | None] = mapped_column(Uuid())
    document_kind: Mapped[str] = mapped_column(String(24), nullable=False)
    credits_document_id: Mapped[UUID | None] = mapped_column(Uuid())
    lifecycle: Mapped[str] = mapped_column(String(16), nullable=False, default="draft")
    series_code: Mapped[str | None] = mapped_column(String(80))
    document_number: Mapped[str | None] = mapped_column(String(255))
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    minor_units: Mapped[int] = mapped_column(Integer, nullable=False)
    subtotal: Mapped[Decimal] = money_column(default=Decimal("0"))
    tax_total: Mapped[Decimal] = money_column(default=Decimal("0"))
    grand_total: Mapped[Decimal] = money_column(default=Decimal("0"))
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    due_date_basis: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    document_profile_code: Mapped[str] = mapped_column(String(120), nullable=False)
    document_profile_version: Mapped[str] = mapped_column(String(120), nullable=False)
    seller_snapshot: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    customer_snapshot: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    payment_instructions: Mapped[dict[str, object]] = mapped_column(
        JSON, nullable=False
    )
    brand_asset: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    locale: Mapped[str] = mapped_column(String(40), nullable=False)
    timezone: Mapped[str] = mapped_column(String(80), nullable=False)
    issued_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class BillingDocument(Base, _TenantRow, _DocumentColumns):
    __tablename__ = "documents"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "billing_account_id"],
            [f"{SCHEMA}.billing_accounts.tenant_id", f"{SCHEMA}.billing_accounts.id"],
            name="fk_documents_account",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "obligation_id", "billing_account_id"],
            [
                f"{SCHEMA}.rated_obligations.tenant_id",
                f"{SCHEMA}.rated_obligations.id",
                f"{SCHEMA}.rated_obligations.billing_account_id",
            ],
            name="fk_documents_obligation_account",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "credits_document_id", "billing_account_id"],
            [
                f"{SCHEMA}.documents.tenant_id",
                f"{SCHEMA}.documents.id",
                f"{SCHEMA}.documents.billing_account_id",
            ],
            name="fk_documents_credited_document_account",
            ondelete="RESTRICT",
        ),
        UniqueConstraint("tenant_id", "id", name="uq_documents_tenant_id_id"),
        UniqueConstraint(
            "tenant_id", "id", "billing_account_id", name="uq_documents_id_account"
        ),
        UniqueConstraint(
            "tenant_id", "id", "obligation_id", name="uq_documents_id_obligation"
        ),
        UniqueConstraint(
            "tenant_id",
            "series_code",
            "document_number",
            name="uq_documents_tenant_number",
        ),
        schema_table_args(SCHEMA),
    )


class PlatformBillingDocument(Base, _DocumentColumns):
    __tablename__ = "platform_documents"
    __table_args__ = (
        ForeignKeyConstraint(
            ["billing_account_id"],
            [f"{SCHEMA}.platform_billing_accounts.id"],
            name="fk_platform_documents_account",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["obligation_id", "billing_account_id"],
            [
                f"{SCHEMA}.platform_rated_obligations.id",
                f"{SCHEMA}.platform_rated_obligations.billing_account_id",
            ],
            name="fk_platform_documents_obligation_account",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["credits_document_id", "billing_account_id"],
            [
                f"{SCHEMA}.platform_documents.id",
                f"{SCHEMA}.platform_documents.billing_account_id",
            ],
            name="fk_platform_documents_credited_account",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "id", "billing_account_id", name="uq_platform_documents_id_account"
        ),
        UniqueConstraint(
            "id", "obligation_id", name="uq_platform_documents_id_obligation"
        ),
        UniqueConstraint(
            "series_code", "document_number", name="uq_platform_documents_number"
        ),
        schema_table_args(SCHEMA),
    )


class _DocumentLineColumns(_EvidenceTime):
    id: Mapped[UUID] = uuid_pk()
    document_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    obligation_id: Mapped[UUID | None] = mapped_column(Uuid())
    line_number: Mapped[int] = mapped_column(Integer, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    quantity: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    unit_code: Mapped[str] = mapped_column(String(40), nullable=False)
    unit_amount: Mapped[Decimal] = money_column()
    pre_tax_amount: Mapped[Decimal] = money_column()
    tax_amount: Mapped[Decimal] = money_column()
    total_amount: Mapped[Decimal] = money_column()
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    minor_units: Mapped[int] = mapped_column(Integer, nullable=False)
    price_source_version: Mapped[str] = mapped_column(String(255), nullable=False)


class DocumentLine(Base, _TenantRow, _DocumentLineColumns):
    __tablename__ = "document_lines"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "document_id"],
            [f"{SCHEMA}.documents.tenant_id", f"{SCHEMA}.documents.id"],
            name="fk_document_lines_document",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "document_id", "obligation_id"],
            [
                f"{SCHEMA}.documents.tenant_id",
                f"{SCHEMA}.documents.id",
                f"{SCHEMA}.documents.obligation_id",
            ],
            name="fk_document_lines_document_obligation",
            ondelete="RESTRICT",
        ),
        UniqueConstraint("tenant_id", "id", name="uq_document_lines_tenant_id_id"),
        UniqueConstraint(
            "tenant_id",
            "document_id",
            "line_number",
            name="uq_document_lines_tenant_line",
        ),
        UniqueConstraint(
            "tenant_id", "obligation_id", name="uq_document_lines_tenant_obligation"
        ),
        schema_table_args(SCHEMA),
    )


class PlatformDocumentLine(Base, _DocumentLineColumns):
    __tablename__ = "platform_document_lines"
    __table_args__ = (
        ForeignKeyConstraint(
            ["document_id"],
            [f"{SCHEMA}.platform_documents.id"],
            name="fk_platform_document_lines_document",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["document_id", "obligation_id"],
            [
                f"{SCHEMA}.platform_documents.id",
                f"{SCHEMA}.platform_documents.obligation_id",
            ],
            name="fk_platform_document_lines_obligation",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "document_id", "line_number", name="uq_platform_document_lines_line"
        ),
        UniqueConstraint("obligation_id", name="uq_platform_document_lines_obligation"),
        schema_table_args(SCHEMA),
    )


class _DocumentEventColumns(_EvidenceTime):
    id: Mapped[UUID] = uuid_pk()
    document_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    event_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    reason: Mapped[str | None] = mapped_column(Text)
    actor_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


class DocumentEvent(Base, _TenantRow, _DocumentEventColumns):
    __tablename__ = "document_events"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "document_id"],
            [f"{SCHEMA}.documents.tenant_id", f"{SCHEMA}.documents.id"],
            name="fk_document_events_document",
            ondelete="RESTRICT",
        ),
        UniqueConstraint("tenant_id", "id", name="uq_document_events_tenant_id_id"),
        UniqueConstraint(
            "tenant_id",
            "document_id",
            "event_kind",
            name="uq_document_events_tenant_kind",
        ),
        schema_table_args(SCHEMA),
    )


class PlatformDocumentEvent(Base, _DocumentEventColumns):
    __tablename__ = "platform_document_events"
    __table_args__ = (
        ForeignKeyConstraint(
            ["document_id"],
            [f"{SCHEMA}.platform_documents.id"],
            name="fk_platform_document_events_document",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "document_id", "event_kind", name="uq_platform_document_events_kind"
        ),
        schema_table_args(SCHEMA),
    )


class _SettlementColumns(_EvidenceTime):
    id: Mapped[UUID] = uuid_pk()
    billing_account_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    source_system: Mapped[str] = mapped_column(String(120), nullable=False)
    source_settlement_key: Mapped[str] = mapped_column(String(255), nullable=False)
    source_version: Mapped[str] = mapped_column(String(120), nullable=False)
    request_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    amount: Mapped[Decimal] = money_column()
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    minor_units: Mapped[int] = mapped_column(Integer, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    observed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    confirmation_evidence: Mapped[str] = mapped_column(String(120), nullable=False)
    funding_lane: Mapped[str] = mapped_column(String(32), nullable=False)


class ConfirmedSettlement(Base, _TenantRow, _SettlementColumns):
    __tablename__ = "confirmed_settlements"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "billing_account_id"],
            [f"{SCHEMA}.billing_accounts.tenant_id", f"{SCHEMA}.billing_accounts.id"],
            name="fk_settlements_account",
            ondelete="RESTRICT",
        ),
        UniqueConstraint("tenant_id", "id", name="uq_settlements_tenant_id_id"),
        UniqueConstraint(
            "tenant_id", "id", "billing_account_id", name="uq_settlements_id_account"
        ),
        UniqueConstraint(
            "tenant_id",
            "source_system",
            "source_settlement_key",
            name="uq_settlements_tenant_source_key",
        ),
        schema_table_args(SCHEMA),
    )


class PlatformConfirmedSettlement(Base, _SettlementColumns):
    __tablename__ = "platform_confirmed_settlements"
    __table_args__ = (
        ForeignKeyConstraint(
            ["billing_account_id"],
            [f"{SCHEMA}.platform_billing_accounts.id"],
            name="fk_platform_settlements_account",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "id", "billing_account_id", name="uq_platform_settlements_id_account"
        ),
        UniqueConstraint(
            "source_system",
            "source_settlement_key",
            name="uq_platform_settlements_source_key",
        ),
        schema_table_args(SCHEMA),
    )


class _PostingGroupColumns(_EvidenceTime):
    id: Mapped[UUID] = uuid_pk()
    billing_account_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    group_kind: Mapped[str] = mapped_column(String(40), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    minor_units: Mapped[int] = mapped_column(Integer, nullable=False)
    source_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    source_version: Mapped[int] = mapped_column(BigInteger, nullable=False)
    reverses_group_id: Mapped[UUID | None] = mapped_column(Uuid())
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


class PostingGroup(Base, _TenantRow, _PostingGroupColumns):
    __tablename__ = "posting_groups"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "billing_account_id"],
            [f"{SCHEMA}.billing_accounts.tenant_id", f"{SCHEMA}.billing_accounts.id"],
            name="fk_posting_groups_account",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "reverses_group_id", "billing_account_id"],
            [
                f"{SCHEMA}.posting_groups.tenant_id",
                f"{SCHEMA}.posting_groups.id",
                f"{SCHEMA}.posting_groups.billing_account_id",
            ],
            name="fk_posting_groups_reverses_account",
            ondelete="RESTRICT",
        ),
        UniqueConstraint("tenant_id", "id", name="uq_posting_groups_tenant_id_id"),
        UniqueConstraint(
            "tenant_id", "id", "billing_account_id", name="uq_posting_groups_id_account"
        ),
        UniqueConstraint(
            "tenant_id",
            "billing_account_id",
            "source_version",
            name="uq_posting_groups_tenant_account_version",
        ),
        schema_table_args(SCHEMA),
    )


class PlatformPostingGroup(Base, _PostingGroupColumns):
    __tablename__ = "platform_posting_groups"
    __table_args__ = (
        ForeignKeyConstraint(
            ["billing_account_id"],
            [f"{SCHEMA}.platform_billing_accounts.id"],
            name="fk_platform_posting_groups_account",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["reverses_group_id", "billing_account_id"],
            [
                f"{SCHEMA}.platform_posting_groups.id",
                f"{SCHEMA}.platform_posting_groups.billing_account_id",
            ],
            name="fk_platform_posting_groups_reverses_account",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "id", "billing_account_id", name="uq_platform_posting_groups_id_account"
        ),
        UniqueConstraint(
            "billing_account_id",
            "source_version",
            name="uq_platform_posting_groups_account_version",
        ),
        schema_table_args(SCHEMA),
    )


class _PostingEffectColumns(_EvidenceTime):
    id: Mapped[UUID] = uuid_pk()
    posting_group_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    billing_account_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    lane: Mapped[str] = mapped_column(String(32), nullable=False)
    amount_delta: Mapped[Decimal] = money_column()
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    minor_units: Mapped[int] = mapped_column(Integer, nullable=False)


class PostingEffect(Base, _TenantRow, _PostingEffectColumns):
    __tablename__ = "posting_effects"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "posting_group_id", "billing_account_id"],
            [
                f"{SCHEMA}.posting_groups.tenant_id",
                f"{SCHEMA}.posting_groups.id",
                f"{SCHEMA}.posting_groups.billing_account_id",
            ],
            name="fk_posting_effects_group_account",
            ondelete="RESTRICT",
        ),
        UniqueConstraint("tenant_id", "id", name="uq_posting_effects_tenant_id_id"),
        UniqueConstraint(
            "tenant_id",
            "posting_group_id",
            "lane",
            name="uq_posting_effects_tenant_lane",
        ),
        schema_table_args(SCHEMA),
    )


class PlatformPostingEffect(Base, _PostingEffectColumns):
    __tablename__ = "platform_posting_effects"
    __table_args__ = (
        ForeignKeyConstraint(
            ["posting_group_id", "billing_account_id"],
            [
                f"{SCHEMA}.platform_posting_groups.id",
                f"{SCHEMA}.platform_posting_groups.billing_account_id",
            ],
            name="fk_platform_posting_effects_group_account",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "posting_group_id", "lane", name="uq_platform_posting_effects_lane"
        ),
        schema_table_args(SCHEMA),
    )


class _AllocationEffectColumns(_EvidenceTime):
    id: Mapped[UUID] = uuid_pk()
    posting_group_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    billing_account_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    settlement_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    document_id: Mapped[UUID | None] = mapped_column(Uuid())
    effect_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    amount_delta: Mapped[Decimal] = money_column()
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    minor_units: Mapped[int] = mapped_column(Integer, nullable=False)
    offsets_allocation_id: Mapped[UUID | None] = mapped_column(Uuid())


class AllocationEffect(Base, _TenantRow, _AllocationEffectColumns):
    __tablename__ = "allocation_effects"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "posting_group_id", "billing_account_id"],
            [
                f"{SCHEMA}.posting_groups.tenant_id",
                f"{SCHEMA}.posting_groups.id",
                f"{SCHEMA}.posting_groups.billing_account_id",
            ],
            name="fk_allocation_effects_group_account",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "settlement_id", "billing_account_id"],
            [
                f"{SCHEMA}.confirmed_settlements.tenant_id",
                f"{SCHEMA}.confirmed_settlements.id",
                f"{SCHEMA}.confirmed_settlements.billing_account_id",
            ],
            name="fk_allocation_effects_settlement_account",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "document_id", "billing_account_id"],
            [
                f"{SCHEMA}.documents.tenant_id",
                f"{SCHEMA}.documents.id",
                f"{SCHEMA}.documents.billing_account_id",
            ],
            name="fk_allocation_effects_document_account",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "offsets_allocation_id", "billing_account_id"],
            [
                f"{SCHEMA}.allocation_effects.tenant_id",
                f"{SCHEMA}.allocation_effects.id",
                f"{SCHEMA}.allocation_effects.billing_account_id",
            ],
            name="fk_allocation_effects_offset_account",
            ondelete="RESTRICT",
        ),
        UniqueConstraint("tenant_id", "id", name="uq_allocation_effects_tenant_id_id"),
        UniqueConstraint(
            "tenant_id",
            "id",
            "billing_account_id",
            name="uq_allocation_effects_id_account",
        ),
        UniqueConstraint(
            "tenant_id",
            "posting_group_id",
            "settlement_id",
            "document_id",
            name="uq_allocation_effects_tenant_edge",
        ),
        schema_table_args(SCHEMA),
    )


class PlatformAllocationEffect(Base, _AllocationEffectColumns):
    __tablename__ = "platform_allocation_effects"
    __table_args__ = (
        ForeignKeyConstraint(
            ["posting_group_id", "billing_account_id"],
            [
                f"{SCHEMA}.platform_posting_groups.id",
                f"{SCHEMA}.platform_posting_groups.billing_account_id",
            ],
            name="fk_platform_allocations_group_account",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["settlement_id", "billing_account_id"],
            [
                f"{SCHEMA}.platform_confirmed_settlements.id",
                f"{SCHEMA}.platform_confirmed_settlements.billing_account_id",
            ],
            name="fk_platform_allocations_settlement_account",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["document_id", "billing_account_id"],
            [
                f"{SCHEMA}.platform_documents.id",
                f"{SCHEMA}.platform_documents.billing_account_id",
            ],
            name="fk_platform_allocations_document_account",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["offsets_allocation_id", "billing_account_id"],
            [
                f"{SCHEMA}.platform_allocation_effects.id",
                f"{SCHEMA}.platform_allocation_effects.billing_account_id",
            ],
            name="fk_platform_allocations_offset_account",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "id", "billing_account_id", name="uq_platform_allocations_id_account"
        ),
        UniqueConstraint(
            "posting_group_id",
            "settlement_id",
            "document_id",
            name="uq_platform_allocation_effects_edge",
        ),
        schema_table_args(SCHEMA),
    )


class _TaxSnapshotColumns(_EvidenceTime):
    id: Mapped[UUID] = uuid_pk()
    obligation_id: Mapped[UUID | None] = mapped_column(Uuid())
    document_id: Mapped[UUID | None] = mapped_column(Uuid())
    treatment_code: Mapped[str] = mapped_column(String(120), nullable=False)
    jurisdiction_code: Mapped[str] = mapped_column(String(120), nullable=False)
    policy_id: Mapped[str] = mapped_column(String(255), nullable=False)
    policy_version: Mapped[str] = mapped_column(String(120), nullable=False)
    rate: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    taxable_basis: Mapped[Decimal] = money_column()
    tax_amount: Mapped[Decimal] = money_column()
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    minor_units: Mapped[int] = mapped_column(Integer, nullable=False)


class AppliedTaxSnapshot(Base, _TenantRow, _TaxSnapshotColumns):
    __tablename__ = "applied_tax_snapshots"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "obligation_id"],
            [f"{SCHEMA}.rated_obligations.tenant_id", f"{SCHEMA}.rated_obligations.id"],
            name="fk_tax_snapshots_obligation",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "document_id"],
            [f"{SCHEMA}.documents.tenant_id", f"{SCHEMA}.documents.id"],
            name="fk_tax_snapshots_document",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "(obligation_id IS NULL) <> (document_id IS NULL)",
            name="ck_tax_snapshots_one_parent",
        ),
        UniqueConstraint("tenant_id", "id", name="uq_tax_snapshots_tenant_id_id"),
        schema_table_args(SCHEMA),
    )


class PlatformAppliedTaxSnapshot(Base, _TaxSnapshotColumns):
    __tablename__ = "platform_applied_tax_snapshots"
    __table_args__ = (
        ForeignKeyConstraint(
            ["obligation_id"],
            [f"{SCHEMA}.platform_rated_obligations.id"],
            name="fk_platform_tax_snapshots_obligation",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["document_id"],
            [f"{SCHEMA}.platform_documents.id"],
            name="fk_platform_tax_snapshots_document",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "(obligation_id IS NULL) <> (document_id IS NULL)",
            name="ck_platform_tax_snapshots_one_parent",
        ),
        schema_table_args(SCHEMA),
    )


class _FxSnapshotColumns(_EvidenceTime):
    id: Mapped[UUID] = uuid_pk()
    obligation_id: Mapped[UUID | None] = mapped_column(Uuid())
    document_id: Mapped[UUID | None] = mapped_column(Uuid())
    observation_id: Mapped[str] = mapped_column(String(255), nullable=False)
    observation_version: Mapped[str] = mapped_column(String(120), nullable=False)
    base_currency: Mapped[str] = mapped_column(String(3), nullable=False)
    quote_currency: Mapped[str] = mapped_column(String(3), nullable=False)
    rate: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    rate_purpose: Mapped[str] = mapped_column(String(120), nullable=False)
    observed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    effective_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    rounding_policy: Mapped[str] = mapped_column(String(120), nullable=False)
    provenance: Mapped[str] = mapped_column(String(255), nullable=False)


class AppliedFxSnapshot(Base, _TenantRow, _FxSnapshotColumns):
    __tablename__ = "applied_fx_snapshots"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "obligation_id"],
            [f"{SCHEMA}.rated_obligations.tenant_id", f"{SCHEMA}.rated_obligations.id"],
            name="fk_fx_snapshots_obligation",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "document_id"],
            [f"{SCHEMA}.documents.tenant_id", f"{SCHEMA}.documents.id"],
            name="fk_fx_snapshots_document",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "(obligation_id IS NULL) <> (document_id IS NULL)",
            name="ck_fx_snapshots_one_parent",
        ),
        UniqueConstraint("tenant_id", "id", name="uq_fx_snapshots_tenant_id_id"),
        schema_table_args(SCHEMA),
    )


class PlatformAppliedFxSnapshot(Base, _FxSnapshotColumns):
    __tablename__ = "platform_applied_fx_snapshots"
    __table_args__ = (
        ForeignKeyConstraint(
            ["obligation_id"],
            [f"{SCHEMA}.platform_rated_obligations.id"],
            name="fk_platform_fx_snapshots_obligation",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["document_id"],
            [f"{SCHEMA}.platform_documents.id"],
            name="fk_platform_fx_snapshots_document",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "(obligation_id IS NULL) <> (document_id IS NULL)",
            name="ck_platform_fx_snapshots_one_parent",
        ),
        schema_table_args(SCHEMA),
    )


class _PartyTaxColumns(_EvidenceTime):
    id: Mapped[UUID] = uuid_pk()
    document_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    party_role: Mapped[str] = mapped_column(String(16), nullable=False)
    identity_type: Mapped[str] = mapped_column(String(120), nullable=False)
    identity_value: Mapped[str] = mapped_column(String(255), nullable=False)
    country_code: Mapped[str] = mapped_column(String(3), nullable=False)
    source_authority: Mapped[str] = mapped_column(String(120), nullable=False)
    source_version: Mapped[str] = mapped_column(String(120), nullable=False)


class PartyTaxIdentitySnapshot(Base, _TenantRow, _PartyTaxColumns):
    __tablename__ = "party_tax_identity_snapshots"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "document_id"],
            [f"{SCHEMA}.documents.tenant_id", f"{SCHEMA}.documents.id"],
            name="fk_party_tax_document",
            ondelete="RESTRICT",
        ),
        UniqueConstraint("tenant_id", "id", name="uq_party_tax_tenant_id_id"),
        UniqueConstraint(
            "tenant_id",
            "document_id",
            "party_role",
            "identity_type",
            name="uq_party_tax_tenant_identity",
        ),
        schema_table_args(SCHEMA),
    )


class PlatformPartyTaxIdentitySnapshot(Base, _PartyTaxColumns):
    __tablename__ = "platform_party_tax_identity_snapshots"
    __table_args__ = (
        ForeignKeyConstraint(
            ["document_id"],
            [f"{SCHEMA}.platform_documents.id"],
            name="fk_platform_party_tax_document",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "document_id",
            "party_role",
            "identity_type",
            name="uq_platform_party_tax_identity",
        ),
        schema_table_args(SCHEMA),
    )


class _DocumentFactColumns(_EvidenceTime):
    id: Mapped[UUID] = uuid_pk()
    document_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    fact_version: Mapped[int] = mapped_column(Integer, nullable=False)
    contract_version: Mapped[str] = mapped_column(String(80), nullable=False)
    presentation_model_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    fact_payload: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)


class InvoiceDocumentFact(Base, _TenantRow, _DocumentFactColumns):
    __tablename__ = "invoice_document_facts"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "document_id"],
            [f"{SCHEMA}.documents.tenant_id", f"{SCHEMA}.documents.id"],
            name="fk_document_facts_document",
            ondelete="RESTRICT",
        ),
        UniqueConstraint("tenant_id", "id", name="uq_document_facts_tenant_id_id"),
        UniqueConstraint(
            "tenant_id",
            "id",
            "document_id",
            "fact_version",
            name="uq_document_facts_artifact_parent",
        ),
        UniqueConstraint(
            "tenant_id",
            "document_id",
            "fact_version",
            name="uq_document_facts_tenant_version",
        ),
        schema_table_args(SCHEMA),
    )


class PlatformInvoiceDocumentFact(Base, _DocumentFactColumns):
    __tablename__ = "platform_invoice_document_facts"
    __table_args__ = (
        ForeignKeyConstraint(
            ["document_id"],
            [f"{SCHEMA}.platform_documents.id"],
            name="fk_platform_document_facts_document",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "id",
            "document_id",
            "fact_version",
            name="uq_platform_document_facts_artifact_parent",
        ),
        UniqueConstraint(
            "document_id", "fact_version", name="uq_platform_document_facts_version"
        ),
        schema_table_args(SCHEMA),
    )


class _ArtifactColumns(_EvidenceTime):
    id: Mapped[UUID] = uuid_pk()
    document_fact_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    document_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    document_number: Mapped[str] = mapped_column(String(255), nullable=False)
    fact_version: Mapped[int] = mapped_column(Integer, nullable=False)
    media_type: Mapped[str] = mapped_column(String(120), nullable=False)
    file_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    checksum_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    byte_length: Mapped[int] = mapped_column(BigInteger, nullable=False)
    renderer_code: Mapped[str] = mapped_column(String(120), nullable=False)
    renderer_version: Mapped[str] = mapped_column(String(120), nullable=False)
    template_version: Mapped[str] = mapped_column(String(120), nullable=False)
    presentation_model_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    rendered_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    correlation_id: Mapped[str] = mapped_column(String(255), nullable=False)
    issued_by: Mapped[str] = mapped_column(String(40), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(512), nullable=False)
    request_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    superseded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    superseded_by_artifact_id: Mapped[UUID | None] = mapped_column(Uuid())
    supersession_reason: Mapped[str | None] = mapped_column(String(120))
    withdrawn_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    withdrawal_reason: Mapped[str | None] = mapped_column(String(120))


class DocumentArtifact(Base, _TenantRow, _ArtifactColumns):
    __tablename__ = "document_artifacts"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "document_fact_id", "document_id", "fact_version"],
            [
                f"{SCHEMA}.invoice_document_facts.tenant_id",
                f"{SCHEMA}.invoice_document_facts.id",
                f"{SCHEMA}.invoice_document_facts.document_id",
                f"{SCHEMA}.invoice_document_facts.fact_version",
            ],
            name="fk_artifacts_semantic_fact",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            [
                "tenant_id",
                "superseded_by_artifact_id",
                "document_fact_id",
                "media_type",
            ],
            [
                f"{SCHEMA}.document_artifacts.tenant_id",
                f"{SCHEMA}.document_artifacts.id",
                f"{SCHEMA}.document_artifacts.document_fact_id",
                f"{SCHEMA}.document_artifacts.media_type",
            ],
            name="fk_artifacts_replacement_identity",
            ondelete="RESTRICT",
            deferrable=True,
            initially="DEFERRED",
        ),
        UniqueConstraint("tenant_id", "id", name="uq_artifacts_tenant_id_id"),
        UniqueConstraint(
            "tenant_id",
            "id",
            "document_fact_id",
            "media_type",
            name="uq_artifacts_replacement_parent",
        ),
        UniqueConstraint(
            "tenant_id",
            "document_fact_id",
            "media_type",
            "file_id",
            name="uq_artifacts_tenant_file",
        ),
        Index(
            "uq_artifacts_tenant_current",
            "tenant_id",
            "document_fact_id",
            "media_type",
            unique=True,
            postgresql_where=text("superseded_at IS NULL"),
        ),
        schema_table_args(SCHEMA),
    )


class PlatformDocumentArtifact(Base, _ArtifactColumns):
    __tablename__ = "platform_document_artifacts"
    __table_args__ = (
        ForeignKeyConstraint(
            ["document_fact_id", "document_id", "fact_version"],
            [
                f"{SCHEMA}.platform_invoice_document_facts.id",
                f"{SCHEMA}.platform_invoice_document_facts.document_id",
                f"{SCHEMA}.platform_invoice_document_facts.fact_version",
            ],
            name="fk_platform_artifacts_semantic_fact",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["superseded_by_artifact_id", "document_fact_id", "media_type"],
            [
                f"{SCHEMA}.platform_document_artifacts.id",
                f"{SCHEMA}.platform_document_artifacts.document_fact_id",
                f"{SCHEMA}.platform_document_artifacts.media_type",
            ],
            name="fk_platform_artifacts_replacement_identity",
            ondelete="RESTRICT",
            deferrable=True,
            initially="DEFERRED",
        ),
        UniqueConstraint(
            "id",
            "document_fact_id",
            "media_type",
            name="uq_platform_artifacts_replacement_parent",
        ),
        UniqueConstraint(
            "document_fact_id",
            "media_type",
            "file_id",
            name="uq_platform_artifacts_file",
        ),
        Index(
            "uq_platform_artifacts_current",
            "document_fact_id",
            "media_type",
            unique=True,
            postgresql_where=text("superseded_at IS NULL"),
        ),
        schema_table_args(SCHEMA),
    )


class _AccountingFactColumns(_EvidenceTime):
    id: Mapped[UUID] = uuid_pk()
    posting_group_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    fact_version: Mapped[int] = mapped_column(Integer, nullable=False)
    source_system: Mapped[str] = mapped_column(String(120), nullable=False)
    source_authority: Mapped[str] = mapped_column(String(40), nullable=False)
    effect_kind: Mapped[str] = mapped_column(String(40), nullable=False)
    fact_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    fact_payload: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    reverses_fact_id: Mapped[UUID | None] = mapped_column(Uuid())
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


class AccountingFact(Base, _TenantRow, _AccountingFactColumns):
    __tablename__ = "accounting_facts"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "posting_group_id"],
            [f"{SCHEMA}.posting_groups.tenant_id", f"{SCHEMA}.posting_groups.id"],
            name="fk_accounting_facts_posting_group",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "reverses_fact_id"],
            [f"{SCHEMA}.accounting_facts.tenant_id", f"{SCHEMA}.accounting_facts.id"],
            name="fk_accounting_facts_reverses",
            ondelete="RESTRICT",
        ),
        UniqueConstraint("tenant_id", "id", name="uq_accounting_facts_tenant_id_id"),
        UniqueConstraint(
            "tenant_id",
            "posting_group_id",
            "fact_version",
            name="uq_accounting_facts_tenant_version",
        ),
        schema_table_args(SCHEMA),
    )


class PlatformAccountingFact(Base, _AccountingFactColumns):
    __tablename__ = "platform_accounting_facts"
    __table_args__ = (
        ForeignKeyConstraint(
            ["posting_group_id"],
            [f"{SCHEMA}.platform_posting_groups.id"],
            name="fk_platform_accounting_facts_group",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["reverses_fact_id"],
            [f"{SCHEMA}.platform_accounting_facts.id"],
            name="fk_platform_accounting_facts_reverses",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "posting_group_id",
            "fact_version",
            name="uq_platform_accounting_facts_version",
        ),
        schema_table_args(SCHEMA),
    )


class _PositionFactColumns(_EvidenceTime):
    id: Mapped[UUID] = uuid_pk()
    billing_account_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    source_owner: Mapped[str] = mapped_column(String(120), nullable=False)
    source_version: Mapped[int] = mapped_column(BigInteger, nullable=False)
    posting_group_watermark: Mapped[UUID | None] = mapped_column(Uuid())
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    minor_units: Mapped[int] = mapped_column(Integer, nullable=False)
    collectible_receivable: Mapped[Decimal] = money_column()
    available_credit: Mapped[Decimal] = money_column()
    prepaid_funding: Mapped[Decimal] = money_column()
    state_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    rebuild_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    source_authority: Mapped[str] = mapped_column(String(40), nullable=False)
    projection_mode: Mapped[str] = mapped_column(String(24), nullable=False)
    derived_from: Mapped[str] = mapped_column(String(32), nullable=False)
    completeness: Mapped[str] = mapped_column(String(32), nullable=False)
    completeness_reason_code: Mapped[str | None] = mapped_column(String(120))
    financial_state: Mapped[str] = mapped_column(String(32), nullable=False)
    observed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


class ReceivablePositionFact(Base, _TenantRow, _PositionFactColumns):
    __tablename__ = "receivable_position_facts"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "billing_account_id"],
            [f"{SCHEMA}.billing_accounts.tenant_id", f"{SCHEMA}.billing_accounts.id"],
            name="fk_position_facts_account",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "posting_group_watermark", "billing_account_id"],
            [
                f"{SCHEMA}.posting_groups.tenant_id",
                f"{SCHEMA}.posting_groups.id",
                f"{SCHEMA}.posting_groups.billing_account_id",
            ],
            name="fk_position_facts_watermark_account",
            ondelete="RESTRICT",
        ),
        UniqueConstraint("tenant_id", "id", name="uq_position_facts_tenant_id_id"),
        UniqueConstraint(
            "tenant_id",
            "source_owner",
            "billing_account_id",
            "currency",
            "source_version",
            name="uq_position_tenant_identity_version",
        ),
        schema_table_args(SCHEMA),
    )


class PlatformReceivablePositionFact(Base, _PositionFactColumns):
    __tablename__ = "platform_receivable_position_facts"
    __table_args__ = (
        ForeignKeyConstraint(
            ["billing_account_id"],
            [f"{SCHEMA}.platform_billing_accounts.id"],
            name="fk_platform_position_facts_account",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["posting_group_watermark", "billing_account_id"],
            [
                f"{SCHEMA}.platform_posting_groups.id",
                f"{SCHEMA}.platform_posting_groups.billing_account_id",
            ],
            name="fk_platform_position_watermark_account",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "source_owner",
            "billing_account_id",
            "currency",
            "source_version",
            name="uq_platform_position_identity_version",
        ),
        schema_table_args(SCHEMA),
    )


class _ExposureFactColumns(_EvidenceTime):
    id: Mapped[UUID] = uuid_pk()
    billing_account_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    document_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    source_owner: Mapped[str] = mapped_column(String(120), nullable=False)
    exposure_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    subject_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    service_ref: Mapped[str | None] = mapped_column(String(255))
    collection_timing: Mapped[str] = mapped_column(String(16), nullable=False)
    source_version: Mapped[int] = mapped_column(BigInteger, nullable=False)
    posting_group_watermark: Mapped[UUID | None] = mapped_column(Uuid())
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    minor_units: Mapped[int] = mapped_column(Integer, nullable=False)
    collectible_receivable: Mapped[Decimal] = money_column()
    state_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    source_authority: Mapped[str] = mapped_column(String(40), nullable=False)
    projection_mode: Mapped[str] = mapped_column(String(24), nullable=False)
    derived_from: Mapped[str] = mapped_column(String(32), nullable=False)
    completeness: Mapped[str] = mapped_column(String(32), nullable=False)
    completeness_reason_code: Mapped[str | None] = mapped_column(String(120))
    financial_state: Mapped[str] = mapped_column(String(32), nullable=False)
    observed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    service_period: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    due_date_basis: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)


class ReceivableExposureFact(Base, _TenantRow, _ExposureFactColumns):
    __tablename__ = "receivable_exposure_facts"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "document_id", "billing_account_id"],
            [
                f"{SCHEMA}.documents.tenant_id",
                f"{SCHEMA}.documents.id",
                f"{SCHEMA}.documents.billing_account_id",
            ],
            name="fk_exposure_facts_document_account",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "posting_group_watermark", "billing_account_id"],
            [
                f"{SCHEMA}.posting_groups.tenant_id",
                f"{SCHEMA}.posting_groups.id",
                f"{SCHEMA}.posting_groups.billing_account_id",
            ],
            name="fk_exposure_facts_watermark_account",
            ondelete="RESTRICT",
        ),
        UniqueConstraint("tenant_id", "id", name="uq_exposure_facts_tenant_id_id"),
        UniqueConstraint(
            "tenant_id",
            "source_owner",
            "exposure_ref",
            "source_version",
            name="uq_exposure_tenant_identity_version",
        ),
        schema_table_args(SCHEMA),
    )


class PlatformReceivableExposureFact(Base, _ExposureFactColumns):
    __tablename__ = "platform_receivable_exposure_facts"
    __table_args__ = (
        ForeignKeyConstraint(
            ["document_id", "billing_account_id"],
            [
                f"{SCHEMA}.platform_documents.id",
                f"{SCHEMA}.platform_documents.billing_account_id",
            ],
            name="fk_platform_exposure_document_account",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["posting_group_watermark", "billing_account_id"],
            [
                f"{SCHEMA}.platform_posting_groups.id",
                f"{SCHEMA}.platform_posting_groups.billing_account_id",
            ],
            name="fk_platform_exposure_watermark_account",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "source_owner",
            "exposure_ref",
            "source_version",
            name="uq_platform_exposure_identity_version",
        ),
        schema_table_args(SCHEMA),
    )


TENANT_TABLES = (
    "billing_accounts",
    "rated_obligations",
    "documents",
    "document_lines",
    "document_events",
    "confirmed_settlements",
    "posting_groups",
    "posting_effects",
    "allocation_effects",
    "applied_tax_snapshots",
    "applied_fx_snapshots",
    "party_tax_identity_snapshots",
    "invoice_document_facts",
    "document_artifacts",
    "accounting_facts",
    "receivable_position_facts",
    "receivable_exposure_facts",
)

PLATFORM_TABLES = (
    "platform_billing_accounts",
    "platform_rated_obligations",
    "platform_documents",
    "platform_document_lines",
    "platform_document_events",
    "platform_confirmed_settlements",
    "platform_posting_groups",
    "platform_posting_effects",
    "platform_allocation_effects",
    "platform_applied_tax_snapshots",
    "platform_applied_fx_snapshots",
    "platform_party_tax_identity_snapshots",
    "platform_invoice_document_facts",
    "platform_document_artifacts",
    "platform_accounting_facts",
    "platform_receivable_position_facts",
    "platform_receivable_exposure_facts",
)

__all__ = [
    "PLATFORM_TABLES",
    "SCHEMA",
    "TENANT_TABLES",
    "AccountingFact",
    "AllocationEffect",
    "AppliedFxSnapshot",
    "AppliedTaxSnapshot",
    "Base",
    "BillingAccount",
    "BillingDocument",
    "ConfirmedSettlement",
    "DocumentArtifact",
    "DocumentEvent",
    "DocumentLine",
    "InvoiceDocumentFact",
    "PartyTaxIdentitySnapshot",
    "PlatformAccountingFact",
    "PlatformAllocationEffect",
    "PlatformAppliedFxSnapshot",
    "PlatformAppliedTaxSnapshot",
    "PlatformBillingAccount",
    "PlatformBillingDocument",
    "PlatformConfirmedSettlement",
    "PlatformDocumentArtifact",
    "PlatformDocumentEvent",
    "PlatformDocumentLine",
    "PlatformInvoiceDocumentFact",
    "PlatformPartyTaxIdentitySnapshot",
    "PlatformPostingEffect",
    "PlatformPostingGroup",
    "PlatformRatedObligation",
    "PlatformReceivablePositionFact",
    "PlatformReceivableExposureFact",
    "PostingEffect",
    "PostingGroup",
    "RatedObligation",
    "ReceivablePositionFact",
    "ReceivableExposureFact",
]
