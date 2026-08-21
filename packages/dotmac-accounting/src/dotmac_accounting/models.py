"""Tenant accounting persistence in the allocated ``mod_accounting`` schema."""

from __future__ import annotations

import enum
from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

import sqlalchemy as sa
from dotmac_kernel.models import Base, Tenant, TimestampMixin, uuid_pk
from dotmac_kernel.namespaces import module_schema, schema_table_args
from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    Date,
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
)
from sqlalchemy.orm import Mapped, mapped_column

from dotmac_accounting.contracts import (
    AccountClass,
    AccountKind,
    JournalKind,
    JournalStatus,
    NormalBalance,
    PeriodStatus,
)

SCHEMA = module_schema("accounting")
MONEY = Numeric(20, 6)
RATE = Numeric(20, 10)


def _enum(enum_type: type[enum.Enum], name: str) -> sa.Enum:
    return sa.Enum(
        enum_type,
        name=name,
        native_enum=False,
        values_callable=lambda cls: [member.value for member in cls],
        create_constraint=True,
    )


def _tenant_id() -> Mapped[UUID]:
    return mapped_column(
        Uuid(), ForeignKey(Tenant.__table__.c.id, ondelete="CASCADE"), nullable=False
    )


class AccountCategory(Base, TimestampMixin):
    __tablename__ = "account_categories"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_account_categories_tenant_id_id"),
        UniqueConstraint("tenant_id", "code", name="uq_account_categories_tenant_code"),
        ForeignKeyConstraint(
            ["tenant_id", "parent_id"],
            [
                f"{SCHEMA}.account_categories.tenant_id",
                f"{SCHEMA}.account_categories.id",
            ],
            ondelete="RESTRICT",
            name="fk_account_categories_tenant_parent",
        ),
        Index("ix_account_categories_tenant_parent", "tenant_id", "parent_id"),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = _tenant_id()
    code: Mapped[str] = mapped_column(String(30), nullable=False)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    account_class: Mapped[AccountClass] = mapped_column(
        _enum(AccountClass, "accounting_account_class"), nullable=False
    )
    description: Mapped[str | None] = mapped_column(Text)
    parent_id: Mapped[UUID | None] = mapped_column(Uuid())
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=sa.true()
    )


class Account(Base, TimestampMixin):
    __tablename__ = "accounts"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_accounts_tenant_id_id"),
        UniqueConstraint("tenant_id", "code", name="uq_accounts_tenant_code"),
        ForeignKeyConstraint(
            ["tenant_id", "category_id"],
            [
                f"{SCHEMA}.account_categories.tenant_id",
                f"{SCHEMA}.account_categories.id",
            ],
            ondelete="RESTRICT",
            name="fk_accounts_tenant_category",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "parent_id"],
            [f"{SCHEMA}.accounts.tenant_id", f"{SCHEMA}.accounts.id"],
            ondelete="RESTRICT",
            name="fk_accounts_tenant_parent",
        ),
        CheckConstraint(
            "currency_code IS NULL OR length(currency_code) = 3",
            name="ck_accounts_currency_length",
        ),
        Index("ix_accounts_tenant_category", "tenant_id", "category_id"),
        Index("ix_accounts_tenant_parent", "tenant_id", "parent_id"),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = _tenant_id()
    category_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    parent_id: Mapped[UUID | None] = mapped_column(Uuid())
    code: Mapped[str] = mapped_column(String(40), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    kind: Mapped[AccountKind] = mapped_column(
        _enum(AccountKind, "accounting_account_kind"), nullable=False
    )
    normal_balance: Mapped[NormalBalance] = mapped_column(
        _enum(NormalBalance, "accounting_normal_balance"), nullable=False
    )
    currency_code: Mapped[str | None] = mapped_column(String(3))
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=sa.true()
    )
    posting_allowed: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=sa.true()
    )


