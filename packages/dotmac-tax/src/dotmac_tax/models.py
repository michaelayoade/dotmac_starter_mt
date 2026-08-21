"""Tenant-scoped tax policy data, determinations and filing evidence."""

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
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

SCHEMA = module_schema("tax")
MONEY = Numeric(20, 6)
RATE = Numeric(12, 8)


class TaxAuthority(Base, TimestampMixin):
    __tablename__ = "tax_authorities"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_tax_authorities_tenant_id_id"),
        UniqueConstraint("tenant_id", "code", name="uq_tax_authorities_code"),
        CheckConstraint(
            "status IN ('active','retired')", name="ck_tax_authorities_status"
        ),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(), ForeignKey(Tenant.__table__.c.id, ondelete="CASCADE"), nullable=False
    )
    code: Mapped[str] = mapped_column(String(80), nullable=False)
    name: Mapped[str] = mapped_column(String(240), nullable=False)
    authority_level_code: Mapped[str | None] = mapped_column(String(80))
    status: Mapped[str] = mapped_column(String(20), nullable=False)


class TaxJurisdiction(Base, TimestampMixin):
    __tablename__ = "tax_jurisdictions"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_tax_jurisdictions_tenant_id_id"),
        UniqueConstraint("tenant_id", "code", name="uq_tax_jurisdictions_code"),
        ForeignKeyConstraint(
            ["tenant_id", "authority_id"],
            [f"{SCHEMA}.tax_authorities.tenant_id", f"{SCHEMA}.tax_authorities.id"],
            ondelete="RESTRICT",
            name="fk_tax_jurisdictions_authority",
        ),
        CheckConstraint(
            "minor_units BETWEEN 0 AND 6", name="ck_tax_jurisdictions_minor_units"
        ),
        CheckConstraint(
            "status IN ('active','retired')", name="ck_tax_jurisdictions_status"
        ),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(), ForeignKey(Tenant.__table__.c.id, ondelete="CASCADE"), nullable=False
    )
    authority_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    code: Mapped[str] = mapped_column(String(80), nullable=False)
    name: Mapped[str] = mapped_column(String(240), nullable=False)
    country_code: Mapped[str] = mapped_column(String(2), nullable=False)
    subdivision_code: Mapped[str | None] = mapped_column(String(80))
    currency_code: Mapped[str] = mapped_column(String(3), nullable=False)
    minor_units: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)


class TaxCode(Base, TimestampMixin):
    __tablename__ = "tax_codes"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_tax_codes_tenant_id_id"),
        UniqueConstraint(
            "tenant_id", "jurisdiction_id", "code", name="uq_tax_codes_code"
        ),
        ForeignKeyConstraint(
            ["tenant_id", "jurisdiction_id"],
            [
                f"{SCHEMA}.tax_jurisdictions.tenant_id",
                f"{SCHEMA}.tax_jurisdictions.id",
            ],
            ondelete="RESTRICT",
            name="fk_tax_codes_jurisdiction",
        ),
        CheckConstraint("status IN ('active','retired')", name="ck_tax_codes_status"),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(), ForeignKey(Tenant.__table__.c.id, ondelete="CASCADE"), nullable=False
    )
    jurisdiction_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    code: Mapped[str] = mapped_column(String(80), nullable=False)
    name: Mapped[str] = mapped_column(String(240), nullable=False)
    tax_kind_code: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), nullable=False)


