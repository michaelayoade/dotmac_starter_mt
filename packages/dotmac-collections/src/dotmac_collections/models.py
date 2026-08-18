"""Tenant-only Collections persistence facts.

Financial values here are decision evidence for policy/case behavior, never an
operational receivable balance. The current position is always reread from its
declared owner before a consequence is requested.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from dotmac_kernel.models import Base, Tenant, uuid_pk
from dotmac_kernel.namespaces import module_schema, schema_table_args
from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.orm import Mapped, declared_attr, mapped_column

SCHEMA = module_schema("coll")
MONEY_VALUE = Numeric(20, 6)

TENANT_TABLES: tuple[str, ...] = (
    "collection_policies",
    "collection_policy_versions",
    "collection_policy_steps",
    "collection_cases",
    "collection_case_exposures",
    "collection_case_transitions",
    "collection_step_attempts",
    "payment_arrangements",
    "payment_arrangement_exposures",
    "payment_arrangement_installments",
    "payment_arrangement_settlement_receipts",
    "collection_grace_grants",
    "collection_notice_requests",
    "collection_notice_receipts",
    "collection_action_requests",
    "collection_action_receipts",
    "collection_reconciliations",
)
PLATFORM_TABLES: tuple[str, ...] = ()


class _TenantFact:
    @declared_attr
    def id(cls) -> Mapped[UUID]:
        return uuid_pk()

    @declared_attr
    def tenant_id(cls) -> Mapped[UUID]:
        return mapped_column(
            Uuid(),
            ForeignKey(Tenant.__table__.c.id, ondelete="CASCADE"),
            nullable=False,
        )

    @declared_attr
    def created_at(cls) -> Mapped[datetime]:
        return mapped_column(
            DateTime(timezone=True), nullable=False, server_default=func.now()
        )


def _identity(name: str) -> UniqueConstraint:
    return UniqueConstraint("tenant_id", "id", name=f"uq_{name}_tenant_id_id")


class CollectionPolicy(Base, _TenantFact):
    __tablename__ = "collection_policies"
    __table_args__ = (
        _identity(__tablename__),
        UniqueConstraint(
            "tenant_id", "policy_code", name="uq_collection_policies_code"
        ),
        schema_table_args(SCHEMA),
    )

    policy_code: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)


class CollectionPolicyVersion(Base, _TenantFact):
    __tablename__ = "collection_policy_versions"
    __table_args__ = (
        _identity(__tablename__),
        ForeignKeyConstraint(
            ["tenant_id", "policy_id"],
            [
                f"{SCHEMA}.collection_policies.tenant_id",
                f"{SCHEMA}.collection_policies.id",
            ],
            ondelete="CASCADE",
        ),
        UniqueConstraint(
            "tenant_id",
            "policy_id",
            "version",
            name="uq_collection_policy_versions_number",
        ),
        schema_table_args(SCHEMA),
    )

    policy_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    reason_code: Mapped[str] = mapped_column(String(120), nullable=False)
    collection_timing: Mapped[str] = mapped_column(String(20), nullable=False)
    grace: Mapped[dict[str, object] | None] = mapped_column(JSON)
    effective_from: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    actor_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    publication_reason: Mapped[str] = mapped_column(Text, nullable=False)
    published_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    version_fingerprint: Mapped[str] = mapped_column(String(71), nullable=False)


class CollectionPolicyStep(Base, _TenantFact):
    __tablename__ = "collection_policy_steps"
    __table_args__ = (
        _identity(__tablename__),
        ForeignKeyConstraint(
            ["tenant_id", "policy_version_id"],
            [
                f"{SCHEMA}.collection_policy_versions.tenant_id",
                f"{SCHEMA}.collection_policy_versions.id",
            ],
            ondelete="CASCADE",
        ),
        UniqueConstraint(
            "tenant_id",
            "policy_version_id",
            "ordinal",
            name="uq_collection_policy_steps_ordinal",
        ),
        UniqueConstraint(
            "tenant_id",
            "policy_version_id",
            "step_code",
            name="uq_collection_policy_steps_code",
        ),
        schema_table_args(SCHEMA),
    )

    policy_version_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    step_code: Mapped[str] = mapped_column(String(120), nullable=False)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    offset_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    offset_anchor: Mapped[str] = mapped_column(String(50), nullable=False)
    request_kind: Mapped[str] = mapped_column(String(20), nullable=False)
    action_code: Mapped[str | None] = mapped_column(String(120))
    receipt_required: Mapped[bool] = mapped_column(Boolean, nullable=False)


class CollectionCase(Base, _TenantFact):
    __tablename__ = "collection_cases"
    __table_args__ = (
        _identity(__tablename__),
        ForeignKeyConstraint(
            ["tenant_id", "policy_version_id"],
            [
                f"{SCHEMA}.collection_policy_versions.tenant_id",
                f"{SCHEMA}.collection_policy_versions.id",
            ],
        ),
        UniqueConstraint(
            "tenant_id",
            "source_owner",
            "exposure_ref",
            name="uq_collection_cases_exposure",
        ),
        schema_table_args(SCHEMA),
    )

    policy_version_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    source_owner: Mapped[str] = mapped_column(String(120), nullable=False)
    exposure_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    subject_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    service_ref: Mapped[str | None] = mapped_column(String(255))
    collection_timing: Mapped[str] = mapped_column(String(20), nullable=False)
    reason_code: Mapped[str] = mapped_column(String(120), nullable=False)
    lifecycle: Mapped[str] = mapped_column(String(32), nullable=False)
    source_version: Mapped[int] = mapped_column(Integer, nullable=False)
    position_fingerprint: Mapped[str] = mapped_column(String(255), nullable=False)
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class CollectionCaseExposure(Base, _TenantFact):
    __tablename__ = "collection_case_exposures"
    __table_args__ = (
        _identity(__tablename__),
        ForeignKeyConstraint(
            ["tenant_id", "case_id"],
            [
                f"{SCHEMA}.collection_cases.tenant_id",
                f"{SCHEMA}.collection_cases.id",
            ],
            ondelete="CASCADE",
        ),
        UniqueConstraint(
            "tenant_id",
            "case_id",
            "source_version",
            name="uq_collection_case_exposures_version",
        ),
        schema_table_args(SCHEMA),
    )

    case_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    source_owner: Mapped[str] = mapped_column(String(120), nullable=False)
    exposure_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    source_version: Mapped[int] = mapped_column(Integer, nullable=False)
    position_fingerprint: Mapped[str] = mapped_column(String(255), nullable=False)
    position_snapshot: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class CollectionCaseTransition(Base, _TenantFact):
    __tablename__ = "collection_case_transitions"
    __table_args__ = (
        _identity(__tablename__),
        ForeignKeyConstraint(
            ["tenant_id", "case_id"],
            [
                f"{SCHEMA}.collection_cases.tenant_id",
                f"{SCHEMA}.collection_cases.id",
            ],
            ondelete="CASCADE",
        ),
        UniqueConstraint(
            "tenant_id",
            "case_id",
            "transition_ordinal",
            name="uq_collection_case_transitions_ordinal",
        ),
        schema_table_args(SCHEMA),
    )

    case_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    transition_ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    from_state: Mapped[str | None] = mapped_column(String(32))
    to_state: Mapped[str] = mapped_column(String(32), nullable=False)
    reason_code: Mapped[str] = mapped_column(String(120), nullable=False)
    actor_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    transitioned_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


class CollectionStepAttempt(Base, _TenantFact):
    __tablename__ = "collection_step_attempts"
    __table_args__ = (
        _identity(__tablename__),
        ForeignKeyConstraint(
            ["tenant_id", "case_id"],
            [
                f"{SCHEMA}.collection_cases.tenant_id",
                f"{SCHEMA}.collection_cases.id",
            ],
            ondelete="CASCADE",
        ),
        UniqueConstraint(
            "tenant_id",
            "case_id",
            "policy_step_code",
            "attempt_ordinal",
            name="uq_collection_step_attempts_attempt",
        ),
        schema_table_args(SCHEMA),
    )

    case_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    policy_step_code: Mapped[str] = mapped_column(String(120), nullable=False)
    attempt_ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    request_kind: Mapped[str] = mapped_column(String(20), nullable=False)
    request_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    decision_fingerprint: Mapped[str] = mapped_column(String(255), nullable=False)
    attempted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class PaymentArrangement(Base, _TenantFact):
    __tablename__ = "payment_arrangements"
    __table_args__ = (
        _identity(__tablename__),
        UniqueConstraint(
            "tenant_id", "arrangement_ref", name="uq_payment_arrangements_ref"
        ),
        schema_table_args(SCHEMA),
    )

    arrangement_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    subject_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    minor_units: Mapped[int] = mapped_column(Integer, nullable=False)
    lifecycle: Mapped[str] = mapped_column(String(32), nullable=False)
    proposed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PaymentArrangementExposure(Base, _TenantFact):
    __tablename__ = "payment_arrangement_exposures"
    __table_args__ = (
        _identity(__tablename__),
        ForeignKeyConstraint(
            ["tenant_id", "arrangement_id"],
            [
                f"{SCHEMA}.payment_arrangements.tenant_id",
                f"{SCHEMA}.payment_arrangements.id",
            ],
            ondelete="CASCADE",
        ),
        UniqueConstraint(
            "tenant_id",
            "arrangement_id",
            "source_owner",
            "exposure_ref",
            name="uq_payment_arrangement_exposures_member",
        ),
        schema_table_args(SCHEMA),
    )

    arrangement_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    source_owner: Mapped[str] = mapped_column(String(120), nullable=False)
    exposure_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    source_version: Mapped[int] = mapped_column(Integer, nullable=False)
    position_fingerprint: Mapped[str] = mapped_column(String(255), nullable=False)
    subject_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    service_ref: Mapped[str | None] = mapped_column(String(255))
    admitted_amount: Mapped[Decimal] = mapped_column(MONEY_VALUE, nullable=False)


class PaymentArrangementInstallment(Base, _TenantFact):
    __tablename__ = "payment_arrangement_installments"
    __table_args__ = (
        _identity(__tablename__),
        ForeignKeyConstraint(
            ["tenant_id", "arrangement_id"],
            [
                f"{SCHEMA}.payment_arrangements.tenant_id",
                f"{SCHEMA}.payment_arrangements.id",
            ],
            ondelete="CASCADE",
        ),
        UniqueConstraint(
            "tenant_id",
            "arrangement_id",
            "ordinal",
            name="uq_payment_arrangement_installments_ordinal",
        ),
        schema_table_args(SCHEMA),
    )

    arrangement_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    amount: Mapped[Decimal] = mapped_column(MONEY_VALUE, nullable=False)
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class PaymentArrangementSettlementReceipt(Base, _TenantFact):
    __tablename__ = "payment_arrangement_settlement_receipts"
    __table_args__ = (
        _identity(__tablename__),
        ForeignKeyConstraint(
            ["tenant_id", "arrangement_id"],
            [
                f"{SCHEMA}.payment_arrangements.tenant_id",
                f"{SCHEMA}.payment_arrangements.id",
            ],
            ondelete="CASCADE",
        ),
        UniqueConstraint(
            "tenant_id",
            "arrangement_id",
            "source_owner",
            "settlement_ref",
            name="uq_payment_arrangement_settlement_receipts_source",
        ),
        schema_table_args(SCHEMA),
    )

    arrangement_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    source_owner: Mapped[str] = mapped_column(String(120), nullable=False)
    settlement_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    source_version: Mapped[int] = mapped_column(Integer, nullable=False)
    receipt_fingerprint: Mapped[str] = mapped_column(String(255), nullable=False)
    settled_amount: Mapped[Decimal] = mapped_column(MONEY_VALUE, nullable=False)
    settled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class CollectionGraceGrant(Base, _TenantFact):
    __tablename__ = "collection_grace_grants"
    __table_args__ = (
        _identity(__tablename__),
        ForeignKeyConstraint(
            ["tenant_id", "case_id"],
            [
                f"{SCHEMA}.collection_cases.tenant_id",
                f"{SCHEMA}.collection_cases.id",
            ],
            ondelete="CASCADE",
        ),
        schema_table_args(SCHEMA),
    )

    case_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    anchor_kind: Mapped[str] = mapped_column(String(50), nullable=False)
    anchor_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    duration_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    actor_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    reason_code: Mapped[str] = mapped_column(String(120), nullable=False)
    granted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class CollectionNoticeRequest(Base, _TenantFact):
    __tablename__ = "collection_notice_requests"
    __table_args__ = (
        _identity(__tablename__),
        ForeignKeyConstraint(
            ["tenant_id", "case_id"],
            [
                f"{SCHEMA}.collection_cases.tenant_id",
                f"{SCHEMA}.collection_cases.id",
            ],
        ),
        UniqueConstraint(
            "tenant_id", "idempotency_key", name="uq_collection_notice_requests_key"
        ),
        schema_table_args(SCHEMA),
    )

    case_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    request_fingerprint: Mapped[str] = mapped_column(String(255), nullable=False)
    policy_version_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    policy_step_code: Mapped[str] = mapped_column(String(120), nullable=False)
    attempt_ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    purpose_code: Mapped[str] = mapped_column(String(120), nullable=False)
    decision_evidence: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class CollectionNoticeReceipt(Base, _TenantFact):
    __tablename__ = "collection_notice_receipts"
    __table_args__ = (
        _identity(__tablename__),
        ForeignKeyConstraint(
            ["tenant_id", "request_id"],
            [
                f"{SCHEMA}.collection_notice_requests.tenant_id",
                f"{SCHEMA}.collection_notice_requests.id",
            ],
            ondelete="CASCADE",
        ),
        UniqueConstraint(
            "tenant_id", "request_id", name="uq_collection_notice_receipts_request"
        ),
        schema_table_args(SCHEMA),
    )

    request_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    receipt_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    owner_code: Mapped[str] = mapped_column(String(120), nullable=False)
    owner_receipt_id: Mapped[str] = mapped_column(String(255), nullable=False)
    receipt_fingerprint: Mapped[str] = mapped_column(String(255), nullable=False)
    receipt_evidence: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class CollectionActionRequest(Base, _TenantFact):
    __tablename__ = "collection_action_requests"
    __table_args__ = (
        _identity(__tablename__),
        ForeignKeyConstraint(
            ["tenant_id", "case_id"],
            [
                f"{SCHEMA}.collection_cases.tenant_id",
                f"{SCHEMA}.collection_cases.id",
            ],
        ),
        UniqueConstraint(
            "tenant_id", "idempotency_key", name="uq_collection_action_requests_key"
        ),
        schema_table_args(SCHEMA),
    )

    case_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    request_fingerprint: Mapped[str] = mapped_column(String(255), nullable=False)
    policy_version_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    policy_step_code: Mapped[str] = mapped_column(String(120), nullable=False)
    attempt_ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    action_code: Mapped[str] = mapped_column(String(120), nullable=False)
    effect_scope: Mapped[str] = mapped_column(String(120), nullable=False)
    decision_evidence: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class CollectionActionReceipt(Base, _TenantFact):
    __tablename__ = "collection_action_receipts"
    __table_args__ = (
        _identity(__tablename__),
        ForeignKeyConstraint(
            ["tenant_id", "request_id"],
            [
                f"{SCHEMA}.collection_action_requests.tenant_id",
                f"{SCHEMA}.collection_action_requests.id",
            ],
            ondelete="CASCADE",
        ),
        UniqueConstraint(
            "tenant_id", "request_id", name="uq_collection_action_receipts_request"
        ),
        schema_table_args(SCHEMA),
    )

    request_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    receipt_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    owner_code: Mapped[str] = mapped_column(String(120), nullable=False)
    owner_receipt_id: Mapped[str] = mapped_column(String(255), nullable=False)
    receipt_fingerprint: Mapped[str] = mapped_column(String(255), nullable=False)
    receipt_evidence: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class CollectionReconciliation(Base, _TenantFact):
    __tablename__ = "collection_reconciliations"
    __table_args__ = (
        _identity(__tablename__),
        ForeignKeyConstraint(
            ["tenant_id", "case_id"],
            [
                f"{SCHEMA}.collection_cases.tenant_id",
                f"{SCHEMA}.collection_cases.id",
            ],
            ondelete="CASCADE",
        ),
        UniqueConstraint(
            "tenant_id",
            "case_id",
            "source_version",
            name="uq_collection_reconciliations_version",
        ),
        schema_table_args(SCHEMA),
    )

    case_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    source_owner: Mapped[str] = mapped_column(String(120), nullable=False)
    exposure_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    source_version: Mapped[int] = mapped_column(Integer, nullable=False)
    source_fingerprint: Mapped[str] = mapped_column(String(255), nullable=False)
    rebuilt_fingerprint: Mapped[str] = mapped_column(String(255), nullable=False)
    outcome: Mapped[str] = mapped_column(String(32), nullable=False)
    reconciled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


__all__ = [
    "CollectionActionReceipt",
    "CollectionActionRequest",
    "CollectionCase",
    "CollectionCaseExposure",
    "CollectionCaseTransition",
    "CollectionGraceGrant",
    "CollectionNoticeReceipt",
    "CollectionNoticeRequest",
    "CollectionPolicy",
    "CollectionPolicyStep",
    "CollectionPolicyVersion",
    "CollectionReconciliation",
    "CollectionStepAttempt",
    "PaymentArrangement",
    "PaymentArrangementExposure",
    "PaymentArrangementInstallment",
    "PaymentArrangementSettlementReceipt",
    "PLATFORM_TABLES",
    "SCHEMA",
    "TENANT_TABLES",
]
