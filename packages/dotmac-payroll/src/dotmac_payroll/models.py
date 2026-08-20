"""Tenant-scoped payroll configuration, calculations and liabilities."""

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
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

SCHEMA = module_schema("payroll")
MONEY = Numeric(20, 6)
RATE = Numeric(12, 8)


class PayComponent(Base, TimestampMixin):
    __tablename__ = "pay_components"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_pay_components_tenant_id_id"),
        UniqueConstraint("tenant_id", "component_code", name="uq_pay_components_code"),
        CheckConstraint(
            "kind IN ('earning','deduction','employer_liability','information')",
            name="ck_pay_components_kind",
        ),
        CheckConstraint(
            "status IN ('active','retired')", name="ck_pay_components_status"
        ),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(), ForeignKey(Tenant.__table__.c.id, ondelete="CASCADE"), nullable=False
    )
    component_code: Mapped[str] = mapped_column(String(80), nullable=False)
    name: Mapped[str] = mapped_column(String(240), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(String(30), nullable=False)
    expense_account_ref: Mapped[str | None] = mapped_column(String(240))
    liability_account_ref: Mapped[str | None] = mapped_column(String(240))
    liability_destination_ref: Mapped[str | None] = mapped_column(String(240))
    status: Mapped[str] = mapped_column(String(20), nullable=False)


class PayStructure(Base, TimestampMixin):
    __tablename__ = "pay_structures"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_pay_structures_tenant_id_id"),
        UniqueConstraint("tenant_id", "code", name="uq_pay_structures_code"),
        CheckConstraint(
            "minor_units BETWEEN 0 AND 6", name="ck_pay_structures_minor_units"
        ),
        CheckConstraint(
            "status IN ('active','retired')", name="ck_pay_structures_status"
        ),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(), ForeignKey(Tenant.__table__.c.id, ondelete="CASCADE"), nullable=False
    )
    code: Mapped[str] = mapped_column(String(80), nullable=False)
    name: Mapped[str] = mapped_column(String(240), nullable=False)
    currency_code: Mapped[str] = mapped_column(String(3), nullable=False)
    minor_units: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)


class PayStructureRevision(Base):
    __tablename__ = "pay_structure_revisions"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "id", name="uq_pay_structure_revisions_tenant_id_id"
        ),
        UniqueConstraint(
            "tenant_id",
            "structure_id",
            "version",
            name="uq_pay_structure_revisions_version",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "structure_id"],
            [f"{SCHEMA}.pay_structures.tenant_id", f"{SCHEMA}.pay_structures.id"],
            ondelete="RESTRICT",
            name="fk_pay_structure_revisions_structure",
        ),
        CheckConstraint("version > 0", name="ck_pay_structure_revisions_version"),
        CheckConstraint(
            "effective_to IS NULL OR effective_to >= effective_from",
            name="ck_pay_structure_revisions_effective",
        ),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(), ForeignKey(Tenant.__table__.c.id, ondelete="CASCADE"), nullable=False
    )
    structure_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    effective_from: Mapped[date] = mapped_column(Date, nullable=False)
    effective_to: Mapped[date | None] = mapped_column(Date)
    net_payable_account_ref: Mapped[str] = mapped_column(String(240), nullable=False)
    published_by_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    published_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )

    rules: Mapped[list[PayStructureRule]] = relationship(
        back_populates="revision",
        cascade="all, delete-orphan",
        order_by="PayStructureRule.sequence",
    )