class FiscalYear(Base, TimestampMixin):
    __tablename__ = "fiscal_years"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_fiscal_years_tenant_id_id"),
        UniqueConstraint("tenant_id", "code", name="uq_fiscal_years_tenant_code"),
        CheckConstraint("start_date <= end_date", name="ck_fiscal_years_date_order"),
        Index("ix_fiscal_years_tenant_dates", "tenant_id", "start_date", "end_date"),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = _tenant_id()
    code: Mapped[str] = mapped_column(String(20), nullable=False)
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date] = mapped_column(Date, nullable=False)


class FiscalPeriod(Base, TimestampMixin):
    __tablename__ = "fiscal_periods"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_fiscal_periods_tenant_id_id"),
        UniqueConstraint(
            "tenant_id",
            "fiscal_year_id",
            "period_number",
            name="uq_fiscal_periods_year_number",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "fiscal_year_id"],
            [f"{SCHEMA}.fiscal_years.tenant_id", f"{SCHEMA}.fiscal_years.id"],
            ondelete="RESTRICT",
            name="fk_fiscal_periods_tenant_year",
        ),
        CheckConstraint("start_date <= end_date", name="ck_fiscal_periods_date_order"),
        CheckConstraint("period_number > 0", name="ck_fiscal_periods_number_positive"),
        Index("ix_fiscal_periods_tenant_dates", "tenant_id", "start_date", "end_date"),
        Index("ix_fiscal_periods_tenant_status", "tenant_id", "status"),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = _tenant_id()
    fiscal_year_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    period_number: Mapped[int] = mapped_column(Integer, nullable=False)
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date] = mapped_column(Date, nullable=False)
    is_adjustment: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=sa.false()
    )
    status: Mapped[PeriodStatus] = mapped_column(
        _enum(PeriodStatus, "accounting_period_status"),
        nullable=False,
        default=PeriodStatus.FUTURE,
        server_default=PeriodStatus.FUTURE.value,
    )
    reopen_token: Mapped[UUID | None] = mapped_column(Uuid())


class AccountingDimension(Base, TimestampMixin):
    __tablename__ = "accounting_dimensions"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "id", name="uq_accounting_dimensions_tenant_id_id"
        ),
        UniqueConstraint(
            "tenant_id", "code", name="uq_accounting_dimensions_tenant_code"
        ),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = _tenant_id()
    code: Mapped[str] = mapped_column(String(40), nullable=False)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=sa.true()
    )


class AccountingDimensionValue(Base, TimestampMixin):
    __tablename__ = "accounting_dimension_values"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "id", name="uq_accounting_dimension_values_tenant_id_id"
        ),
        UniqueConstraint(
            "tenant_id",
            "dimension_id",
            "code",
            name="uq_accounting_dimension_values_code",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "dimension_id"],
            [
                f"{SCHEMA}.accounting_dimensions.tenant_id",
                f"{SCHEMA}.accounting_dimensions.id",
            ],
            ondelete="RESTRICT",
            name="fk_accounting_dimension_values_dimension",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "parent_id"],
            [
                f"{SCHEMA}.accounting_dimension_values.tenant_id",
                f"{SCHEMA}.accounting_dimension_values.id",
            ],
            ondelete="RESTRICT",
            name="fk_accounting_dimension_values_parent",
        ),
        Index("ix_accounting_dimension_values_dimension", "tenant_id", "dimension_id"),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = _tenant_id()
    dimension_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    parent_id: Mapped[UUID | None] = mapped_column(Uuid())
    code: Mapped[str] = mapped_column(String(80), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=sa.true()
    )


