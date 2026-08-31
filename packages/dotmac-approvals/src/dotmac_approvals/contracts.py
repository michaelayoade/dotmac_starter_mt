"""The approval vocabulary — typed, frozen, and free of persistence.

Every type here is a value: frozen, hashable where it can be, and importing no
ORM, no session and no plane. That is what lets `policy.py` decide an approval
without touching a database, and what lets the tenant and platform services
share one set of rules rather than two implementations that drift.

Two things this vocabulary deliberately does NOT contain, both ruled out by
ADR-0026:

- **Money, currency, FX rate or conversion date.** Threshold routing is the
  domain's (§ 7a). A caller arrives having already resolved which policy
  revision applies; the module never derives one, so it never needs to compare
  an amount. An approval module that knew about currency would be one schema
  change away from owning pricing.
- **A subject vocabulary.** `subject_type` is an opaque declared string owned by
  the consuming module's manifest (§ 4). This package ships no enum of document
  types, and adding one would be making a product decision for every adopter.

`policy_code`, by contrast, is data — operator configuration created at runtime,
not a manifest declaration (§ 4). It is a plain string here for exactly that
reason.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any, ClassVar, Final
from uuid import UUID

# `sha256:` + 64 hex characters. Written as a length rather than a regex in the
# column definition, and validated here where the value is constructed.
DIGEST_PREFIX: Final[str] = "sha256:"
DIGEST_LENGTH: Final[int] = len(DIGEST_PREFIX) + 64


class ApprovalState(StrEnum):
    """The only four answers the module gives.

    `PENDING` is the sole non-terminal state. ERP additionally carried
    `ESCALATED`, which is not ported: escalation changes WHO may approve, not
    whether the content is approved, and the audit found no production writer
    that set it.
    """

    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    CANCELLED = "cancelled"


TERMINAL_STATES: Final[frozenset[ApprovalState]] = frozenset(
    {ApprovalState.APPROVED, ApprovalState.REJECTED, ApprovalState.CANCELLED}
)


class DecisionAction(StrEnum):
    """What an actor did.

    ERP's source enum also had `DELEGATE`, `ESCALATE` and `REQUEST_INFO`. Only
    the two that change approval state are ported; delegation survives as
    PROVENANCE on an approve (`delegated_from`), which is what its production
    rows actually recorded.
    """

    APPROVE = "approve"
    REJECT = "reject"


class ApproverKind(StrEnum):
    """How a level names who may decide it.

    ERP's `approver_type` also allowed `DEPARTMENT_HEAD` and
    `COST_CENTER_OWNER`. Those are finance org-chart lookups — domain
    vocabulary, and its own service already refused them as "unsupported
    approver type". The module resolves a user or a role and nothing else; a
    domain that wants "the department head" resolves it to a role or a user
    before it calls.
    """

    USER = "user"
    ROLE = "role"


class SoDRule(StrEnum):
    """Separation-of-duties constraints a level may impose."""

    CANNOT_BE_REQUESTER = "cannot_be_requester"
    CANNOT_BE_PREVIOUS_APPROVER = "cannot_be_previous_approver"


# ── Errors ──────────────────────────────────────────────────────────────────


class ApprovalError(Exception):
    """Base for every refusal this module makes.

    A typed domain error, never an HTTP exception: ERP's service raised
    `HTTPException` from its service layer, which welds a transport concern into
    business logic and is not ported (ADR-0026 § 7, hard rule 1).
    """


class PolicyNotFound(ApprovalError):
    """No such `(policy_code, version)`.

    Fail CLOSED. ERP's `check_workflow_required` returned `None` when no
    workflow matched, which a caller could read as "no approval required" — a
    fail-open default the audit recorded and this module does not port.
    """


class PolicyVersionExists(ApprovalError):
    """A published policy version may never be rewritten (Vendor CP delta)."""


class InvalidPolicy(ApprovalError):
    """The policy's own shape is unusable — no levels, a quorum below one."""


