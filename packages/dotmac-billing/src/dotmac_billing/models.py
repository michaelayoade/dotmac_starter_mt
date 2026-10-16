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
    authoring_mode: Mapped[str] = mapped_column(
        String(24), nullable=False, server_default=text("'legacy_single'")
    )
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
        CheckConstraint(
            "authoring_mode IN ('legacy_single', 'explicit_multiline', "
            "'imported_open_item')",
            name="ck_documents_authoring_mode",
        ),
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
        CheckConstraint(
            "authoring_mode IN ('legacy_single', 'explicit_multiline', "
            "'imported_open_item')",
            name="ck_platform_documents_authoring_mode",
        ),
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
    billing_account_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
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
            ["tenant_id", "document_id", "billing_account_id"],
            [
                f"{SCHEMA}.documents.tenant_id",
                f"{SCHEMA}.documents.id",
                f"{SCHEMA}.documents.billing_account_id",
            ],
            name="fk_document_lines_document_account",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "obligation_id", "billing_account_id"],
            [
                f"{SCHEMA}.rated_obligations.tenant_id",
                f"{SCHEMA}.rated_obligations.id",
                f"{SCHEMA}.rated_obligations.billing_account_id",
            ],
            name="fk_document_lines_obligation_account",
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
            ["document_id", "billing_account_id"],
            [
                f"{SCHEMA}.platform_documents.id",
                f"{SCHEMA}.platform_documents.billing_account_id",
            ],
            name="fk_platform_document_lines_document_account",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["obligation_id", "billing_account_id"],
            [
                f"{SCHEMA}.platform_rated_obligations.id",
                f"{SCHEMA}.platform_rated_obligations.billing_account_id",
            ],
            name="fk_platform_document_lines_obligation_account",
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
        UniqueConstraint(
            "tenant_id",
            "id",
            "billing_account_id",
            "source_system",
            "source_settlement_key",
            "currency",
            "minor_units",
            name="uq_settlements_claim_binding",
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
        UniqueConstraint(
            "id",
            "billing_account_id",
            "source_system",
            "source_settlement_key",
            "currency",
            "minor_units",
            name="uq_platform_settlements_claim_binding",
        ),
        schema_table_args(SCHEMA),
    )


