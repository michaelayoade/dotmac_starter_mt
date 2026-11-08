"""Opening variance ledger: closed contracts, reason policy and pure equations.

The opening never adjusts what it transfers to make totals agree. For every
partition (account x currency) and lane Billing proves, exactly and in integer
minor units, that ``source = transferred + sum(variance)``. Each variance is a
typed record with structural identity (never a metadata key), a closed reason
code, and a source row that exists in the frozen manifest. Nothing here opens a
session or posts anything; see ``opening_variance`` for the writers.

Basis semantics (design DV-1): ``ledger_lane`` is the authoritative money
control and the only basis that reaches the GL suspense account. The other
three bases are memo bases: they are recorded and closed but never post.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Final, Literal, Protocol
from uuid import UUID

from dotmac_kernel.cache import Scope
from dotmac_kernel.fingerprints import fingerprint_of
from dotmac_kernel.money import Money

from dotmac_billing._opening_values import OpeningCashValue
from dotmac_billing.opening_contracts import (
    OpeningEvidenceError,
    OpeningPartitionV1,
    OpeningSourceIdentityV1,
    _ClosedEvidence,
)

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

LANES: Final = ("receivable", "customer_credit", "prepaid_funding")
LEDGER_BASIS: Final = "ledger_lane"
MEMO_BASES: Final = ("document_residual", "stored_balance", "rebuild")
BASES: Final = (LEDGER_BASIS, *MEMO_BASES)
SOURCE_KINDS: Final = (
    "invoice",
    "settlement",
    "lane",
    "ledger_entry",
    "credit_note",
    "payment",
    "control_row",
    "rebuild_account",
    "partition_remainder",
)
#: Aggregate citations name a measures/control row, not a population row.
AGGREGATE_KINDS: Final = frozenset(
    {"lane", "control_row", "rebuild_account", "partition_remainder"}
)
SCOPE_LEVELS: Final = ("partition", "batch")
RESOLUTION_KINDS: Final = ("position_corrected", "recognised", "evidence_confirmed")
EVENT_KINDS: Final = (
    "recorded",
    "resolving",
    "partially_resolved",
    "resolved",
    "voided_by_import_reversal",
)
#: Cumulative flow fields of the lane controls: history rows, never an equation.
HISTORY_FIELDS: Final = (
    "prepaid_funding_consumed_minor",
    "written_off_total_minor",
    "refunded_total_minor",
    "adjustment_total_minor",
)
GL_TARGET_CLASSES: Final = ("bad_debt", "revenue_adjustment", "customer_liability")
#: Typed reasons a sealing operator may give for listing an UNMARKED partition.
OPERATIONAL_HOLD_REASONS: Final = frozenset({"target_not_pristine", "import_refused"})
ITEM_NOT_TRANSFERRED_CODES: Final = frozenset(
    {
        "v1_vat_evidence_missing",
        "original_evidence_missing",
        "status_residual_conflict",
        "invoice_provenance",
        "duplicate_identity",
        "source_after_cutoff",
        "currency_precision",
        "settlement_provenance",
    }
)
UNEXPLAINED: Final = "unexplained_difference"
REMAINDER_KIND: Final = "partition_remainder"
_DIGEST64 = re.compile(r"[0-9a-f]{64}")
_REBUILD_CLASS = re.compile(r"rebuild_[a-z0-9]+(?:_[a-z0-9]+)*")
_MAX_REF: Final = 255


@dataclass(frozen=True, slots=True)
class _Policy:
    bases: frozenset[str]
    kinds: frozenset[str]
    lanes: frozenset[str]
    level: str  # partition | batch


_L = frozenset(LANES)
_FIXED: Final[dict[str, _Policy]] = {
    "lane_document_difference": _Policy(
        frozenset({LEDGER_BASIS}),
        frozenset({"lane", "invoice", "ledger_entry"}),
        frozenset({"receivable"}),
        "partition",
    ),
    "stored_balance_difference": _Policy(
        frozenset({"stored_balance"}),
        frozenset({"invoice"}),
        frozenset({"receivable"}),
        "partition",
    ),
    "closed_invoice_residue": _Policy(
        frozenset({"document_residual", "stored_balance"}),
        frozenset({"invoice"}),
        frozenset({"receivable"}),
        "partition",
    ),
    "negative_residue": _Policy(
        frozenset({LEDGER_BASIS, "document_residual"}),
        frozenset({"invoice"}),
        frozenset({"receivable"}),
        "partition",
    ),
    "draft_allocation": _Policy(
        frozenset({LEDGER_BASIS}),
        frozenset({"settlement"}),
        frozenset({"customer_credit"}),
        "partition",
    ),
    "reseller_fold_unavailable": _Policy(
        frozenset({LEDGER_BASIS}), frozenset({"lane"}), _L, "partition"
    ),
    "unattributed_source": _Policy(
        frozenset({LEDGER_BASIS}), frozenset({"control_row", "payment"}), _L, "batch"
    ),
}
_ITEM = _Policy(
    frozenset({LEDGER_BASIS}),
    frozenset({"invoice"}),
    frozenset({"receivable"}),
    "partition",
)
_NOT_TRANSFERRED = _Policy(
    frozenset({LEDGER_BASIS}),
    frozenset({"lane"}),
    frozenset({"customer_credit", "prepaid_funding"}),
    "partition",
)
#: The rebuild is a net, credit-positive figure: only the receivable lane compares.
_REBUILD = _Policy(
    frozenset({"rebuild"}),
    frozenset({"rebuild_account"}),
    frozenset({"receivable"}),
    "partition",
)
_REMAINDER = _Policy(frozenset(BASES), frozenset({REMAINDER_KIND}), _L, "partition")


def reason_policy(reason: str) -> _Policy | None:
    """The closed policy for a reason code, or ``None`` when it is not a reason."""
    fixed = _FIXED.get(reason)
    if fixed is not None:
        return fixed
    if reason == UNEXPLAINED:
        return _REMAINDER
    family, _, suffix = reason.partition(":")
    if family == "item_not_transferred" and suffix in ITEM_NOT_TRANSFERRED_CODES:
        return _ITEM
    if family == "lane_not_transferred" and suffix in {
        "customer_credit",
        "prepaid_funding",
    }:
        return _NOT_TRANSFERRED
    if ":" not in reason and _REBUILD_CLASS.fullmatch(reason):
        return _REBUILD
    return None


def is_customer_adverse(lane: str, amount_minor: int) -> bool:
    """DV-3: Starter would collect more, or hold less credit/funding, than source."""
    if lane == "receivable":
        return amount_minor < 0
    return amount_minor > 0


@dataclass(frozen=True, slots=True)
class OpeningVarianceV1(_ClosedEvidence):
    """One variance. ``amount_minor > 0``: the source measure exceeds Starter."""

    level: str
    partition: OpeningPartitionV1 | None
    currency: str
    precision: int
    lane: str
    basis: str
    reason_code: str
    source: OpeningSourceIdentityV1
    amount_minor: int
    evidence_digest: str
    rules_digest: str

    def __post_init__(self) -> None:
        _ClosedEvidence.__post_init__(self)
        policy = reason_policy(self.reason_code)
        if (
            policy is None
            or self.level not in SCOPE_LEVELS
            or self.lane not in LANES
            or self.basis not in BASES
            or self.source.kind not in SOURCE_KINDS
        ):
            raise OpeningEvidenceError("variance reason, level, lane or basis invalid")
        if (
            self.level != policy.level
            or self.basis not in policy.bases
            or self.lane not in policy.lanes
            or self.source.kind not in policy.kinds
        ):
            raise OpeningEvidenceError("variance source kind not allowed for reason")
        if self.basis == "rebuild" and self.lane != "receivable":
            raise OpeningEvidenceError("the rebuild basis is receivable-lane only")
        if self.reason_code.startswith("lane_not_transferred:") and (
            self.reason_code.partition(":")[2] != self.lane
        ):
            raise OpeningEvidenceError("lane_not_transferred names another lane")
        if (self.level == "batch") != (self.partition is None):
            raise OpeningEvidenceError("only a batch variance has no partition")
        if self.partition is not None and (
            self.partition.currency != self.currency
            or self.partition.precision != self.precision
        ):
            raise OpeningEvidenceError("variance money identity differs from partition")
        if self.amount_minor == 0:
            raise OpeningEvidenceError("a variance is never zero")
        if (
            len(self.source.key) > _MAX_REF
            or _DIGEST64.fullmatch(self.evidence_digest) is None
            or _DIGEST64.fullmatch(self.rules_digest) is None
            or not 0 <= self.precision <= 18
        ):
            raise OpeningEvidenceError("variance identity or digest invalid")

    @property
    def account_id(self) -> UUID | None:
        return None if self.partition is None else self.partition.account_id

    @property
    def identity(self) -> tuple[object, ...]:
        """Structural identity (typed columns; the window is implicit)."""
        return (
            self.account_id,
            self.currency,
            self.lane,
            self.basis,
            self.reason_code,
            self.source.kind,
            self.source.key,
        )


@dataclass(frozen=True, slots=True)
class OpeningPartitionMeasuresV1(_ClosedEvidence):
    """The memo-basis figures and the count control for one partition.

    The ledger-lane figures are the partition's ``OpeningLaneControlsV1``; the
    three memo bases and the census row counts live here. ``rebuild_minor`` is
    ``None`` only with a ``rebuild_gap_reason``: that opens a control gap
    (E4 cannot be formed) and never blocks the account.
    """

    partition: OpeningPartitionV1
    document_residual_minor: int
    stored_balance_minor: int
    rebuild_minor: int | None
    rebuild_gap_reason: str | None
    population_rows: int
    transferred_rows: int
    carried_rows: int
    excluded_rows: int
    archived_rows: int
    non_carryable_rows: int

    def __post_init__(self) -> None:
        _ClosedEvidence.__post_init__(self)
        if (self.rebuild_minor is None) == (self.rebuild_gap_reason is None):
            raise OpeningEvidenceError(
                "a rebuild figure xor a rebuild gap reason is required"
            )
        if min(
            self.population_rows,
            self.transferred_rows,
            self.carried_rows,
            self.excluded_rows,
            self.archived_rows,
            self.non_carryable_rows,
        ) < 0 or self.population_rows != (
            self.transferred_rows
            + self.carried_rows
            + self.excluded_rows
            + self.archived_rows
            + self.non_carryable_rows
        ):
            raise OpeningEvidenceError("every census row lands in exactly one bucket")


@dataclass(frozen=True, slots=True)
class OpeningBatchControlV1(_ClosedEvidence):
    """Source figure of rows that belong to no partition (batch scope)."""

    currency: str
    precision: int
    lane: str
    basis: str
    source_minor: int
    rows: tuple[OpeningSourceIdentityV1, ...]


@dataclass(frozen=True, slots=True)
class OpeningOperationalHoldV1(_ClosedEvidence):
    """Typed reason for listing an UNMARKED partition on the sealed hold list."""

    account_id: UUID
    reason_code: str

    def __post_init__(self) -> None:
        _ClosedEvidence.__post_init__(self)
        if self.reason_code not in OPERATIONAL_HOLD_REASONS:
            raise OpeningEvidenceError(
                "operational hold reason is not in the closed set"
            )


# --------------------------------------------------------------------------
# Resolution commands (maker-checker arrives through a trusted port)
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class OpeningVarianceResolutionRefV1(_ClosedEvidence):
    """Carried by a governed primitive (credit note, grant).

    The primitive's own transaction appends the resolution events, but only
    after the trusted approvals port vouches for exactly this variance, kind,
    amount, the primitive's request fingerprint and maker. Billing never trusts
    a caller-supplied approver or actor.
    """

    variance_id: UUID
    amount_minor: int
    approval_request_id: UUID
    maker_ref: str

    def __post_init__(self) -> None:
        _ClosedEvidence.__post_init__(self)
        if (
            self.amount_minor == 0
            or not self.maker_ref.strip()
            or len(self.maker_ref) > _MAX_REF
        ):
            raise OpeningEvidenceError("variance resolution reference invalid")


@dataclass(frozen=True, slots=True)
class RecogniseOpeningVarianceV1(_ClosedEvidence):
    """W-V2 (DV-4): recognise a ledger-lane variance with no customer effect."""

    scope: Scope
    variance_id: UUID
    amount_minor: int
    gl_target_class: str
    approval_request_id: UUID
    maker_ref: str
    idempotency_key: str
    occurred_at: datetime

    def __post_init__(self) -> None:
        _ClosedEvidence.__post_init__(self)
        if (
            self.amount_minor == 0
            or self.gl_target_class not in GL_TARGET_CLASSES
            or max(len(self.maker_ref), len(self.idempotency_key)) > _MAX_REF
        ):
            raise OpeningEvidenceError("recognise command invalid")


@dataclass(frozen=True, slots=True)
class ConfirmOpeningVarianceEvidenceV1(_ClosedEvidence):
    """Memo bases only: record the evidence showing the authoritative measure stands."""

    scope: Scope
    variance_id: UUID
    amount_minor: int
    evidence_ref: str
    approval_request_id: UUID
    maker_ref: str
    idempotency_key: str
    occurred_at: datetime

    def __post_init__(self) -> None:
        _ClosedEvidence.__post_init__(self)
        if (
            self.amount_minor == 0
            or max(
                len(self.evidence_ref), len(self.maker_ref), len(self.idempotency_key)
            )
            > _MAX_REF
        ):
            raise OpeningEvidenceError("confirm-evidence command invalid")


@dataclass(frozen=True, slots=True)
class OpeningVarianceApprovalV1(_ClosedEvidence):
    """What Approvals vouched for, observed under the request's SHARE lock."""

    request_id: UUID
    subject_type: str
    subject_id: str
    content_digest: str
    requester_ref: str
    approver_refs: tuple[str, ...]