class PayStructureRule(Base):
    __tablename__ = "pay_structure_rules"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_pay_structure_rules_tenant_id_id"),
        UniqueConstraint(
            "tenant_id",
            "revision_id",
            "component_id",
            name="uq_pay_structure_rules_component",
        ),
        UniqueConstraint(
            "tenant_id",
            "revision_id",
            "sequence",
            name="uq_pay_structure_rules_sequence",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "revision_id"],
            [
                f"{SCHEMA}.pay_structure_revisions.tenant_id",
                f"{SCHEMA}.pay_structure_revisions.id",
            ],
            ondelete="CASCADE",
            name="fk_pay_structure_rules_revision",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "component_id"],
            [f"{SCHEMA}.pay_components.tenant_id", f"{SCHEMA}.pay_components.id"],
            ondelete="RESTRICT",
            name="fk_pay_structure_rules_component",
        ),
        CheckConstraint(
            "calculation_method IN ('input','fixed','percentage')",
            name="ck_pay_structure_rules_method",
        ),
        CheckConstraint(
            "rate IS NULL OR rate >= 0", name="ck_pay_structure_rules_rate"
        ),
        CheckConstraint(
            "fixed_amount IS NULL OR fixed_amount >= 0",
            name="ck_pay_structure_rules_fixed_amount",
        ),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(), ForeignKey(Tenant.__table__.c.id, ondelete="CASCADE"), nullable=False
    )
    revision_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    component_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    component_code: Mapped[str] = mapped_column(String(80), nullable=False)
    component_name: Mapped[str] = mapped_column(String(240), nullable=False)
    component_kind: Mapped[str] = mapped_column(String(30), nullable=False)
    expense_account_ref: Mapped[str | None] = mapped_column(String(240))
    liability_account_ref: Mapped[str | None] = mapped_column(String(240))
    liability_destination_ref: Mapped[str | None] = mapped_column(String(240))
    calculation_method: Mapped[str] = mapped_column(String(24), nullable=False)
    fixed_amount: Mapped[Decimal | None] = mapped_column(MONEY)
    rate: Mapped[Decimal | None] = mapped_column(RATE)
    prorates: Mapped[bool] = mapped_column(Boolean, nullable=False)

    revision: Mapped[PayStructureRevision] = relationship(back_populates="rules")
    bases: Mapped[list[PayRuleBasis]] = relationship(
        back_populates="rule", cascade="all, delete-orphan"
    )


class PayRuleBasis(Base):
    __tablename__ = "pay_rule_bases"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_pay_rule_bases_tenant_id_id"),
        UniqueConstraint(
            "tenant_id", "rule_id", "component_id", name="uq_pay_rule_bases_component"
        ),
        ForeignKeyConstraint(
            ["tenant_id", "rule_id"],
            [
                f"{SCHEMA}.pay_structure_rules.tenant_id",
                f"{SCHEMA}.pay_structure_rules.id",
            ],
            ondelete="CASCADE",
            name="fk_pay_rule_bases_rule",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "component_id"],
            [f"{SCHEMA}.pay_components.tenant_id", f"{SCHEMA}.pay_components.id"],
            ondelete="RESTRICT",
            name="fk_pay_rule_bases_component",
        ),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(), ForeignKey(Tenant.__table__.c.id, ondelete="CASCADE"), nullable=False
    )
    rule_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    component_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)

    rule: Mapped[PayStructureRule] = relationship(back_populates="bases")


class EmployeePayAssignment(Base, TimestampMixin):
    __tablename__ = "employee_pay_assignments"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "id", name="uq_employee_pay_assignments_tenant_id_id"
        ),
        UniqueConstraint(
            "tenant_id",
            "employee_ref",
            "effective_from",
            name="uq_employee_pay_assignments_effective",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "revision_id"],
            [
                f"{SCHEMA}.pay_structure_revisions.tenant_id",
                f"{SCHEMA}.pay_structure_revisions.id",
            ],
            ondelete="RESTRICT",
            name="fk_employee_pay_assignments_revision",
        ),
        CheckConstraint(
            "effective_to IS NULL OR effective_to >= effective_from",
            name="ck_employee_pay_assignments_effective",
        ),
        CheckConstraint(
            "status IN ('active','ended')", name="ck_employee_pay_assignments_status"
        ),
        Index(
            "ix_employee_pay_assignments_lookup",
            "tenant_id",
            "employee_ref",
            "effective_from",
        ),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(), ForeignKey(Tenant.__table__.c.id, ondelete="CASCADE"), nullable=False
    )
    employee_ref: Mapped[str] = mapped_column(String(240), nullable=False)
    revision_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    effective_from: Mapped[date] = mapped_column(Date, nullable=False)
    effective_to: Mapped[date | None] = mapped_column(Date)
    source_ref: Mapped[str] = mapped_column(String(240), nullable=False)
    source_version: Mapped[str] = mapped_column(String(100), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)