class ContentChanged(ApprovalError):
    """The digest presented does not match the digest that was approved."""


class RequestNotPending(ApprovalError):
    """The request already reached a terminal state."""


class NotEligible(ApprovalError):
    """The actor is not named by this level."""


class SoDViolation(ApprovalError):
    """The actor is eligible but separation of duties forbids this decision."""


class SelfApprovalRefused(ApprovalError):
    """The requester may not approve their own request under this policy."""


class DuplicateDecision(ApprovalError):
    """This actor already decided this level — a second vote is not a vote."""


class NotRequester(ApprovalError):
    """Only the original requester may cancel (ported from ERP)."""


class MFARequired(ApprovalError):
    """The level demands verified MFA and the decision did not carry it."""


# ── Values ──────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class ApprovalLevel:
    """One ordered rung of a policy.

    `quorum` is ERP's `required_count` renamed: "how many DISTINCT actors must
    approve at this level". Distinctness is not a property of the number — it is
    enforced by `policy.satisfied_by` and, durably, by a unique constraint on
    the decision table, which is the Vendor CP delta that makes a duplicate vote
    impossible rather than merely refused.
    """

    sequence: int
    approver_kind: ApproverKind
    approver_id: str
    quorum: int = 1
    sod_rule: SoDRule | None = None
    requires_mfa: bool = False
    allow_delegation: bool = True

    def __post_init__(self) -> None:
        if self.sequence < 1:
            raise InvalidPolicy(f"level sequence {self.sequence} must start at 1")
        if self.quorum < 1:
            raise InvalidPolicy(
                f"level {self.sequence} declares quorum {self.quorum}; a level "
                "that needs nobody is not a level"
            )
        if not self.approver_id.strip():
            raise InvalidPolicy(
                f"level {self.sequence} names no approver — ERP's own service "
                "refused this configuration at decision time, which is too late"
            )

    def as_document(self) -> dict[str, object]:
        """The JSON form persisted on the policy row and hashed into its digest."""
        return {
            "sequence": self.sequence,
            "approver_kind": str(self.approver_kind),
            "approver_id": self.approver_id,
            "quorum": self.quorum,
            "sod_rule": str(self.sod_rule) if self.sod_rule else None,
            "requires_mfa": self.requires_mfa,
            "allow_delegation": self.allow_delegation,
        }

    @classmethod
    def from_document(cls, document: Mapping[str, Any]) -> ApprovalLevel:
        rule = document.get("sod_rule")
        return cls(
            sequence=int(document["sequence"]),
            approver_kind=ApproverKind(document["approver_kind"]),
            approver_id=str(document["approver_id"]),
            quorum=int(document["quorum"]),
            sod_rule=SoDRule(rule) if rule else None,
            requires_mfa=bool(document.get("requires_mfa", False)),
            allow_delegation=bool(document.get("allow_delegation", True)),
        )


