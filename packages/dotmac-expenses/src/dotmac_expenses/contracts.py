"""Typed public contract for the Expenses owner."""

from __future__ import annotations

from dataclasses import dataclass, field, fields
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Protocol
from uuid import UUID

from dotmac_kernel.cache import TenantScope
from dotmac_kernel.semantic_encoding import (
    ABSENT,
    digest_of,
    encode_fields,
    encode_ordered,
)
from sqlalchemy.orm import Session


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
    """Base typed domain refusal.

    ``code`` is an optional machine-readable refusal code (a2, additive). Every
    refusal introduced in a2 sets it and also carries it in parentheses in the
    message; refusals raised by a1 are unchanged and leave it ``None``.
    """

    code: str | None

    def __init__(self, *args: object, code: str | None = None) -> None:
        super().__init__(*args)
        self.code = code


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


# --- a2: claim liability lifecycle -------------------------------------------------


class ClaimLiabilityStatus(StrEnum):
    """Derived from the amounts after every transition; never set directly."""

    OPEN = "open"
    PARTIALLY_SETTLED = "partially_settled"
    SETTLED = "settled"
    REVERSED = "reversed"


class LiabilityOrigin(StrEnum):
    ACCEPTED = "accepted"
    HISTORICAL = "historical"


class ClaimInputVat(StrEnum):
    """The ``expenses.claim_input_vat`` tenant setting (default ``none``).

    ``immediate`` recovers input VAT at acceptance to the immediate account
    (never the deferral account: the employee paid the merchant at purchase).
    The value is read by the assembly at acceptance and frozen on the liability.
    """

    NONE = "none"
    IMMEDIATE = "immediate"


CLAIM_INPUT_VAT_DOMAIN = "expenses"
CLAIM_INPUT_VAT_KEY = "claim_input_vat"
CLAIM_INPUT_VAT_SETTING = f"{CLAIM_INPUT_VAT_DOMAIN}.{CLAIM_INPUT_VAT_KEY}"
CLAIM_INPUT_VAT_DEFAULT = ClaimInputVat.NONE
# The only account input VAT may reach under ``immediate``.
INPUT_VAT_IMMEDIATE_ACCOUNT_REF = "1440"


class RefusalCode(StrEnum):
    """Machine-readable ``ExpensesError.code`` values introduced in a2.

    The last three are raised by the ERP historical-admission and reclass
    adapters, never by this package; they are declared here so the vocabulary
    has one owner.
    """

    CLAIM_NOT_ELIGIBLE = "claim_not_eligible"
    CLAIM_ALREADY_ACCEPTED = "claim_already_accepted"
    ACCEPTANCE_LINES_MISMATCH = "acceptance_lines_mismatch"
    ACCEPTANCE_ACCOUNT_CONFLICT = "acceptance_account_conflict"
    CLAIM_VAT_DISABLED = "claim_vat_disabled"
    CLAIM_VAT_ACCOUNT_NOT_IMMEDIATE = "claim_vat_account_not_immediate"
    LIABILITY_REVERSED = "liability_reversed"
    LIABILITY_NOT_OPEN = "liability_not_open"
    SETTLEMENT_EXCEEDS_OUTSTANDING = "settlement_exceeds_outstanding"
    SETTLEMENT_NOT_IN_LEDGER = "settlement_not_in_ledger"
    ADJUSTMENT_EXCEEDS_OUTSTANDING = "adjustment_exceeds_outstanding"
    OFFSET_IS_LIABILITY_ACCOUNT = "offset_is_liability_account"
    CLAIM_HAS_LIABILITY = "claim_has_liability"
    HISTORICAL_LIABILITY_HAS_NO_CONSEQUENCE = "historical_liability_has_no_consequence"
    HISTORICAL_CLAIM_NOT_OPEN = "historical_claim_not_open"
    LEGACY_GL_DOES_NOT_HOLD_CLAIM = "legacy_gl_does_not_hold_claim"
    CLAIM_WITHOUT_EMPLOYEE = "claim_without_employee"
    IDEMPOTENCY_KEY_REUSED = "idempotency_key_reused"
    DISTINCT_ACTOR_REQUIRED = "distinct_actor_required"
    # ERP side (reserved; not raised here).
    LEGACY_CLAIM_GL_UNATTRIBUTABLE = "legacy_claim_gl_unattributable"
    CLAIM_ADVANCE_UNPROVEN = "claim_advance_unproven"
    HISTORICAL_CLAIM_WINDOW_CLOSED = "historical_claim_window_closed"


DIMENSION_REFS = tuple[tuple[str, str], ...]


@dataclass(frozen=True, slots=True)
class AcceptedClaimLine:
    """One claim line as accepted: explicit account refs, frozen in rows.

    ``tax_amount`` is the recoverable input VAT inside the approved amount
    (net = approved - tax). It is an optional fact; whether it may be non-zero
    is the frozen ``ClaimInputVat`` mode of the acceptance.
    """

    claim_line_id: UUID
    expense_account_ref: str
    dimension_refs: DIMENSION_REFS = ()
    tax_account_ref: str | None = None
    tax_amount: Decimal = Decimal("0")


