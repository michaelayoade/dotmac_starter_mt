"""One owner for how many attempts a learner has, and whether they are spent.

Before this module the question "may this learner sit again?" was answered in
five places, each spelling the same expression by hand::

    activity.max_attempts is not None and used >= activity.max_attempts

``assessment.submit_activity`` used it to refuse a submission,
``assessment.reveal_feedback`` to decide whether the answer key may be shown,
``learner_dashboard`` to render "1 of 3 attempts left", ``success_queue`` to
flag a stuck learner, and ``insights`` to report ``attempts_exhausted``. All
five agreed only because none of them had changed yet.

Granting an extra attempt is what breaks that. An entitlement that lives on
``Activity.max_attempts`` alone cannot express "this learner, this activity,
one more go" — so the moment grants exist, every one of those five expressions
is wrong in a different way. The dashboard would promise a retake the submit
path refuses, or the submit path would allow one the answer key had already
been revealed for, which hands back a graded attempt the learner has the
answers to.

So the entitlement is computed here, once, and the five callers ask.

See ``docs/adr/0005-assessment-engine-owns-exam-logic.md`` — this is the same
correction one layer down from selection and validity: a decision with several
implementations drifts as soon as any of them moves.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol
from uuid import UUID

from dotmac_kernel import write_audit_event
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import results
from .attempt_contracts import (
    AttemptAuditContext,
    AttemptGrantFact,
    GrantExtraAttemptCommand,
)
from .attempt_models import AttemptGrant
from .models import Submission
from .references import ActivityFact
from .result_contracts import (
    AdmissionDecision,
    AdmissionObservation,
    MCQResultCommand,
    ResultWrite,
)
from .results import _transaction, _unchanged


class ActivityTerms(Protocol):
    """The activity settings used by entitlement arithmetic."""

    @property
    def max_attempts(self) -> int | None: ...

    @property
    def min_attempt_interval_seconds(self) -> int: ...


class BoundGrantReader(Protocol):
    """Read the grant total for an already-bound learner and activity."""

    def total(self) -> int: ...


@dataclass(frozen=True)
class Entitlement:
    """What a learner may still do on one activity.

    ``limit`` is None for an uncapped activity, in which case nothing is ever
    exhausted and ``remaining`` is None rather than a large number — callers
    render "unlimited", and a number would invite them to count down from it.

    ``next_allowed_at`` is the cooldown horizon (HM-13): the earliest instant
    a further attempt may be admitted. It is not exhaustion — attempts may
    remain while the cooldown is still running.
    """

    used: int
    granted: int
    limit: int | None
    next_allowed_at: datetime | None = None

    @property
    def remaining(self) -> int | None:
        if self.limit is None:
            return None
        return max(self.limit - self.used, 0)

    @property
    def exhausted(self) -> bool:
        return self.limit is not None and self.used >= self.limit


def granted_for(
    db: Session, *, tenant_id: UUID, person_id: UUID, activity_id: UUID
) -> int:
    """Extra attempts this learner has been granted on this activity."""
    return int(
        db.scalar(
            select(func.coalesce(func.sum(AttemptGrant.extra_attempts), 0))
            .where(AttemptGrant.tenant_id == tenant_id)
            .where(AttemptGrant.activity_id == activity_id)
            .where(AttemptGrant.person_id == person_id)
        )
        or 0
    )


def granted_by_activity(
    db: Session, *, tenant_id: UUID, person_id: UUID
) -> dict[UUID, int]:
    """Grants per activity for one learner, in ONE grouped query.

    The dashboard and the success queue both render every activity on a course
    at once; asking per activity would turn one page into N queries.
    """
    rows = db.execute(
        select(AttemptGrant.activity_id, func.sum(AttemptGrant.extra_attempts))
        .where(AttemptGrant.tenant_id == tenant_id)
        .where(AttemptGrant.person_id == person_id)
        .group_by(AttemptGrant.activity_id)
    ).all()
    return {activity_id: int(n or 0) for activity_id, n in rows}


def entitlement(
    activity: ActivityTerms,
    *,
    used: int,
    granted: int = 0,
    last_attempt_at: datetime | None = None,
) -> Entitlement:
    """The pure part: what the limit is, given the counts.

    Separate from the queries so callers that have already batched their counts
    — the dashboard, the success queue — reuse the same arithmetic without
    going back to the database per activity.
    """
    limit = None if activity.max_attempts is None else activity.max_attempts + granted
    interval = activity.min_attempt_interval_seconds
    if interval > 0 and last_attempt_at is not None:
        if last_attempt_at.tzinfo is None:
            last_attempt_at = last_attempt_at.replace(tzinfo=UTC)
        next_allowed_at = last_attempt_at + timedelta(seconds=interval)
    else:
        next_allowed_at = None
    return Entitlement(
        used=used, granted=granted, limit=limit, next_allowed_at=next_allowed_at
    )


def entitlement_with_grants(
    activity: ActivityTerms,
    *,
    used: int,
    reader: BoundGrantReader,
    last_attempt_at: datetime | None = None,
) -> Entitlement:
    """Apply the shared arithmetic to terms and one bound grant read."""
    return entitlement(
        activity, used=used, granted=reader.total(), last_attempt_at=last_attempt_at
    )


@dataclass(frozen=True)
class _LearnerGrantReader:
    db: Session
    tenant_id: UUID
    person_id: UUID
    activity_id: UUID

    def total(self) -> int:
        return granted_for(
            self.db,
            tenant_id=self.tenant_id,
            person_id=self.person_id,
            activity_id=self.activity_id,
        )


def for_learner(
    db: Session, *, tenant_id: UUID, person_id: UUID, activity: ActivityFact, used: int
) -> Entitlement:
    """The entitlement, fetching this learner's grants and last attempt."""
    reader = _LearnerGrantReader(
        db, tenant_id=tenant_id, person_id=person_id, activity_id=activity.id
    )
    last_attempt_at = (
        db.scalar(
            select(func.max(Submission.created_at)).where(
                Submission.tenant_id == tenant_id,
                Submission.person_id == person_id,
                Submission.activity_id == activity.id,
            )
        )
        if activity.min_attempt_interval_seconds > 0
        else None
    )
    return entitlement_with_grants(
        activity, used=used, reader=reader, last_attempt_at=last_attempt_at
    )