VARIANCE_APPROVAL_SUBJECT_TYPE: Final = "billing.opening.variance.resolve.v1"


class OpeningVarianceApprovalPort(Protocol):
    """Trusted dependency over the Approvals public contract; never caller data.

    The adapter holds the approval request FOR SHARE until the caller's
    transaction ends. Return ``None`` (or raise ``OpeningVerificationUnavailable``)
    when the request does not stand as approved for exactly this subject and
    digest. Billing owns no approval state.
    """

    def hold_approval(
        self,
        db: Session,
        *,
        scope: Scope,
        request_id: UUID,
        subject_type: str,
        subject_id: str,
        content_digest: str,
    ) -> OpeningVarianceApprovalV1 | None: ...


def variance_approval_subject_id(variance_id: UUID) -> str:
    return str(variance_id)


def variance_resolution_content_digest(
    *,
    kind: str,
    variance_id: UUID,
    amount_minor: int,
    detail: str,
    maker_ref: str,
) -> str:
    """Digest the approver approves: one variance, one kind, one amount."""
    return fingerprint_of(
        {
            "domain": "billing.opening.variance.resolve.v1",
            "kind": kind,
            "variance_id": str(variance_id),
            "amount_minor": amount_minor,
            "detail": detail,
            "maker_ref": maker_ref,
        }
    )