@dataclass(frozen=True, slots=True)
class PolicyRevision:
    """An immutable `(policy_code, version)` and the levels it fixes.

    Immutability is the central Vendor CP delta. ERP's `approval_workflow` row
    was mutable, so editing a workflow silently reinterpreted every request
    already in flight against it — and no migration can recover what such a
    request originally meant. Here a change is a NEW version, and a request
    records the exact version it was opened against.
    """

    policy_code: str
    version: int
    levels: tuple[ApprovalLevel, ...]
    allow_self_approval: bool = False

    def __post_init__(self) -> None:
        if not self.policy_code.strip():
            raise InvalidPolicy("policy_code must be a non-empty string")
        if self.version < 1:
            raise InvalidPolicy(f"policy version {self.version} must start at 1")
        if not self.levels:
            raise InvalidPolicy(
                f"{self.policy_code} v{self.version} declares no levels; a policy "
                "that can be satisfied by nobody would approve by default"
            )
        expected = tuple(range(1, len(self.levels) + 1))
        actual = tuple(level.sequence for level in self.levels)
        if actual != expected:
            raise InvalidPolicy(
                f"{self.policy_code} v{self.version} levels are {actual}, expected "
                f"{expected} — levels are ORDERED and dense, not a set of labels"
            )

    @property
    def total_levels(self) -> int:
        return len(self.levels)

    def level(self, sequence: int) -> ApprovalLevel:
        if sequence < 1 or sequence > len(self.levels):
            raise InvalidPolicy(
                f"{self.policy_code} v{self.version} has no level {sequence}"
            )
        return self.levels[sequence - 1]

    def as_document(self) -> dict[str, object]:
        return {
            "policy_code": self.policy_code,
            "version": self.version,
            "allow_self_approval": self.allow_self_approval,
            "levels": [level.as_document() for level in self.levels],
        }

    @classmethod
    def from_document(cls, document: Mapping[str, Any]) -> PolicyRevision:
        levels = document["levels"]
        # A typed refusal, not an `assert`. This parses a document read back
        # from the database, so the check must survive `python -O` — an assert
        # is removed there, and a malformed row would reach `ApprovalLevel
        # .from_document` as whatever it happened to be. `InvalidPolicy` is also
        # what every other malformed-policy path already raises.
        if isinstance(levels, str) or not isinstance(levels, Sequence):
            raise InvalidPolicy(
                f"policy {document.get('policy_code')!r} stores `levels` as "
                f"{type(levels).__name__}; an ordered sequence is required"
            )
        return cls(
            policy_code=str(document["policy_code"]),
            version=int(document["version"]),
            levels=tuple(ApprovalLevel.from_document(entry) for entry in levels),
            allow_self_approval=bool(document.get("allow_self_approval", False)),
        )


@dataclass(frozen=True, slots=True)
class Actor:
    """Who is deciding, and what the caller has already proven about them.

    Role membership is resolved by the CALLER and passed in. The module does not
    query an identity estate: ERP's service joined `PersonRole`/`Role` directly,
    which is exactly the coupling that would make this module un-installable
    beside a product whose RBAC lives elsewhere.
    """

    actor_id: UUID
    role_ids: frozenset[UUID] = field(default_factory=frozenset)
    mfa_verified: bool = False


@dataclass(frozen=True, slots=True)
class RecordedDecision:
    """One decision as the evaluator sees it — persistence-shaped, plane-free."""

    level: int
    actor_id: UUID
    action: DecisionAction


@dataclass(frozen=True, slots=True)
class Evaluation:
    """The whole answer, and enough of the reasoning to explain it.

    `reason` is a stable code, never a sentence to parse: `pending`,
    `satisfied`, `rejected`, `cancelled`. Vendor CP's `ApprovalDecision`
    dataclass carried the same idea and it is worth keeping — an outcome a
    caller cannot explain is one an auditor cannot accept.
    """

    state: ApprovalState
    current_level: int
    total_levels: int
    satisfied_levels: int
    reason: str

    @property
    def is_approved(self) -> bool:
        return self.state is ApprovalState.APPROVED


@dataclass(frozen=True, slots=True)
class ApprovalEvent:
    """What the consuming domain reacts to (ADR-0026 § 6).

    The module never performs the approved transition. It emits one of these and
    the subject's owner runs its own guarded transition — which is the whole
    reason this module is not a workflow engine.
    """

    event_type: str
    subject_type: str
    subject_id: str
    request_id: UUID
    policy_code: str
    policy_version: int
    content_digest: str
    state: ApprovalState

    def payload(self) -> dict[str, object]:
        return {
            "request_id": str(self.request_id),
            "subject_type": self.subject_type,
            "subject_id": self.subject_id,
            "policy_code": self.policy_code,
            "policy_version": self.policy_version,
            "content_digest": self.content_digest,
            "state": str(self.state),
        }