class JournalEntry(Base, TimestampMixin):
    __tablename__ = "journal_entries"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_journal_entries_tenant_id_id"),
        UniqueConstraint(
            "tenant_id", "number", name="uq_journal_entries_tenant_number"
        ),
        UniqueConstraint(
            "tenant_id",
            "source_owner",
            "source_document_kind",
            "source_document_id",
            "source_version",
            name="uq_journal_entries_source_identity",
        ),
        UniqueConstraint(
            "tenant_id",
            "reverses_journal_id",
            name="uq_journal_entries_single_reversal",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "fiscal_period_id"],
            [f"{SCHEMA}.fiscal_periods.tenant_id", f"{SCHEMA}.fiscal_periods.id"],
            ondelete="RESTRICT",
            name="fk_journal_entries_tenant_period",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "reverses_journal_id"],
            [f"{SCHEMA}.journal_entries.tenant_id", f"{SCHEMA}.journal_entries.id"],
            ondelete="RESTRICT",
            name="fk_journal_entries_tenant_reverses",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "reversal_journal_id"],
            [f"{SCHEMA}.journal_entries.tenant_id", f"{SCHEMA}.journal_entries.id"],
            ondelete="RESTRICT",
            name="fk_journal_entries_tenant_reversal",
            use_alter=True,
        ),
        CheckConstraint(
            "length(currency_code) = 3", name="ck_journal_entries_currency"
        ),
        CheckConstraint("exchange_rate > 0", name="ck_journal_entries_rate_positive"),
        CheckConstraint(
            "total_debit >= 0 AND total_credit >= 0",
            name="ck_journal_entries_totals_nonnegative",
        ),
        CheckConstraint(
            "total_debit_functional >= 0 AND total_credit_functional >= 0",
            name="ck_journal_entries_functional_nonnegative",
        ),
        Index("ix_journal_entries_tenant_status", "tenant_id", "status"),
        Index("ix_journal_entries_tenant_period", "tenant_id", "fiscal_period_id"),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = _tenant_id()
    fiscal_period_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    number: Mapped[str] = mapped_column(String(50), nullable=False)
    kind: Mapped[JournalKind] = mapped_column(
        _enum(JournalKind, "accounting_journal_kind"), nullable=False
    )
    status: Mapped[JournalStatus] = mapped_column(
        _enum(JournalStatus, "accounting_journal_status"),
        nullable=False,
        default=JournalStatus.DRAFT,
        server_default=JournalStatus.DRAFT.value,
    )
    entry_date: Mapped[date] = mapped_column(Date, nullable=False)
    posting_date: Mapped[date] = mapped_column(Date, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    reference: Mapped[str | None] = mapped_column(String(255))
    currency_code: Mapped[str] = mapped_column(String(3), nullable=False)
    exchange_rate: Mapped[Decimal] = mapped_column(RATE, nullable=False)
    total_debit: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    total_credit: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    total_debit_functional: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    total_credit_functional: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    source_owner: Mapped[str] = mapped_column(String(120), nullable=False)
    source_document_kind: Mapped[str] = mapped_column(String(80), nullable=False)
    source_document_id: Mapped[str] = mapped_column(String(255), nullable=False)
    source_version: Mapped[str] = mapped_column(String(120), nullable=False)
    source_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    request_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    approval_reference: Mapped[str | None] = mapped_column(String(255))
    posted_by: Mapped[str | None] = mapped_column(String(255))
    posted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reverses_journal_id: Mapped[UUID | None] = mapped_column(Uuid())
    reversal_journal_id: Mapped[UUID | None] = mapped_column(Uuid())


class JournalLine(Base, TimestampMixin):
    __tablename__ = "journal_lines"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_journal_lines_tenant_id_id"),
        UniqueConstraint(
            "tenant_id",
            "journal_id",
            "line_number",
            name="uq_journal_lines_journal_number",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "journal_id"],
            [f"{SCHEMA}.journal_entries.tenant_id", f"{SCHEMA}.journal_entries.id"],
            ondelete="CASCADE",
            name="fk_journal_lines_tenant_journal",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "account_id"],
            [f"{SCHEMA}.accounts.tenant_id", f"{SCHEMA}.accounts.id"],
            ondelete="RESTRICT",
            name="fk_journal_lines_tenant_account",
        ),
        CheckConstraint(
            "(debit > 0 AND credit = 0) OR (credit > 0 AND debit = 0)",
            name="ck_journal_lines_one_side",
        ),
        CheckConstraint(
            "(debit_functional > 0 AND credit_functional = 0) OR "
            "(credit_functional > 0 AND debit_functional = 0)",
            name="ck_journal_lines_functional_one_side",
        ),
        Index("ix_journal_lines_tenant_journal", "tenant_id", "journal_id"),
        Index("ix_journal_lines_tenant_account", "tenant_id", "account_id"),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = _tenant_id()
    journal_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    line_number: Mapped[int] = mapped_column(Integer, nullable=False)
    account_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    debit: Mapped[Decimal] = mapped_column(MONEY, nullable=False, default=0)
    credit: Mapped[Decimal] = mapped_column(MONEY, nullable=False, default=0)
    debit_functional: Mapped[Decimal] = mapped_column(MONEY, nullable=False, default=0)
    credit_functional: Mapped[Decimal] = mapped_column(MONEY, nullable=False, default=0)