class PayrollRun(Base):
    __tablename__ = "payroll_runs"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_payroll_runs_tenant_id_id"),
        UniqueConstraint("tenant_id", "run_ref", name="uq_payroll_runs_ref"),
        CheckConstraint("period_end >= period_start", name="ck_payroll_runs_period"),
        CheckConstraint(
            "status IN ('draft','approved','finalized','cancelled')",
            name="ck_payroll_runs_status",
        ),
        CheckConstraint(
            "minor_units BETWEEN 0 AND 6", name="ck_payroll_runs_minor_units"
        ),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(), ForeignKey(Tenant.__table__.c.id, ondelete="CASCADE"), nullable=False
    )
    run_ref: Mapped[str] = mapped_column(String(160), nullable=False)
    period_start: Mapped[date] = mapped_column(Date, nullable=False)
    period_end: Mapped[date] = mapped_column(Date, nullable=False)
    currency_code: Mapped[str] = mapped_column(String(3), nullable=False)
    minor_units: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    created_by_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    approved_by_id: Mapped[UUID | None] = mapped_column(Uuid())
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finalized_by_id: Mapped[UUID | None] = mapped_column(Uuid())
    finalized_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PayrollCalculation(Base):
    __tablename__ = "payroll_calculations"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "id", name="uq_payroll_calculations_tenant_id_id"
        ),
        UniqueConstraint(
            "tenant_id",
            "run_id",
            "employee_ref",
            name="uq_payroll_calculations_employee",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "run_id"],
            [f"{SCHEMA}.payroll_runs.tenant_id", f"{SCHEMA}.payroll_runs.id"],
            ondelete="RESTRICT",
            name="fk_payroll_calculations_run",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "assignment_id"],
            [
                f"{SCHEMA}.employee_pay_assignments.tenant_id",
                f"{SCHEMA}.employee_pay_assignments.id",
            ],
            ondelete="RESTRICT",
            name="fk_payroll_calculations_assignment",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "revision_id"],
            [
                f"{SCHEMA}.pay_structure_revisions.tenant_id",
                f"{SCHEMA}.pay_structure_revisions.id",
            ],
            ondelete="RESTRICT",
            name="fk_payroll_calculations_revision",
        ),
        CheckConstraint(
            "gross_amount >= 0 AND deduction_amount >= 0 AND net_amount >= 0 AND "
            "employer_liability_amount >= 0",
            name="ck_payroll_calculations_amounts",
        ),
        CheckConstraint(
            "proration_factor BETWEEN 0 AND 1",
            name="ck_payroll_calculations_proration",
        ),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(), ForeignKey(Tenant.__table__.c.id, ondelete="CASCADE"), nullable=False
    )
    run_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    employee_ref: Mapped[str] = mapped_column(String(240), nullable=False)
    assignment_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    revision_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    revision_version: Mapped[int] = mapped_column(Integer, nullable=False)
    proration_factor: Mapped[Decimal] = mapped_column(RATE, nullable=False)
    gross_amount: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    deduction_amount: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    net_amount: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    employer_liability_amount: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    calculated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


class PayrollCalculationLine(Base):
    __tablename__ = "payroll_calculation_lines"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "id", name="uq_payroll_calculation_lines_tenant_id_id"
        ),
        UniqueConstraint(
            "tenant_id",
            "calculation_id",
            "sequence",
            name="uq_payroll_calculation_lines_sequence",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "calculation_id"],
            [
                f"{SCHEMA}.payroll_calculations.tenant_id",
                f"{SCHEMA}.payroll_calculations.id",
            ],
            ondelete="CASCADE",
            name="fk_payroll_calculation_lines_calculation",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "component_id"],
            [f"{SCHEMA}.pay_components.tenant_id", f"{SCHEMA}.pay_components.id"],
            ondelete="RESTRICT",
            name="fk_payroll_calculation_lines_component",
        ),
        CheckConstraint("amount >= 0", name="ck_payroll_calculation_lines_amount"),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(), ForeignKey(Tenant.__table__.c.id, ondelete="CASCADE"), nullable=False
    )
    calculation_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    component_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    component_code: Mapped[str] = mapped_column(String(80), nullable=False)
    component_name: Mapped[str] = mapped_column(String(240), nullable=False)
    component_kind: Mapped[str] = mapped_column(String(30), nullable=False)
    calculation_method: Mapped[str] = mapped_column(String(24), nullable=False)
    amount: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    evidence_ref: Mapped[str] = mapped_column(String(240), nullable=False)
    expense_account_ref: Mapped[str | None] = mapped_column(String(240))
    liability_account_ref: Mapped[str | None] = mapped_column(String(240))
    liability_destination_ref: Mapped[str | None] = mapped_column(String(240))