class TaxRule(Base):
    __tablename__ = "tax_rules"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_tax_rules_tenant_id_id"),
        UniqueConstraint(
            "tenant_id", "tax_code_id", "version", name="uq_tax_rules_version"
        ),
        ForeignKeyConstraint(
            ["tenant_id", "tax_code_id"],
            [f"{SCHEMA}.tax_codes.tenant_id", f"{SCHEMA}.tax_codes.id"],
            ondelete="RESTRICT",
            name="fk_tax_rules_code",
        ),
        CheckConstraint(
            "effective_to IS NULL OR effective_to >= effective_from",
            name="ck_tax_rules_effective_dates",
        ),
        CheckConstraint("version > 0", name="ck_tax_rules_version"),
        CheckConstraint(
            "transaction_side IN ('input','output','withholding','liability')",
            name="ck_tax_rules_side",
        ),
        CheckConstraint(
            "calculation_method IN ('percentage','fixed','progressive')",
            name="ck_tax_rules_method",
        ),
        CheckConstraint(
            "recoverable_rate BETWEEN 0 AND 1", name="ck_tax_rules_recovery"
        ),
        CheckConstraint("rate IS NULL OR rate >= 0", name="ck_tax_rules_rate"),
        CheckConstraint(
            "fixed_amount IS NULL OR fixed_amount >= 0",
            name="ck_tax_rules_fixed_amount",
        ),
        Index(
            "ix_tax_rules_selection",
            "tenant_id",
            "fact_kind",
            "recognition_basis_code",
            "effective_from",
        ),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(), ForeignKey(Tenant.__table__.c.id, ondelete="CASCADE"), nullable=False
    )
    tax_code_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    effective_from: Mapped[date] = mapped_column(Date, nullable=False)
    effective_to: Mapped[date | None] = mapped_column(Date)
    priority: Mapped[int] = mapped_column(Integer, nullable=False)
    fact_kind: Mapped[str] = mapped_column(String(100), nullable=False)
    recognition_basis_code: Mapped[str] = mapped_column(String(100), nullable=False)
    transaction_side: Mapped[str] = mapped_column(String(20), nullable=False)
    calculation_method: Mapped[str] = mapped_column(String(24), nullable=False)
    rate: Mapped[Decimal | None] = mapped_column(RATE)
    fixed_amount: Mapped[Decimal | None] = mapped_column(MONEY)
    inclusive: Mapped[bool] = mapped_column(Boolean, nullable=False)
    recoverable_rate: Mapped[Decimal] = mapped_column(RATE, nullable=False)
    party_category: Mapped[str | None] = mapped_column(String(100))
    supply_category: Mapped[str | None] = mapped_column(String(100))
    place_code: Mapped[str | None] = mapped_column(String(100))
    published_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )

    bands: Mapped[list[TaxRuleBand]] = relationship(
        back_populates="rule",
        cascade="all, delete-orphan",
        order_by="TaxRuleBand.sequence",
    )


class TaxRuleBand(Base):
    __tablename__ = "tax_rule_bands"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_tax_rule_bands_tenant_id_id"),
        UniqueConstraint(
            "tenant_id", "rule_id", "sequence", name="uq_tax_rule_bands_sequence"
        ),
        ForeignKeyConstraint(
            ["tenant_id", "rule_id"],
            [f"{SCHEMA}.tax_rules.tenant_id", f"{SCHEMA}.tax_rules.id"],
            ondelete="CASCADE",
            name="fk_tax_rule_bands_rule",
        ),
        CheckConstraint("sequence > 0", name="ck_tax_rule_bands_sequence"),
        CheckConstraint("lower_bound >= 0", name="ck_tax_rule_bands_lower"),
        CheckConstraint(
            "upper_bound IS NULL OR upper_bound > lower_bound",
            name="ck_tax_rule_bands_upper",
        ),
        CheckConstraint("rate BETWEEN 0 AND 1", name="ck_tax_rule_bands_rate"),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(), ForeignKey(Tenant.__table__.c.id, ondelete="CASCADE"), nullable=False
    )
    rule_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    lower_bound: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    upper_bound: Mapped[Decimal | None] = mapped_column(MONEY)
    rate: Mapped[Decimal] = mapped_column(RATE, nullable=False)

    rule: Mapped[TaxRule] = relationship(back_populates="bands")


