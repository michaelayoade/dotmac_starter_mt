"""`AgreementService` — the one owner of commercial-agreement shape and lifecycle.

Ported from `dotmac_vendor_control_plane:src/vendor_cp/contracts/service.py`
(658 lines, two tables, migration `v004_contracts`), with the three couplings
cut at the ports described in `ports.py`. What is NOT changed is the shape of a
transition, and that shape is the reason the source qualified:

**Every state change is a named, guarded command that commits FOUR things in ONE
transaction** — the state change, an append-only history row, a platform audit
record, and a platform outbox event. A decision and its emitted consequence can
never diverge, because there is no path that writes one without the others.

## Transaction authority (hard rule 8)

This module receives a `Session` and only `add`/`flush`. It never commits, never
rolls back, and never constructs a session. The boundary that owns the
transaction owns the commit.

## Every command is idempotent, through the kernel's one owner

`process_once_platform` (hard rule 23, ADR-0014). Nothing is reserved before the
effect, so a crashed attempt leaves no marker and the retry re-drives. The
command id is the caller's; this module invents none.

## Expected-state concurrency, on every transition

Two operators acting on one agreement from two screens is the ordinary case. A
command carries the status and `record_version` the caller believed it was
acting on, and a mismatch raises `ExpectedStateError` rather than overwriting.
`expected_version=None` opts out for a caller that genuinely has no prior read —
an outbox consumer reacting to a fact, say — and `expected_status` still applies.

## The ordering inside every handler is fixed

Load → check expected state → check the transition is legal → check evidence →
mutate → append history → audit → emit. Evidence is checked **before** any row
changes, so a refused activation leaves no partial write and no history entry
claiming a transition that did not happen.

## What this module refuses to do

- **Infer approval.** `approve()` takes `ApprovalEvidence` and compares its
  `content_digest` to the digest frozen at `propose()`. There is no
  `approved: bool` parameter, and no status a caller can set directly.
- **Resolve a reference.** Counterparty, release, offer and approval-decision
  references are opaque strings. This module never joins, dereferences or
  validates their existence — it is not their owner.
- **Compute money.** Amounts are frozen strings. Totals, tax, proration and
  invoicing belong to billing.
- **Touch an allocation, a licence, a deployment or an invoice.** It emits a
  fact; the assembly routes it (ADR-0024).
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any
from uuid import UUID, uuid4

from dotmac_kernel.audit import write_platform_audit_event
from dotmac_kernel.messaging import enqueue_platform_event, process_once_platform
from sqlalchemy import select
from sqlalchemy.orm import Session

from dotmac_commercial_agreements import facts
from dotmac_commercial_agreements.models import (
    TERMINAL_STATUSES,
    Agreement,
    AgreementEvent,
    AgreementLine,
    AgreementStatus,
)
from dotmac_commercial_agreements.ports import (
    ActivationEvidence,
    AgreementPeriod,
    ApprovalEvidence,
    CapabilityCatalogueReader,
    EmptyAgreementError,
    EvidenceRefusedError,
    ExpectedStateError,
    LineInput,
    TransitionRefusedError,
)

#: The one audit action this module declares and writes. Singular deliberately:
#: the ACTION is "a commercial agreement transitioned"; WHICH transition is a
#: detail in the record, not a separate vocabulary member. Declaring ten codes
#: would put the lifecycle in two places — the manifest and the enum — and
#: ADR-0008's registries exist to prevent exactly that kind of duplicate
#: vocabulary, not to encourage a code per verb.
AUDIT_ACTION_TRANSITIONED: str = "commercial_agreement.transitioned"

#: Idempotency scopes name the OPERATION, never an HTTP route (ADR-0014).
SCOPE_DRAFT = "commercial_agreement.draft"
SCOPE_PROPOSE = "commercial_agreement.propose"
SCOPE_APPROVE = "commercial_agreement.approve"
SCOPE_REJECT = "commercial_agreement.reject"
SCOPE_ACTIVATE = "commercial_agreement.activate"
SCOPE_SUSPEND = "commercial_agreement.suspend"
SCOPE_REINSTATE = "commercial_agreement.reinstate"
SCOPE_TERMINATE = "commercial_agreement.terminate"
SCOPE_EXPIRE = "commercial_agreement.expire"
SCOPE_CANCEL = "commercial_agreement.cancel"
SCOPE_AMEND = "commercial_agreement.amend"

_ENTITY = "commercial_agreement"


# ── Commands ────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class DraftCommand:
    """Open a `draft`. Nothing is frozen yet and no fact is published."""

    command_id: str
    reference: str
    counterparty_ref: str
    agreement_type: str
    period: AgreementPeriod
    lines: tuple[LineInput, ...]
    actor_admin_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class TransitionCommand:
    """A guarded transition, carrying its idempotency key and expected state.

    `expected_status` and `expected_version` are the optimistic-concurrency
    pair. `expected_version=None` opts a caller out of the version check — an
    outbox consumer reacting to a fact has no prior read to compare against —
    while `expected_status` still applies, so opting out never means opting out
    of the guard entirely.
    """

    command_id: str
    agreement_id: UUID
    expected_status: str | None = None
    expected_version: int | None = None
    reason: str | None = None
    actor_admin_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class ProposeCommand:
    """`draft → proposed`. Freezes the accepted snapshot and its digest."""

    command_id: str
    agreement_id: UUID
    approval_policy_code: str
    approval_policy_version: int
    expected_version: int | None = None
    actor_admin_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class ApproveCommand:
    """`proposed → approved`, on evidence bound to the frozen digest."""

    command_id: str
    agreement_id: UUID
    evidence: ApprovalEvidence
    expected_version: int | None = None
    actor_admin_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class ActivateCommand:
    """`approved → active`, on satisfied-activation-rule evidence.

    Requires the approval evidence AGAIN, not because this module distrusts its
    own `approved` column, but because the task this extraction serves states
    the requirement directly: activation must require the exact approval
    evidence and the accepted snapshot. Re-supplying it means activation is
    verifiable from the command alone — an auditor reading the history row does
    not have to trust that an earlier row was written correctly.
    """

    command_id: str
    agreement_id: UUID
    approval_evidence: ApprovalEvidence
    activation_evidence: ActivationEvidence
    expected_version: int | None = None
    actor_admin_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class TerminateCommand:
    """`active | suspended → terminated`, on an acknowledged impact preview.

    The acknowledgement and effective date are the source's guard, kept because
    the failure it prevents is real: a termination without an effective date is
    a dispute, and one without an acknowledged impact preview is a support
    incident about data nobody was told would stop.
    """

    command_id: str
    agreement_id: UUID
    effective_date: date
    impact_acknowledged: bool
    reason: str
    expected_status: str | None = None
    expected_version: int | None = None
    actor_admin_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class AmendCommand:
    """Supersede an agreement with a new version of the same family.

    An amendment is a NEW agreement row, not an edit. The predecessor becomes
    `superseded` and keeps every line and every history row it had; the
    successor starts at `draft` with the amended lines and must be proposed and
    approved on its own terms. That is what makes "what did we agree, and when"
    answerable years later — editing in place destroys the answer.
    """

    command_id: str
    agreement_id: UUID
    reference: str
    lines: tuple[LineInput, ...]
    period: AgreementPeriod | None = None
    reason: str | None = None
    expected_version: int | None = None
    actor_admin_id: UUID | None = None


# ── Snapshot and digest ─────────────────────────────────────────────────────


def accepted_snapshot(row: Agreement) -> dict[str, Any]:
    """The canonical accepted commercial snapshot for an agreement.

    Deterministic by construction: keys are sorted at serialisation and lines
    are sorted by a total order over the fields that identify them. A digest
    over a dict whose iteration order is insertion order would change when the
    same agreement was rebuilt in a different order, which would silently
    invalidate an approval nobody changed.
    """
    return {
        "reference": row.reference,
        "counterparty_ref": row.counterparty_ref,
        "agreement_type": row.agreement_type,
        "agreement_family_id": str(row.agreement_family_id),
        "agreement_version": row.agreement_version,
        "effective_date": row.effective_date.isoformat(),
        "expiry_date": row.expiry_date.isoformat(),
        "lines": sorted(
            (
                {
                    "line_no": line.line_no,
                    "product_code": line.product_code,
                    "capability_code": line.capability_code,
                    "quantity": line.quantity,
                    "unit_amount": line.unit_amount,
                    "unit_currency_code": line.unit_currency_code,
                    "release_ref": line.release_ref,
                    "offer_ref": line.offer_ref,
                }
                for line in row.lines
            ),
            key=lambda d: (
                int(d["line_no"]),
                str(d["product_code"]),
                str(d["capability_code"]),
            ),
        ),
    }


def snapshot_digest(snapshot: Mapping[str, Any]) -> str:
    """SHA-256 over the canonical JSON encoding of an accepted snapshot.

    `sort_keys=True` and the tightest separators, so the digest depends on the
    VALUES and not on how a serialiser felt about whitespace.
    """
    canonical = json.dumps(snapshot, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


# ── Internals ───────────────────────────────────────────────────────────────


def _load(session: Session, agreement_id: UUID) -> Agreement:
    row = session.get(Agreement, agreement_id)
    if row is None:
        raise TransitionRefusedError(f"agreement {agreement_id} not found")
    return row


def _require_expected(
    row: Agreement, *, expected_status: str | None, expected_version: int | None
) -> None:
    """Optimistic concurrency, checked before anything else changes."""
    status_ok = expected_status is None or row.status == expected_status
    version_ok = expected_version is None or row.record_version == expected_version
    if not (status_ok and version_ok):
        raise ExpectedStateError(
            row.id,
            expected_status=expected_status,
            actual_status=row.status,
            expected_version=expected_version,
            actual_version=row.record_version,
        )


def _require_status(row: Agreement, allowed: frozenset[str]) -> None:
    if row.status not in allowed:
        raise TransitionRefusedError(
            f"agreement {row.id} is {row.status!r}; this transition requires "
            f"one of {sorted(allowed)}"
        )


def _require_bound_approval(row: Agreement, evidence: ApprovalEvidence) -> None:
    """The check that makes an approval non-transferable.

    ADR-0026 § 2's digest binding, enforced at the boundary where it is
    checkable. Change the terms and the digest changes, which makes a prior
    approval **stale rather than transferable** — without this, "approved" is a
    token movable onto broader terms than anyone reviewed.
    """
    if not row.content_hash:
        raise EvidenceRefusedError(
            f"agreement {row.id} has no frozen snapshot; propose it before "
            "supplying approval evidence"
        )
    if evidence.content_digest != row.content_hash:
        raise EvidenceRefusedError(
            f"approval evidence binds to digest {evidence.content_digest!r} but "
            f"agreement {row.id} froze {row.content_hash!r}; the terms changed "
            "after approval, so a new approval is required"
        )
    if evidence.policy_code != (row.approval_policy_code or ""):
        raise EvidenceRefusedError(
            f"approval evidence names policy {evidence.policy_code!r} but "
            f"agreement {row.id} was proposed under "
            f"{row.approval_policy_code!r}"
        )
    if evidence.policy_version != row.approval_policy_version:
        raise EvidenceRefusedError(
            f"approval evidence names policy version {evidence.policy_version} "
            f"but agreement {row.id} was proposed under "
            f"{row.approval_policy_version}"
        )


def _next_sequence(session: Session, agreement_id: UUID) -> int:
    """The next dense per-agreement history sequence.

    Dense and per-agreement so a gap is detectable. Reading MAX inside the
    handler is safe because the unique constraint on `(agreement_id, sequence)`
    is the real arbiter: two concurrent transitions cannot both commit the same
    sequence, and the loser retries through the caller's own conflict handling
    rather than silently writing a duplicate history entry.
    """
    current = session.execute(
        select(AgreementEvent.sequence)
        .where(AgreementEvent.agreement_id == agreement_id)
        .order_by(AgreementEvent.sequence.desc())
        .limit(1)
    ).scalar_one_or_none()
    return 1 if current is None else int(current) + 1


def _record(
    session: Session,
    row: Agreement,
    *,
    command_id: str,
    event_type: str,
    from_status: str | None,
    actor_admin_id: UUID | None,
    reason: str | None = None,
    evidence: Mapping[str, Any] | None = None,
    extra: Mapping[str, Any] | None = None,
) -> None:
    """The atomic consequence of a transition: history, audit, and outbox fact.

    All three, in the caller's transaction, or none. Writing the state change
    without the fact would leave a consumer permanently unaware; writing the
    fact without the history would leave an auditor unable to say why.
    """
    actor_ref = str(actor_admin_id) if actor_admin_id is not None else None
    session.add(
        AgreementEvent(
            agreement_id=row.id,
            sequence=_next_sequence(session, row.id),
            event_type=event_type,
            from_status=from_status,
            to_status=row.status,
            actor_ref=actor_ref,
            reason=reason,
            evidence=dict(evidence) if evidence else None,
            command_id=command_id,
        )
    )

    details: dict[str, object] = {
        "reference": row.reference,
        "counterparty_ref": row.counterparty_ref,
        "agreement_version": row.agreement_version,
        "from_status": from_status,
        "to_status": row.status,
        "content_hash": row.content_hash,
        "event_type": event_type,
    }
    if reason:
        details["reason"] = reason
    if extra:
        details.update(extra)

    write_platform_audit_event(
        session,
        actor_admin_id=actor_admin_id,
        action=AUDIT_ACTION_TRANSITIONED,
        entity_type=_ENTITY,
        entity_id=str(row.id),
        details=details,
    )
    enqueue_platform_event(
        session,
        event_type=event_type,
        payload={
            "agreement_id": str(row.id),
            "agreement_family_id": str(row.agreement_family_id),
            "record_version": row.record_version,
            **details,
        },
        correlation_id=str(row.agreement_family_id),
    )


def _advance(row: Agreement, to: AgreementStatus) -> str:
    """Move the status and bump the record version. Returns the previous status."""
    previous = row.status
    row.status = to.value
    row.record_version += 1
    return previous


def _view(row: Agreement) -> facts.AgreementView:
    return facts.AgreementView(
        id=row.id,
        reference=row.reference,
        agreement_family_id=row.agreement_family_id,
        agreement_version=row.agreement_version,
        counterparty_ref=row.counterparty_ref,
        agreement_type=row.agreement_type,
        status=row.status,
        effective_date=row.effective_date,
        expiry_date=row.expiry_date,
        content_hash=row.content_hash,
        record_version=row.record_version,
        approval_policy_code=row.approval_policy_code,
        approval_policy_version=row.approval_policy_version,
        approval_decision_ref=row.approval_decision_ref,
        approved_at=row.approved_at,
        activation_rule=row.activation_rule,
        activated_at=row.activated_at,
        supersedes_id=row.supersedes_id,
        superseded_by_id=row.superseded_by_id,
        lines=tuple(
            facts.PromisedLine(
                line_no=line.line_no,
                product_code=line.product_code,
                capability_code=line.capability_code,
                quantity=line.quantity,
                unit_amount=line.unit_amount,
                unit_currency_code=line.unit_currency_code,
                release_ref=line.release_ref,
                offer_ref=line.offer_ref,
            )
            for line in row.lines
        ),
    )


def _add_lines(session: Session, row: Agreement, lines: tuple[LineInput, ...]) -> None:
    """Write the promised lines, numbered densely from 1 in caller order."""
    for index, line in enumerate(lines, start=1):
        session.add(
            AgreementLine(
                agreement_id=row.id,
                line_no=index,
                product_code=line.product_code,
                capability_code=line.capability_code,
                quantity=line.quantity,
                unit_amount=line.terms.unit_amount,
                unit_currency_code=line.terms.currency_code,
                release_ref=line.release_ref,
                offer_ref=line.offer_ref,
            )
        )


def _validate_against_catalogue(
    lines: tuple[LineInput, ...], catalogue: CapabilityCatalogueReader
) -> None:
    """Every promised code, checked per product, before anything is written.

    Grouped by product because a code is only meaningful against the product
    that declares it, and validated wholesale rather than as we go: a loop that
    wrote rows and validated the next code would leave a partially frozen
    snapshot when the fourth code turned out undeclared.
    """
    by_product: dict[str, list[str]] = {}
    for line in lines:
        by_product.setdefault(line.product_code, []).append(line.capability_code)
    for product_code in sorted(by_product):
        catalogue.require_declared(
            product_code, tuple(sorted(set(by_product[product_code])))
        )


# ── Commands ────────────────────────────────────────────────────────────────


def open_draft(
    db: Session, command: DraftCommand, *, catalogue: CapabilityCatalogueReader
) -> facts.AgreementView:
    """Create a `draft` agreement with its promised lines.

    Validates against the catalogue here as well as at `propose()`. That is not
    redundant: catching an undeclared capability while a human is still editing
    is worth far more than catching it at the moment they try to send the
    agreement out, and proposal re-checks because the catalogue may have moved
    in between.
    """
    if not command.lines:
        raise EmptyAgreementError("an agreement needs at least one promised line")
    _validate_against_catalogue(command.lines, catalogue)

    def handler(session: Session) -> Mapping[str, object]:
        row = Agreement(
            reference=command.reference,
            agreement_family_id=uuid4(),
            agreement_version=1,
            counterparty_ref=command.counterparty_ref,
            agreement_type=command.agreement_type,
            status=AgreementStatus.DRAFT.value,
            effective_date=command.period.effective_date,
            expiry_date=command.period.expiry_date,
            record_version=1,
        )
        session.add(row)
        session.flush()
        _add_lines(session, row, command.lines)
        session.flush()
        return {"id": str(row.id)}

    outcome = process_once_platform(
        db,
        command_id=command.command_id,
        command_type=SCOPE_DRAFT,
        handler=handler,
    )
    return _view(_load(db, UUID(str(outcome.result["id"]))))


def propose(
    db: Session, command: ProposeCommand, *, catalogue: CapabilityCatalogueReader
) -> facts.AgreementView:
    """`draft → proposed`. Freezes the accepted snapshot and computes its digest.

    This is the only place a snapshot is frozen, and after it there is no
    supported path that edits a line. Amending means a new version.
    """

    def handler(session: Session) -> Mapping[str, object]:
        row = _load(session, command.agreement_id)
        _require_expected(
            row,
            expected_status=AgreementStatus.DRAFT.value,
            expected_version=command.expected_version,
        )
        if not row.lines:
            raise EmptyAgreementError(
                f"agreement {row.id} has no promised lines and cannot be proposed"
            )
        _validate_against_catalogue(
            tuple(
                LineInput(
                    product_code=line.product_code,
                    capability_code=line.capability_code,
                    quantity=line.quantity,
                    terms=_terms_of(line),
                    release_ref=line.release_ref,
                    offer_ref=line.offer_ref,
                )
                for line in row.lines
            ),
            catalogue,
        )
        row.approval_policy_code = command.approval_policy_code
        row.approval_policy_version = command.approval_policy_version
        snapshot = accepted_snapshot(row)
        row.accepted_snapshot = snapshot
        row.content_hash = snapshot_digest(snapshot)
        previous = _advance(row, AgreementStatus.PROPOSED)
        session.flush()
        _record(
            session,
            row,
            command_id=command.command_id,
            event_type=facts.AGREEMENT_PROPOSED_V1,
            from_status=previous,
            actor_admin_id=command.actor_admin_id,
            evidence={
                "approval_policy_code": command.approval_policy_code,
                "approval_policy_version": command.approval_policy_version,
            },
        )
        return {"id": str(row.id)}

    process_once_platform(
        db,
        command_id=command.command_id,
        command_type=SCOPE_PROPOSE,
        handler=handler,
    )
    return _view(_load(db, command.agreement_id))


def approve(db: Session, command: ApproveCommand) -> facts.AgreementView:
    """`proposed → approved`, on evidence bound to the frozen digest.

    Commercial approval ONLY. It does not make the agreement active — that is a
    separate, separately evidenced decision, and keeping them apart is what lets
    the model express "signed but not yet countersigned".
    """

    def handler(session: Session) -> Mapping[str, object]:
        row = _load(session, command.agreement_id)
        _require_expected(
            row,
            expected_status=AgreementStatus.PROPOSED.value,
            expected_version=command.expected_version,
        )
        _require_bound_approval(row, command.evidence)
        row.approval_decision_ref = command.evidence.decision_ref
        row.approved_at = command.evidence.decided_at
        previous = _advance(row, AgreementStatus.APPROVED)
        session.flush()
        _record(
            session,
            row,
            command_id=command.command_id,
            event_type=facts.AGREEMENT_APPROVED_V1,
            from_status=previous,
            actor_admin_id=command.actor_admin_id,
            evidence=_approval_evidence_document(command.evidence),
        )
        return {"id": str(row.id)}

    process_once_platform(
        db,
        command_id=command.command_id,
        command_type=SCOPE_APPROVE,
        handler=handler,
    )
    return _view(_load(db, command.agreement_id))


def reject(db: Session, command: TransitionCommand) -> facts.AgreementView:
    """`proposed → draft`. Clears the frozen snapshot and the policy record.

    Clearing is the point: any approval collected against the old digest is now
    unusable, because there is no digest for it to bind to. Re-proposal computes
    a fresh one.
    """

    def handler(session: Session) -> Mapping[str, object]:
        row = _load(session, command.agreement_id)
        _require_expected(
            row,
            expected_status=AgreementStatus.PROPOSED.value,
            expected_version=command.expected_version,
        )
        row.accepted_snapshot = None
        row.content_hash = None
        row.approval_policy_code = None
        row.approval_policy_version = None
        row.approval_decision_ref = None
        row.approved_at = None
        row.last_reason = command.reason
        previous = _advance(row, AgreementStatus.DRAFT)
        session.flush()
        _record(
            session,
            row,
            command_id=command.command_id,
            event_type=facts.AGREEMENT_REJECTED_V1,
            from_status=previous,
            actor_admin_id=command.actor_admin_id,
            reason=command.reason,
        )
        return {"id": str(row.id)}

    process_once_platform(
        db,
        command_id=command.command_id,
        command_type=SCOPE_REJECT,
        handler=handler,
    )
    return _view(_load(db, command.agreement_id))


def activate(db: Session, command: ActivateCommand) -> facts.AgreementView:
    """`approved → active`, requiring BOTH evidences.

    The approval evidence is re-checked against the frozen digest, and the
    activation evidence must name a satisfied rule with a reference. Neither is
    a formality: without the first, activation would trust a status column an
    adapter could have written; without the second, "active" would mean "somebody
    pressed a button".

    Mutates no allocation, licence or deployment state. The assembly reacts to
    `agreement.activated.v1`.
    """

    def handler(session: Session) -> Mapping[str, object]:
        row = _load(session, command.agreement_id)
        _require_expected(
            row,
            expected_status=AgreementStatus.APPROVED.value,
            expected_version=command.expected_version,
        )
        _require_bound_approval(row, command.approval_evidence)
        if not command.activation_evidence.rule.strip():
            raise EvidenceRefusedError(
                f"agreement {row.id} cannot activate without a named activation "
                "rule; activation is rule-driven, not a bare request"
            )
        if not command.activation_evidence.reference.strip():
            raise EvidenceRefusedError(
                f"activation rule {command.activation_evidence.rule!r} requires a "
                "reference (countersignature, confirmation), not a bare request"
            )
        row.activation_rule = command.activation_evidence.rule
        row.activation_reference = command.activation_evidence.reference
        row.activated_at = command.activation_evidence.satisfied_at
        previous = _advance(row, AgreementStatus.ACTIVE)
        session.flush()
        _record(
            session,
            row,
            command_id=command.command_id,
            event_type=facts.AGREEMENT_ACTIVATED_V1,
            from_status=previous,
            actor_admin_id=command.actor_admin_id,
            evidence={
                "approval": _approval_evidence_document(command.approval_evidence),
                "activation": {
                    "rule": command.activation_evidence.rule,
                    "reference": command.activation_evidence.reference,
                    "satisfied_at": (
                        command.activation_evidence.satisfied_at.isoformat()
                    ),
                },
                "accepted_snapshot": row.accepted_snapshot,
            },
            extra={"activation_rule": row.activation_rule},
        )
        return {"id": str(row.id)}

    process_once_platform(
        db,
        command_id=command.command_id,
        command_type=SCOPE_ACTIVATE,
        handler=handler,
    )
    return _view(_load(db, command.agreement_id))


def suspend(db: Session, command: TransitionCommand) -> facts.AgreementView:
    """`active → suspended`. A restriction, never a deletion.

    The source's rule, kept verbatim in intent: suspension projects to
    entitlement RESTRICTION owned elsewhere. Payment failure or module
    disablement never implicitly deletes a counterparty's data (ADR-0003).
    """
    return _simple(
        db,
        command,
        scope=SCOPE_SUSPEND,
        allowed=frozenset({AgreementStatus.ACTIVE.value}),
        to=AgreementStatus.SUSPENDED,
        event_type=facts.AGREEMENT_SUSPENDED_V1,
        reason_field="suspension_reason",
    )


def reinstate(db: Session, command: TransitionCommand) -> facts.AgreementView:
    """`suspended → active`."""
    return _simple(
        db,
        command,
        scope=SCOPE_REINSTATE,
        allowed=frozenset({AgreementStatus.SUSPENDED.value}),
        to=AgreementStatus.ACTIVE,
        event_type=facts.AGREEMENT_REINSTATED_V1,
    )


def cancel(db: Session, command: TransitionCommand) -> facts.AgreementView:
    """`draft | proposed → cancelled`. Only before anything downstream exists."""
    return _simple(
        db,
        command,
        scope=SCOPE_CANCEL,
        allowed=frozenset(
            {AgreementStatus.DRAFT.value, AgreementStatus.PROPOSED.value}
        ),
        to=AgreementStatus.CANCELLED,
        event_type=facts.AGREEMENT_CANCELLED_V1,
    )


def terminate(db: Session, command: TerminateCommand) -> facts.AgreementView:
    """`active | suspended → terminated`, on an acknowledged impact preview."""

    def handler(session: Session) -> Mapping[str, object]:
        row = _load(session, command.agreement_id)
        _require_expected(
            row,
            expected_status=command.expected_status,
            expected_version=command.expected_version,
        )
        _require_status(
            row,
            frozenset({AgreementStatus.ACTIVE.value, AgreementStatus.SUSPENDED.value}),
        )
        if not command.impact_acknowledged:
            raise EvidenceRefusedError(
                f"terminating agreement {row.id} requires an acknowledged impact "
                "preview; a termination nobody was shown the consequences of is "
                "a support incident, not a decision"
            )
        row.termination_reason = command.reason
        row.last_reason = command.reason
        previous = _advance(row, AgreementStatus.TERMINATED)
        session.flush()
        _record(
            session,
            row,
            command_id=command.command_id,
            event_type=facts.AGREEMENT_TERMINATED_V1,
            from_status=previous,
            actor_admin_id=command.actor_admin_id,
            reason=command.reason,
            evidence={
                "effective_date": command.effective_date.isoformat(),
                "impact_acknowledged": True,
            },
        )
        return {"id": str(row.id)}

    process_once_platform(
        db,
        command_id=command.command_id,
        command_type=SCOPE_TERMINATE,
        handler=handler,
    )
    return _view(_load(db, command.agreement_id))


def expire(
    db: Session, command: TransitionCommand, *, as_of: date
) -> facts.AgreementView:
    """`active | suspended → expired`, guarded on the term having actually ended.

    Clock-driven, and the guard is the point: an expiry command that trusted its
    caller's word about the date would let a mis-scheduled job expire a live
    agreement a year early. `as_of` is passed in rather than read from the
    system clock so the decision is reproducible in a test and in a replay.
    """

    def handler(session: Session) -> Mapping[str, object]:
        row = _load(session, command.agreement_id)
        _require_expected(
            row,
            expected_status=command.expected_status,
            expected_version=command.expected_version,
        )
        _require_status(
            row,
            frozenset({AgreementStatus.ACTIVE.value, AgreementStatus.SUSPENDED.value}),
        )
        if as_of <= row.expiry_date:
            raise TransitionRefusedError(
                f"agreement {row.id} expires {row.expiry_date}; it is not expired "
                f"as of {as_of}"
            )
        previous = _advance(row, AgreementStatus.EXPIRED)
        session.flush()
        _record(
            session,
            row,
            command_id=command.command_id,
            event_type=facts.AGREEMENT_EXPIRED_V1,
            from_status=previous,
            actor_admin_id=command.actor_admin_id,
            evidence={
                "as_of": as_of.isoformat(),
                "expiry_date": row.expiry_date.isoformat(),
            },
        )
        return {"id": str(row.id)}

    process_once_platform(
        db,
        command_id=command.command_id,
        command_type=SCOPE_EXPIRE,
        handler=handler,
    )
    return _view(_load(db, command.agreement_id))


def amend(
    db: Session, command: AmendCommand, *, catalogue: CapabilityCatalogueReader
) -> facts.AgreementView:
    """Supersede an agreement with a new `draft` version of the same family.

    Returns the SUCCESSOR, not the predecessor — the caller's next action is
    always on the new version, and returning the superseded one would be a
    footgun shaped like a convenience.

    The predecessor may be `active`, `suspended` or `approved`. It may not be
    terminal: amending an expired or terminated agreement is a new agreement,
    not an amendment, and conflating them would let a superseded chain claim
    continuity it does not have.
    """
    if not command.lines:
        raise EmptyAgreementError("an amendment needs at least one promised line")
    _validate_against_catalogue(command.lines, catalogue)

    def handler(session: Session) -> Mapping[str, object]:
        predecessor = _load(session, command.agreement_id)
        _require_expected(
            predecessor,
            expected_status=None,
            expected_version=command.expected_version,
        )
        if predecessor.status in TERMINAL_STATUSES:
            raise TransitionRefusedError(
                f"agreement {predecessor.id} is {predecessor.status!r}; a terminal "
                "agreement is superseded by a new agreement, never amended"
            )
        if predecessor.superseded_by_id is not None:
            raise TransitionRefusedError(
                f"agreement {predecessor.id} is already superseded by "
                f"{predecessor.superseded_by_id}"
            )
        period = command.period or AgreementPeriod(
            effective_date=predecessor.effective_date,
            expiry_date=predecessor.expiry_date,
        )
        successor = Agreement(
            reference=command.reference,
            agreement_family_id=predecessor.agreement_family_id,
            agreement_version=predecessor.agreement_version + 1,
            counterparty_ref=predecessor.counterparty_ref,
            agreement_type=predecessor.agreement_type,
            status=AgreementStatus.DRAFT.value,
            effective_date=period.effective_date,
            expiry_date=period.expiry_date,
            supersedes_id=predecessor.id,
            record_version=1,
        )
        session.add(successor)
        session.flush()
        _add_lines(session, successor, command.lines)

        predecessor.superseded_by_id = successor.id
        predecessor.last_reason = command.reason
        previous = _advance(predecessor, AgreementStatus.SUPERSEDED)
        session.flush()

        _record(
            session,
            predecessor,
            command_id=command.command_id,
            event_type=facts.AGREEMENT_AMENDED_V1,
            from_status=previous,
            actor_admin_id=command.actor_admin_id,
            reason=command.reason,
            evidence={
                "superseded_by": str(successor.id),
                "successor_version": successor.agreement_version,
            },
            extra={"superseded_by": str(successor.id)},
        )
        return {"id": str(successor.id)}

    outcome = process_once_platform(
        db,
        command_id=command.command_id,
        command_type=SCOPE_AMEND,
        handler=handler,
    )
    return _view(_load(db, UUID(str(outcome.result["id"]))))


# ── Reads ───────────────────────────────────────────────────────────────────


def get(db: Session, agreement_id: UUID) -> facts.AgreementView | None:
    row = db.get(Agreement, agreement_id)
    return _view(row) if row is not None else None


def history(db: Session, agreement_id: UUID) -> tuple[facts.TransitionRecord, ...]:
    """The append-only transition history, oldest first."""
    rows = (
        db.execute(
            select(AgreementEvent)
            .where(AgreementEvent.agreement_id == agreement_id)
            .order_by(AgreementEvent.sequence)
        )
        .scalars()
        .all()
    )
    return tuple(
        facts.TransitionRecord(
            sequence=row.sequence,
            event_type=row.event_type,
            from_status=row.from_status,
            to_status=row.to_status,
            occurred_at=row.created_at,
            actor_ref=row.actor_ref,
            reason=row.reason,
            command_id=row.command_id,
        )
        for row in rows
    )


def family(db: Session, agreement_family_id: UUID) -> tuple[facts.AgreementView, ...]:
    """Every version of one commercial relationship, oldest version first."""
    rows = (
        db.execute(
            select(Agreement)
            .where(Agreement.agreement_family_id == agreement_family_id)
            .order_by(Agreement.agreement_version)
        )
        .scalars()
        .all()
    )
    return tuple(_view(row) for row in rows)


# ── Small helpers ───────────────────────────────────────────────────────────


def _terms_of(line: AgreementLine) -> Any:
    from dotmac_commercial_agreements.ports import CommercialTerms

    return CommercialTerms(
        unit_amount=line.unit_amount, currency_code=line.unit_currency_code
    )


def _approval_evidence_document(evidence: ApprovalEvidence) -> dict[str, Any]:
    """The approval evidence as it is stored — every field, nothing derived.

    Stored whole so an auditor reading one history row can reconstruct the
    decision without joining to an owner that may since have retired the policy.
    """
    return {
        "policy_code": evidence.policy_code,
        "policy_version": evidence.policy_version,
        "decision_ref": evidence.decision_ref,
        "content_digest": evidence.content_digest,
        "decided_at": evidence.decided_at.isoformat(),
        "approver_refs": list(evidence.approver_refs),
    }


def _simple(
    db: Session,
    command: TransitionCommand,
    *,
    scope: str,
    allowed: frozenset[str],
    to: AgreementStatus,
    event_type: str,
    reason_field: str | None = None,
) -> facts.AgreementView:
    """A transition whose only guard is the status it comes from."""

    def handler(session: Session) -> Mapping[str, object]:
        row = _load(session, command.agreement_id)
        _require_expected(
            row,
            expected_status=command.expected_status,
            expected_version=command.expected_version,
        )
        _require_status(row, allowed)
        if reason_field is not None:
            setattr(row, reason_field, command.reason)
        row.last_reason = command.reason
        previous = _advance(row, to)
        session.flush()
        _record(
            session,
            row,
            command_id=command.command_id,
            event_type=event_type,
            from_status=previous,
            actor_admin_id=command.actor_admin_id,
            reason=command.reason,
        )
        return {"id": str(row.id)}

    process_once_platform(
        db, command_id=command.command_id, command_type=scope, handler=handler
    )
    return _view(_load(db, command.agreement_id))


#: Kept for callers that want the module's clock in one place rather than
#: scattered `datetime.now(UTC)` calls. Nothing in this module reads it — every
#: timestamp that matters comes from the evidence that justified it, which is
#: what makes the history reproducible.
def _now() -> datetime:
    return datetime.now(UTC)


__all__ = [
    "AUDIT_ACTION_TRANSITIONED",
    "SCOPE_ACTIVATE",
    "SCOPE_AMEND",
    "SCOPE_APPROVE",
    "SCOPE_CANCEL",
    "SCOPE_DRAFT",
    "SCOPE_EXPIRE",
    "SCOPE_PROPOSE",
    "SCOPE_REINSTATE",
    "SCOPE_REJECT",
    "SCOPE_SUSPEND",
    "SCOPE_TERMINATE",
    "ActivateCommand",
    "AmendCommand",
    "ApproveCommand",
    "DraftCommand",
    "ProposeCommand",
    "TerminateCommand",
    "TransitionCommand",
    "accepted_snapshot",
    "activate",
    "amend",
    "approve",
    "cancel",
    "expire",
    "family",
    "get",
    "history",
    "open_draft",
    "propose",
    "reinstate",
    "reject",
    "snapshot_digest",
    "suspend",
    "terminate",
]