# --------------------------------------------------------------------------
# AccountingFactV6: ledger-lane variance facts (distinct family; V1-V5 unchanged)
# --------------------------------------------------------------------------

ACCOUNTING_FACT_V6_CONTRACT: Final = "billing.accounting.fact.v6"
VARIANCE_FACT_KINDS: Final = (
    "opening_variance_recorded",
    "opening_variance_voided",
    "opening_variance_recognised",
    "opening_variance_position_corrected",
)


@dataclass(frozen=True, slots=True)
class OpeningVarianceFactBlockV1(OpeningCashValue):
    """The variance a V6 fact is about. ``amount`` is signed: the variance for a
    recorded or voided fact, the resolution amount for the other two."""

    variance_id: UUID
    window_id: UUID
    event_id: UUID
    event_seq: int
    lane: Literal["receivable", "customer_credit", "prepaid_funding"]
    basis: Literal["ledger_lane"]
    reason_code: str
    source_kind: str
    source_ref: str
    amount: Money
    customer_adverse: bool
    import_receipt_id: UUID
    evidence_digest: str
    rules_digest: str

    def __post_init__(self) -> None:
        OpeningCashValue.__post_init__(self)
        policy = reason_policy(self.reason_code)
        if (
            policy is None
            or policy.level != "partition"
            or LEDGER_BASIS not in policy.bases
            or self.lane not in policy.lanes
            or self.source_kind not in policy.kinds
            or self.source_kind not in SOURCE_KINDS
            or self.event_seq < 1
            or self.amount.amount == 0
            or self.customer_adverse
            != is_customer_adverse(self.lane, 1 if self.amount.amount > 0 else -1)
            or _DIGEST64.fullmatch(self.evidence_digest) is None
            or _DIGEST64.fullmatch(self.rules_digest) is None
            or len(self.source_ref) > _MAX_REF
        ):
            raise OpeningEvidenceError("variance fact block invalid")