class TaxDetermination(Base):
    __tablename__ = "tax_determinations"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_tax_determinations_tenant_id_id"),
        UniqueConstraint(
            "tenant_id",
            "source_ref",
            "source_version",
            name="uq_tax_determinations_source",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "jurisdiction_id"],
            [
                f"{SCHEMA}.tax_jurisdictions.tenant_id",
                f"{SCHEMA}.tax_jurisdictions.id",
            ],
            ondelete="RESTRICT",
            name="fk_tax_determinations_jurisdiction",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "tax_code_id"],
            [f"{SCHEMA}.tax_codes.tenant_id", f"{SCHEMA}.tax_codes.id"],
            ondelete="RESTRICT",
            name="fk_tax_determinations_code",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "rule_id"],
            [f"{SCHEMA}.tax_rules.tenant_id", f"{SCHEMA}.tax_rules.id"],
            ondelete="RESTRICT",
            name="fk_tax_determinations_rule",
        ),
        CheckConstraint(
            "base_amount >= 0 AND tax_amount >= 0", name="ck_tax_determinations_amounts"
        ),
        CheckConstraint(
            "recoverable_amount >= 0 AND non_recoverable_amount >= 0 AND "
            "recoverable_amount + non_recoverable_amount = tax_amount",
            name="ck_tax_determinations_recovery",
        ),
        CheckConstraint(
            "minor_units BETWEEN 0 AND 6", name="ck_tax_determinations_minor_units"
        ),
        Index(
            "ix_tax_determinations_period",
            "tenant_id",
            "jurisdiction_id",
            "occurred_on",
        ),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(), ForeignKey(Tenant.__table__.c.id, ondelete="CASCADE"), nullable=False
    )
    jurisdiction_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    tax_code_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    rule_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    rule_version: Mapped[int] = mapped_column(Integer, nullable=False)
    occurred_on: Mapped[date] = mapped_column(Date, nullable=False)
    fact_kind: Mapped[str] = mapped_column(String(100), nullable=False)
    recognition_basis_code: Mapped[str] = mapped_column(String(100), nullable=False)
    transaction_side: Mapped[str] = mapped_column(String(20), nullable=False)
    base_amount: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    tax_amount: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    recoverable_amount: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    non_recoverable_amount: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    currency_code: Mapped[str] = mapped_column(String(3), nullable=False)
    minor_units: Mapped[int] = mapped_column(Integer, nullable=False)
    source_ref: Mapped[str] = mapped_column(String(240), nullable=False)
    source_version: Mapped[str] = mapped_column(String(100), nullable=False)
    source_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    evidence_ref: Mapped[str] = mapped_column(String(240), nullable=False)
    counterparty_ref: Mapped[str | None] = mapped_column(String(240))
    determined_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )

    lines: Mapped[list[TaxDeterminationLine]] = relationship(
        back_populates="determination",
        cascade="all, delete-orphan",
        order_by="TaxDeterminationLine.sequence",
    )


class TaxDeterminationLine(Base):
    __tablename__ = "tax_determination_lines"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "id", name="uq_tax_determination_lines_tenant_id_id"
        ),
        UniqueConstraint(
            "tenant_id",
            "determination_id",
            "sequence",
            name="uq_tax_determination_lines_sequence",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "determination_id"],
            [
                f"{SCHEMA}.tax_determinations.tenant_id",
                f"{SCHEMA}.tax_determinations.id",
            ],
            ondelete="CASCADE",
            name="fk_tax_determination_lines_determination",
        ),
        CheckConstraint(
            "taxable_amount >= 0 AND tax_amount >= 0",
            name="ck_tax_determination_lines_amounts",
        ),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(), ForeignKey(Tenant.__table__.c.id, ondelete="CASCADE"), nullable=False
    )
    determination_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    taxable_amount: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    rate: Mapped[Decimal | None] = mapped_column(RATE)
    tax_amount: Mapped[Decimal] = mapped_column(MONEY, nullable=False)

    determination: Mapped[TaxDetermination] = relationship(back_populates="lines")


class StatutoryReportDefinition(Base, TimestampMixin):
    __tablename__ = "statutory_report_definitions"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "id", name="uq_statutory_report_definitions_tenant_id_id"
        ),
        UniqueConstraint(
            "tenant_id", "jurisdiction_id", "code", name="uq_statutory_report_defs_code"
        ),
        ForeignKeyConstraint(
            ["tenant_id", "jurisdiction_id"],
            [
                f"{SCHEMA}.tax_jurisdictions.tenant_id",
                f"{SCHEMA}.tax_jurisdictions.id",
            ],
            ondelete="RESTRICT",
            name="fk_statutory_report_defs_jurisdiction",
        ),
        CheckConstraint(
            "minor_units BETWEEN 0 AND 6", name="ck_statutory_report_defs_minor_units"
        ),
        CheckConstraint(
            "status IN ('active','retired')", name="ck_statutory_report_defs_status"
        ),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(), ForeignKey(Tenant.__table__.c.id, ondelete="CASCADE"), nullable=False
    )
    jurisdiction_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    code: Mapped[str] = mapped_column(String(100), nullable=False)
    name: Mapped[str] = mapped_column(String(240), nullable=False)
    currency_code: Mapped[str] = mapped_column(String(3), nullable=False)
    minor_units: Mapped[int] = mapped_column(Integer, nullable=False)
    payable_box_code: Mapped[str] = mapped_column(String(100), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)

    boxes: Mapped[list[StatutoryReportBox]] = relationship(
        back_populates="definition",
        cascade="all, delete-orphan",
        order_by="StatutoryReportBox.sequence",
    )