@dataclass(frozen=True, slots=True)
class AcceptClaimForReimbursement:
    claim_id: UUID
    liability_account_ref: str
    acceptance_reference: str
    accepted_by: str
    lines: tuple[AcceptedClaimLine, ...]
    input_vat_mode: ClaimInputVat = CLAIM_INPUT_VAT_DEFAULT


@dataclass(frozen=True, slots=True)
class HistoricalClaimSettlement:
    """One opaque piece of reimbursement evidence before admission.

    The verifier defines what ``evidence_ref`` names (a legacy reimbursement
    journal, a reviewed opening row, ...); the owner only checks the amounts.
    """

    evidence_ref: str
    amount: Decimal
    evidence_fingerprint: str


@dataclass(frozen=True, slots=True)
class HistoricalClaimLiabilityCommand:
    source_owner: str
    source_claim_id: str
    claim_id: UUID
    source_snapshot_sha256: str
    evidence_fingerprint: str
    evidence_reference: str
    admitted_by: str


def _historical_scalar(value: object) -> object:
    if value is None:
        return ABSENT
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, date) and not isinstance(value, datetime):
        return value.isoformat()
    return value


@dataclass(frozen=True, slots=True)
class HistoricalClaimLiabilityEvidence:
    """Verifier proof for a legacy posted, partly or wholly unreimbursed claim.

    ``carried_control_amount`` is the legacy payable-account net for this
    claim's journals, credit-positive; it must equal ``residual`` exactly.
    ``carried_account_ref`` names the legacy family account where that net sits
    now (input to the assembly's one reclass journal) and
    ``carried_evidence_ref`` names where the figure was read (basis-neutral).
    ``liability_account_ref`` is the NEW claims-payable account.
    """

    tenant_id: UUID
    source_owner: str
    source_claim_id: str
    source_snapshot_sha256: str
    claim_id: UUID
    claimant_party_id: UUID | None
    currency_code: str
    net_payable_amount: Decimal
    settled_before_admission: Decimal
    residual: Decimal
    settlements: tuple[HistoricalClaimSettlement, ...]
    liability_account_ref: str
    carried_control_amount: Decimal
    carried_account_ref: str
    carried_evidence_ref: str
    source_journal_ref: str
    original_posted_at: datetime
    accounting_evidence_fingerprint: str
    facts_fingerprint: str

    def fingerprint(self) -> str:
        values: list[tuple[str, object]] = []
        for field_ in fields(self):
            value = getattr(self, field_.name)
            if field_.name == "settlements":
                value = encode_ordered(
                    encode_fields(
                        [
                            (f.name, _historical_scalar(getattr(item, f.name)))
                            for f in fields(item)
                        ]
                    )
                    for item in value
                )
            values.append((field_.name, _historical_scalar(value)))
        return digest_of(
            "expenses.historical-claim-liability.v1", encode_fields(values)
        )


class HistoricalClaimLiabilityVerifier(Protocol):
    def verify(
        self,
        *,
        scope: TenantScope,
        command: HistoricalClaimLiabilityCommand,
        destination: Session,
    ) -> HistoricalClaimLiabilityEvidence: ...


@dataclass(frozen=True, slots=True)
class ClaimAccountingReceiptInput:
    """The assembly's claim that one consequence was posted.

    The owner rebuilds the consequence from its own rows plus the two inputs the
    rows do not hold (the caller-supplied cash GL and posting date, where the
    kind needs them) and compares the fingerprint.
    """

    document_kind: str
    document_id: UUID
    consequence_fingerprint: str
    accounting_reference: str
    accounting_evidence_fingerprint: str
    bank_account_ref: str | None = None
    posting_date: date | None = None


def refuse(
    exc_type: type[ExpensesError], message: str, code: RefusalCode
) -> ExpensesError:
    """Build an a2 refusal: the code is an attribute and also in the message."""
    return exc_type(f"{message} ({code.value})", code=code.value)


__all__ = [
    "CLAIM_INPUT_VAT_DEFAULT",
    "CLAIM_INPUT_VAT_DOMAIN",
    "CLAIM_INPUT_VAT_KEY",
    "CLAIM_INPUT_VAT_SETTING",
    "DIMENSION_REFS",
    "INPUT_VAT_IMMEDIATE_ACCOUNT_REF",
    "AcceptClaimForReimbursement",
    "AcceptedClaimLine",
    "ClaimAccountingReceiptInput",
    "ClaimInputVat",
    "ClaimLiabilityStatus",
    "HistoricalClaimLiabilityCommand",
    "HistoricalClaimLiabilityEvidence",
    "HistoricalClaimLiabilityVerifier",
    "HistoricalClaimSettlement",
    "LiabilityOrigin",
    "RefusalCode",
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
