"""Tenant-scoped banking masters, observations and reconciliation evidence."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from dotmac_kernel.models import Base, Tenant, TimestampMixin, uuid_pk
from dotmac_kernel.namespaces import module_schema, schema_table_args
from sqlalchemy import (
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
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

SCHEMA = module_schema("banking")
MONEY = Numeric(20, 6)


def _tenant_identity(table: str) -> tuple[UniqueConstraint, dict[str, str]]:
    return (
        UniqueConstraint("tenant_id", "id", name=f"uq_{table}_tenant_id_id"),
        schema_table_args(SCHEMA),
    )


class BankInstitution(Base, TimestampMixin):
    __tablename__ = "bank_institutions"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_bank_institutions_tenant_id_id"),
        UniqueConstraint("tenant_id", "code", name="uq_bank_institutions_code"),
        CheckConstraint(
            "status IN ('active','retired')", name="ck_bank_institutions_status"
        ),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(), ForeignKey(Tenant.__table__.c.id, ondelete="CASCADE"), nullable=False
    )
    code: Mapped[str] = mapped_column(String(60), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    country_code: Mapped[str] = mapped_column(String(2), nullable=False)
    clearing_code: Mapped[str | None] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(20), nullable=False)


class BankAccount(Base, TimestampMixin):
    __tablename__ = "bank_accounts"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_bank_accounts_tenant_id_id"),
        UniqueConstraint(
            "tenant_id", "account_code", name="uq_bank_accounts_account_code"
        ),
        UniqueConstraint(
            "tenant_id",
            "institution_id",
            "account_identifier",
            name="uq_bank_accounts_identifier",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "institution_id"],
            [
                f"{SCHEMA}.bank_institutions.tenant_id",
                f"{SCHEMA}.bank_institutions.id",
            ],
            ondelete="RESTRICT",
            name="fk_bank_accounts_institution",
        ),
        CheckConstraint(
            "status IN ('active','suspended','closed')",
            name="ck_bank_accounts_status",
        ),
        CheckConstraint(
            "minor_units BETWEEN 0 AND 6", name="ck_bank_accounts_minor_units"
        ),
        Index("ix_bank_accounts_status", "tenant_id", "status"),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(), ForeignKey(Tenant.__table__.c.id, ondelete="CASCADE"), nullable=False
    )
    institution_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    account_code: Mapped[str] = mapped_column(String(60), nullable=False)
    account_name: Mapped[str] = mapped_column(String(200), nullable=False)
    account_identifier: Mapped[str] = mapped_column(String(100), nullable=False)
    account_type_code: Mapped[str] = mapped_column(String(60), nullable=False)
    currency_code: Mapped[str] = mapped_column(String(3), nullable=False)
    minor_units: Mapped[int] = mapped_column(Integer, nullable=False)
    cash_account_ref: Mapped[str] = mapped_column(String(240), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)


class BankStatement(Base):
    __tablename__ = "bank_statements"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_bank_statements_tenant_id_id"),
        UniqueConstraint(
            "tenant_id", "account_id", "statement_ref", name="uq_bank_statements_ref"
        ),
        UniqueConstraint(
            "tenant_id",
            "source_ref",
            "source_version",
            name="uq_bank_statements_source",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "account_id"],
            [f"{SCHEMA}.bank_accounts.tenant_id", f"{SCHEMA}.bank_accounts.id"],
            ondelete="RESTRICT",
            name="fk_bank_statements_account",
        ),
        CheckConstraint("period_end >= period_start", name="ck_bank_statements_period"),
        CheckConstraint(
            "status IN ('imported','reconciled','closed')",
            name="ck_bank_statements_status",
        ),
        CheckConstraint(
            "minor_units BETWEEN 0 AND 6", name="ck_bank_statements_minor_units"
        ),
        CheckConstraint(
            "total_credits >= 0 AND total_debits >= 0 AND total_lines >= 0 AND "
            "matched_lines >= 0 AND matched_lines <= total_lines",
            name="ck_bank_statements_totals",
        ),
        Index("ix_bank_statements_period", "tenant_id", "account_id", "period_end"),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(), ForeignKey(Tenant.__table__.c.id, ondelete="CASCADE"), nullable=False
    )
    account_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    statement_ref: Mapped[str] = mapped_column(String(160), nullable=False)
    period_start: Mapped[date] = mapped_column(Date, nullable=False)
    period_end: Mapped[date] = mapped_column(Date, nullable=False)
    opening_balance: Mapped[Decimal | None] = mapped_column(MONEY)
    closing_balance: Mapped[Decimal | None] = mapped_column(MONEY)
    total_credits: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    total_debits: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    currency_code: Mapped[str] = mapped_column(String(3), nullable=False)
    minor_units: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    total_lines: Mapped[int] = mapped_column(Integer, nullable=False)
    matched_lines: Mapped[int] = mapped_column(Integer, nullable=False)
    source_ref: Mapped[str] = mapped_column(String(240), nullable=False)
    source_version: Mapped[str] = mapped_column(String(100), nullable=False)
    evidence_ref: Mapped[str] = mapped_column(String(240), nullable=False)
    imported_by_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    imported_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    lines: Mapped[list[BankStatementLine]] = relationship(
        back_populates="statement",
        cascade="all, delete-orphan",
        order_by="BankStatementLine.line_number",
    )


class BankStatementLine(Base):
    __tablename__ = "bank_statement_lines"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "id", name="uq_bank_statement_lines_tenant_id_id"
        ),
        UniqueConstraint(
            "tenant_id", "statement_id", "line_number", name="uq_statement_line_no"
        ),
        ForeignKeyConstraint(
            ["tenant_id", "statement_id"],
            [f"{SCHEMA}.bank_statements.tenant_id", f"{SCHEMA}.bank_statements.id"],
            ondelete="CASCADE",
            name="fk_statement_lines_statement",
        ),
        CheckConstraint(
            "direction IN ('credit','debit')", name="ck_statement_lines_direction"
        ),
        CheckConstraint("amount > 0", name="ck_statement_lines_amount"),
        Index("ix_statement_lines_date", "tenant_id", "transaction_date"),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(), ForeignKey(Tenant.__table__.c.id, ondelete="CASCADE"), nullable=False
    )
    statement_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    line_number: Mapped[int] = mapped_column(Integer, nullable=False)
    transaction_date: Mapped[date] = mapped_column(Date, nullable=False)
    value_date: Mapped[date | None] = mapped_column(Date)
    direction: Mapped[str] = mapped_column(String(12), nullable=False)
    amount: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    external_ref: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(String(500), nullable=False)
    reference: Mapped[str | None] = mapped_column(String(200))
    counterparty: Mapped[str | None] = mapped_column(String(240))
    bank_transaction_code: Mapped[str | None] = mapped_column(String(80))
    is_matched: Mapped[bool] = mapped_column(Boolean, nullable=False)
    matched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    statement: Mapped[BankStatement] = relationship(back_populates="lines")


class CashAccountObservation(Base):
    __tablename__ = "cash_account_observations"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "id", name="uq_cash_account_observations_tenant_id_id"
        ),
        UniqueConstraint(
            "tenant_id",
            "source_ref",
            "source_version",
            name="uq_cash_observations_source",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "account_id"],
            [f"{SCHEMA}.bank_accounts.tenant_id", f"{SCHEMA}.bank_accounts.id"],
            ondelete="RESTRICT",
            name="fk_cash_observations_account",
        ),
        CheckConstraint(
            "direction IN ('credit','debit')", name="ck_cash_observations_direction"
        ),
        CheckConstraint("amount > 0", name="ck_cash_observations_amount"),
        CheckConstraint(
            "minor_units BETWEEN 0 AND 6", name="ck_cash_observations_minor_units"
        ),
        Index("ix_cash_observations_date", "tenant_id", "account_id", "effective_on"),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(), ForeignKey(Tenant.__table__.c.id, ondelete="CASCADE"), nullable=False
    )
    account_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    effective_on: Mapped[date] = mapped_column(Date, nullable=False)
    direction: Mapped[str] = mapped_column(String(12), nullable=False)
    amount: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    currency_code: Mapped[str] = mapped_column(String(3), nullable=False)
    minor_units: Mapped[int] = mapped_column(Integer, nullable=False)
    source_ref: Mapped[str] = mapped_column(String(240), nullable=False)
    source_version: Mapped[str] = mapped_column(String(100), nullable=False)
    evidence_ref: Mapped[str] = mapped_column(String(240), nullable=False)
    description: Mapped[str] = mapped_column(String(500), nullable=False)
    reference: Mapped[str | None] = mapped_column(String(200))
    counterparty_ref: Mapped[str | None] = mapped_column(String(240))
    observed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


class MatchPolicy(Base, TimestampMixin):
    __tablename__ = "match_policies"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_match_policies_tenant_id_id"),
        UniqueConstraint("tenant_id", "code", name="uq_match_policies_code"),
        CheckConstraint("amount_tolerance >= 0", name="ck_match_policy_tolerance"),
        CheckConstraint("date_window_days >= 0", name="ck_match_policy_window"),
        CheckConstraint(
            "reference_match_mode IN ('none','contains','exact')",
            name="ck_match_policy_reference_mode",
        ),
        CheckConstraint(
            "direction IS NULL OR direction IN ('credit','debit')",
            name="ck_match_policy_direction",
        ),
        CheckConstraint(
            "amount_weight >= 0 AND date_weight >= 0 AND reference_weight >= 0 "
            "AND amount_weight + date_weight + reference_weight = 100",
            name="ck_match_policy_weights",
        ),
        CheckConstraint(
            "minimum_confidence BETWEEN 0 AND 100",
            name="ck_match_policy_confidence",
        ),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(), ForeignKey(Tenant.__table__.c.id, ondelete="CASCADE"), nullable=False
    )
    code: Mapped[str] = mapped_column(String(80), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    amount_tolerance: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    date_window_days: Mapped[int] = mapped_column(Integer, nullable=False)
    reference_match_mode: Mapped[str] = mapped_column(String(20), nullable=False)
    amount_weight: Mapped[int] = mapped_column(Integer, nullable=False)
    date_weight: Mapped[int] = mapped_column(Integer, nullable=False)
    reference_weight: Mapped[int] = mapped_column(Integer, nullable=False)
    minimum_confidence: Mapped[int] = mapped_column(Integer, nullable=False)
    direction: Mapped[str | None] = mapped_column(String(12))
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False)


class MatchDecision(Base):
    __tablename__ = "match_decisions"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_match_decisions_tenant_id_id"),
        UniqueConstraint(
            "tenant_id", "statement_line_id", name="uq_match_decisions_line"
        ),
        ForeignKeyConstraint(
            ["tenant_id", "statement_line_id"],
            [
                f"{SCHEMA}.bank_statement_lines.tenant_id",
                f"{SCHEMA}.bank_statement_lines.id",
            ],
            ondelete="RESTRICT",
            name="fk_match_decisions_line",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "policy_id"],
            [f"{SCHEMA}.match_policies.tenant_id", f"{SCHEMA}.match_policies.id"],
            ondelete="RESTRICT",
            name="fk_match_decisions_policy",
        ),
        CheckConstraint(
            "status IN ('accepted','reversed')", name="ck_match_decisions_status"
        ),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(), ForeignKey(Tenant.__table__.c.id, ondelete="CASCADE"), nullable=False
    )
    statement_line_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    policy_id: Mapped[UUID | None] = mapped_column(Uuid())
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    decided_by_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    decided_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


class MatchAllocation(Base):
    __tablename__ = "match_allocations"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_match_allocations_tenant_id_id"),
        UniqueConstraint(
            "tenant_id",
            "decision_id",
            "observation_id",
            name="uq_match_allocations_pair",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "decision_id"],
            [f"{SCHEMA}.match_decisions.tenant_id", f"{SCHEMA}.match_decisions.id"],
            ondelete="CASCADE",
            name="fk_match_allocations_decision",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "observation_id"],
            [
                f"{SCHEMA}.cash_account_observations.tenant_id",
                f"{SCHEMA}.cash_account_observations.id",
            ],
            ondelete="RESTRICT",
            name="fk_match_allocations_observation",
        ),
        CheckConstraint("amount > 0", name="ck_match_allocations_amount"),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(), ForeignKey(Tenant.__table__.c.id, ondelete="CASCADE"), nullable=False
    )
    decision_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    observation_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    amount: Mapped[Decimal] = mapped_column(MONEY, nullable=False)


class Reconciliation(Base):
    __tablename__ = "reconciliations"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_reconciliations_tenant_id_id"),
        UniqueConstraint(
            "tenant_id", "statement_id", name="uq_reconciliations_statement"
        ),
        ForeignKeyConstraint(
            ["tenant_id", "statement_id"],
            [f"{SCHEMA}.bank_statements.tenant_id", f"{SCHEMA}.bank_statements.id"],
            ondelete="RESTRICT",
            name="fk_reconciliations_statement",
        ),
        CheckConstraint(
            "status IN ('prepared','approved','rejected')",
            name="ck_reconciliations_status",
        ),
        CheckConstraint(
            "total_lines >= 0 AND matched_lines >= 0 AND matched_lines <= total_lines",
            name="ck_reconciliations_counts",
        ),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(), ForeignKey(Tenant.__table__.c.id, ondelete="CASCADE"), nullable=False
    )
    statement_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    cash_opening_balance: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    cash_closing_balance: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    statement_closing_balance: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    difference: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    total_lines: Mapped[int] = mapped_column(Integer, nullable=False)
    matched_lines: Mapped[int] = mapped_column(Integer, nullable=False)
    snapshot_ref: Mapped[str] = mapped_column(String(240), nullable=False)
    prepared_by_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    prepared_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    approved_by_id: Mapped[UUID | None] = mapped_column(Uuid())
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


TENANT_MODELS = (
    BankInstitution,
    BankAccount,
    BankStatement,
    BankStatementLine,
    CashAccountObservation,
    MatchPolicy,
    MatchDecision,
    MatchAllocation,
    Reconciliation,
)
TENANT_TABLES = tuple(model.__tablename__ for model in TENANT_MODELS)

__all__ = [
    "BankAccount",
    "BankInstitution",
    "BankStatement",
    "BankStatementLine",
    "CashAccountObservation",
    "MatchAllocation",
    "MatchDecision",
    "MatchPolicy",
    "Reconciliation",
    "TENANT_MODELS",
    "TENANT_TABLES",
]