class StatutoryReportBox(Base):
    __tablename__ = "statutory_report_boxes"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "id", name="uq_statutory_report_boxes_tenant_id_id"
        ),
        UniqueConstraint(
            "tenant_id",
            "definition_id",
            "box_code",
            name="uq_statutory_report_boxes_code",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "definition_id"],
            [
                f"{SCHEMA}.statutory_report_definitions.tenant_id",
                f"{SCHEMA}.statutory_report_definitions.id",
            ],
            ondelete="CASCADE",
            name="fk_statutory_report_boxes_definition",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "tax_code_id"],
            [f"{SCHEMA}.tax_codes.tenant_id", f"{SCHEMA}.tax_codes.id"],
            ondelete="RESTRICT",
            name="fk_statutory_report_boxes_code",
        ),
        CheckConstraint(
            "value_source IN ('base_amount','tax_amount','recoverable_amount',"
            "'non_recoverable_amount')",
            name="ck_statutory_report_boxes_source",
        ),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(), ForeignKey(Tenant.__table__.c.id, ondelete="CASCADE"), nullable=False
    )
    definition_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    box_code: Mapped[str] = mapped_column(String(100), nullable=False)
    label: Mapped[str] = mapped_column(String(240), nullable=False)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    tax_code_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    value_source: Mapped[str] = mapped_column(String(40), nullable=False)
    multiplier: Mapped[Decimal] = mapped_column(RATE, nullable=False)

    definition: Mapped[StatutoryReportDefinition] = relationship(back_populates="boxes")


class TaxFilingObligation(Base, TimestampMixin):
    __tablename__ = "tax_filing_obligations"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "id", name="uq_tax_filing_obligations_tenant_id_id"
        ),
        UniqueConstraint(
            "tenant_id", "obligation_ref", name="uq_tax_filing_obligations_ref"
        ),
        ForeignKeyConstraint(
            ["tenant_id", "definition_id"],
            [
                f"{SCHEMA}.statutory_report_definitions.tenant_id",
                f"{SCHEMA}.statutory_report_definitions.id",
            ],
            ondelete="RESTRICT",
            name="fk_tax_filing_obligations_definition",
        ),
        CheckConstraint(
            "period_end >= period_start", name="ck_tax_filing_obligations_period"
        ),
        CheckConstraint(
            "status IN ('open','filed','accepted','closed')",
            name="ck_tax_filing_obligations_status",
        ),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(), ForeignKey(Tenant.__table__.c.id, ondelete="CASCADE"), nullable=False
    )
    definition_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    obligation_ref: Mapped[str] = mapped_column(String(180), nullable=False)
    period_start: Mapped[date] = mapped_column(Date, nullable=False)
    period_end: Mapped[date] = mapped_column(Date, nullable=False)
    due_on: Mapped[date] = mapped_column(Date, nullable=False)
    taxpayer_ref: Mapped[str] = mapped_column(String(240), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)


class StatutoryReport(Base):
    __tablename__ = "statutory_reports"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_statutory_reports_tenant_id_id"),
        UniqueConstraint(
            "tenant_id",
            "obligation_id",
            "version",
            name="uq_statutory_reports_obligation_version",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "definition_id"],
            [
                f"{SCHEMA}.statutory_report_definitions.tenant_id",
                f"{SCHEMA}.statutory_report_definitions.id",
            ],
            ondelete="RESTRICT",
            name="fk_statutory_reports_definition",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "obligation_id"],
            [
                f"{SCHEMA}.tax_filing_obligations.tenant_id",
                f"{SCHEMA}.tax_filing_obligations.id",
            ],
            ondelete="RESTRICT",
            name="fk_statutory_reports_obligation",
        ),
        CheckConstraint(
            "minor_units BETWEEN 0 AND 6", name="ck_statutory_reports_minor_units"
        ),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(), ForeignKey(Tenant.__table__.c.id, ondelete="CASCADE"), nullable=False
    )
    definition_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    obligation_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    total_payable: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    currency_code: Mapped[str] = mapped_column(String(3), nullable=False)
    minor_units: Mapped[int] = mapped_column(Integer, nullable=False)
    snapshot_ref: Mapped[str] = mapped_column(String(240), nullable=False)
    generated_by_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    generated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )

    values: Mapped[list[StatutoryReportValue]] = relationship(
        back_populates="report",
        cascade="all, delete-orphan",
        order_by="StatutoryReportValue.sequence",
    )