def grant_extra_attempt(
    db: Session,
    payload: GrantExtraAttemptCommand,
    *,
    audit_context: AttemptAuditContext,
) -> AttemptGrantFact:
    """Append a grant and its required canonical Kernel audit in caller scope."""
    boundary = _transaction(db)
    if audit_context.tenant_id != payload.tenant_id:
        raise ValueError("attempt audit context scope does not match command")
    if payload.extra_attempts < 1:
        raise ValueError("a grant must add at least one attempt")
    if not (payload.reason or "").strip():
        raise ValueError("a grant must record why it was given")
    row = AttemptGrant(
        tenant_id=payload.tenant_id,
        person_id=payload.person_id,
        activity_id=payload.activity_id,
        reason=payload.reason.strip(),
        granted_by=payload.granted_by,
        extra_attempts=payload.extra_attempts,
    )
    db.add(row)
    db.flush()
    write_audit_event(
        db,
        tenant_id=payload.tenant_id,
        actor_type=audit_context.actor_type,
        actor_id=audit_context.actor_id,
        actor_label=audit_context.actor_label,
        source_application=audit_context.source_application,
        actor_party_id=payload.granted_by,
        action="attempt_granted",
        entity_type="activity",
        entity_id=str(payload.activity_id),
        details={
            "person_id": str(payload.person_id),
            "extra_attempts": payload.extra_attempts,
            "reason": row.reason,
        },
    )
    _unchanged(db, boundary)
    return AttemptGrantFact(
        **{name: getattr(row, name) for name in AttemptGrantFact.__dataclass_fields__}
    )


@dataclass(frozen=True)
class BoundAttemptPolicy:
    """The source grant reader bound to this exact caller Session and scope."""

    db: Session
    tenant_id: UUID
    person_id: UUID
    activity_id: UUID

    def evaluate(self, observation: AdmissionObservation) -> AdmissionDecision:
        boundary = _transaction(self.db)
        if (observation.tenant_id, observation.person_id, observation.activity_id) != (
            self.tenant_id,
            self.person_id,
            self.activity_id,
        ):
            raise ValueError(
                "attempt admission observation scope does not match policy"
            )
        reader = _LearnerGrantReader(
            self.db, self.tenant_id, self.person_id, self.activity_id
        )
        ent = entitlement_with_grants(
            observation,
            used=observation.used,
            reader=reader,
            last_attempt_at=observation.last_attempt_at,
        )
        allowed = not ent.exhausted
        if allowed and ent.next_allowed_at is not None:
            horizon = ent.next_allowed_at
            if horizon.tzinfo is None:
                horizon = horizon.replace(tzinfo=UTC)
            allowed = horizon <= datetime.now(UTC)
        _unchanged(self.db, boundary)
        return AdmissionDecision(allowed=allowed)


def record_mcq_result_with_attempt_policy(
    db: Session, payload: MCQResultCommand
) -> ResultWrite:
    """Delegate to the result writer and its required consequences."""
    boundary = _transaction(db)
    result = results.record_mcq_result(
        db,
        payload,
        policy=BoundAttemptPolicy(
            db, payload.tenant_id, payload.person_id, payload.activity_id
        ),
    )
    _unchanged(db, boundary)
    return result


__all__ = [
    "ActivityTerms",
    "BoundGrantReader",
    "Entitlement",
    "granted_for",
    "granted_by_activity",
    "entitlement",
    "entitlement_with_grants",
    "for_learner",
    "grant_extra_attempt",
    "BoundAttemptPolicy",
    "record_mcq_result_with_attempt_policy",
]