@dataclass(frozen=True, slots=True)
class OpeningVarianceResolutionFactV1(OpeningCashValue):
    resolution_kind: Literal["recognised", "position_corrected"]
    resolved_state: Literal["resolved", "partially_resolved"]
    gl_target_class: (
        Literal["bad_debt", "revenue_adjustment", "customer_liability"] | None
    )
    corrected_by_posting_group_id: UUID | None
    approval_request_id: UUID
    maker_ref: str
    approver_refs: tuple[str, ...]

    def __post_init__(self) -> None:
        OpeningCashValue.__post_init__(self)
        if (
            len(set(self.approver_refs)) != len(self.approver_refs)
            or not self.approver_refs
            or self.maker_ref in self.approver_refs
            or (self.resolution_kind == "recognised")
            != (self.gl_target_class is not None)
            or (self.resolution_kind == "position_corrected")
            != (self.corrected_by_posting_group_id is not None)
        ):
            raise OpeningEvidenceError("variance resolution fact invalid")


_RESOLUTION_OF_KIND: Final = {
    "opening_variance_recognised": "recognised",
    "opening_variance_position_corrected": "position_corrected",
}


@dataclass(frozen=True, slots=True)
class AccountingFactV6(OpeningCashValue):
    """``billing.accounting.fact.v6``: one ledger-lane opening-variance fact on an
    effect-free posting group whose kind equals ``effect_kind``."""

    scope: Scope
    source_system: str
    fact_id: UUID
    fact_version: Literal[6]
    billing_account_id: UUID
    posting_group_id: UUID
    source_ref: str
    source_authority: Literal["internal"]
    effect_kind: Literal[
        "opening_variance_recorded",
        "opening_variance_voided",
        "opening_variance_recognised",
        "opening_variance_position_corrected",
    ]
    occurred_at: datetime
    committed_at: datetime
    variance: OpeningVarianceFactBlockV1
    resolution: OpeningVarianceResolutionFactV1 | None
    reverses_fact_id: UUID | None

    def __post_init__(self) -> None:
        OpeningCashValue.__post_init__(self)
        expected = _RESOLUTION_OF_KIND.get(self.effect_kind)
        gl = None if self.resolution is None else self.resolution.gl_target_class
        lane_ok = True
        if gl is not None:
            lane_ok = (
                gl in ("bad_debt", "revenue_adjustment")
                if self.variance.lane == "receivable"
                else gl == "customer_liability"
            )
        if (
            self.source_system != "dotmac-billing"
            or self.source_ref != f"opening-variance-event:{self.variance.event_id}"
            or (self.resolution is None) != (expected is None)
            or (
                self.resolution is not None
                and self.resolution.resolution_kind != expected
            )
            or not lane_ok
            or (self.effect_kind == "opening_variance_voided")
            != (self.reverses_fact_id is not None)
            or (
                self.effect_kind == "opening_variance_recognised"
                and self.variance.customer_adverse
            )
        ):
            raise OpeningEvidenceError("variance fact invalid")