class _CashSourceClaimColumns:
    id: Mapped[UUID] = uuid_pk()
    settlement_id: Mapped[UUID | None] = mapped_column(Uuid())
    opening_cash_lot_id: Mapped[UUID | None] = mapped_column(Uuid())
    billing_account_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    source_system: Mapped[str] = mapped_column(String(120), nullable=False)
    source_settlement_key: Mapped[str] = mapped_column(String(255), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    minor_units: Mapped[int] = mapped_column(Integer, nullable=False)
    # Recording time, never a replacement for settlement confirmation time.
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class CashSourceClaim(Base, _TenantRow, _CashSourceClaimColumns):
    __tablename__ = "cash_source_claims"
    __table_args__ = (
        UniqueConstraint("tenant_id", "settlement_id", name="uq_cash_claim_settlement"),
        UniqueConstraint(
            "tenant_id",
            "source_system",
            "source_settlement_key",
            name="uq_cash_claim_source",
        ),
        ForeignKeyConstraint(
            [
                "tenant_id",
                "settlement_id",
                "billing_account_id",
                "source_system",
                "source_settlement_key",
                "currency",
                "minor_units",
            ],
            [
                f"{SCHEMA}.confirmed_settlements.{column}"
                for column in (
                    "tenant_id",
                    "id",
                    "billing_account_id",
                    "source_system",
                    "source_settlement_key",
                    "currency",
                    "minor_units",
                )
            ],
            name="fk_cash_claim_settlement",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "(settlement_id IS NULL) <> (opening_cash_lot_id IS NULL)",
            name="ck_cash_claim_owner",
        ),
        UniqueConstraint(
            "tenant_id", "opening_cash_lot_id", name="uq_cash_claim_opening_lot"
        ),
        ForeignKeyConstraint(
            [
                "tenant_id",
                "opening_cash_lot_id",
                "billing_account_id",
                "source_system",
                "source_settlement_key",
                "currency",
                "minor_units",
            ],
            [
                f"{SCHEMA}.opening_cash_lots.tenant_id",
                f"{SCHEMA}.opening_cash_lots.id",
                f"{SCHEMA}.opening_cash_lots.billing_account_id",
                f"{SCHEMA}.opening_cash_lots.source_system",
                f"{SCHEMA}.opening_cash_lots.source_settlement_key",
                f"{SCHEMA}.opening_cash_lots.currency",
                f"{SCHEMA}.opening_cash_lots.minor_units",
            ],
            name="fk_cash_claim_opening_lot",
            ondelete="RESTRICT",
        ),
        schema_table_args(SCHEMA),
    )


class PlatformCashSourceClaim(Base, _CashSourceClaimColumns):
    __tablename__ = "platform_cash_source_claims"
    __table_args__ = (
        UniqueConstraint("settlement_id", name="uq_platform_cash_claim_settlement"),
        UniqueConstraint(
            "source_system",
            "source_settlement_key",
            name="uq_platform_cash_claim_source",
        ),
        ForeignKeyConstraint(
            [
                "settlement_id",
                "billing_account_id",
                "source_system",
                "source_settlement_key",
                "currency",
                "minor_units",
            ],
            [
                f"{SCHEMA}.platform_confirmed_settlements.{column}"
                for column in (
                    "id",
                    "billing_account_id",
                    "source_system",
                    "source_settlement_key",
                    "currency",
                    "minor_units",
                )
            ],
            name="fk_platform_cash_claim_settlement",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "(settlement_id IS NULL) <> (opening_cash_lot_id IS NULL)",
            name="ck_platform_cash_claim_owner",
        ),
        UniqueConstraint(
            "opening_cash_lot_id", name="uq_platform_cash_claim_opening_lot"
        ),
        ForeignKeyConstraint(
            [
                "opening_cash_lot_id",
                "billing_account_id",
                "source_system",
                "source_settlement_key",
                "currency",
                "minor_units",
            ],
            [
                f"{SCHEMA}.platform_opening_cash_lots.id",
                f"{SCHEMA}.platform_opening_cash_lots.billing_account_id",
                f"{SCHEMA}.platform_opening_cash_lots.source_system",
                f"{SCHEMA}.platform_opening_cash_lots.source_settlement_key",
                f"{SCHEMA}.platform_opening_cash_lots.currency",
                f"{SCHEMA}.platform_opening_cash_lots.minor_units",
            ],
            name="fk_platform_cash_claim_opening_lot",
            ondelete="RESTRICT",
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


class _WithholdingDischargeColumns(_EvidenceTime):
    id: Mapped[UUID] = uuid_pk()
    billing_account_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    document_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    posting_group_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    source_owner: Mapped[str] = mapped_column(String(120), nullable=False)
    source_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    tax_source_owner: Mapped[str] = mapped_column(String(120), nullable=False)
    determination_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    component_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    claim_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    claimed_amount: Mapped[Decimal] = money_column()
    amount_delta: Mapped[Decimal] = money_column()
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    minor_units: Mapped[int] = mapped_column(Integer, nullable=False)
    evidence: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    settlement_id: Mapped[UUID | None] = mapped_column(Uuid())
    allocation_id: Mapped[UUID | None] = mapped_column(Uuid())
    reverses_discharge_id: Mapped[UUID | None] = mapped_column(Uuid())


def _withholding_constraints(*, tenant: bool) -> tuple[object, ...]:
    prefix = "" if tenant else "platform_"
    table = f"{prefix}withholding_discharges"
    scope = ["tenant_id"] if tenant else []
    constraints: list[object] = []
    for column, target in (
        ("posting_group_id", "posting_groups"),
        ("document_id", "documents"),
        ("settlement_id", "confirmed_settlements"),
        ("allocation_id", "allocation_effects"),
    ):
        target_table = f"{SCHEMA}.{prefix}{target}"
        constraints.append(
            ForeignKeyConstraint(
                [*scope, column, "billing_account_id"],
                [
                    *(f"{target_table}.{c}" for c in scope),
                    f"{target_table}.id",
                    f"{target_table}.billing_account_id",
                ],
                name=f"fk_{table}_{column}",
                ondelete="RESTRICT",
            )
        )
    constraints.extend(
        (
            UniqueConstraint(*scope, "id", name=f"uq_{table}_id"),
            UniqueConstraint(
                *scope, "id", "billing_account_id", name=f"uq_{table}_account"
            ),
            UniqueConstraint(
                *scope,
                "id",
                "billing_account_id",
                "document_id",
                name=f"uq_{table}_document",
            ),
            UniqueConstraint(*scope, "posting_group_id", name=f"uq_{table}_group"),
            UniqueConstraint(
                *scope, "source_owner", "source_ref", name=f"uq_{table}_source"
            ),
            UniqueConstraint(
                *scope, "reverses_discharge_id", name=f"uq_{table}_reversal"
            ),
            ForeignKeyConstraint(
                [*scope, "reverses_discharge_id", "billing_account_id", "document_id"],
                [
                    *(f"{SCHEMA}.{table}.{c}" for c in scope),
                    f"{SCHEMA}.{table}.id",
                    f"{SCHEMA}.{table}.billing_account_id",
                    f"{SCHEMA}.{table}.document_id",
                ],
                name=f"fk_{table}_correction",
                ondelete="RESTRICT",
            ),
            CheckConstraint(
                "claimed_amount > 0 AND amount_delta <> 0 "
                "AND abs(amount_delta) <= claimed_amount",
                name=f"ck_{table}_amount",
            ),
            CheckConstraint(
                "(reverses_discharge_id IS NULL AND amount_delta > 0) "
                "OR (reverses_discharge_id IS NOT NULL AND amount_delta < 0)",
                name=f"ck_{table}_direction",
            ),
            CheckConstraint(
                "minor_units BETWEEN 0 AND 6", name=f"ck_{table}_precision"
            ),
            Index(
                f"uq_{prefix}withholding_tax_component",
                *scope,
                "tax_source_owner",
                "determination_ref",
                "component_ref",
                unique=True,
                postgresql_where=text("reverses_discharge_id IS NULL"),
                sqlite_where=text("reverses_discharge_id IS NULL"),
            ),
            schema_table_args(SCHEMA),
        )
    )
    return tuple(constraints)


class WithholdingDischarge(Base, _TenantRow, _WithholdingDischargeColumns):
    __tablename__ = "withholding_discharges"
    __table_args__ = _withholding_constraints(tenant=True)


class PlatformWithholdingDischarge(Base, _WithholdingDischargeColumns):
    __tablename__ = "platform_withholding_discharges"
    __table_args__ = _withholding_constraints(tenant=False)


class _NonCashCreditGrantColumns(_EvidenceTime):
    id: Mapped[UUID] = uuid_pk()
    billing_account_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    posting_group_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    restricted_document_id: Mapped[UUID | None] = mapped_column(Uuid())
    replaces_grant_id: Mapped[UUID | None] = mapped_column(Uuid())
    replaces_effect_id: Mapped[UUID | None] = mapped_column(Uuid())
    source_owner: Mapped[str] = mapped_column(String(120), nullable=False)
    source_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    source_version: Mapped[str] = mapped_column(String(255), nullable=False)
    original_amount: Mapped[Decimal] = money_column()
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    minor_units: Mapped[int] = mapped_column(Integer, nullable=False)
    category: Mapped[str] = mapped_column(String(120), nullable=False)
    config_owner: Mapped[str] = mapped_column(String(120), nullable=False)
    config_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    config_version: Mapped[str] = mapped_column(String(255), nullable=False)
    config_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    authorization_actor_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    authorization_evidence_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    request_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    evidence_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    evidence: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)


class _NonCashCreditEffectColumns(_EvidenceTime):
    id: Mapped[UUID] = uuid_pk()
    billing_account_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    grant_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    posting_group_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    document_id: Mapped[UUID | None] = mapped_column(Uuid())
    original_application_id: Mapped[UUID | None] = mapped_column(Uuid())
    effect_kind: Mapped[str] = mapped_column(String(40), nullable=False)
    availability_delta: Mapped[Decimal] = money_column()
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    minor_units: Mapped[int] = mapped_column(Integer, nullable=False)
    source_owner: Mapped[str] = mapped_column(String(120), nullable=False)
    source_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    source_version: Mapped[str] = mapped_column(String(255), nullable=False)
    authorization_actor_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    authorization_evidence_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    request_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    evidence_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    evidence: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)


def _non_cash_constraints(*, tenant: bool, effect: bool) -> tuple[object, ...]:
    prefix = "" if tenant else "platform_"
    table = f"{prefix}non_cash_credit_{'effects' if effect else 'grants'}"
    scope = ["tenant_id"] if tenant else []
    constraints: list[object] = [
        UniqueConstraint(*scope, "id", name=f"uq_{table}_id"),
        UniqueConstraint(
            *scope, "id", "billing_account_id", name=f"uq_{table}_account"
        ),
        UniqueConstraint(
            *scope, "source_owner", "source_ref", name=f"uq_{table}_source"
        ),
        CheckConstraint("minor_units BETWEEN 0 AND 6", name=f"ck_{table}_precision"),
    ]
    links = [("posting_group_id", "posting_groups")]
    if effect:
        links += [
            ("grant_id", "non_cash_credit_grants"),
            ("document_id", "documents"),
            ("original_application_id", "non_cash_credit_effects"),
        ]
        constraints += [
            CheckConstraint("availability_delta <> 0", name=f"ck_{table}_amount"),
            CheckConstraint(
                "(effect_kind = 'grant' AND availability_delta > 0 "
                "AND document_id IS NULL "
                "AND original_application_id IS NULL) OR "
                "(effect_kind = 'application' AND availability_delta < 0 "
                "AND document_id IS NOT NULL "
                "AND original_application_id IS NULL) OR "
                "(effect_kind = 'application_reversal' AND availability_delta > 0 "
                "AND document_id IS NOT NULL "
                "AND original_application_id IS NOT NULL) OR "
                "(effect_kind = 'unused_reduction' AND availability_delta < 0 "
                "AND document_id IS NULL "
                "AND original_application_id IS NULL)",
                name=f"ck_{table}_direction",
            ),
        ]
    else:
        links += [
            ("restricted_document_id", "documents"),
            ("replaces_grant_id", "non_cash_credit_grants"),
        ]
        constraints.append(
            CheckConstraint("original_amount > 0", name=f"ck_{table}_amount")
        )
        constraints.append(
            UniqueConstraint(*scope, "posting_group_id", name=f"uq_{table}_group")
        )
    if not effect:
        target = f"{SCHEMA}.{prefix}non_cash_credit_effects"
        constraints.extend(
            (
                ForeignKeyConstraint(
                    [*scope, "replaces_effect_id", "billing_account_id"],
                    [
                        *(f"{target}.{c}" for c in scope),
                        f"{target}.id",
                        f"{target}.billing_account_id",
                    ],
                    name=f"fk_{table}_replaces_effect_id",
                    ondelete="RESTRICT",
                    use_alter=True,
                ),
                CheckConstraint(
                    "(replaces_grant_id IS NULL) = (replaces_effect_id IS NULL)",
                    name=f"ck_{table}_replacement_pair",
                ),
            )
        )
    for column, target in links:
        target_table = f"{SCHEMA}.{prefix}{target}"
        constraints.append(
            ForeignKeyConstraint(
                [*scope, column, "billing_account_id"],
                [
                    *(f"{target_table}.{c}" for c in scope),
                    f"{target_table}.id",
                    f"{target_table}.billing_account_id",
                ],
                name=f"fk_{table}_{column}",
                ondelete="RESTRICT",
            )
        )
    constraints.append(schema_table_args(SCHEMA))
    return tuple(constraints)


class NonCashCreditGrant(Base, _TenantRow, _NonCashCreditGrantColumns):
    __tablename__ = "non_cash_credit_grants"
    __table_args__ = _non_cash_constraints(tenant=True, effect=False)


class PlatformNonCashCreditGrant(Base, _NonCashCreditGrantColumns):
    __tablename__ = "platform_non_cash_credit_grants"
    __table_args__ = _non_cash_constraints(tenant=False, effect=False)


class NonCashCreditEffect(Base, _TenantRow, _NonCashCreditEffectColumns):
    __tablename__ = "non_cash_credit_effects"
    __table_args__ = _non_cash_constraints(tenant=True, effect=True)


class PlatformNonCashCreditEffect(Base, _NonCashCreditEffectColumns):
    __tablename__ = "platform_non_cash_credit_effects"
    __table_args__ = _non_cash_constraints(tenant=False, effect=True)


class _PositionFactV2Columns(_PositionFactColumns):
    available_non_cash_credit: Mapped[Decimal] = money_column()


def _position_v2_constraints(*, tenant: bool) -> tuple[object, ...]:
    prefix = "" if tenant else "platform_"
    table = f"{prefix}receivable_position_facts_v2"
    scope = ["tenant_id"] if tenant else []
    target = f"{SCHEMA}.{prefix}posting_groups"
    return (
        UniqueConstraint(*scope, "id", name=f"uq_{table}_id"),
        UniqueConstraint(
            *scope,
            "source_owner",
            "billing_account_id",
            "currency",
            "source_version",
            name=f"uq_{table}_version",
        ),
        ForeignKeyConstraint(
            [*scope, "posting_group_watermark", "billing_account_id"],
            [
                *(f"{target}.{c}" for c in scope),
                f"{target}.id",
                f"{target}.billing_account_id",
            ],
            name=f"fk_{table}_watermark",
            ondelete="RESTRICT",
        ),
        CheckConstraint("available_non_cash_credit >= 0", name=f"ck_{table}_noncash"),
        schema_table_args(SCHEMA),
    )


class ReceivablePositionFactV2(Base, _TenantRow, _PositionFactV2Columns):
    __tablename__ = "receivable_position_facts_v2"
    __table_args__ = _position_v2_constraints(tenant=True)


class PlatformReceivablePositionFactV2(Base, _PositionFactV2Columns):
    __tablename__ = "platform_receivable_position_facts_v2"
    __table_args__ = _position_v2_constraints(tenant=False)


class _OpeningEvidenceColumns(_EvidenceTime):
    id: Mapped[UUID] = uuid_pk()
    billing_account_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    posting_group_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    minor_units: Mapped[int] = mapped_column(Integer, nullable=False)
    evidence_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    evidence: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


class _OpeningImportReceiptColumns(_OpeningEvidenceColumns):
    receipt_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    original_receipt_id: Mapped[UUID | None] = mapped_column(Uuid())
    manifest_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    request_identity: Mapped[str] = mapped_column(String(255), nullable=False)
    request_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    total_residual: Mapped[Decimal] = money_column()
    expected_target_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    expected_target_watermark: Mapped[str] = mapped_column(String(255), nullable=False)
    migration_authority_ref: Mapped[str] = mapped_column(String(255), nullable=False)


class _OpeningCashLotColumns(_OpeningEvidenceColumns):
    receipt_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    source_system: Mapped[str] = mapped_column(String(120), nullable=False)
    source_settlement_key: Mapped[str] = mapped_column(String(255), nullable=False)
    gross_amount: Mapped[Decimal] = money_column()
    historical_consumed: Mapped[Decimal] = money_column()
    residual_amount: Mapped[Decimal] = money_column()


class _OpeningCashEffectColumns(_OpeningEvidenceColumns):
    lot_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    effect_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    amount_delta: Mapped[Decimal] = money_column()
    document_id: Mapped[UUID | None] = mapped_column(Uuid())
    original_effect_id: Mapped[UUID | None] = mapped_column(Uuid())
    source_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    authorization_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    request_digest: Mapped[str] = mapped_column(String(64), nullable=False)


def _opening_constraints(*, tenant: bool, kind: str) -> tuple[object, ...]:
    prefix = "" if tenant else "platform_"
    scope = ["tenant_id"] if tenant else []
    table = f"{prefix}opening_{kind}"
    constraints: list[object] = [
        CheckConstraint("minor_units BETWEEN 0 AND 6", name=f"ck_{table}_precision"),
        UniqueConstraint(
            *scope, "id", "billing_account_id", name=f"uq_{table}_account"
        ),
        ForeignKeyConstraint(
            [*scope, "posting_group_id", "billing_account_id"],
            [
                f"{SCHEMA}.{prefix}posting_groups.{key}"
                for key in (*scope, "id", "billing_account_id")
            ],
            name=f"fk_{table}_group",
            ondelete="RESTRICT",
        ),
    ]
    links = []
    if kind == "import_receipts":
        constraints.extend(
            (
                UniqueConstraint(
                    *scope,
                    "receipt_kind",
                    "request_identity",
                    name=f"uq_{table}_request",
                ),
                UniqueConstraint(
                    *scope, "original_receipt_id", name=f"uq_{table}_inverse"
                ),
            )
        )
        links.append(("original_receipt_id", "import_receipts"))
    elif kind == "cash_lots":
        constraints.extend(
            (
                UniqueConstraint(
                    *scope, "receipt_id", "sequence", name=f"uq_{table}_sequence"
                ),
                UniqueConstraint(
                    *scope,
                    "id",
                    "billing_account_id",
                    "source_system",
                    "source_settlement_key",
                    "currency",
                    "minor_units",
                    name=f"uq_{prefix}opening_lot_claim_binding",
                ),
            )
        )
        links.append(("receipt_id", "import_receipts"))
    else:
        constraints.extend(
            (
                Index(
                    f"{prefix}opening_refund_one_correction",
                    *scope,
                    "original_effect_id",
                    unique=True,
                    postgresql_where=text("effect_kind = 'refund_correction'"),
                ),
                Index(
                    f"{prefix}opening_lot_one_recognition",
                    *scope,
                    "lot_id",
                    unique=True,
                    postgresql_where=text("effect_kind = 'recognition'"),
                ),
            )
        )
        constraints.extend(
            (
                UniqueConstraint(
                    *scope, "posting_group_id", "sequence", name=f"uq_{table}_sequence"
                ),
                UniqueConstraint(
                    *scope, "id", "lot_id", "billing_account_id", name=f"uq_{table}_lot"
                ),
                ForeignKeyConstraint(
                    [*scope, "original_effect_id", "lot_id", "billing_account_id"],
                    [
                        f"{SCHEMA}.{table}.{key}"
                        for key in (*scope, "id", "lot_id", "billing_account_id")
                    ],
                    name=f"fk_{table}_original",
                    ondelete="RESTRICT",
                ),
            )
        )
        links.append(("lot_id", "cash_lots"))
        constraints.append(
            ForeignKeyConstraint(
                [*scope, "document_id", "billing_account_id"],
                [
                    f"{SCHEMA}.{prefix}documents.{key}"
                    for key in (*scope, "id", "billing_account_id")
                ],
                name=f"fk_{table}_document",
                ondelete="RESTRICT",
            )
        )
    for column, target in links:
        constraints.append(
            ForeignKeyConstraint(
                [*scope, column, "billing_account_id"],
                [
                    f"{SCHEMA}.{prefix}opening_{target}.{key}"
                    for key in (*scope, "id", "billing_account_id")
                ],
                name=f"fk_{table}_{column}",
                ondelete="RESTRICT",
            )
        )
    return (*constraints, schema_table_args(SCHEMA))


class OpeningImportReceipt(Base, _TenantRow, _OpeningImportReceiptColumns):
    __tablename__ = "opening_import_receipts"
    __table_args__ = _opening_constraints(tenant=True, kind="import_receipts")


class PlatformOpeningImportReceipt(Base, _OpeningImportReceiptColumns):
    __tablename__ = "platform_opening_import_receipts"
    __table_args__ = _opening_constraints(tenant=False, kind="import_receipts")


class OpeningCashLot(Base, _TenantRow, _OpeningCashLotColumns):
    __tablename__ = "opening_cash_lots"
    __table_args__ = _opening_constraints(tenant=True, kind="cash_lots")


class PlatformOpeningCashLot(Base, _OpeningCashLotColumns):
    __tablename__ = "platform_opening_cash_lots"
    __table_args__ = _opening_constraints(tenant=False, kind="cash_lots")


class OpeningCashEffect(Base, _TenantRow, _OpeningCashEffectColumns):
    __tablename__ = "opening_cash_effects"
    __table_args__ = _opening_constraints(tenant=True, kind="cash_effects")


class PlatformOpeningCashEffect(Base, _OpeningCashEffectColumns):
    __tablename__ = "platform_opening_cash_effects"
    __table_args__ = _opening_constraints(tenant=False, kind="cash_effects")


class _OpeningReceivableBasisColumns(_EvidenceTime):
    id: Mapped[UUID] = uuid_pk()
    receipt_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    document_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    billing_account_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    posting_group_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    source_version: Mapped[int] = mapped_column(BigInteger, nullable=False)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    installation_id: Mapped[str] = mapped_column(String(255), nullable=False)
    source_system: Mapped[str] = mapped_column(String(120), nullable=False)
    source_invoice_key: Mapped[str] = mapped_column(String(255), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    minor_units: Mapped[int] = mapped_column(Integer, nullable=False)
    initial_receivable: Mapped[Decimal] = money_column()
    evidence: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    evidence_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


def _receivable_basis_constraints(tenant: bool) -> tuple[object, ...]:
    scope = ["tenant_id"] if tenant else []
    prefix = "" if tenant else "platform_"
    table = f"{prefix}opening_receivable_bases"
    return (
        UniqueConstraint(
            *scope, "id", "billing_account_id", name=f"uq_{table}_account"
        ),
        UniqueConstraint(*scope, "document_id", name=f"uq_{table}_document"),
        UniqueConstraint(*scope, "receipt_id", "sequence", name=f"uq_{table}_sequence"),
        UniqueConstraint(
            *scope,
            "installation_id",
            "source_system",
            "source_invoice_key",
            name=f"uq_{table}_source",
        ),
        CheckConstraint(
            "initial_receivable > 0 AND minor_units BETWEEN 0 AND 6",
            name=f"ck_{table}_amount",
        ),
        *(
            ForeignKeyConstraint(
                [*scope, column, "billing_account_id"],
                [
                    f"{SCHEMA}.{prefix}{parent}.{key}"
                    for key in (*scope, "id", "billing_account_id")
                ],
                name=f"fk_{table}_{column}",
                ondelete="RESTRICT",
            )
            for column, parent in (
                ("receipt_id", "opening_import_receipts"),
                ("document_id", "documents"),
                ("posting_group_id", "posting_groups"),
            )
        ),
        schema_table_args(SCHEMA),
    )


class OpeningReceivableBasis(Base, _TenantRow, _OpeningReceivableBasisColumns):
    __tablename__ = "opening_receivable_bases"
    __table_args__ = _receivable_basis_constraints(True)


class PlatformOpeningReceivableBasis(Base, _OpeningReceivableBasisColumns):
    __tablename__ = "platform_opening_receivable_bases"
    __table_args__ = _receivable_basis_constraints(False)


class _OpeningHistoricalClaimColumns(_EvidenceTime):
    id: Mapped[UUID] = uuid_pk()
    basis_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    billing_account_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    operation: Mapped[str] = mapped_column(String(120), nullable=False)
    source_owner: Mapped[str] = mapped_column(String(120), nullable=False)
    source_ref: Mapped[str] = mapped_column(String(512), nullable=False)
    evidence_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    determination_ref: Mapped[str | None] = mapped_column(String(255))
    component_ref: Mapped[str | None] = mapped_column(String(255))


def _historical_claim_constraints(tenant: bool) -> tuple[object, ...]:
    scope = ["tenant_id"] if tenant else []
    prefix = "" if tenant else "platform_"
    table = f"{prefix}opening_historical_discharge_claims"
    return (
        UniqueConstraint(
            *scope, "id", "billing_account_id", name=f"uq_{table}_account"
        ),
        UniqueConstraint(
            *scope,
            "operation",
            "source_owner",
            "determination_ref",
            "component_ref",
            name=f"uq_{table}_taxcomponent",
        ),
        CheckConstraint(
            "(operation = 'billing.withholding.tax_component' AND "
            "determination_ref IS NOT NULL AND component_ref IS NOT NULL) OR "
            "(operation <> 'billing.withholding.tax_component' AND "
            "determination_ref IS NULL AND component_ref IS NULL)",
            name=f"ck_{table}_taxcomponent",
        ),
        Index(
            f"uq_{table}_source",
            *scope,
            "operation",
            "source_owner",
            "source_ref",
            unique=True,
            postgresql_where=text("operation <> 'billing.withholding.tax_component'"),
            sqlite_where=text("operation <> 'billing.withholding.tax_component'"),
        ),
        ForeignKeyConstraint(
            [*scope, "basis_id", "billing_account_id"],
            [
                f"{SCHEMA}.{prefix}opening_receivable_bases.{key}"
                for key in (*scope, "id", "billing_account_id")
            ],
            name=f"fk_{table}_basis",
            ondelete="RESTRICT",
        ),
        schema_table_args(SCHEMA),
    )


class OpeningHistoricalDischargeClaim(Base, _TenantRow, _OpeningHistoricalClaimColumns):
    __tablename__ = "opening_historical_discharge_claims"
    __table_args__ = _historical_claim_constraints(True)


class PlatformOpeningHistoricalDischargeClaim(Base, _OpeningHistoricalClaimColumns):
    __tablename__ = "platform_opening_historical_discharge_claims"
    __table_args__ = _historical_claim_constraints(False)


class _ReceivableSourceClaimColumns(_EvidenceTime):
    id: Mapped[UUID] = uuid_pk()
    billing_account_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    namespace: Mapped[str] = mapped_column(String(80), nullable=False)
    source_owner: Mapped[str] = mapped_column(String(120), nullable=False)
    key1: Mapped[str] = mapped_column(String(512), nullable=False)
    key2: Mapped[str] = mapped_column(String(255), nullable=False)
    historical_claim_id: Mapped[UUID | None] = mapped_column(Uuid())
    withholding_discharge_id: Mapped[UUID | None] = mapped_column(Uuid())
    non_cash_grant_id: Mapped[UUID | None] = mapped_column(Uuid())
    non_cash_effect_id: Mapped[UUID | None] = mapped_column(Uuid())
    credit_document_fact_id: Mapped[UUID | None] = mapped_column(Uuid())


def _receivable_source_claim_constraints(tenant: bool) -> tuple[object, ...]:
    scope = ["tenant_id"] if tenant else []
    prefix = "" if tenant else "platform_"
    table = f"{prefix}receivable_source_claims"
    owners = (
        ("historical_claim_id", "opening_historical_discharge_claims"),
        ("withholding_discharge_id", "withholding_discharges"),
        ("non_cash_grant_id", "non_cash_credit_grants"),
        ("non_cash_effect_id", "non_cash_credit_effects"),
        ("credit_document_fact_id", "invoice_document_facts"),
    )
    return (
        UniqueConstraint(
            *scope,
            "namespace",
            "source_owner",
            "key1",
            "key2",
            name=f"uq_{table}_identity",
        ),
        CheckConstraint(
            " + ".join(
                f"(CASE WHEN {column} IS NULL THEN 0 ELSE 1 END)"
                for column, _ in owners
            )
            + " = 1",
            name=f"ck_{table}_owner",
        ),
        ForeignKeyConstraint(
            [*scope, "billing_account_id"],
            [f"{SCHEMA}.{prefix}billing_accounts.{key}" for key in (*scope, "id")],
            name=f"fk_{table}_account",
            ondelete="RESTRICT",
        ),
        *(
            ForeignKeyConstraint(
                [
                    *scope,
                    column,
                    *(
                        ()
                        if parent == "invoice_document_facts"
                        else ("billing_account_id",)
                    ),
                ],
                [
                    f"{SCHEMA}.{prefix}{parent}.{key}"
                    for key in (
                        *scope,
                        "id",
                        *(
                            ()
                            if parent == "invoice_document_facts"
                            else ("billing_account_id",)
                        ),
                    )
                ],
                name=f"fk_{table}_{column}",
                ondelete="RESTRICT",
            )
            for column, parent in owners
        ),
        schema_table_args(SCHEMA),
    )


class ReceivableSourceClaim(Base, _TenantRow, _ReceivableSourceClaimColumns):
    __tablename__ = "receivable_source_claims"
    __table_args__ = _receivable_source_claim_constraints(True)


class PlatformReceivableSourceClaim(Base, _ReceivableSourceClaimColumns):
    __tablename__ = "platform_receivable_source_claims"
    __table_args__ = _receivable_source_claim_constraints(False)


class _OpeningExposureColumns(_EvidenceTime):
    id: Mapped[UUID] = uuid_pk()
    basis_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    billing_account_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    document_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    posting_group_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    source_version: Mapped[int] = mapped_column(BigInteger, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    minor_units: Mapped[int] = mapped_column(Integer, nullable=False)
    collectible_receivable: Mapped[Decimal] = money_column()
    evidence: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    evidence_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


def _opening_exposure_constraints(tenant: bool) -> tuple[object, ...]:
    scope = ["tenant_id"] if tenant else []
    prefix = "" if tenant else "platform_"
    table = f"{prefix}receivable_exposure_facts_v2"
    return (
        UniqueConstraint(
            *scope, "basis_id", "source_version", name=f"uq_{table}_version"
        ),
        *(
            ForeignKeyConstraint(
                [*scope, column, "billing_account_id"],
                [
                    f"{SCHEMA}.{prefix}{parent}.{key}"
                    for key in (*scope, "id", "billing_account_id")
                ],
                name=f"fk_{table}_{column}",
                ondelete="RESTRICT",
            )
            for column, parent in (
                ("basis_id", "opening_receivable_bases"),
                ("document_id", "documents"),
                ("posting_group_id", "posting_groups"),
            )
        ),
        schema_table_args(SCHEMA),
    )


class ReceivableExposureFactV2(Base, _TenantRow, _OpeningExposureColumns):
    __tablename__ = "receivable_exposure_facts_v2"
    __table_args__ = _opening_exposure_constraints(True)


class PlatformReceivableExposureFactV2(Base, _OpeningExposureColumns):
    __tablename__ = "platform_receivable_exposure_facts_v2"
    __table_args__ = _opening_exposure_constraints(False)


class _WindowTenantRow:
    @declared_attr
    def tenant_id(cls) -> Mapped[UUID]:
        return mapped_column(
            Uuid(),
            ForeignKey(Tenant.__table__.c.id, ondelete="RESTRICT"),
            nullable=False,
        )


class _WindowColumns(_EvidenceTime):
    id: Mapped[UUID] = uuid_pk()
    epoch: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    manifest_version: Mapped[int] = mapped_column(Integer, nullable=False)
    manifest_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    principal_name: Mapped[str] = mapped_column(String(63), nullable=False)
    principal_oid: Mapped[int] = mapped_column(BigInteger, nullable=False)
    provision_authority_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    generation: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default="0"
    )
    evidence: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    evidence_digest: Mapped[str] = mapped_column(String(64), nullable=False)


class OpeningWindow(Base, _WindowTenantRow, _WindowColumns):
    __tablename__ = "opening_windows"
    __table_args__ = (
        UniqueConstraint("tenant_id", name="uq_opening_window_scope"),
        UniqueConstraint("tenant_id", "id", name="uq_opening_window_scoped_id"),
        schema_table_args(SCHEMA),
    )


class PlatformOpeningWindow(Base, _WindowColumns):
    __tablename__ = "platform_opening_windows"
    __table_args__ = (
        Index("uq_platform_opening_window_singleton", text("(true)"), unique=True),
        schema_table_args(SCHEMA),
    )


class _AdmissionColumns(_EvidenceTime):
    id: Mapped[UUID] = uuid_pk()
    window_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    receipt_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    billing_account_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    request_identity: Mapped[str] = mapped_column(String(255), nullable=False)
    request_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    manifest_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)


class OpeningWindowAdmission(Base, _WindowTenantRow, _AdmissionColumns):
    __tablename__ = "opening_window_admissions"
    __table_args__ = (
        UniqueConstraint("tenant_id", "receipt_id", name="uq_window_admission_receipt"),
        ForeignKeyConstraint(
            ["tenant_id", "window_id"],
            [f"{SCHEMA}.opening_windows.tenant_id", f"{SCHEMA}.opening_windows.id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "receipt_id", "billing_account_id"],
            [
                f"{SCHEMA}.opening_import_receipts.tenant_id",
                f"{SCHEMA}.opening_import_receipts.id",
                f"{SCHEMA}.opening_import_receipts.billing_account_id",
            ],
            ondelete="RESTRICT",
        ),
        schema_table_args(SCHEMA),
    )


class PlatformOpeningWindowAdmission(Base, _AdmissionColumns):
    __tablename__ = "platform_opening_window_admissions"
    __table_args__ = (
        UniqueConstraint("receipt_id", name="uq_platform_window_admission_receipt"),
        ForeignKeyConstraint(
            ["window_id"],
            [f"{SCHEMA}.platform_opening_windows.id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["receipt_id", "billing_account_id"],
            [
                f"{SCHEMA}.platform_opening_import_receipts.id",
                f"{SCHEMA}.platform_opening_import_receipts.billing_account_id",
            ],
            ondelete="RESTRICT",
        ),
        schema_table_args(SCHEMA),
    )


class _SealColumns(_EvidenceTime):
    id: Mapped[UUID] = uuid_pk()
    window_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    population_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    population_count: Mapped[int] = mapped_column(BigInteger, nullable=False)
    coupled_watermark: Mapped[str] = mapped_column(Text, nullable=False)
    command_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    evidence: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    evidence_digest: Mapped[str] = mapped_column(String(64), nullable=False)


class OpeningWindowSeal(Base, _WindowTenantRow, _SealColumns):
    __tablename__ = "opening_window_seals"
    __table_args__ = (
        UniqueConstraint("tenant_id", "window_id", name="uq_window_seal"),
        ForeignKeyConstraint(
            ["tenant_id", "window_id"],
            [f"{SCHEMA}.opening_windows.tenant_id", f"{SCHEMA}.opening_windows.id"],
            ondelete="RESTRICT",
        ),
        schema_table_args(SCHEMA),
    )


class PlatformOpeningWindowSeal(Base, _SealColumns):
    __tablename__ = "platform_opening_window_seals"
    __table_args__ = (
        UniqueConstraint("window_id", name="uq_platform_window_seal"),
        ForeignKeyConstraint(
            ["window_id"],
            [f"{SCHEMA}.platform_opening_windows.id"],
            ondelete="RESTRICT",
        ),
        schema_table_args(SCHEMA),
    )


TENANT_TABLES = (
    "opening_windows",
    "opening_window_admissions",
    "opening_window_seals",
    "receivable_source_claims",
    "opening_receivable_bases",
    "opening_historical_discharge_claims",
    "receivable_exposure_facts_v2",
    "opening_import_receipts",
    "opening_cash_lots",
    "opening_cash_effects",
    "cash_source_claims",
    "non_cash_credit_grants",
    "non_cash_credit_effects",
    "receivable_position_facts_v2",
    "withholding_discharges",
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
    "platform_opening_windows",
    "platform_opening_window_admissions",
    "platform_opening_window_seals",
    "platform_receivable_source_claims",
    "platform_opening_receivable_bases",
    "platform_opening_historical_discharge_claims",
    "platform_receivable_exposure_facts_v2",
    "platform_opening_import_receipts",
    "platform_opening_cash_lots",
    "platform_opening_cash_effects",
    "platform_cash_source_claims",
    "platform_non_cash_credit_grants",
    "platform_non_cash_credit_effects",
    "platform_receivable_position_facts_v2",
    "platform_withholding_discharges",
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
    "OpeningWindow",
    "PlatformOpeningWindow",
    "OpeningWindowAdmission",
    "PlatformOpeningWindowAdmission",
    "OpeningWindowSeal",
    "PlatformOpeningWindowSeal",
    "OpeningImportReceipt",
    "PlatformOpeningImportReceipt",
    "OpeningCashLot",
    "PlatformOpeningCashLot",
    "OpeningCashEffect",
    "PlatformOpeningCashEffect",
    "CashSourceClaim",
    "PlatformCashSourceClaim",
    "NonCashCreditGrant",
    "PlatformNonCashCreditGrant",
    "NonCashCreditEffect",
    "PlatformNonCashCreditEffect",
    "ReceivablePositionFactV2",
    "PlatformReceivablePositionFactV2",
    "WithholdingDischarge",
    "PlatformWithholdingDischarge",
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
