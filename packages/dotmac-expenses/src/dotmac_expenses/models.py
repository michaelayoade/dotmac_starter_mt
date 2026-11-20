"""Tenant expense persistence in the allocated ``mod_expenses`` schema."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import cast
from uuid import UUID

import sqlalchemy as sa
from dotmac_kernel.models import Base, Party, Tenant, TimestampMixin, uuid_pk
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
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from dotmac_expenses.contracts import (
    ClaimLiabilityStatus,
    ClaimStatus,
    EvaluationResult,
    LimitAction,
    LimitPeriod,
    PolicyStatus,
    PolicyTarget,
    ReceiptVerificationStatus,
    RequestStatus,
)

SCHEMA = module_schema("expenses")


def _enum(enum_type: type, name: str) -> sa.Enum:
    return sa.Enum(
        enum_type,
        name=name,
        native_enum=False,
        values_callable=lambda cls: [member.value for member in cls],
        create_constraint=True,
    )


class ExpenseCategory(Base, TimestampMixin):
    __tablename__ = "expense_categories"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_expense_categories_tenant_id_id"),
        UniqueConstraint("tenant_id", "code", name="uq_expense_categories_tenant_code"),
        CheckConstraint(
            "receipt_threshold IS NULL OR receipt_threshold >= 0",
            name="ck_expense_categories_receipt_threshold",
        ),
        CheckConstraint(
            "max_amount_per_line IS NULL OR max_amount_per_line > 0",
            name="ck_expense_categories_line_limit",
        ),
        CheckConstraint(
            "max_amount_per_claim IS NULL OR max_amount_per_claim > 0",
            name="ck_expense_categories_claim_limit",
        ),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(), ForeignKey(Tenant.__table__.c.id, ondelete="CASCADE"), nullable=False
    )
    code: Mapped[str] = mapped_column(String(40), nullable=False)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    requires_receipt: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=sa.false()
    )
    receipt_threshold: Mapped[Decimal | None] = mapped_column(
        Numeric(18, 2), nullable=True
    )
    max_amount_per_line: Mapped[Decimal | None] = mapped_column(
        Numeric(18, 2), nullable=True
    )
    max_amount_per_claim: Mapped[Decimal | None] = mapped_column(
        Numeric(18, 2), nullable=True
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=sa.true()
    )


class ExpensePolicy(Base, TimestampMixin):
    __tablename__ = "expense_policies"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_expense_policies_tenant_id_id"),
        UniqueConstraint(
            "tenant_id",
            "code",
            "version",
            name="uq_expense_policies_tenant_code_version",
        ),
        CheckConstraint("version > 0", name="ck_expense_policies_positive_version"),
        CheckConstraint(
            "effective_to IS NULL OR effective_to >= effective_from",
            name="ck_expense_policies_effective_dates",
        ),
        Index(
            "ix_expense_policies_tenant_effective",
            "tenant_id",
            "status",
            "effective_from",
            "effective_to",
        ),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(), ForeignKey(Tenant.__table__.c.id, ondelete="CASCADE"), nullable=False
    )
    code: Mapped[str] = mapped_column(String(60), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    currency_code: Mapped[str] = mapped_column(String(3), nullable=False)
    effective_from: Mapped[date] = mapped_column(Date, nullable=False)
    effective_to: Mapped[date | None] = mapped_column(Date, nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[PolicyStatus] = mapped_column(
        _enum(PolicyStatus, "expense_policy_status"),
        nullable=False,
        default=PolicyStatus.DRAFT,
        server_default=PolicyStatus.DRAFT.value,
    )
    published_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    retired_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    rules: Mapped[list[ExpensePolicyRule]] = relationship(
        back_populates="policy",
        order_by="ExpensePolicyRule.priority, ExpensePolicyRule.code",
    )


class ExpensePolicyRule(Base, TimestampMixin):
    __tablename__ = "expense_policy_rules"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "id", name="uq_expense_policy_rules_tenant_id_id"
        ),
        UniqueConstraint(
            "tenant_id",
            "policy_id",
            "code",
            name="uq_expense_policy_rules_policy_code",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "policy_id"],
            [f"{SCHEMA}.expense_policies.tenant_id", f"{SCHEMA}.expense_policies.id"],
            ondelete="CASCADE",
            name="fk_expense_policy_rules_policy",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "category_id"],
            [
                f"{SCHEMA}.expense_categories.tenant_id",
                f"{SCHEMA}.expense_categories.id",
            ],
            ondelete="RESTRICT",
            name="fk_expense_policy_rules_category",
        ),
        CheckConstraint(
            "limit_amount > 0", name="ck_expense_policy_rules_positive_limit"
        ),
        CheckConstraint("priority >= 0", name="ck_expense_policy_rules_priority"),
        Index(
            "ix_expense_policy_rules_tenant_policy",
            "tenant_id",
            "policy_id",
            "priority",
        ),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(), ForeignKey(Tenant.__table__.c.id, ondelete="CASCADE"), nullable=False
    )
    policy_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    category_id: Mapped[UUID | None] = mapped_column(Uuid(), nullable=True)
    code: Mapped[str] = mapped_column(String(80), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    target: Mapped[PolicyTarget] = mapped_column(
        _enum(PolicyTarget, "expense_policy_target"), nullable=False
    )
    period: Mapped[LimitPeriod] = mapped_column(
        _enum(LimitPeriod, "expense_limit_period"), nullable=False
    )
    action: Mapped[LimitAction] = mapped_column(
        _enum(LimitAction, "expense_limit_action"), nullable=False
    )
    limit_amount: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    applicability_key: Mapped[str | None] = mapped_column(String(200), nullable=True)
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=100)

    policy: Mapped[ExpensePolicy] = relationship(back_populates="rules")


class ExpenseRequest(Base, TimestampMixin):
    __tablename__ = "expense_requests"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_expense_requests_tenant_id_id"),
        UniqueConstraint(
            "tenant_id", "reference", name="uq_expense_requests_tenant_reference"
        ),
        ForeignKeyConstraint(
            ["tenant_id", "requester_party_id"],
            [Party.__table__.c.tenant_id, Party.__table__.c.id],
            ondelete="RESTRICT",
            name="fk_expense_requests_requester_party",
        ),
        CheckConstraint(
            "total_requested_amount > 0",
            name="ck_expense_requests_positive_total",
        ),
        Index(
            "ix_expense_requests_tenant_requester",
            "tenant_id",
            "requester_party_id",
            "status",
        ),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(), ForeignKey(Tenant.__table__.c.id, ondelete="CASCADE"), nullable=False
    )
    reference: Mapped[str] = mapped_column(String(80), nullable=False)
    requester_party_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    purpose: Mapped[str] = mapped_column(String(500), nullable=False)
    currency_code: Mapped[str] = mapped_column(String(3), nullable=False)
    needed_by: Mapped[date] = mapped_column(Date, nullable=False)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    total_requested_amount: Mapped[Decimal] = mapped_column(
        Numeric(18, 2), nullable=False
    )
    status: Mapped[RequestStatus] = mapped_column(
        _enum(RequestStatus, "expense_request_status"),
        nullable=False,
        default=RequestStatus.DRAFT,
        server_default=RequestStatus.DRAFT.value,
    )
    evaluation_batch_id: Mapped[UUID | None] = mapped_column(Uuid(), nullable=True)
    submitted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    decided_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    decision_reference: Mapped[str | None] = mapped_column(String(255), nullable=True)
    decision_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    cancelled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    converted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    lines: Mapped[list[ExpenseRequestLine]] = relationship(
        back_populates="request",
        cascade="all, delete-orphan",
        order_by="ExpenseRequestLine.sequence, ExpenseRequestLine.created_at",
    )
    claims: Mapped[list[ExpenseClaim]] = relationship(back_populates="request")


class ExpenseRequestLine(Base):
    __tablename__ = "expense_request_lines"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "id", name="uq_expense_request_lines_tenant_id_id"
        ),
        UniqueConstraint(
            "tenant_id",
            "request_id",
            "sequence",
            name="uq_expense_request_lines_request_sequence",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "request_id"],
            [f"{SCHEMA}.expense_requests.tenant_id", f"{SCHEMA}.expense_requests.id"],
            ondelete="CASCADE",
            name="fk_expense_request_lines_request",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "category_id"],
            [
                f"{SCHEMA}.expense_categories.tenant_id",
                f"{SCHEMA}.expense_categories.id",
            ],
            ondelete="RESTRICT",
            name="fk_expense_request_lines_category",
        ),
        CheckConstraint("amount > 0", name="ck_expense_request_lines_positive_amount"),
        CheckConstraint("sequence >= 0", name="ck_expense_request_lines_sequence"),
        Index(
            "ix_expense_request_lines_tenant_request",
            "tenant_id",
            "request_id",
        ),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(), ForeignKey(Tenant.__table__.c.id, ondelete="CASCADE"), nullable=False
    )
    request_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    category_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    description: Mapped[str] = mapped_column(String(500), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    expected_on: Mapped[date] = mapped_column(Date, nullable=False)
    vendor_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    )

    request: Mapped[ExpenseRequest] = relationship(back_populates="lines")


class ExpenseClaim(Base, TimestampMixin):
    __tablename__ = "expense_claims"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_expense_claims_tenant_id_id"),
        UniqueConstraint(
            "tenant_id", "reference", name="uq_expense_claims_tenant_reference"
        ),
        UniqueConstraint(
            "tenant_id", "request_id", name="uq_expense_claims_tenant_request"
        ),
        ForeignKeyConstraint(
            ["tenant_id", "claimant_party_id"],
            [Party.__table__.c.tenant_id, Party.__table__.c.id],
            ondelete="RESTRICT",
            name="fk_expense_claims_claimant_party",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "request_id"],
            [f"{SCHEMA}.expense_requests.tenant_id", f"{SCHEMA}.expense_requests.id"],
            ondelete="RESTRICT",
            name="fk_expense_claims_request",
        ),
        CheckConstraint(
            "expense_period_end IS NULL OR expense_period_start IS NULL "
            "OR expense_period_end >= expense_period_start",
            name="ck_expense_claims_period_dates",
        ),
        CheckConstraint(
            "total_claimed_amount > 0", name="ck_expense_claims_positive_claimed"
        ),
        CheckConstraint(
            "total_approved_amount IS NULL OR total_approved_amount >= 0",
            name="ck_expense_claims_nonnegative_approved",
        ),
        Index(
            "ix_expense_claims_tenant_claimant",
            "tenant_id",
            "claimant_party_id",
            "status",
        ),
        Index("ix_expense_claims_tenant_date", "tenant_id", "claim_date", "status"),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(), ForeignKey(Tenant.__table__.c.id, ondelete="CASCADE"), nullable=False
    )
    reference: Mapped[str] = mapped_column(String(80), nullable=False)
    claimant_party_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    request_id: Mapped[UUID | None] = mapped_column(Uuid(), nullable=True)
    purpose: Mapped[str] = mapped_column(String(500), nullable=False)
    claim_date: Mapped[date] = mapped_column(Date, nullable=False)
    expense_period_start: Mapped[date | None] = mapped_column(Date, nullable=True)
    expense_period_end: Mapped[date | None] = mapped_column(Date, nullable=True)
    currency_code: Mapped[str] = mapped_column(String(3), nullable=False)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    total_claimed_amount: Mapped[Decimal] = mapped_column(
        Numeric(18, 2), nullable=False
    )
    total_approved_amount: Mapped[Decimal | None] = mapped_column(
        Numeric(18, 2), nullable=True
    )
    status: Mapped[ClaimStatus] = mapped_column(
        _enum(ClaimStatus, "expense_claim_status"),
        nullable=False,
        default=ClaimStatus.DRAFT,
        server_default=ClaimStatus.DRAFT.value,
    )
    evaluation_batch_id: Mapped[UUID | None] = mapped_column(Uuid(), nullable=True)
    submitted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    decided_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    decision_reference: Mapped[str | None] = mapped_column(String(255), nullable=True)
    decision_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    cancelled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    request: Mapped[ExpenseRequest | None] = relationship(back_populates="claims")
    lines: Mapped[list[ExpenseClaimLine]] = relationship(
        back_populates="claim",
        cascade="all, delete-orphan",
        order_by="ExpenseClaimLine.sequence, ExpenseClaimLine.created_at",
    )


class ExpenseClaimLine(Base):
    __tablename__ = "expense_claim_lines"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_expense_claim_lines_tenant_id_id"),
        UniqueConstraint(
            "tenant_id",
            "claim_id",
            "sequence",
            name="uq_expense_claim_lines_claim_sequence",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "claim_id"],
            [f"{SCHEMA}.expense_claims.tenant_id", f"{SCHEMA}.expense_claims.id"],
            ondelete="CASCADE",
            name="fk_expense_claim_lines_claim",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "category_id"],
            [
                f"{SCHEMA}.expense_categories.tenant_id",
                f"{SCHEMA}.expense_categories.id",
            ],
            ondelete="RESTRICT",
            name="fk_expense_claim_lines_category",
        ),
        CheckConstraint(
            "claimed_amount > 0", name="ck_expense_claim_lines_positive_claimed"
        ),
        CheckConstraint(
            "approved_amount IS NULL OR (approved_amount >= 0 "
            "AND approved_amount <= claimed_amount)",
            name="ck_expense_claim_lines_approved_range",
        ),
        CheckConstraint("sequence >= 0", name="ck_expense_claim_lines_sequence"),
        Index("ix_expense_claim_lines_tenant_claim", "tenant_id", "claim_id"),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(), ForeignKey(Tenant.__table__.c.id, ondelete="CASCADE"), nullable=False
    )
    claim_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    category_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    description: Mapped[str] = mapped_column(String(500), nullable=False)
    claimed_amount: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    approved_amount: Mapped[Decimal | None] = mapped_column(
        Numeric(18, 2), nullable=True
    )
    expense_date: Mapped[date] = mapped_column(Date, nullable=False)
    vendor_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    )

    claim: Mapped[ExpenseClaim] = relationship(back_populates="lines")
    receipts: Mapped[list[ExpenseReceipt]] = relationship(
        back_populates="claim_line",
        cascade="all, delete-orphan",
        order_by="ExpenseReceipt.created_at",
    )


class ExpenseReceipt(Base):
    __tablename__ = "expense_receipts"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_expense_receipts_tenant_id_id"),
        UniqueConstraint(
            "tenant_id",
            "claim_line_id",
            "file_id",
            name="uq_expense_receipts_line_file",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "claim_line_id"],
            [
                f"{SCHEMA}.expense_claim_lines.tenant_id",
                f"{SCHEMA}.expense_claim_lines.id",
            ],
            ondelete="CASCADE",
            name="fk_expense_receipts_claim_line",
        ),
        CheckConstraint("size_bytes > 0", name="ck_expense_receipts_positive_size"),
        CheckConstraint(
            "gross_amount IS NULL OR gross_amount >= 0",
            name="ck_expense_receipts_nonnegative_amount",
        ),
        Index("ix_expense_receipts_tenant_line", "tenant_id", "claim_line_id"),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(), ForeignKey(Tenant.__table__.c.id, ondelete="CASCADE"), nullable=False
    )
    claim_line_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    file_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    media_type: Mapped[str] = mapped_column(String(120), nullable=False)
    size_bytes: Mapped[int] = mapped_column(sa.BigInteger, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    receipt_number: Mapped[str | None] = mapped_column(String(100), nullable=True)
    merchant_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    issued_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    gross_amount: Mapped[Decimal | None] = mapped_column(Numeric(18, 2), nullable=True)
    currency_code: Mapped[str | None] = mapped_column(String(3), nullable=True)
    verification_status: Mapped[ReceiptVerificationStatus] = mapped_column(
        _enum(ReceiptVerificationStatus, "expense_receipt_verification_status"),
        nullable=False,
        default=ReceiptVerificationStatus.PENDING,
        server_default=ReceiptVerificationStatus.PENDING.value,
    )
    verification_reference: Mapped[str | None] = mapped_column(
        String(255), nullable=True
    )
    verified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    )

    claim_line: Mapped[ExpenseClaimLine] = relationship(back_populates="receipts")


class ExpensePolicyEvaluation(Base):
    __tablename__ = "expense_policy_evaluations"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "id",
            name="uq_expense_policy_evaluations_tenant_id_id",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "policy_id"],
            [f"{SCHEMA}.expense_policies.tenant_id", f"{SCHEMA}.expense_policies.id"],
            ondelete="RESTRICT",
            name="fk_expense_policy_evaluations_policy",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "rule_id"],
            [
                f"{SCHEMA}.expense_policy_rules.tenant_id",
                f"{SCHEMA}.expense_policy_rules.id",
            ],
            ondelete="RESTRICT",
            name="fk_expense_policy_evaluations_rule",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "request_id"],
            [f"{SCHEMA}.expense_requests.tenant_id", f"{SCHEMA}.expense_requests.id"],
            ondelete="RESTRICT",
            name="fk_expense_policy_evaluations_request",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "request_line_id"],
            [
                f"{SCHEMA}.expense_request_lines.tenant_id",
                f"{SCHEMA}.expense_request_lines.id",
            ],
            ondelete="RESTRICT",
            name="fk_expense_policy_evaluations_request_line",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "claim_id"],
            [f"{SCHEMA}.expense_claims.tenant_id", f"{SCHEMA}.expense_claims.id"],
            ondelete="RESTRICT",
            name="fk_expense_policy_evaluations_claim",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "claim_line_id"],
            [
                f"{SCHEMA}.expense_claim_lines.tenant_id",
                f"{SCHEMA}.expense_claim_lines.id",
            ],
            ondelete="RESTRICT",
            name="fk_expense_policy_evaluations_claim_line",
        ),
        CheckConstraint(
            "(request_id IS NOT NULL AND claim_id IS NULL) OR "
            "(request_id IS NULL AND claim_id IS NOT NULL)",
            name="ck_expense_policy_evaluations_one_subject",
        ),
        CheckConstraint(
            "actual_amount >= 0", name="ck_expense_policy_evaluations_actual"
        ),
        Index("ix_expense_policy_evaluations_batch", "tenant_id", "batch_id"),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(), ForeignKey(Tenant.__table__.c.id, ondelete="CASCADE"), nullable=False
    )
    batch_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    policy_id: Mapped[UUID | None] = mapped_column(Uuid(), nullable=True)
    rule_id: Mapped[UUID | None] = mapped_column(Uuid(), nullable=True)
    request_id: Mapped[UUID | None] = mapped_column(Uuid(), nullable=True)
    request_line_id: Mapped[UUID | None] = mapped_column(Uuid(), nullable=True)
    claim_id: Mapped[UUID | None] = mapped_column(Uuid(), nullable=True)
    claim_line_id: Mapped[UUID | None] = mapped_column(Uuid(), nullable=True)
    result: Mapped[EvaluationResult] = mapped_column(
        _enum(EvaluationResult, "expense_evaluation_result"), nullable=False
    )
    reason_code: Mapped[str] = mapped_column(String(120), nullable=False)
    actual_amount: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    limit_amount: Mapped[Decimal | None] = mapped_column(Numeric(18, 2), nullable=True)
    period_start: Mapped[date | None] = mapped_column(Date, nullable=True)
    period_end: Mapped[date | None] = mapped_column(Date, nullable=True)
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    evaluated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    )


class ExpenseLifecycleEvent(Base):
    __tablename__ = "expense_lifecycle_events"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "id", name="uq_expense_lifecycle_events_tenant_id_id"
        ),
        ForeignKeyConstraint(
            ["tenant_id", "request_id"],
            [f"{SCHEMA}.expense_requests.tenant_id", f"{SCHEMA}.expense_requests.id"],
            ondelete="RESTRICT",
            name="fk_expense_lifecycle_events_request",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "claim_id"],
            [f"{SCHEMA}.expense_claims.tenant_id", f"{SCHEMA}.expense_claims.id"],
            ondelete="RESTRICT",
            name="fk_expense_lifecycle_events_claim",
        ),
        CheckConstraint(
            "(request_id IS NOT NULL AND claim_id IS NULL) OR "
            "(request_id IS NULL AND claim_id IS NOT NULL)",
            name="ck_expense_lifecycle_events_one_subject",
        ),
        Index(
            "ix_expense_lifecycle_events_request",
            "tenant_id",
            "request_id",
            "occurred_at",
        ),
        Index(
            "ix_expense_lifecycle_events_claim", "tenant_id", "claim_id", "occurred_at"
        ),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(), ForeignKey(Tenant.__table__.c.id, ondelete="CASCADE"), nullable=False
    )
    request_id: Mapped[UUID | None] = mapped_column(Uuid(), nullable=True)
    claim_id: Mapped[UUID | None] = mapped_column(Uuid(), nullable=True)
    from_status: Mapped[str | None] = mapped_column(String(40), nullable=True)
    to_status: Mapped[str] = mapped_column(String(40), nullable=False)
    actor_reference: Mapped[str] = mapped_column(String(255), nullable=False)
    decision_reference: Mapped[str | None] = mapped_column(String(255), nullable=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    )


# --- a2: claim liability ------------------------------------------------------------

MONEY = Numeric(20, 6)
_LIABILITY_STATUSES = "('open', 'partially_settled', 'settled', 'reversed')"
LIABILITY_AMOUNTS_CHECK = (
    "amount > 0 AND amount_settled >= 0 "
    "AND amount + amount_adjusted >= 0 "
    "AND amount_settled <= amount + amount_adjusted"
)
# Status is DERIVED from the amounts (payroll a6 rule): settled when the settled
# amount equals the effective amount (a full write-down gives settled with
# settled 0), open when nothing is settled, otherwise partially settled.
# ``reversed`` is the one status the amounts cannot derive: it implies nothing
# settled and nothing adjusted.
LIABILITY_STATUS_CHECK = (
    "(status = 'reversed' AND amount_settled = 0 AND amount_adjusted = 0) "
    "OR (status = 'settled' AND amount_settled = amount + amount_adjusted) "
    "OR (status = 'open' AND amount_settled = 0 "
    "AND amount_settled < amount + amount_adjusted) "
    "OR (status = 'partially_settled' AND amount_settled > 0 "
    "AND amount_settled < amount + amount_adjusted)"
)


def _tenant_id() -> Mapped[UUID]:
    return mapped_column(
        Uuid(), ForeignKey(Tenant.__table__.c.id, ondelete="CASCADE"), nullable=False
    )


def _liability_fk(table: str) -> ForeignKeyConstraint:
    return ForeignKeyConstraint(
        ["tenant_id", "liability_id"],
        [f"{SCHEMA}.claim_liabilities.tenant_id", f"{SCHEMA}.claim_liabilities.id"],
        ondelete="RESTRICT",
        name=f"fk_{table}_liability",
    )


class ClaimLiability(Base):
    """What an accepted (or historically admitted) claim owes the claimant.

    ``amount`` is the acceptance-bound original and is never changed; the two
    projections ``amount_settled`` and ``amount_adjusted`` and the derived
    ``status`` are the only columns that move, guarded by a database trigger.
    """

    __tablename__ = "claim_liabilities"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_claim_liabilities_tenant_id_id"),
        UniqueConstraint("tenant_id", "claim_id", name="uq_claim_liabilities_claim"),
        ForeignKeyConstraint(
            ["tenant_id", "claim_id"],
            [f"{SCHEMA}.expense_claims.tenant_id", f"{SCHEMA}.expense_claims.id"],
            ondelete="RESTRICT",
            name="fk_claim_liabilities_claim",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "claimant_party_id"],
            [Party.__table__.c.tenant_id, Party.__table__.c.id],
            ondelete="RESTRICT",
            name="fk_claim_liabilities_claimant_party",
        ),
        CheckConstraint(LIABILITY_AMOUNTS_CHECK, name="ck_claim_liabilities_amounts"),
        CheckConstraint(
            f"status IN {_LIABILITY_STATUSES}", name="ck_claim_liabilities_status"
        ),
        CheckConstraint(LIABILITY_STATUS_CHECK, name="ck_claim_liabilities_derived"),
        CheckConstraint(
            "origin IN ('accepted', 'historical')", name="ck_claim_liabilities_origin"
        ),
        CheckConstraint(
            "minor_units BETWEEN 0 AND 6", name="ck_claim_liabilities_minor_units"
        ),
        CheckConstraint(
            "(origin = 'accepted' AND input_vat_mode IN ('none', 'immediate') "
            "AND acceptance_reference IS NOT NULL) "
            "OR (origin = 'historical' AND input_vat_mode IS NULL "
            "AND acceptance_reference IS NULL)",
            name="ck_claim_liabilities_origin_facts",
        ),
        Index(
            "ix_claim_liabilities_tenant_claimant",
            "tenant_id",
            "claimant_party_id",
            "status",
        ),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = _tenant_id()
    claim_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    claimant_party_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    currency_code: Mapped[str] = mapped_column(String(3), nullable=False)
    minor_units: Mapped[int] = mapped_column(Integer, nullable=False)
    liability_account_ref: Mapped[str] = mapped_column(String(240), nullable=False)
    origin: Mapped[str] = mapped_column(String(16), nullable=False)
    input_vat_mode: Mapped[str | None] = mapped_column(String(16), nullable=True)
    acceptance_reference: Mapped[str | None] = mapped_column(String(240), nullable=True)
    accepted_by: Mapped[str] = mapped_column(String(255), nullable=False)
    accepted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    amount: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    amount_settled: Mapped[Decimal] = mapped_column(
        MONEY, nullable=False, default=Decimal("0"), server_default="0"
    )
    amount_adjusted: Mapped[Decimal] = mapped_column(
        MONEY, nullable=False, default=Decimal("0"), server_default="0"
    )
    status: Mapped[str] = mapped_column(
        String(24),
        nullable=False,
        default=ClaimLiabilityStatus.OPEN.value,
        server_default=ClaimLiabilityStatus.OPEN.value,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    )

    lines: Mapped[list[ClaimLiabilityLine]] = relationship(
        back_populates="liability",
        order_by="ClaimLiabilityLine.line_number",
    )


class ClaimLiabilityLine(Base):
    """One accepted claim line: net, tax and the frozen account refs."""

    __tablename__ = "claim_liability_lines"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "id", name="uq_claim_liability_lines_tenant_id_id"
        ),
        UniqueConstraint(
            "tenant_id",
            "liability_id",
            "claim_line_id",
            name="uq_claim_liability_lines_claim_line",
        ),
        UniqueConstraint(
            "tenant_id",
            "liability_id",
            "line_number",
            name="uq_claim_liability_lines_number",
        ),
        _liability_fk("claim_liability_lines"),
        ForeignKeyConstraint(
            ["tenant_id", "claim_line_id"],
            [
                f"{SCHEMA}.expense_claim_lines.tenant_id",
                f"{SCHEMA}.expense_claim_lines.id",
            ],
            ondelete="RESTRICT",
            name="fk_claim_liability_lines_claim_line",
        ),
        CheckConstraint("net_amount > 0", name="ck_claim_liability_lines_net"),
        CheckConstraint("tax_amount >= 0", name="ck_claim_liability_lines_tax"),
        CheckConstraint(
            "tax_amount = 0 OR (tax_account_ref IS NOT NULL "
            "AND tax_account_ref <> expense_account_ref)",
            name="ck_claim_liability_lines_tax_account",
        ),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = _tenant_id()
    liability_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    claim_line_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    line_number: Mapped[int] = mapped_column(Integer, nullable=False)
    expense_account_ref: Mapped[str] = mapped_column(String(240), nullable=False)
    dimension_refs: Mapped[list[list[str]]] = mapped_column(JSON, nullable=False)
    tax_account_ref: Mapped[str | None] = mapped_column(String(240), nullable=True)
    net_amount: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    tax_amount: Mapped[Decimal] = mapped_column(MONEY, nullable=False)

    liability: Mapped[ClaimLiability] = relationship(back_populates="lines")


class ClaimLiabilitySettlement(Base):
    __tablename__ = "claim_liability_settlements"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "id", name="uq_claim_liability_settlements_tenant_id_id"
        ),
        UniqueConstraint(
            "tenant_id",
            "liability_id",
            "settlement_ref",
            name="uq_claim_liability_settlements_ref",
        ),
        _liability_fk("claim_liability_settlements"),
        CheckConstraint("amount > 0", name="ck_claim_liability_settlements_amount"),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = _tenant_id()
    liability_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    amount: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    settlement_ref: Mapped[str] = mapped_column(String(240), nullable=False)
    evidence_ref: Mapped[str] = mapped_column(String(240), nullable=False)
    value_date: Mapped[date] = mapped_column(Date, nullable=False)
    recorded_by: Mapped[str] = mapped_column(String(255), nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


class ClaimSettlementReversal(Base):
    """A bank return: one whole settlement given back, append-only."""

    __tablename__ = "claim_settlement_reversals"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "id", name="uq_claim_settlement_reversals_tenant_id_id"
        ),
        UniqueConstraint(
            "tenant_id",
            "settlement_id",
            name="uq_claim_settlement_reversals_settlement",
        ),
        _liability_fk("claim_settlement_reversals"),
        ForeignKeyConstraint(
            ["tenant_id", "settlement_id"],
            [
                f"{SCHEMA}.claim_liability_settlements.tenant_id",
                f"{SCHEMA}.claim_liability_settlements.id",
            ],
            ondelete="RESTRICT",
            name="fk_claim_settlement_reversals_settlement",
        ),
        CheckConstraint("amount > 0", name="ck_claim_settlement_reversals_amount"),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = _tenant_id()
    settlement_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    liability_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    amount: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    return_ref: Mapped[str] = mapped_column(String(240), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    reversed_by: Mapped[str] = mapped_column(String(255), nullable=False)
    reversed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


class ClaimLiabilityAdjustment(Base):
    __tablename__ = "claim_liability_adjustments"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "id", name="uq_claim_liability_adjustments_tenant_id_id"
        ),
        UniqueConstraint(
            "tenant_id",
            "liability_id",
            "adjustment_ref",
            name="uq_claim_liability_adjustments_ref",
        ),
        _liability_fk("claim_liability_adjustments"),
        CheckConstraint("amount <> 0", name="ck_claim_liability_adjustments_amount"),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = _tenant_id()
    liability_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    amount: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    adjustment_ref: Mapped[str] = mapped_column(String(240), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    evidence_ref: Mapped[str] = mapped_column(String(240), nullable=False)
    offset_account_ref: Mapped[str] = mapped_column(String(240), nullable=False)
    adjusted_by: Mapped[str] = mapped_column(String(255), nullable=False)
    adjusted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


class ClaimLiabilityReversal(Base):
    """Reversal of an acceptance (OPEN only, terminal)."""

    __tablename__ = "claim_liability_reversals"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "id", name="uq_claim_liability_reversals_tenant_id_id"
        ),
        UniqueConstraint(
            "tenant_id", "liability_id", name="uq_claim_liability_reversals_liability"
        ),
        _liability_fk("claim_liability_reversals"),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = _tenant_id()
    liability_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    evidence_ref: Mapped[str] = mapped_column(String(240), nullable=False)
    reversed_by: Mapped[str] = mapped_column(String(255), nullable=False)
    reversed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


class HistoricalClaimReceipt(Base):
    """Permanent evidence that a legacy claim was admitted with no GL."""

    __tablename__ = "historical_claim_receipts"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "id", name="uq_historical_claim_receipts_tenant_id_id"
        ),
        UniqueConstraint(
            "tenant_id", "claim_id", name="uq_historical_claim_receipts_claim"
        ),
        UniqueConstraint(
            "tenant_id", "liability_id", name="uq_historical_claim_receipts_liability"
        ),
        UniqueConstraint(
            "tenant_id",
            "source_owner",
            "source_claim_id",
            name="uq_historical_claim_receipts_source",
        ),
        _liability_fk("historical_claim_receipts"),
        ForeignKeyConstraint(
            ["tenant_id", "claim_id"],
            [f"{SCHEMA}.expense_claims.tenant_id", f"{SCHEMA}.expense_claims.id"],
            ondelete="RESTRICT",
            name="fk_historical_claim_receipts_claim",
        ),
        CheckConstraint("residual > 0", name="ck_historical_claim_receipts_residual"),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = _tenant_id()
    claim_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    liability_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    source_owner: Mapped[str] = mapped_column(String(120), nullable=False)
    source_claim_id: Mapped[str] = mapped_column(String(255), nullable=False)
    source_snapshot_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    evidence_fingerprint: Mapped[str] = mapped_column(String(68), nullable=False)
    facts_fingerprint: Mapped[str] = mapped_column(String(68), nullable=False)
    accounting_evidence_fingerprint: Mapped[str] = mapped_column(
        String(68), nullable=False
    )
    source_journal_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    carried_evidence_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    carried_account_ref: Mapped[str] = mapped_column(String(240), nullable=False)
    evidence_reference: Mapped[str] = mapped_column(String(255), nullable=False)
    admitted_by: Mapped[str] = mapped_column(String(255), nullable=False)
    net_payable_amount: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    settled_before_admission: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    residual: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    carried_control_amount: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    evidence: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    original_posted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    admitted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


class ClaimAccountingReceipt(Base):
    """The assembly's proof that one consequence was posted (fingerprint-bound)."""

    __tablename__ = "claim_accounting_receipts"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "id", name="uq_claim_accounting_receipts_tenant_id_id"
        ),
        UniqueConstraint(
            "tenant_id",
            "document_kind",
            "document_id",
            name="uq_claim_accounting_receipts_document",
        ),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = _tenant_id()
    document_kind: Mapped[str] = mapped_column(String(80), nullable=False)
    document_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    consequence_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    accounting_reference: Mapped[str] = mapped_column(String(255), nullable=False)
    accounting_evidence_fingerprint: Mapped[str] = mapped_column(
        String(64), nullable=False
    )
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


