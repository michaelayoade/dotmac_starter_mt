"""The module's inbound contract: what a caller supplies, and what it must prove.

Three value types, one protocol, and one rule that all of them exist to enforce:
**this module never infers that something happened elsewhere.** It is handed the
evidence, and it checks the evidence against state it froze itself.

The source implementation (`dotmac_vendor_control_plane:src/vendor_cp/contracts/
service.py`, 658 lines) had three couplings that cannot survive a module
boundary, and each needed a different cut:

**1. `from vendor_cp.approvals import service as approvals`**, called inside
`approve()`. A module never imports a sibling (ADR-0024), and approvals decide
approval rather than performing the domain's transition (ADR-0026 § 6).
*The cut:* `ApprovalEvidence` — a value the assembly passes in, whose
`content_digest` this module checks against the digest it froze itself.

**2. `from vendor_cp.offers.models import OfferVersion`**, plus a
`session.get(OfferVersion, ...)` and a foreign key from `contract_lines`.
Ruling A2(b) detached the offer catalogue, and ADR-0006 D1 forbids the
cross-lineage foreign key.
*The cut:* `LineInput.offer_ref` / `release_ref` — opaque references, with the
price frozen from a caller-supplied `CommercialTerms`.

**3. `customer_ref` as a `vendor_accounts` id.** ADR-0019 § 1 and ruling A3:
Party is not an account, and `vendor_accounts` must not retire into kernel
`Party`.
*The cut:* `counterparty_ref` — an opaque string this module never resolves,
dereferences or joins.

## Why `ApprovalEvidence` is a value and not a call

The task this module was extracted for states the requirement directly:
*activation must require the exact approval evidence and accepted agreement
snapshot; it must not infer approval from a status string maintained by an
adapter.*

A boolean `approved=True` parameter would satisfy the type checker and nothing
else — it is the adapter asserting a conclusion the module cannot check. So the
evidence carries a `content_digest`, and `approve()` compares it against the
digest this module computed when it froze the snapshot at `propose()`. If the
terms changed after approval, the digests differ and the approval is **stale
rather than transferable** — ADR-0026 § 2's property, applied at the boundary
where it is checkable.

That is also why there is no `AgreementService.set_status()`. Every status this
module writes is written by a named command that verified its own precondition.

## The catalogue is a port, not a dependency

The module VALIDATES; the caller supplies ACCESS to the authority. Identical to
`dotmac-entitlement-allocation`'s `CapabilityCatalogueReader` and to the kernel's
`grant_entitlement`, and for the same reason: the invariant holds across every
adapter — HTTP route, outbox consumer, CLI backfill — rather than once per
adapter, where the newest one is always the one that forgot.

The protocol lives HERE rather than in the kernel deliberately. Adding it there
would be a new kernel facility, and ADR-0017's moratorium holds. A module-owned
port needs no kernel change and no exception.

**An adapter TRANSLATES; the module does not guess.** Your `require_declared`
must raise this module's `UnknownProductError` or `UndeclaredCapabilityError` —
not the backing store's own exception type. The service catches nothing broad,
so an adapter defect surfaces as itself rather than being reported as an
undeclared capability.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Protocol
from uuid import UUID

# ── Errors ──────────────────────────────────────────────────────────────────


class AgreementError(ValueError):
    """Base: this command cannot be applied to this agreement."""


class UnknownProductError(AgreementError):
    """The catalogue reader does not know this product.

    Fail closed. An unknown product is not an empty catalogue — it is a caller
    that cannot prove anything about the codes it is promising, and treating the
    two alike would let a typo in `product_code` promise arbitrary capabilities
    against a product nobody has declared.
    """

    def __init__(self, product_code: str) -> None:
        self.product_code = product_code
        super().__init__(
            f"no capability catalogue for product {product_code!r}; "
            "an unknown product cannot be promised anything"
        )


class UndeclaredCapabilityError(AgreementError):
    """A promised capability code is not declared by the named product.

    Carries the full offending set, not just the first: a caller fixing a
    manifest wants every missing code at once, and reporting one at a time turns
    a single review into several.
    """

    def __init__(self, product_code: str, codes: tuple[str, ...]) -> None:
        self.product_code = product_code
        self.codes = codes
        super().__init__(
            f"product {product_code!r} declares no capability "
            f"{', '.join(repr(code) for code in codes)}; "
            "an agreement may never invent a capability code"
        )


class TransitionRefusedError(AgreementError):
    """The agreement is not in a status from which this transition is legal.

    Distinct from `ExpectedStateError` on purpose: this one means the command is
    wrong for the agreement, that one means the caller's view of the agreement
    is stale. Collapsing them would make a retry-able conflict and a
    programming error look the same to a caller deciding whether to retry.
    """


class ExpectedStateError(AgreementError):
    """The caller's expected status or record version does not match.

    Optimistic concurrency. Two operators acting on one agreement from two
    screens is the ordinary case, not the exotic one; without this the second
    write silently wins and the first operator's decision disappears.
    """

    def __init__(
        self,
        agreement_id: UUID,
        *,
        expected_status: str | None,
        actual_status: str,
        expected_version: int | None,
        actual_version: int,
    ) -> None:
        self.agreement_id = agreement_id
        self.expected_status = expected_status
        self.actual_status = actual_status
        self.expected_version = expected_version
        self.actual_version = actual_version
        super().__init__(
            f"agreement {agreement_id} has moved: caller expected "
            f"status={expected_status!r} version={expected_version} but it is "
            f"status={actual_status!r} version={actual_version}"
        )


class EvidenceRefusedError(AgreementError):
    """Supplied evidence does not bind to what this module froze.

    The one error that makes activation trustworthy. It fires when an approval's
    `content_digest` does not equal the digest computed over the accepted
    snapshot, which is exactly the case where "approved" would otherwise be a
    token movable onto terms nobody reviewed.
    """


class EmptyAgreementError(AgreementError):
    """An agreement with no lines promises nothing and cannot be proposed."""


# ── Inbound values ──────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class CommercialTerms:
    """The money on one line, frozen at proposal.

    `unit_amount` is a STRING and stays one, all the way to the column. ADR-0003
    requires exact Money and forbids float; a decimal that round-trips through a
    JSON snapshot and a digest must be byte-stable, and the only representation
    that is byte-stable across a serialiser is the text the caller supplied.
    This module performs no arithmetic on it — it freezes it, digests it, and
    hands it back. Totals belong to billing.
    """

    unit_amount: str
    currency_code: str


@dataclass(frozen=True, slots=True)
class LineInput:
    """One promised line: an opaque product/release/offer reference, the
    capability it entitles, a quantity, and the terms frozen at proposal.

    `release_ref` and `offer_ref` are OPAQUE. This module does not own product or
    release definitions (`dotmac-release-catalog` does), does not resolve them,
    and holds no foreign key to either — ADR-0006 D1 forbids splicing two
    independently released lineages, and an agreement must stay readable after a
    release is superseded.
    """

    product_code: str
    capability_code: str
    quantity: int
    terms: CommercialTerms
    release_ref: str | None = None
    offer_ref: str | None = None


@dataclass(frozen=True, slots=True)
class ApprovalEvidence:
    """Proof, supplied by the assembly, that `dotmac-approvals` decided.

    Every field is load-bearing:

    - `content_digest` is checked against the digest this module froze. This is
      the whole reason the type exists.
    - `policy_code` / `policy_version` record WHICH policy revision decided, so
      the decision stays explainable after the policy changes.
    - `decision_ref` is an opaque handle into the deciding owner's record. This
      module never dereferences it; it stores it so an auditor can.
    - `decided_at` is the deciding owner's clock, not this module's. Two owners
      recording two different times for one decision is the drift an audit
      cannot resolve later.
    """

    policy_code: str
    policy_version: int
    decision_ref: str
    content_digest: str
    decided_at: datetime
    approver_refs: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ActivationEvidence:
    """Proof that the contracted activation rule was satisfied.

    Ported from the source's guard verbatim in intent: activation is
    *rule-driven, not "a form was submitted"*. `rule` is a stable code and never
    a plan name, mode string or deployment-profile string — ADR-0003 forbids
    feature code branching on those, and a rule that encodes a plan name would
    make this column exactly such a branch.
    """

    rule: str
    reference: str
    satisfied_at: datetime


# ── The one port ────────────────────────────────────────────────────────────


class CapabilityCatalogueReader(Protocol):
    """Access to the capability codes a named product declares.

    One method. The caller implements it over whatever holds the truth for that
    product — in the vendor control plane, a thin wrapper over the kernel's
    `CapabilityCatalogue.require`.

    **Never wrap `active_capabilities()`.** It describes the modules installed in
    the process doing the asking, not the ones declared by the target
    application. Validating against it checks the wrong product's manifest. This
    is `dotmac-entitlement-allocation`'s recorded lesson, restated because the
    mistake is available here in exactly the same shape.
    """

    def require_declared(self, product_code: str, codes: tuple[str, ...]) -> None:
        """Raise `UnknownProductError` or `UndeclaredCapabilityError`, or return.

        Returning is an assertion that every code in `codes` is declared by
        `product_code`. Raising anything else is an adapter defect and is
        deliberately not caught.
        """
        ...


@dataclass(frozen=True, slots=True)
class AgreementPeriod:
    """Effective and expiry dates, validated as a pair.

    A pair rather than two parameters because the only invalid combination is a
    relationship between them, and a type that can hold an invalid pair pushes
    the check to every call site.
    """

    effective_date: date
    expiry_date: date

    def __post_init__(self) -> None:
        if self.expiry_date < self.effective_date:
            raise AgreementError(
                f"expiry {self.expiry_date} precedes effective "
                f"{self.effective_date}; an agreement cannot end before it starts"
            )


__all__ = [
    "ActivationEvidence",
    "AgreementError",
    "AgreementPeriod",
    "ApprovalEvidence",
    "CapabilityCatalogueReader",
    "CommercialTerms",
    "EmptyAgreementError",
    "EvidenceRefusedError",
    "ExpectedStateError",
    "LineInput",
    "TransitionRefusedError",
    "UndeclaredCapabilityError",
    "UnknownProductError",
]