# --------------------------------------------------------------------------
# Read models
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class OpeningVarianceRecordV1(_ClosedEvidence):
    """A stored variance (typed columns) with its derived lifecycle state."""

    variance_id: UUID
    window_id: UUID
    level: str
    account_id: UUID | None
    currency: str
    precision: int
    lane: str
    basis: str
    reason_code: str
    source_kind: str
    source_ref: str
    import_receipt_id: UUID | None
    amount_minor: int
    evidence_digest: str
    rules_digest: str
    state: str  # recorded | resolving | partially_resolved | resolved | voided
    resolved_minor: int
    customer_adverse: bool


@dataclass(frozen=True, slots=True)
class OpeningControlRowV1(_ClosedEvidence):
    """One closure row: ``difference = source - transferred - variance`` (must be 0)."""

    lane: str
    basis: str
    currency: str
    source_minor: int
    transferred_minor: int
    variance_minor: int
    difference_minor: int
    variance_count: int


@dataclass(frozen=True, slots=True)
class OpeningReasonTotalV1(_ClosedEvidence):
    reason_code: str
    count: int
    total_minor: int


@dataclass(frozen=True, slots=True)
class OpeningHistoryRowV1(_ClosedEvidence):
    """A cumulative flow field: history, never an equation or a blocker."""

    field: str
    currency: str
    total_minor: int
    nonzero_accounts: int