class JournalLineDimension(Base):
    __tablename__ = "journal_line_dimensions"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "id", name="uq_journal_line_dimensions_tenant_id_id"
        ),
        UniqueConstraint(
            "tenant_id",
            "journal_line_id",
            "dimension_id",
            name="uq_journal_line_dimensions_one_value",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "journal_line_id"],
            [f"{SCHEMA}.journal_lines.tenant_id", f"{SCHEMA}.journal_lines.id"],
            ondelete="CASCADE",
            name="fk_journal_line_dimensions_tenant_line",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "dimension_id"],
            [
                f"{SCHEMA}.accounting_dimensions.tenant_id",
                f"{SCHEMA}.accounting_dimensions.id",
            ],
            ondelete="RESTRICT",
            name="fk_journal_line_dimensions_tenant_dimension",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "dimension_value_id"],
            [
                f"{SCHEMA}.accounting_dimension_values.tenant_id",
                f"{SCHEMA}.accounting_dimension_values.id",
            ],
            ondelete="RESTRICT",
            name="fk_journal_line_dimensions_tenant_value",
        ),
        Index("ix_journal_line_dimensions_tenant_line", "tenant_id", "journal_line_id"),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = _tenant_id()
    journal_line_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    dimension_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    dimension_value_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class PostedLedgerLine(Base):
    __tablename__ = "posted_ledger_lines"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_posted_ledger_lines_tenant_id_id"),
        UniqueConstraint(
            "tenant_id", "journal_line_id", name="uq_posted_ledger_lines_journal_line"
        ),
        ForeignKeyConstraint(
            ["tenant_id", "journal_id"],
            [f"{SCHEMA}.journal_entries.tenant_id", f"{SCHEMA}.journal_entries.id"],
            ondelete="RESTRICT",
            name="fk_posted_ledger_lines_tenant_journal",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "journal_line_id"],
            [f"{SCHEMA}.journal_lines.tenant_id", f"{SCHEMA}.journal_lines.id"],
            ondelete="RESTRICT",
            name="fk_posted_ledger_lines_tenant_line",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "fiscal_period_id"],
            [f"{SCHEMA}.fiscal_periods.tenant_id", f"{SCHEMA}.fiscal_periods.id"],
            ondelete="RESTRICT",
            name="fk_posted_ledger_lines_tenant_period",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "account_id"],
            [f"{SCHEMA}.accounts.tenant_id", f"{SCHEMA}.accounts.id"],
            ondelete="RESTRICT",
            name="fk_posted_ledger_lines_tenant_account",
        ),
        CheckConstraint(
            "(debit > 0 AND credit = 0) OR (credit > 0 AND debit = 0)",
            name="ck_posted_ledger_lines_one_side",
        ),
        Index(
            "ix_posted_ledger_lines_tenant_account_period",
            "tenant_id",
            "account_id",
            "fiscal_period_id",
        ),
        Index(
            "ix_posted_ledger_lines_tenant_posting_date", "tenant_id", "posting_date"
        ),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = _tenant_id()
    journal_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    journal_line_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    fiscal_period_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    account_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    account_code: Mapped[str] = mapped_column(String(40), nullable=False)
    journal_number: Mapped[str] = mapped_column(String(50), nullable=False)
    entry_date: Mapped[date] = mapped_column(Date, nullable=False)
    posting_date: Mapped[date] = mapped_column(Date, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    currency_code: Mapped[str] = mapped_column(String(3), nullable=False)
    debit: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    credit: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    original_debit: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    original_credit: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    exchange_rate: Mapped[Decimal] = mapped_column(RATE, nullable=False)
    source_owner: Mapped[str] = mapped_column(String(120), nullable=False)
    source_document_kind: Mapped[str] = mapped_column(String(80), nullable=False)
    source_document_id: Mapped[str] = mapped_column(String(255), nullable=False)
    source_version: Mapped[str] = mapped_column(String(120), nullable=False)
    source_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    posted_by: Mapped[str] = mapped_column(String(255), nullable=False)
    posted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class PostedLedgerDimension(Base):
    __tablename__ = "posted_ledger_dimensions"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "id", name="uq_posted_ledger_dimensions_tenant_id_id"
        ),
        UniqueConstraint(
            "tenant_id",
            "ledger_line_id",
            "dimension_code",
            name="uq_posted_ledger_dimensions_code",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "ledger_line_id"],
            [
                f"{SCHEMA}.posted_ledger_lines.tenant_id",
                f"{SCHEMA}.posted_ledger_lines.id",
            ],
            ondelete="RESTRICT",
            name="fk_posted_ledger_dimensions_tenant_line",
        ),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = _tenant_id()
    ledger_line_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    dimension_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    dimension_code: Mapped[str] = mapped_column(String(40), nullable=False)
    dimension_value_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    value_code: Mapped[str] = mapped_column(String(80), nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


class PeriodEvent(Base):
    __tablename__ = "period_events"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_period_events_tenant_id_id"),
        ForeignKeyConstraint(
            ["tenant_id", "period_id"],
            [f"{SCHEMA}.fiscal_periods.tenant_id", f"{SCHEMA}.fiscal_periods.id"],
            ondelete="RESTRICT",
            name="fk_period_events_tenant_period",
        ),
        Index(
            "ix_period_events_tenant_period", "tenant_id", "period_id", "occurred_at"
        ),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = _tenant_id()
    period_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    event_kind: Mapped[str] = mapped_column(String(40), nullable=False)
    from_status: Mapped[str] = mapped_column(String(24), nullable=False)
    to_status: Mapped[str] = mapped_column(String(24), nullable=False)
    actor_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    approval_reference: Mapped[str | None] = mapped_column(String(255))
    evidence: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


ALL_MODELS = (
    AccountCategory,
    Account,
    FiscalYear,
    FiscalPeriod,
    AccountingDimension,
    AccountingDimensionValue,
    JournalEntry,
    JournalLine,
    JournalLineDimension,
    PostedLedgerLine,
    PostedLedgerDimension,
    PeriodEvent,
)
TABLES = tuple(model.__tablename__ for model in ALL_MODELS)


def metadata_table(name: str) -> sa.Table:
    return Base.metadata.tables[f"{SCHEMA}.{name}"]


__all__ = [
    "ALL_MODELS",
    "TABLES",
    "Account",
    "AccountCategory",
    "AccountingDimension",
    "AccountingDimensionValue",
    "FiscalPeriod",
    "FiscalYear",
    "JournalEntry",
    "JournalLine",
    "JournalLineDimension",
    "PeriodEvent",
    "PostedLedgerDimension",
    "PostedLedgerLine",
    "metadata_table",
]