class PayrollLiability(Base):
    __tablename__ = "payroll_liabilities"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_payroll_liabilities_tenant_id_id"),
        UniqueConstraint(
            "tenant_id",
            "calculation_id",
            "liability_key",
            name="uq_payroll_liabilities_key",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "run_id"],
            [f"{SCHEMA}.payroll_runs.tenant_id", f"{SCHEMA}.payroll_runs.id"],
            ondelete="RESTRICT",
            name="fk_payroll_liabilities_run",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "calculation_id"],
            [
                f"{SCHEMA}.payroll_calculations.tenant_id",
                f"{SCHEMA}.payroll_calculations.id",
            ],
            ondelete="RESTRICT",
            name="fk_payroll_liabilities_calculation",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "component_id"],
            [f"{SCHEMA}.pay_components.tenant_id", f"{SCHEMA}.pay_components.id"],
            ondelete="RESTRICT",
            name="fk_payroll_liabilities_component",
        ),
        CheckConstraint(
            "beneficiary_type IN ('employee','external')",
            name="ck_payroll_liabilities_beneficiary",
        ),
        CheckConstraint(
            "status IN ('outstanding','partially_settled','settled')",
            name="ck_payroll_liabilities_status",
        ),
        CheckConstraint(
            "amount >= 0 AND amount_settled >= 0 AND amount_settled <= amount",
            name="ck_payroll_liabilities_amounts",
        ),
        CheckConstraint(
            "minor_units BETWEEN 0 AND 6", name="ck_payroll_liabilities_minor_units"
        ),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(), ForeignKey(Tenant.__table__.c.id, ondelete="CASCADE"), nullable=False
    )
    run_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    calculation_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    liability_key: Mapped[str] = mapped_column(String(180), nullable=False)
    beneficiary_type: Mapped[str] = mapped_column(String(20), nullable=False)
    beneficiary_ref: Mapped[str] = mapped_column(String(240), nullable=False)
    component_id: Mapped[UUID | None] = mapped_column(Uuid())
    liability_account_ref: Mapped[str] = mapped_column(String(240), nullable=False)
    amount: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    amount_settled: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    currency_code: Mapped[str] = mapped_column(String(3), nullable=False)
    minor_units: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class PayrollLiabilitySettlement(Base):
    __tablename__ = "payroll_liability_settlements"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "id", name="uq_payroll_liability_settlements_tenant_id_id"
        ),
        UniqueConstraint(
            "tenant_id",
            "liability_id",
            "settlement_ref",
            name="uq_payroll_liability_settlements_ref",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "liability_id"],
            [
                f"{SCHEMA}.payroll_liabilities.tenant_id",
                f"{SCHEMA}.payroll_liabilities.id",
            ],
            ondelete="RESTRICT",
            name="fk_payroll_liability_settlements_liability",
        ),
        CheckConstraint("amount > 0", name="ck_payroll_liability_settlements_amount"),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(), ForeignKey(Tenant.__table__.c.id, ondelete="CASCADE"), nullable=False
    )
    liability_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    amount: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    settlement_ref: Mapped[str] = mapped_column(String(240), nullable=False)
    evidence_ref: Mapped[str] = mapped_column(String(240), nullable=False)
    recorded_by_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


TENANT_MODELS = (
    PayComponent,
    PayStructure,
    PayStructureRevision,
    PayStructureRule,
    PayRuleBasis,
    EmployeePayAssignment,
    PayrollRun,
    PayrollCalculation,
    PayrollCalculationLine,
    PayrollLiability,
    PayrollLiabilitySettlement,
)
TENANT_TABLES = tuple(model.__tablename__ for model in TENANT_MODELS)

__all__ = [
    "EmployeePayAssignment",
    "PayComponent",
    "PayRuleBasis",
    "PayStructure",
    "PayStructureRevision",
    "PayStructureRule",
    "PayrollCalculation",
    "PayrollCalculationLine",
    "PayrollLiability",
    "PayrollLiabilitySettlement",
    "PayrollRun",
    "TENANT_MODELS",
    "TENANT_TABLES",
]