@dataclass(frozen=True, slots=True)
class OpeningHoldRowV1(_ClosedEvidence):
    account_id: str
    reason_code: str


@dataclass(frozen=True, slots=True)
class OpeningControlReportV1(_ClosedEvidence):
    """Window-level closure. The seal refuses unless every row is zero."""

    rows: tuple[OpeningControlRowV1, ...]
    reason_totals: tuple[OpeningReasonTotalV1, ...]
    customer_adverse_count: int
    customer_adverse_minor: int
    operational_holds: tuple[OpeningHoldRowV1, ...]
    history: tuple[OpeningHistoryRowV1, ...]
    closed: bool
    digest: str


# --------------------------------------------------------------------------
# Pure equations (used by the validator, the report and the tests)
# --------------------------------------------------------------------------


def variance_set_digest(variances: tuple[OpeningVarianceV1, ...]) -> str:
    """Order-independent digest of a variance set (identity, amount, digests)."""
    rows = sorted(
        (
            "" if v.account_id is None else str(v.account_id),
            v.currency,
            v.lane,
            v.basis,
            v.reason_code,
            v.source.kind,
            v.source.key,
            v.amount_minor,
            v.evidence_digest,
            v.rules_digest,
        )
        for v in variances
    )
    return fingerprint_of(
        {"domain": "billing.opening.variance.set.v1", "rows": [list(r) for r in rows]}
    )


def sum_by_basis(
    variances: tuple[OpeningVarianceV1, ...],
) -> dict[tuple[object, str, str], int]:
    """Signed sums keyed by (account_id | None, lane, basis)."""
    totals: dict[tuple[object, str, str], int] = defaultdict(int)
    for v in variances:
        totals[(v.account_id, v.lane, v.basis)] += v.amount_minor
    return dict(totals)


__all__ = [
    "ACCOUNTING_FACT_V6_CONTRACT",
    "AGGREGATE_KINDS",
    "BASES",
    "EVENT_KINDS",
    "GL_TARGET_CLASSES",
    "HISTORY_FIELDS",
    "ITEM_NOT_TRANSFERRED_CODES",
    "LANES",
    "LEDGER_BASIS",
    "MEMO_BASES",
    "OPERATIONAL_HOLD_REASONS",
    "REMAINDER_KIND",
    "RESOLUTION_KINDS",
    "SCOPE_LEVELS",
    "SOURCE_KINDS",
    "UNEXPLAINED",
    "VARIANCE_APPROVAL_SUBJECT_TYPE",
    "VARIANCE_FACT_KINDS",
    "AccountingFactV6",
    "OpeningVarianceFactBlockV1",
    "OpeningVarianceResolutionFactV1",
    "ConfirmOpeningVarianceEvidenceV1",
    "OpeningBatchControlV1",
    "OpeningControlReportV1",
    "OpeningControlRowV1",
    "OpeningHistoryRowV1",
    "OpeningHoldRowV1",
    "OpeningOperationalHoldV1",
    "OpeningPartitionMeasuresV1",
    "OpeningReasonTotalV1",
    "OpeningVarianceApprovalPort",
    "OpeningVarianceApprovalV1",
    "OpeningVarianceRecordV1",
    "OpeningVarianceResolutionRefV1",
    "OpeningVarianceV1",
    "RecogniseOpeningVarianceV1",
    "is_customer_adverse",
    "reason_policy",
    "sum_by_basis",
    "variance_approval_subject_id",
    "variance_resolution_content_digest",
    "variance_set_digest",
]