TENANT_TABLES: tuple[str, ...] = (
    "expense_categories",
    "expense_policies",
    "expense_policy_rules",
    "expense_requests",
    "expense_request_lines",
    "expense_claims",
    "expense_claim_lines",
    "expense_receipts",
    "expense_policy_evaluations",
    "expense_lifecycle_events",
    "claim_liabilities",
    "claim_liability_lines",
    "claim_liability_settlements",
    "claim_settlement_reversals",
    "claim_liability_adjustments",
    "claim_liability_reversals",
    "historical_claim_receipts",
    "claim_accounting_receipts",
)

ALL_MODELS = (
    ExpenseCategory,
    ExpensePolicy,
    ExpensePolicyRule,
    ExpenseRequest,
    ExpenseRequestLine,
    ExpenseClaim,
    ExpenseClaimLine,
    ExpenseReceipt,
    ExpensePolicyEvaluation,
    ExpenseLifecycleEvent,
    ClaimLiability,
    ClaimLiabilityLine,
    ClaimLiabilitySettlement,
    ClaimSettlementReversal,
    ClaimLiabilityAdjustment,
    ClaimLiabilityReversal,
    HistoricalClaimReceipt,
    ClaimAccountingReceipt,
)

_TABLE_BY_NAME: dict[str, sa.Table] = {
    model.__tablename__: cast(sa.Table, model.__table__) for model in ALL_MODELS
}


def metadata_table(table_name: str) -> sa.Table:
    """Return one declared table for composition and live-catalogue gates."""

    return _TABLE_BY_NAME[table_name]


__all__ = [
    "ALL_MODELS",
    "LIABILITY_AMOUNTS_CHECK",
    "LIABILITY_STATUS_CHECK",
    "MONEY",
    "SCHEMA",
    "ClaimAccountingReceipt",
    "ClaimLiability",
    "ClaimLiabilityAdjustment",
    "ClaimLiabilityLine",
    "ClaimLiabilityReversal",
    "ClaimLiabilitySettlement",
    "ClaimSettlementReversal",
    "HistoricalClaimReceipt",
    "TENANT_TABLES",
    "ExpenseCategory",
    "ExpenseClaim",
    "ExpenseClaimLine",
    "ExpenseLifecycleEvent",
    "ExpensePolicy",
    "ExpensePolicyEvaluation",
    "ExpensePolicyRule",
    "ExpenseReceipt",
    "ExpenseRequest",
    "ExpenseRequestLine",
    "metadata_table",
]