# ── Read contracts ──────────────────────────────────────────────────────────
#
# The typed values a browser surface renders. Wave 2 step 1: a queue screen and
# a request detail have to be buildable without a consumer querying `approvals`
# and without a template deciding who may approve.
#
# Everything below is a VALUE. A surface handed a live `ApprovalRequest` holds a
# session-bound object that lazy-loads on attribute access and expires with the
# transaction, which turns a template into a query planner and a detached row
# into a runtime error mid-render.


class RequestAction(StrEnum):
    """What a specific actor may do to a specific pending request.

    Owner-derived, always. Eligibility here is the product of the policy
    revision, the decisions already recorded, separation of duties, the
    self-approval rule and MFA — five inputs a row action downstream does not
    have and must not guess at. A screen that decided for itself whether to show
    an Approve button would be a second, weaker implementation of
    `policy.authorise_approval`, and the two would disagree the first time a
    quorum was reached between the render and the click.
    """

    APPROVE = "approve"
    REJECT = "reject"
    CANCEL = "cancel"


@dataclass(frozen=True, slots=True)
class ActionRefusal:
    """Why one action is not permitted, in the module's own vocabulary.

    `code` is the refusal type's name — `NotEligible`, `SoDViolation`,
    `MFARequired`, `DuplicateDecision`, `SelfApprovalRefused` — so a surface can
    tell an approver why the button is absent instead of leaving them to guess.
    A stable code, never a sentence to parse.
    """

    action: RequestAction
    code: str
    detail: str


@dataclass(frozen=True, slots=True)
class DecisionView:
    """One recorded decision, as an immutable reference.

    `decided_at` is the module's clock, not the caller's, and `actor_id` is the
    actor the module AUTHORISED rather than a name the request supplied. That is
    what makes this row a reference an auditor can rely on: there is no input
    anywhere on this module's write surface that can put a different approver or
    a different time into it.
    """

    id: UUID
    request_id: UUID
    level: int
    actor_id: UUID
    action: DecisionAction
    comment: str | None
    delegated_from: UUID | None
    mfa_verified: bool
    decided_at: datetime


@dataclass(frozen=True, slots=True)
class PolicyView:
    """One immutable published policy revision.

    `document_digest` is what makes "this request was approved under THIS
    policy" checkable rather than asserted: the levels are carried as values, so
    a surface can show the rungs without reading the JSON column itself.
    """

    id: UUID
    policy_code: str
    version: int
    levels: tuple[ApprovalLevel, ...]
    allow_self_approval: bool
    document_digest: str
    published_at: datetime


@dataclass(frozen=True, slots=True)
class RequestView:
    """One request as a queue row."""

    id: UUID
    policy_code: str
    policy_version: int
    subject_type: str
    subject_id: str
    content_digest: str
    requested_by: UUID
    state: ApprovalState
    current_level: int
    idempotency_key: str
    note: str | None
    opened_at: datetime
    completed_at: datetime | None


@dataclass(frozen=True, slots=True)
class RequestDetail:
    """One request, the policy it is bound to, its history, and the verdict.

    `evaluation` is re-derived from the policy revision and the recorded
    decisions on every read rather than trusted from the stored `state` column,
    so a surface shows what the rules say and not what a row last happened to be
    set to.

    `permitted_actions` and `refusals` are answered FOR a viewer, and are empty
    when no viewer was named — an anonymous read gets the facts and no
    invitation to act.
    """

    request: RequestView
    policy: PolicyView
    decisions: tuple[DecisionView, ...]
    evaluation: Evaluation
    permitted_actions: tuple[RequestAction, ...] = ()
    refusals: tuple[ActionRefusal, ...] = ()


