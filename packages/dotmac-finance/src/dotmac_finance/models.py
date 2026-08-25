"""Tenant-scoped fixed-asset accounting books and immutable consequences."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from dotmac_kernel.models import Base, Tenant, TimestampMixin, uuid_pk
from dotmac_kernel.namespaces import module_schema, schema_table_args
from sqlalchemy import (
    JSON,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

SCHEMA = module_schema("finance")
MONEY = Numeric(20, 6)


class AssetBook(Base, TimestampMixin):
    __tablename__ = "asset_books"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_asset_books_tenant_id_id"),
        UniqueConstraint(
            "tenant_id", "asset_id", "book_code", name="uq_asset_books_asset_book"
        ),
        CheckConstraint(
            "status IN ('active','derecognized')", name="ck_asset_books_status"
        ),
        CheckConstraint(
            "accounting_model IN ('cost','revaluation')",
            name="ck_asset_books_accounting_model",
        ),
        CheckConstraint(
            "depreciation_method IN "
            "('straight_line','declining_balance','double_declining')",
            name="ck_asset_books_depreciation_method",
        ),
        CheckConstraint(
            "minor_units BETWEEN 0 AND 6", name="ck_asset_books_minor_units"
        ),
        CheckConstraint(
            "acquisition_cost >= 0 AND gross_carrying_amount >= 0 AND "
            "accumulated_depreciation >= 0 AND accumulated_impairment >= 0 AND "
            "carrying_amount >= 0 AND unimpaired_carrying_amount >= 0 AND "
            "residual_value >= 0",
            name="ck_asset_books_nonnegative_values",
        ),
        CheckConstraint(
            "useful_life_months > 0 AND depreciation_periods_taken >= 0 AND "
            "depreciation_periods_taken <= useful_life_months",
            name="ck_asset_books_life",
        ),
        CheckConstraint("version > 0", name="ck_asset_books_version"),
        Index("ix_asset_books_tenant_status", "tenant_id", "status"),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(), ForeignKey(Tenant.__table__.c.id, ondelete="CASCADE"), nullable=False
    )
    asset_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    book_code: Mapped[str] = mapped_column(String(40), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    accounting_model: Mapped[str] = mapped_column(String(24), nullable=False)
    depreciation_method: Mapped[str] = mapped_column(String(32), nullable=False)
    currency_code: Mapped[str] = mapped_column(String(3), nullable=False)
    minor_units: Mapped[int] = mapped_column(Integer, nullable=False)
    acquisition_cost: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    gross_carrying_amount: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    accumulated_depreciation: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    accumulated_impairment: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    carrying_amount: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    unimpaired_carrying_amount: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    residual_value: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    useful_life_months: Mapped[int] = mapped_column(Integer, nullable=False)
    depreciation_periods_taken: Mapped[int] = mapped_column(Integer, nullable=False)
    revaluation_reserve_balance: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    prior_revaluation_loss_balance: Mapped[Decimal] = mapped_column(
        MONEY, nullable=False
    )
    impairment_loss_balance: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    impairment_reserve_reduction_balance: Mapped[Decimal] = mapped_column(
        MONEY, nullable=False
    )
    available_for_use_on: Mapped[date] = mapped_column(Date, nullable=False)
    derecognized_on: Mapped[date | None] = mapped_column(Date)
    asset_account_ref: Mapped[str] = mapped_column(String(200), nullable=False)
    accumulated_depreciation_account_ref: Mapped[str] = mapped_column(
        String(200), nullable=False
    )
    accumulated_impairment_account_ref: Mapped[str] = mapped_column(
        String(200), nullable=False
    )
    depreciation_expense_account_ref: Mapped[str] = mapped_column(
        String(200), nullable=False
    )
    impairment_loss_account_ref: Mapped[str] = mapped_column(
        String(200), nullable=False
    )
    revaluation_reserve_account_ref: Mapped[str | None] = mapped_column(String(200))
    disposal_gain_loss_account_ref: Mapped[str] = mapped_column(
        String(200), nullable=False
    )
    cost_center_ref: Mapped[str | None] = mapped_column(String(200))
    source_ref: Mapped[str] = mapped_column(String(240), nullable=False)
    source_version: Mapped[str] = mapped_column(String(80), nullable=False)
    evidence_ref: Mapped[str] = mapped_column(String(240), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)


class DepreciationRun(Base):
    __tablename__ = "depreciation_runs"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_depreciation_runs_tenant_id_id"),
        UniqueConstraint("tenant_id", "run_ref", name="uq_depreciation_runs_ref"),
        CheckConstraint(
            "status IN ('calculated','posted')", name="ck_depreciation_runs_status"
        ),
        CheckConstraint("total_depreciation >= 0", name="ck_depreciation_runs_total"),
        CheckConstraint("assets_processed >= 0", name="ck_depreciation_runs_count"),
        CheckConstraint(
            "minor_units BETWEEN 0 AND 6", name="ck_depreciation_runs_minor_units"
        ),
        Index("ix_depreciation_runs_period", "tenant_id", "period_ref"),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(), ForeignKey(Tenant.__table__.c.id, ondelete="CASCADE"), nullable=False
    )
    run_ref: Mapped[str] = mapped_column(String(160), nullable=False)
    period_ref: Mapped[str] = mapped_column(String(160), nullable=False)
    through_date: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    assets_processed: Mapped[int] = mapped_column(Integer, nullable=False)
    total_depreciation: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    currency_code: Mapped[str] = mapped_column(String(3), nullable=False)
    minor_units: Mapped[int] = mapped_column(Integer, nullable=False)
    created_by_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    calculated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    posted_by_id: Mapped[UUID | None] = mapped_column(Uuid())
    posted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class DepreciationLine(Base):
    __tablename__ = "depreciation_lines"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_depreciation_lines_tenant_id_id"),
        UniqueConstraint(
            "tenant_id", "run_id", "book_id", name="uq_depreciation_lines_run_book"
        ),
        ForeignKeyConstraint(
            ["tenant_id", "run_id"],
            [f"{SCHEMA}.depreciation_runs.tenant_id", f"{SCHEMA}.depreciation_runs.id"],
            ondelete="CASCADE",
            name="fk_depreciation_lines_run",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "book_id"],
            [f"{SCHEMA}.asset_books.tenant_id", f"{SCHEMA}.asset_books.id"],
            ondelete="RESTRICT",
            name="fk_depreciation_lines_book",
        ),
        CheckConstraint(
            "periods > 0 AND book_version > 0", name="ck_depreciation_lines_periods"
        ),
        CheckConstraint(
            "depreciation_amount > 0 AND unimpaired_depreciation_amount >= 0",
            name="ck_depreciation_lines_amounts",
        ),
        CheckConstraint(
            "carrying_amount_opening >= carrying_amount_closing AND "
            "unimpaired_carrying_opening >= unimpaired_carrying_closing AND "
            "remaining_life_opening > remaining_life_closing AND "
            "remaining_life_closing >= 0",
            name="ck_depreciation_lines_closing",
        ),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(), ForeignKey(Tenant.__table__.c.id, ondelete="CASCADE"), nullable=False
    )
    run_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    book_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    book_version: Mapped[int] = mapped_column(Integer, nullable=False)
    periods: Mapped[int] = mapped_column(Integer, nullable=False)
    carrying_amount_opening: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    depreciation_amount: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    carrying_amount_closing: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    unimpaired_carrying_opening: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    unimpaired_depreciation_amount: Mapped[Decimal] = mapped_column(
        MONEY, nullable=False
    )
    unimpaired_carrying_closing: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    remaining_life_opening: Mapped[int] = mapped_column(Integer, nullable=False)
    remaining_life_closing: Mapped[int] = mapped_column(Integer, nullable=False)
    expense_account_ref: Mapped[str] = mapped_column(String(200), nullable=False)
    accumulated_depreciation_account_ref: Mapped[str] = mapped_column(
        String(200), nullable=False
    )
    cost_center_ref: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class AccountingEvent(Base):
    __tablename__ = "accounting_events"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_accounting_events_tenant_id_id"),
        UniqueConstraint(
            "tenant_id", "book_id", "sequence", name="uq_accounting_events_sequence"
        ),
        ForeignKeyConstraint(
            ["tenant_id", "book_id"],
            [f"{SCHEMA}.asset_books.tenant_id", f"{SCHEMA}.asset_books.id"],
            ondelete="RESTRICT",
            name="fk_accounting_events_book",
        ),
        CheckConstraint(
            "event_type IN ('capitalized','depreciated','impaired',"
            "'impairment_reversed','revalued','derecognized')",
            name="ck_accounting_events_type",
        ),
        CheckConstraint("sequence > 0", name="ck_accounting_events_sequence"),
        Index("ix_accounting_events_book_order", "tenant_id", "book_id", "sequence"),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(), ForeignKey(Tenant.__table__.c.id, ondelete="CASCADE"), nullable=False
    )
    book_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    event_type: Mapped[str] = mapped_column(String(32), nullable=False)
    effective_on: Mapped[date] = mapped_column(Date, nullable=False)
    source_ref: Mapped[str] = mapped_column(String(240), nullable=False)
    source_version: Mapped[str] = mapped_column(String(80), nullable=False)
    evidence_ref: Mapped[str] = mapped_column(String(240), nullable=False)
    approval_ref: Mapped[str | None] = mapped_column(String(240))
    actor_id: Mapped[UUID | None] = mapped_column(Uuid())
    carrying_amount_before: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    carrying_amount_after: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    event_data: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


class AccountingConsequence(Base):
    __tablename__ = "accounting_consequences"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "id", name="uq_accounting_consequences_tenant_id_id"
        ),
        UniqueConstraint(
            "tenant_id",
            "source_type",
            "source_id",
            name="uq_accounting_consequences_source",
        ),
        CheckConstraint(
            "source_type IN ('depreciation_run','impairment','revaluation','disposal')",
            name="ck_accounting_consequences_source_type",
        ),
        CheckConstraint(
            "minor_units BETWEEN 0 AND 6", name="ck_accounting_consequences_minor_units"
        ),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(), ForeignKey(Tenant.__table__.c.id, ondelete="CASCADE"), nullable=False
    )
    source_type: Mapped[str] = mapped_column(String(32), nullable=False)
    source_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    effective_on: Mapped[date] = mapped_column(Date, nullable=False)
    currency_code: Mapped[str] = mapped_column(String(3), nullable=False)
    minor_units: Mapped[int] = mapped_column(Integer, nullable=False)
    description: Mapped[str] = mapped_column(String(240), nullable=False)
    evidence_ref: Mapped[str] = mapped_column(String(240), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


class AccountingConsequenceLine(Base):
    __tablename__ = "accounting_consequence_lines"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "id", name="uq_accounting_consequence_lines_tenant_id_id"
        ),
        UniqueConstraint(
            "tenant_id",
            "consequence_id",
            "line_number",
            name="uq_accounting_consequence_lines_number",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "consequence_id"],
            [
                f"{SCHEMA}.accounting_consequences.tenant_id",
                f"{SCHEMA}.accounting_consequences.id",
            ],
            ondelete="CASCADE",
            name="fk_accounting_consequence_lines_group",
        ),
        CheckConstraint(
            "side IN ('debit','credit')", name="ck_accounting_consequence_lines_side"
        ),
        CheckConstraint("amount > 0", name="ck_accounting_consequence_lines_amount"),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(), ForeignKey(Tenant.__table__.c.id, ondelete="CASCADE"), nullable=False
    )
    consequence_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    line_number: Mapped[int] = mapped_column(Integer, nullable=False)
    account_ref: Mapped[str] = mapped_column(String(200), nullable=False)
    side: Mapped[str] = mapped_column(String(8), nullable=False)
    amount: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    purpose: Mapped[str] = mapped_column(String(80), nullable=False)
    cost_center_ref: Mapped[str | None] = mapped_column(String(200))


TENANT_MODELS = (
    AssetBook,
    DepreciationRun,
    DepreciationLine,
    AccountingEvent,
    AccountingConsequence,
    AccountingConsequenceLine,
)
TENANT_TABLES = tuple(model.__tablename__ for model in TENANT_MODELS)

__all__ = [
    "SCHEMA",
    "TENANT_MODELS",
    "TENANT_TABLES",
    "AccountingConsequence",
    "AccountingConsequenceLine",
    "AccountingEvent",
    "AssetBook",
    "DepreciationLine",
    "DepreciationRun",
]
