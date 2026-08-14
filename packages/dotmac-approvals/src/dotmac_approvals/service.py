"""Approval lifecycle on two explicitly named plane surfaces (ADR-0026 § 5).

There is no `platform=` flag and no nullable tenant anywhere in this API. The
tenant functions take a `tenant_id` and cannot be called without one; the
platform functions have no such parameter and cannot be given one. A caller
therefore states which security context it is in as part of naming the
operation, and a mistake is a `TypeError` at the call site rather than a row in
the wrong plane.

The two surfaces share their RULES — every check lives in `policy`, which
imports no persistence — and duplicate only their row loading, because the
models genuinely differ. That is the intended shape: one behaviour, two declared
persistence planes.

**Transaction authority stays with the host.** Every function here `add`s and
`flush`es and never commits, rolls back, or creates a session (hard rule 8).
Ported deliberately AGAINST the source: ERP's service called `db.commit()` in
`submit_for_approval`, `approve`, `reject` and `cancel_request`, which makes a
caller unable to compose an approval with its own state change atomically — the
exact property a consuming domain needs when it records "approved" alongside
whatever it does next.

**This module never performs the approved transition.** It returns events; the
subject's owner reacts to them and runs its own guarded transition (§ 6). See
`outbox.py` for the optional adapter that writes them to the kernel outbox.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from dotmac_approvals import policy as rules
from dotmac_approvals.contracts import (
    EVENT_FOR_STATE,
    Actor,
    ApprovalEvent,
    ApprovalState,
    ContentChanged,
    DecisionAction,
    Evaluation,
    NotRequester,
    PolicyNotFound,
    PolicyRevision,
    PolicyVersionExists,
    RecordedDecision,
    RequestNotPending,
    validate_digest,
)
from dotmac_approvals.models import (
    ApprovalDecision,
    ApprovalPolicy,
    ApprovalRequest,
    PlatformApprovalDecision,
    PlatformApprovalPolicy,
    PlatformApprovalRequest,
)


@dataclass(frozen=True, slots=True)
class ApprovalOutcome:
    """What a lifecycle call produced.

    `events` is a tuple rather than a single event because a decision can both
    record and complete: the caller gets exactly what happened, and decides how
    to deliver it.
    """

    request_id: UUID
    evaluation: Evaluation
    events: tuple[ApprovalEvent, ...]

    @property
    def state(self) -> ApprovalState:
        return self.evaluation.state


def policy_document_digest(revision: PolicyRevision) -> str:
    """The digest of a policy revision's canonical document.

    Canonical means sorted keys and no insignificant whitespace, so the same
    policy hashes the same however it was constructed — which is what makes it
    usable as cutover evidence that an imported revision is the one it replaced.
    """
    canonical = json.dumps(
        revision.as_document(), sort_keys=True, separators=(",", ":")
    )
    return f"sha256:{hashlib.sha256(canonical.encode('utf-8')).hexdigest()}"


def _revision_of(
    levels: object, code: str, version: int, self_approval: bool
) -> PolicyRevision:
    document = {
        "policy_code": code,
        "version": version,
        "allow_self_approval": self_approval,
        "levels": levels,
    }
    return PolicyRevision.from_document(document)


def _recorded(
    decisions: Sequence[ApprovalDecision] | Sequence[PlatformApprovalDecision],
) -> tuple[RecordedDecision, ...]:
    return tuple(
        RecordedDecision(
            level=decision.level,
            actor_id=decision.actor_id,
            action=DecisionAction(decision.action),
        )
        for decision in decisions
    )


def _event(
    *,
    state: ApprovalState,
    request_id: UUID,
    subject_type: str,
    subject_id: str,
    policy_code: str,
    policy_version: int,
    content_digest: str,
) -> ApprovalEvent:
    return ApprovalEvent(
        event_type=EVENT_FOR_STATE[state],
        subject_type=subject_type,
        subject_id=subject_id,
        request_id=request_id,
        policy_code=policy_code,
        policy_version=policy_version,
        content_digest=content_digest,
        state=state,
    )


# ── Tenant plane ────────────────────────────────────────────────────────────


def publish_tenant_policy_version(
    db: Session, *, tenant_id: UUID, revision: PolicyRevision
) -> ApprovalPolicy:
    """Publish an immutable tenant policy revision.

    Raises `PolicyVersionExists` rather than updating: a published revision is
    the thing existing requests were opened against, so rewriting it would
    reinterpret decisions already made.
    """
    existing = db.execute(
        select(ApprovalPolicy).where(
            ApprovalPolicy.tenant_id == tenant_id,
            ApprovalPolicy.policy_code == revision.policy_code,
            ApprovalPolicy.version == revision.version,
        )
    ).scalar_one_or_none()
    if existing is not None:
        raise PolicyVersionExists(
            f"{revision.policy_code} v{revision.version} already exists for this "
            "tenant — policy revisions are immutable"
        )
    row = ApprovalPolicy(
        tenant_id=tenant_id,
        policy_code=revision.policy_code,
        version=revision.version,
        levels=[level.as_document() for level in revision.levels],
        allow_self_approval=revision.allow_self_approval,
        document_digest=policy_document_digest(revision),
    )
    db.add(row)
    db.flush()
    return row


def _tenant_policy(
    db: Session, tenant_id: UUID, code: str, version: int
) -> PolicyRevision:
    row = db.execute(
        select(ApprovalPolicy).where(
            ApprovalPolicy.tenant_id == tenant_id,
            ApprovalPolicy.policy_code == code,
            ApprovalPolicy.version == version,
        )
    ).scalar_one_or_none()
    if row is None:
        # Fail CLOSED — never "no policy, therefore no approval required".
        raise PolicyNotFound(f"no tenant policy {code} v{version}")
    return _revision_of(
        row.levels, row.policy_code, row.version, row.allow_self_approval
    )


def _tenant_request(db: Session, tenant_id: UUID, request_id: UUID) -> ApprovalRequest:
    row = db.execute(
        select(ApprovalRequest).where(
            ApprovalRequest.tenant_id == tenant_id,
            ApprovalRequest.id == request_id,
        )
    ).scalar_one_or_none()
    if row is None:
        raise PolicyNotFound(f"no tenant approval request {request_id}")
    return row


def _tenant_decisions(
    db: Session, tenant_id: UUID, request_id: UUID
) -> tuple[ApprovalDecision, ...]:
    return tuple(
        db.execute(
            select(ApprovalDecision)
            .where(
                ApprovalDecision.tenant_id == tenant_id,
                ApprovalDecision.request_id == request_id,
            )
            .order_by(ApprovalDecision.decided_at, ApprovalDecision.id)
        )
        .scalars()
        .all()
    )


def request_tenant_approval(
    db: Session,
    *,
    tenant_id: UUID,
    policy_code: str,
    policy_version: int,
    subject_type: str,
    subject_id: str,
    content_digest: str,
    requested_by: UUID,
    idempotency_key: str,
) -> ApprovalOutcome:
    """Open a tenant approval request against an exact policy revision.

    Idempotent by `idempotency_key`: the same key with the same content returns
    the existing request, and the same key with DIFFERENT content is a conflict
    rather than a silent second opinion.
    """
    validate_digest(content_digest)
    revision = _tenant_policy(db, tenant_id, policy_code, policy_version)

    existing = db.execute(
        select(ApprovalRequest).where(
            ApprovalRequest.tenant_id == tenant_id,
            ApprovalRequest.idempotency_key == idempotency_key,
        )
    ).scalar_one_or_none()
    if existing is not None:
        if (
            existing.content_digest != content_digest
            or existing.policy_code != policy_code
            or existing.policy_version != policy_version
        ):
            raise ContentChanged(
                f"idempotency key {idempotency_key!r} was used for different "
                "content or a different policy revision"
            )
        return _tenant_outcome(db, tenant_id, existing, revision, events=())

    row = ApprovalRequest(
        tenant_id=tenant_id,
        policy_code=policy_code,
        policy_version=policy_version,
        subject_type=subject_type,
        subject_id=subject_id,
        content_digest=content_digest,
        requested_by=requested_by,
        state=str(ApprovalState.PENDING),
        current_level=1,
        idempotency_key=idempotency_key,
    )
    db.add(row)
    db.flush()
    event = _event(
        state=ApprovalState.PENDING,
        request_id=row.id,
        subject_type=subject_type,
        subject_id=subject_id,
        policy_code=policy_code,
        policy_version=policy_version,
        content_digest=content_digest,
    )
    return _tenant_outcome(db, tenant_id, row, revision, events=(event,))


def record_tenant_decision(
    db: Session,
    *,
    tenant_id: UUID,
    request_id: UUID,
    actor: Actor,
    action: DecisionAction,
    content_digest: str,
    comment: str | None = None,
    delegated_from: UUID | None = None,
) -> ApprovalOutcome:
    """Record one immutable tenant decision and re-evaluate the request.

    `content_digest` is required, not optional: an approver approves CONTENT.
    If the subject changed since the request opened, the caller's digest no
    longer matches and the decision is refused — which is how a content change
    invalidates a prior approval rather than silently inheriting it.
    """
    request = _tenant_request(db, tenant_id, request_id)
    if request.state != str(ApprovalState.PENDING):
        raise RequestNotPending(f"request {request_id} is {request.state}, not pending")
    if request.content_digest != content_digest:
        raise ContentChanged(
            f"request {request_id} was opened for {request.content_digest}, but "
            f"the decision presents {content_digest} — the content changed, so "
            "the prior approval no longer applies"
        )

    revision = _tenant_policy(
        db, tenant_id, request.policy_code, request.policy_version
    )
    decisions = _recorded(_tenant_decisions(db, tenant_id, request_id))

    if action is DecisionAction.APPROVE:
        rules.authorise_approval(
            revision,
            current_level=request.current_level,
            actor=actor,
            requested_by=request.requested_by,
            decisions=decisions,
        )
    else:
        # A rejection needs eligibility for the level, but neither quorum nor a
        # self-approval check: refusing your own request is always permitted.
        level = revision.level(request.current_level)
        rules.check_not_duplicate(level, actor=actor, decisions=decisions)
        rules.check_eligibility(level, actor)
        rules.check_mfa(level, actor)

    db.add(
        ApprovalDecision(
            tenant_id=tenant_id,
            request_id=request_id,
            level=request.current_level,
            actor_id=actor.actor_id,
            action=str(action),
            comment=comment,
            delegated_from=delegated_from,
            mfa_verified=actor.mfa_verified,
            decided_at=datetime.now(UTC),
        )
    )
    db.flush()

    if action is DecisionAction.REJECT:
        request.state = str(ApprovalState.REJECTED)
        request.completed_at = datetime.now(UTC)
        request.note = comment
        db.flush()
        return _tenant_outcome(
            db, tenant_id, request, revision, events=(_state_event(request),)
        )

    updated = _recorded(_tenant_decisions(db, tenant_id, request_id))
    evaluation = rules.evaluate(
        revision,
        state=ApprovalState.PENDING,
        current_level=request.current_level,
        decisions=updated,
    )
    request.current_level = evaluation.current_level
    if evaluation.state is ApprovalState.APPROVED:
        request.state = str(ApprovalState.APPROVED)
        request.completed_at = datetime.now(UTC)
        db.flush()
        return _tenant_outcome(
            db, tenant_id, request, revision, events=(_state_event(request),)
        )
    db.flush()
    return _tenant_outcome(db, tenant_id, request, revision, events=())


def cancel_tenant_request(
    db: Session,
    *,
    tenant_id: UUID,
    request_id: UUID,
    cancelled_by: UUID,
    reason: str,
) -> ApprovalOutcome:
    """Cancel a pending tenant request. Requester-only, ported from ERP."""
    request = _tenant_request(db, tenant_id, request_id)
    if request.state != str(ApprovalState.PENDING):
        raise RequestNotPending(f"request {request_id} is {request.state}, not pending")
    if request.requested_by != cancelled_by:
        raise NotRequester("only the original requester may cancel this request")
    request.state = str(ApprovalState.CANCELLED)
    request.completed_at = datetime.now(UTC)
    request.note = reason
    db.flush()
    revision = _tenant_policy(
        db, tenant_id, request.policy_code, request.policy_version
    )
    return _tenant_outcome(
        db, tenant_id, request, revision, events=(_state_event(request),)
    )


def evaluate_tenant_approval(
    db: Session, *, tenant_id: UUID, request_id: UUID
) -> Evaluation:
    """Is this tenant request approved, and is that still valid?"""
    request = _tenant_request(db, tenant_id, request_id)
    revision = _tenant_policy(
        db, tenant_id, request.policy_code, request.policy_version
    )
    return rules.evaluate(
        revision,
        state=ApprovalState(request.state),
        current_level=request.current_level,
        decisions=_recorded(_tenant_decisions(db, tenant_id, request_id)),
    )


def _tenant_outcome(
    db: Session,
    tenant_id: UUID,
    request: ApprovalRequest,
    revision: PolicyRevision,
    *,
    events: tuple[ApprovalEvent, ...],
) -> ApprovalOutcome:
    evaluation = rules.evaluate(
        revision,
        state=ApprovalState(request.state),
        current_level=request.current_level,
        decisions=_recorded(_tenant_decisions(db, tenant_id, request.id)),
    )
    return ApprovalOutcome(request.id, evaluation, events)


def _state_event(request: ApprovalRequest | PlatformApprovalRequest) -> ApprovalEvent:
    return _event(
        state=ApprovalState(request.state),
        request_id=request.id,
        subject_type=request.subject_type,
        subject_id=request.subject_id,
        policy_code=request.policy_code,
        policy_version=request.policy_version,
        content_digest=request.content_digest,
    )


# ── Platform plane ──────────────────────────────────────────────────────────


def publish_platform_policy_version(
    db: Session, *, revision: PolicyRevision
) -> PlatformApprovalPolicy:
    """Publish an immutable control-plane policy revision."""
    existing = db.execute(
        select(PlatformApprovalPolicy).where(
            PlatformApprovalPolicy.policy_code == revision.policy_code,
            PlatformApprovalPolicy.version == revision.version,
        )
    ).scalar_one_or_none()
    if existing is not None:
        raise PolicyVersionExists(
            f"{revision.policy_code} v{revision.version} already exists — policy "
            "revisions are immutable"
        )
    row = PlatformApprovalPolicy(
        policy_code=revision.policy_code,
        version=revision.version,
        levels=[level.as_document() for level in revision.levels],
        allow_self_approval=revision.allow_self_approval,
        document_digest=policy_document_digest(revision),
    )
    db.add(row)
    db.flush()
    return row


def _platform_policy(db: Session, code: str, version: int) -> PolicyRevision:
    row = db.execute(
        select(PlatformApprovalPolicy).where(
            PlatformApprovalPolicy.policy_code == code,
            PlatformApprovalPolicy.version == version,
        )
    ).scalar_one_or_none()
    if row is None:
        raise PolicyNotFound(f"no platform policy {code} v{version}")
    return _revision_of(
        row.levels, row.policy_code, row.version, row.allow_self_approval
    )


def _platform_request(db: Session, request_id: UUID) -> PlatformApprovalRequest:
    row = db.get(PlatformApprovalRequest, request_id)
    if row is None:
        raise PolicyNotFound(f"no platform approval request {request_id}")
    return row


def _platform_decisions(
    db: Session, request_id: UUID
) -> tuple[PlatformApprovalDecision, ...]:
    return tuple(
        db.execute(
            select(PlatformApprovalDecision)
            .where(PlatformApprovalDecision.request_id == request_id)
            .order_by(PlatformApprovalDecision.decided_at, PlatformApprovalDecision.id)
        )
        .scalars()
        .all()
    )


def request_platform_approval(
    db: Session,
    *,
    policy_code: str,
    policy_version: int,
    subject_type: str,
    subject_id: str,
    content_digest: str,
    requested_by: UUID,
    idempotency_key: str,
) -> ApprovalOutcome:
    """Open a control-plane approval request against an exact policy revision."""
    validate_digest(content_digest)
    revision = _platform_policy(db, policy_code, policy_version)

    existing = db.execute(
        select(PlatformApprovalRequest).where(
            PlatformApprovalRequest.idempotency_key == idempotency_key
        )
    ).scalar_one_or_none()
    if existing is not None:
        if (
            existing.content_digest != content_digest
            or existing.policy_code != policy_code
            or existing.policy_version != policy_version
        ):
            raise ContentChanged(
                f"idempotency key {idempotency_key!r} was used for different "
                "content or a different policy revision"
            )
        return _platform_outcome(db, existing, revision, events=())

    row = PlatformApprovalRequest(
        policy_code=policy_code,
        policy_version=policy_version,
        subject_type=subject_type,
        subject_id=subject_id,
        content_digest=content_digest,
        requested_by=requested_by,
        state=str(ApprovalState.PENDING),
        current_level=1,
        idempotency_key=idempotency_key,
    )
    db.add(row)
    db.flush()
    event = _event(
        state=ApprovalState.PENDING,
        request_id=row.id,
        subject_type=subject_type,
        subject_id=subject_id,
        policy_code=policy_code,
        policy_version=policy_version,
        content_digest=content_digest,
    )
    return _platform_outcome(db, row, revision, events=(event,))


def record_platform_decision(
    db: Session,
    *,
    request_id: UUID,
    actor: Actor,
    action: DecisionAction,
    content_digest: str,
    comment: str | None = None,
    delegated_from: UUID | None = None,
) -> ApprovalOutcome:
    """Record one immutable control-plane decision and re-evaluate."""
    request = _platform_request(db, request_id)
    if request.state != str(ApprovalState.PENDING):
        raise RequestNotPending(f"request {request_id} is {request.state}, not pending")
    if request.content_digest != content_digest:
        raise ContentChanged(
            f"request {request_id} was opened for {request.content_digest}, but "
            f"the decision presents {content_digest} — the content changed, so "
            "the prior approval no longer applies"
        )

    revision = _platform_policy(db, request.policy_code, request.policy_version)
    decisions = _recorded(_platform_decisions(db, request_id))

    if action is DecisionAction.APPROVE:
        rules.authorise_approval(
            revision,
            current_level=request.current_level,
            actor=actor,
            requested_by=request.requested_by,
            decisions=decisions,
        )
    else:
        level = revision.level(request.current_level)
        rules.check_not_duplicate(level, actor=actor, decisions=decisions)
        rules.check_eligibility(level, actor)
        rules.check_mfa(level, actor)

    db.add(
        PlatformApprovalDecision(
            request_id=request_id,
            level=request.current_level,
            actor_id=actor.actor_id,
            action=str(action),
            comment=comment,
            delegated_from=delegated_from,
            mfa_verified=actor.mfa_verified,
            decided_at=datetime.now(UTC),
        )
    )
    db.flush()

    if action is DecisionAction.REJECT:
        request.state = str(ApprovalState.REJECTED)
        request.completed_at = datetime.now(UTC)
        request.note = comment
        db.flush()
        return _platform_outcome(db, request, revision, events=(_state_event(request),))

    updated = _recorded(_platform_decisions(db, request_id))
    evaluation = rules.evaluate(
        revision,
        state=ApprovalState.PENDING,
        current_level=request.current_level,
        decisions=updated,
    )
    request.current_level = evaluation.current_level
    if evaluation.state is ApprovalState.APPROVED:
        request.state = str(ApprovalState.APPROVED)
        request.completed_at = datetime.now(UTC)
        db.flush()
        return _platform_outcome(db, request, revision, events=(_state_event(request),))
    db.flush()
    return _platform_outcome(db, request, revision, events=())


def cancel_platform_request(
    db: Session, *, request_id: UUID, cancelled_by: UUID, reason: str
) -> ApprovalOutcome:
    """Cancel a pending control-plane request. Requester-only."""
    request = _platform_request(db, request_id)
    if request.state != str(ApprovalState.PENDING):
        raise RequestNotPending(f"request {request_id} is {request.state}, not pending")
    if request.requested_by != cancelled_by:
        raise NotRequester("only the original requester may cancel this request")
    request.state = str(ApprovalState.CANCELLED)
    request.completed_at = datetime.now(UTC)
    request.note = reason
    db.flush()
    revision = _platform_policy(db, request.policy_code, request.policy_version)
    return _platform_outcome(db, request, revision, events=(_state_event(request),))


def evaluate_platform_approval(db: Session, *, request_id: UUID) -> Evaluation:
    """Is this control-plane request approved, and is that still valid?"""
    request = _platform_request(db, request_id)
    revision = _platform_policy(db, request.policy_code, request.policy_version)
    return rules.evaluate(
        revision,
        state=ApprovalState(request.state),
        current_level=request.current_level,
        decisions=_recorded(_platform_decisions(db, request_id)),
    )


def _platform_outcome(
    db: Session,
    request: PlatformApprovalRequest,
    revision: PolicyRevision,
    *,
    events: tuple[ApprovalEvent, ...],
) -> ApprovalOutcome:
    evaluation = rules.evaluate(
        revision,
        state=ApprovalState(request.state),
        current_level=request.current_level,
        decisions=_recorded(_platform_decisions(db, request.id)),
    )
    return ApprovalOutcome(request.id, evaluation, events)


__all__ = [
    "ApprovalOutcome",
    "cancel_platform_request",
    "cancel_tenant_request",
    "evaluate_platform_approval",
    "evaluate_tenant_approval",
    "policy_document_digest",
    "publish_platform_policy_version",
    "publish_tenant_policy_version",
    "record_platform_decision",
    "record_tenant_decision",
    "request_platform_approval",
    "request_tenant_approval",
]