@dataclass(frozen=True, slots=True)
class RequestFilter:
    """What an operator may narrow a request queue by.

    A closed set of typed fields. No predicate, no sort column, no raw `where`:
    a consumer that could pass one would own every future query over this
    module's tables, and the module could no longer say what its own read
    surface is.

    There is deliberately nowhere here to state an approver, a decision time, an
    evaluation or a permitted action. Those are the module's to derive, and an
    input that could carry one would be an input a client could use to assert
    it.

    `page_size` is bounded by the TYPE rather than by the caller, because an
    unbounded queue is how an approvals screen becomes a full-table scan the day
    a bulk import opens four thousand requests.
    """

    state: ApprovalState | None = None
    policy_code: str | None = None
    subject_type: str | None = None
    subject_id: str | None = None
    requested_by: UUID | None = None
    page: int = 1
    page_size: int = 50

    MAX_PAGE_SIZE: ClassVar[int] = 200

    def __post_init__(self) -> None:
        # A plain `ValueError`, not an `ApprovalError`. Every typed error in
        # this module is a REFUSAL the domain made — not eligible, content
        # changed, quorum unmet — and a caller catching `ApprovalError` to
        # render "you may not do that" would then render it for a mistyped page
        # size. A bad page size is a bug in the layer above, not a decision.
        if self.page < 1:
            raise ValueError("page is 1-based")
        if not 1 <= self.page_size <= self.MAX_PAGE_SIZE:
            raise ValueError(f"page_size must be 1..{self.MAX_PAGE_SIZE}")


@dataclass(frozen=True, slots=True)
class RequestPage:
    """One page of requests, and enough to render a pager honestly.

    `total` counts what matches the FILTER, not the page, so a surface can say
    "showing 50 of 412" without a second query and without guessing.
    """

    requests: tuple[RequestView, ...]
    total: int
    page: int
    page_size: int

    @property
    def has_more(self) -> bool:
        return self.page * self.page_size < self.total


EVENT_REQUESTED: Final[str] = "approval.requested"
EVENT_APPROVED: Final[str] = "approval.approved"
EVENT_REJECTED: Final[str] = "approval.rejected"
EVENT_CANCELLED: Final[str] = "approval.cancelled"

EVENT_FOR_STATE: Final[Mapping[ApprovalState, str]] = {
    ApprovalState.PENDING: EVENT_REQUESTED,
    ApprovalState.APPROVED: EVENT_APPROVED,
    ApprovalState.REJECTED: EVENT_REJECTED,
    ApprovalState.CANCELLED: EVENT_CANCELLED,
}


def validate_digest(digest: str) -> str:
    """A content digest is `sha256:<64 hex>` or it is not a digest.

    Checked at the boundary rather than trusted, because the digest is the thing
    an approval is FOR: a caller that passed a subject id here by mistake would
    otherwise get an approval bound to a value that never changes when the
    content does.
    """
    if len(digest) != DIGEST_LENGTH or not digest.startswith(DIGEST_PREFIX):
        raise ContentChanged(
            f"content digest {digest!r} is not {DIGEST_PREFIX}<64 hex chars>"
        )
    body = digest[len(DIGEST_PREFIX) :]
    if any(character not in "0123456789abcdef" for character in body):
        raise ContentChanged(f"content digest {digest!r} is not lowercase hex")
    return digest


__all__ = [
    "DIGEST_LENGTH",
    "DIGEST_PREFIX",
    "EVENT_APPROVED",
    "EVENT_CANCELLED",
    "EVENT_FOR_STATE",
    "EVENT_REJECTED",
    "EVENT_REQUESTED",
    "TERMINAL_STATES",
    "ActionRefusal",
    "Actor",
    "ApprovalError",
    "ApprovalEvent",
    "ApprovalLevel",
    "ApprovalState",
    "ApproverKind",
    "ContentChanged",
    "DecisionAction",
    "DecisionView",
    "DuplicateDecision",
    "Evaluation",
    "InvalidPolicy",
    "MFARequired",
    "NotEligible",
    "NotRequester",
    "PolicyNotFound",
    "PolicyRevision",
    "PolicyVersionExists",
    "PolicyView",
    "RecordedDecision",
    "RequestAction",
    "RequestDetail",
    "RequestFilter",
    "RequestNotPending",
    "RequestPage",
    "RequestView",
    "SelfApprovalRefused",
    "SoDRule",
    "SoDViolation",
    "validate_digest",
]
