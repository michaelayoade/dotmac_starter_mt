"""Typed public contract for the Expenses owner."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from enum import StrEnum
from uuid import UUID


class RequestStatus(StrEnum):
    DRAFT = "draft"
    SUBMITTED = "submitted"
    APPROVED = "approved"
    REJECTED = "rejected"
    CANCELLED = "cancelled"
    CONVERTED = "converted"


class ClaimStatus(StrEnum):
    DRAFT = "draft"
    SUBMITTED = "submitted"
    APPROVED = "approved"
    REJECTED = "rejected"
    CANCELLED = "cancelled"
    APPROVAL_WITHDRAWN = "approval_withdrawn"


class PolicyStatus(StrEnum):
    DRAFT = "draft"
    PUBLISHED = "published"
    RETIRED = "retired"


class PolicyTarget(StrEnum):
    REQUEST = "request"
    CLAIM = "claim"


class LimitPeriod(StrEnum):
    TRANSACTION = "transaction"
    DAY = "day"
    WEEK = "week"
    MONTH = "month"
    QUARTER = "quarter"
    YEAR = "year"


class LimitAction(StrEnum):
    WARN = "warn"
    REQUIRE_APPROVAL = "require_approval"
    BLOCK = "block"


class EvaluationResult(StrEnum):
    PASSED = "passed"
    WARNING = "warning"
    APPROVAL_REQUIRED = "approval_required"
    BLOCKED = "blocked"


class Decision(StrEnum):
    APPROVED = "approved"
    REJECTED = "rejected"


class ReceiptVerificationStatus(StrEnum):
    PENDING = "pending"
    VERIFIED = "verified"
    REJECTED = "rejected"


class ExpensesError(RuntimeError):
    """Base typed domain refusal."""


class NotFound(ExpensesError):
    pass


class Conflict(ExpensesError):
    pass


class InvalidLifecycle(ExpensesError):
    pass


class InvalidCommand(ExpensesError):
    pass


@dataclass(frozen=True, slots=True)
class CreateCategory:
    code: str
    name: str
    description: str | None = None
    requires_receipt: bool = False
    receipt_threshold: Decimal | None = None
    max_amount_per_line: Decimal | None = None
    max_amount_per_claim: Decimal | None = None
    is_active: bool = True


@dataclass(frozen=True, slots=True)
class CreatePolicyRevision:
    code: str
    name: str
    version: int
    currency_code: str
    effective_from: date
    effective_to: date | None = None
    description: str | None = None


@dataclass(frozen=True, slots=True)
class AddPolicyRule:
    policy_id: UUID
    code: str
    name: str
    target: PolicyTarget
    period: LimitPeriod
    action: LimitAction
    limit_amount: Decimal
    category_id: UUID | None = None
    applicability_key: str | None = None
    priority: int = 100
    description: str | None = None


@dataclass(frozen=True, slots=True)
class PolicyContext:
    """Caller-resolved applicability facts; Expenses owns no People vocabulary."""

    applicability_keys: frozenset[str] = field(default_factory=frozenset)


@dataclass(frozen=True, slots=True)
class RequestLineDraft:
    category_id: UUID
    description: str
    amount: Decimal
    expected_on: date
    vendor_name: str | None = None
    notes: str | None = None


@dataclass(frozen=True, slots=True)
class CreateRequest:
    reference: str
    requester_party_id: UUID
    purpose: str
    currency_code: str
    needed_by: date
    lines: tuple[RequestLineDraft, ...]
    notes: str | None = None


@dataclass(frozen=True, slots=True)
class ReviseRequest:
    purpose: str
    needed_by: date
    lines: tuple[RequestLineDraft, ...]
    notes: str | None = None


@dataclass(frozen=True, slots=True)
class ClaimLineDraft:
    category_id: UUID
    description: str
    claimed_amount: Decimal
    expense_date: date
    vendor_name: str | None = None
    notes: str | None = None


@dataclass(frozen=True, slots=True)
class CreateClaim:
    reference: str
    claimant_party_id: UUID
    purpose: str
    claim_date: date
    currency_code: str
    lines: tuple[ClaimLineDraft, ...]
    expense_period_start: date | None = None
    expense_period_end: date | None = None
    request_id: UUID | None = None
    notes: str | None = None


@dataclass(frozen=True, slots=True)
class ReviseClaim:
    purpose: str
    claim_date: date
    lines: tuple[ClaimLineDraft, ...]
    expense_period_start: date | None = None
    expense_period_end: date | None = None
    notes: str | None = None


@dataclass(frozen=True, slots=True)
class CreateClaimFromRequest:
    request_id: UUID
    reference: str
    claim_date: date


@dataclass(frozen=True, slots=True)
class AttachReceipt:
    claim_line_id: UUID
    file_id: UUID
    original_filename: str
    media_type: str
    size_bytes: int
    sha256: str
    receipt_number: str | None = None
    merchant_name: str | None = None
    issued_on: date | None = None
    gross_amount: Decimal | None = None
    currency_code: str | None = None


@dataclass(frozen=True, slots=True)
class ApprovedLineAmount:
    line_id: UUID
    amount: Decimal


@dataclass(frozen=True, slots=True)
class ApplyDecision:
    decision: Decision
    decision_reference: str
    reason: str | None = None
    approved_lines: tuple[ApprovedLineAmount, ...] = ()


@dataclass(frozen=True, slots=True)
class EvaluationFinding:
    evaluation_id: UUID
    result: EvaluationResult
    reason_code: str
    actual_amount: Decimal
    limit_amount: Decimal | None
    rule_id: UUID | None
    line_id: UUID | None


@dataclass(frozen=True, slots=True)
class SubmissionOutcome:
    subject_id: UUID
    status: str
    evaluation_batch_id: UUID
    blocked: bool
    approval_required: bool
    evaluations: tuple[EvaluationFinding, ...]


@dataclass(frozen=True, slots=True)
class ReimbursementEligibility:
    claim_id: UUID
    eligible: bool
    reasons: tuple[str, ...]
    approved_amount: Decimal
    currency_code: str
    decision_reference: str | None
    evaluation_batch_id: UUID | None


__all__ = [
    "AddPolicyRule",
    "ApplyDecision",
    "ApprovedLineAmount",
    "AttachReceipt",
    "ClaimLineDraft",
    "ClaimStatus",
    "Conflict",
    "CreateCategory",
    "CreateClaim",
    "CreateClaimFromRequest",
    "CreatePolicyRevision",
    "CreateRequest",
    "Decision",
    "EvaluationFinding",
    "EvaluationResult",
    "ExpensesError",
    "InvalidCommand",
    "InvalidLifecycle",
    "LimitAction",
    "LimitPeriod",
    "NotFound",
    "PolicyContext",
    "PolicyStatus",
    "PolicyTarget",
    "ReceiptVerificationStatus",
    "ReimbursementEligibility",
    "ReviseClaim",
    "ReviseRequest",
    "RequestLineDraft",
    "RequestStatus",
    "SubmissionOutcome",
]