class StatutoryReportValue(Base):
    __tablename__ = "statutory_report_values"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "id", name="uq_statutory_report_values_tenant_id_id"
        ),
        UniqueConstraint(
            "tenant_id", "report_id", "box_code", name="uq_statutory_report_values_box"
        ),
        ForeignKeyConstraint(
            ["tenant_id", "report_id"],
            [f"{SCHEMA}.statutory_reports.tenant_id", f"{SCHEMA}.statutory_reports.id"],
            ondelete="CASCADE",
            name="fk_statutory_report_values_report",
        ),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(), ForeignKey(Tenant.__table__.c.id, ondelete="CASCADE"), nullable=False
    )
    report_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    box_code: Mapped[str] = mapped_column(String(100), nullable=False)
    label: Mapped[str] = mapped_column(String(240), nullable=False)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    amount: Mapped[Decimal] = mapped_column(MONEY, nullable=False)

    report: Mapped[StatutoryReport] = relationship(back_populates="values")


class TaxReturn(Base):
    __tablename__ = "tax_returns"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_tax_returns_tenant_id_id"),
        UniqueConstraint("tenant_id", "report_id", name="uq_tax_returns_report"),
        ForeignKeyConstraint(
            ["tenant_id", "report_id"],
            [f"{SCHEMA}.statutory_reports.tenant_id", f"{SCHEMA}.statutory_reports.id"],
            ondelete="RESTRICT",
            name="fk_tax_returns_report",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "obligation_id"],
            [
                f"{SCHEMA}.tax_filing_obligations.tenant_id",
                f"{SCHEMA}.tax_filing_obligations.id",
            ],
            ondelete="RESTRICT",
            name="fk_tax_returns_obligation",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "original_return_id"],
            [f"{SCHEMA}.tax_returns.tenant_id", f"{SCHEMA}.tax_returns.id"],
            ondelete="RESTRICT",
            name="fk_tax_returns_original",
        ),
        CheckConstraint(
            "status IN ('draft','prepared','approved','filed','accepted',"
            "'rejected','superseded')",
            name="ck_tax_returns_status",
        ),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(), ForeignKey(Tenant.__table__.c.id, ondelete="CASCADE"), nullable=False
    )
    report_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    obligation_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    report_amount: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    adjustment_amount: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    payable_amount: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    currency_code: Mapped[str] = mapped_column(String(3), nullable=False)
    minor_units: Mapped[int] = mapped_column(Integer, nullable=False)
    created_by_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    prepared_by_id: Mapped[UUID | None] = mapped_column(Uuid())
    prepared_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    approved_by_id: Mapped[UUID | None] = mapped_column(Uuid())
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    filed_by_id: Mapped[UUID | None] = mapped_column(Uuid())
    filed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    filing_reference: Mapped[str | None] = mapped_column(String(240))
    authority_reference: Mapped[str | None] = mapped_column(String(240))
    original_return_id: Mapped[UUID | None] = mapped_column(Uuid())
    amendment_reason: Mapped[str | None] = mapped_column(String(500))


class TaxReturnEvent(Base):
    __tablename__ = "tax_return_events"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_tax_return_events_tenant_id_id"),
        UniqueConstraint(
            "tenant_id", "return_id", "sequence", name="uq_tax_return_events_sequence"
        ),
        ForeignKeyConstraint(
            ["tenant_id", "return_id"],
            [f"{SCHEMA}.tax_returns.tenant_id", f"{SCHEMA}.tax_returns.id"],
            ondelete="RESTRICT",
            name="fk_tax_return_events_return",
        ),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(), ForeignKey(Tenant.__table__.c.id, ondelete="CASCADE"), nullable=False
    )
    return_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    from_status: Mapped[str | None] = mapped_column(String(20))
    to_status: Mapped[str] = mapped_column(String(20), nullable=False)
    actor_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    authority_reference: Mapped[str | None] = mapped_column(String(240))
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


TENANT_MODELS = (
    TaxAuthority,
    TaxJurisdiction,
    TaxCode,
    TaxRule,
    TaxRuleBand,
    TaxDetermination,
    TaxDeterminationLine,
    StatutoryReportDefinition,
    StatutoryReportBox,
    TaxFilingObligation,
    StatutoryReport,
    StatutoryReportValue,
    TaxReturn,
    TaxReturnEvent,
)
TENANT_TABLES = tuple(model.__tablename__ for model in TENANT_MODELS)

__all__ = [
    "StatutoryReport",
    "StatutoryReportBox",
    "StatutoryReportDefinition",
    "StatutoryReportValue",
    "TENANT_MODELS",
    "TENANT_TABLES",
    "TaxAuthority",
    "TaxCode",
    "TaxDetermination",
    "TaxDeterminationLine",
    "TaxFilingObligation",
    "TaxJurisdiction",
    "TaxReturn",
    "TaxReturnEvent",
    "TaxRule",
    "TaxRuleBand",
]
