"""Source explainable Success Queue decisions in the caller transaction."""

from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime, timedelta
from math import floor
from typing import cast
from uuid import UUID, uuid4

from dotmac_kernel.audit import write_audit_event
from dotmac_kernel.models import Party, PartyPerson
from pydantic import JsonValue
from sqlalchemy import case, func, inspect, select
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import set_committed_value
from sqlalchemy.sql.elements import UnaryExpression

from . import attempt_policy
from . import events as learning_events
from . import todo as todo_service
from .account_contracts import ActiveAccountSelection
from .assessment_reads import attempts_used
from .attempt_contracts import AttemptAuditContext
from .delivery_models import (
    REASON_BELOW_PASSING,
    REASON_CERTIFICATE_BLOCKED,
    REASON_FAILED_FINAL,
    REASON_INACTIVITY,
    REASON_OVERDUE_WORK,
    STATUS_ACKNOWLEDGED,
    STATUS_OPEN,
    STATUS_RESOLVED,
    SuccessQueueEntry,
)
from .gradebook import attempted_grade
from .models import Activity, Certificate, Course, CourseCompletion
from .participation_models import Cohort, CourseOffering, Enrollment
from .projections import best_scores_for
from .references import _activity_fact
from .results import _transaction, _unchanged
from .success_contracts import (
    QueueFact,
    QueueListFact,
    SegmentDeliveryProtocol,
    SegmentMessageEffect,
    SegmentMessageFact,
    SegmentMessageResult,
    SuccessBadRequest,
    SuccessNotFound,
    SuccessPersonFact,
    SuccessTerms,
    SuccessTransitionEffect,
    SweepResult,
)


def _fact(row: SuccessQueueEntry) -> QueueFact:
    return QueueFact(
        **{
            field: deepcopy(getattr(row, field))
            for field in QueueFact.__dataclass_fields__
        }
    )


def _person(party: Party, profile: PartyPerson) -> SuccessPersonFact:
    return SuccessPersonFact(
        party.id, party.tenant_id, profile.first_name, profile.last_name, party.email
    )


# Severity is stored as text but must sort highest-risk first, not alphabetically
# ("high" < "medium" < "low" as strings would surface medium ahead of high).
_SEVERITY_RANK = {"high": 3, "medium": 2, "low": 1}


def severity_order() -> UnaryExpression[int]:
    """Descending severity ordering for ``SuccessQueueEntry`` queries — the one
    place the text-vs-rank mapping lives, shared by the queue view and the CSV."""
    return case(_SEVERITY_RANK, value=SuccessQueueEntry.severity, else_=0).desc()


SEGMENT_KEYS = (
    "inactive",
    "overdue_work",
    "below_passing",
    "failed_final",
    "almost_complete",
)
# Attendance-based segments arrive with roadmap P4; stated, not silently absent.
SEGMENTS_DEFERRED = ("low_attendance",)


# ---------------------------------------------------------------------------
# Rule evaluation — each returns None or (facts, severity, recommendation)
# ---------------------------------------------------------------------------


def _student_cohort_ids(db: Session, tenant_id: UUID, person_id: UUID) -> list[UUID]:
    return list(
        db.scalars(
            select(Enrollment.cohort_id)
            .where(Enrollment.tenant_id == tenant_id)
            .where(Enrollment.person_id == person_id)
            .where(Enrollment.role_in_cohort == "student")
            .where(Enrollment.status == "active")
        ).all()
    )


def _active_course_ids(
    db: Session, tenant_id: UUID, cohort_ids: list[UUID]
) -> list[UUID]:
    if not cohort_ids:
        return []
    return list(
        db.scalars(
            select(CourseOffering.course_id)
            .where(CourseOffering.tenant_id == tenant_id)
            .where(CourseOffering.cohort_id.in_(cohort_ids))
            .where(CourseOffering.status == "active")
        ).all()
    )


def _rule_inactivity(
    db: Session,
    *,
    tenant_id: UUID,
    person_id: UUID,
    enrolled_since: datetime | None,
    now: datetime,
    terms: SuccessTerms,
) -> tuple[dict[str, JsonValue], str, str] | None:
    threshold_days = terms.inactive_days
    last = learning_events.last_activity_at(
        db, tenant_id=tenant_id, person_id=person_id
    )
    anchor = last or enrolled_since
    if anchor is None:
        return None
    days = (now - anchor).days
    if days < threshold_days:
        return None
    severity = (
        "high" if days >= threshold_days * terms.inactivity_high_factor else "medium"
    )
    facts: dict[str, JsonValue] = {
        "last_activity_at": anchor.isoformat(),
        "days_inactive": days,
        "threshold_days": threshold_days,
        "never_active": last is None,
    }
    action = "Reach out directly — a short call or message; ask what is blocking them."
    return facts, severity, action


def _rule_overdue(
    db: Session, *, tenant_id: UUID, person_id: UUID, now: datetime, terms: SuccessTerms
) -> tuple[dict[str, JsonValue], str, str] | None:
    overdue = [
        r
        for r in todo_service.deadline_rows(
            db, tenant_id=tenant_id, person_id=person_id
        )
        if r.due_at < now
    ]
    if not overdue:
        return None
    oldest = min(r.due_at for r in overdue)
    days_late = (now - oldest).days
    severity = (
        "high"
        if len(overdue) >= terms.overdue_high_count
        or days_late > terms.overdue_high_days
        else "medium"
    )
    facts: dict[str, JsonValue] = {
        "overdue_count": len(overdue),
        "oldest_due_at": oldest.isoformat(),
        "days_late": days_late,
        "titles": cast(
            JsonValue, [r.activity.title for r in overdue][: terms.overdue_title_limit]
        ),
    }
    action = (
        "Agree a catch-up plan for the overdue work; consider a deadline extension."
    )
    return facts, severity, action


# Volume gate for the below-passing rule: only fire once enough of the course
# has actually been graded, so a learner one activity into a ten-activity course
# is not flagged failing. Missing due work is the overdue rule's job.


def _rule_below_passing(
    db: Session,
    *,
    tenant_id: UUID,
    person_id: UUID,
    course_ids: list[UUID],
    terms: SuccessTerms,
) -> tuple[dict[str, JsonValue], str, str] | None:
    min_grade_pct = terms.min_grade_pct
    worst: dict[str, JsonValue] | None = None
    for course_id in course_ids:
        # Graded-only weighted grade: score the learner on submitted work, not
        # on not-yet-due activities counted as 0.
        g = attempted_grade(
            db, tenant_id=tenant_id, person_id=person_id, course_id=course_id
        )
        if g.graded_count < terms.below_passing_min_graded:
            continue
        if g.graded_weight_fraction < terms.below_passing_min_weight_fraction:
            continue
        if g.pct < min_grade_pct and (
            worst is None or g.pct < cast(int, worst["grade_pct"])
        ):
            course = db.get(Course, course_id)
            worst = {
                "course_id": str(course_id),
                "course_title": course.title if course else "",
                "grade_pct": g.pct,
                "graded_count": g.graded_count,
                "graded_weight_pct": round(g.graded_weight_fraction * 100),
                "required_pct": min_grade_pct,
            }
    if worst is None:
        return None
    severity = (
        "high"
        if cast(int, worst["grade_pct"])
        < floor(min_grade_pct * terms.below_passing_high_grade_factor)
        else "medium"
    )
    action = (
        "Review their weakest activities together; point them at the relevant chapters."
    )
    return worst, severity, action


def _rule_failed_final(
    db: Session, *, tenant_id: UUID, person_id: UUID, course_ids: list[UUID]
) -> tuple[dict[str, JsonValue], str, str] | None:
    if not course_ids:
        return None
    finals = db.scalars(
        select(Activity)
        .where(Activity.tenant_id == tenant_id)
        .where(Activity.course_id.in_(course_ids))
        .where(Activity.chapter_number.is_(None))
        .where(Activity.pass_threshold > 0)
    ).all()
    for final in finals:
        best = best_scores_for(
            db, tenant_id=tenant_id, person_id=person_id, course_id=final.course_id
        )
        score = best.get(final.id)
        if score is None or score.passed:
            continue
        used = attempts_used(
            db, tenant_id=tenant_id, person_id=person_id, activity_id=final.id
        )
        ent = attempt_policy.for_learner(
            db,
            tenant_id=tenant_id,
            person_id=person_id,
            activity=_activity_fact(final),
            used=used,
        )
        if not ent.exhausted:
            continue
        course = db.get(Course, final.course_id)
        facts: dict[str, JsonValue] = {
            "course_id": str(final.course_id),
            "course_title": course.title if course else "",
            "activity_title": final.title,
            "best_pct": int(round(score.fraction * 100)),
            "required_pct": int(round(final.pass_threshold * 100)),
            "attempts_used": used,
            "max_attempts": ent.limit,
        }
        action = (
            "Attempts exhausted on the final — review readiness "
            "and authorise a reset if warranted."
        )
        return facts, "high", action
    return None


def _rule_certificate_blocked(
    db: Session, *, tenant_id: UUID, person_id: UUID, now: datetime, grace_hours: int
) -> tuple[dict[str, JsonValue], str, str] | None:
    # Certificates now auto-issue when a course completes (completion owner).
    # A missing certificate therefore means issuance genuinely failed — but only
    # flag it once past a reconciliation grace window, so a just-completed course
    # mid-transaction isn't reported as blocked.
    cutoff = now - timedelta(hours=grace_hours)
    completions = db.scalars(
        select(CourseCompletion)
        .where(CourseCompletion.tenant_id == tenant_id)
        .where(CourseCompletion.person_id == person_id)
        .where(CourseCompletion.status == "completed")
        .where(CourseCompletion.completed_at.is_not(None))
        .where(CourseCompletion.completed_at <= cutoff)
    ).all()
    for comp in completions:
        cert = db.scalars(
            select(Certificate)
            .where(Certificate.tenant_id == tenant_id)
            .where(Certificate.person_id == person_id)
            .where(Certificate.course_id == comp.course_id)
        ).first()
        if cert is not None:
            continue
        course = db.get(Course, comp.course_id)
        # A course that opted out of auto-issue (HM-04) is not blocked:
        # no certificate is ever expected for it.
        if course is not None and not course.certificate_auto_issue:
            continue
        facts: dict[str, JsonValue] = {
            "course_id": str(comp.course_id),
            "course_title": course.title if course else "",
            "completion_pct": int(round(comp.pct * 100)),
            "completed_at": comp.completed_at.isoformat()
            if comp.completed_at
            else None,
            "grace_hours": grace_hours,
            "missing": "certificate",
        }
        action = (
            "Certificate issuance failed for a completed course — "
            "re-issue it or clear the blocker."
        )
        return facts, "medium", action
    return None


# ---------------------------------------------------------------------------
# Sweep — idempotent refresh of the queue
# ---------------------------------------------------------------------------


def _upsert(
    db: Session,
    *,
    tenant_id: UUID,
    person_id: UUID,
    cohort_id: UUID | None,
    reason: str,
    facts: dict[str, JsonValue],
    severity: str,
    action: str,
    now: datetime,
) -> str:
    entry = db.scalars(
        select(SuccessQueueEntry)
        .where(SuccessQueueEntry.tenant_id == tenant_id)
        .where(SuccessQueueEntry.person_id == person_id)
        .where(SuccessQueueEntry.reason_kind == reason)
        .where(SuccessQueueEntry.status.in_([STATUS_OPEN, STATUS_ACKNOWLEDGED]))
    ).first()
    if entry is not None:
        entry.supporting_facts = facts
        entry.severity = severity
        entry.detected_at = now
        entry.recommended_action = action
        entry.cohort_id = cohort_id
        db.flush()
        return "refreshed"
    db.add(
        SuccessQueueEntry(
            tenant_id=tenant_id,
            person_id=person_id,
            cohort_id=cohort_id,
            reason_kind=reason,
            supporting_facts=facts,
            severity=severity,
            detected_at=now,
            recommended_action=action,
            status=STATUS_OPEN,
        )
    )
    db.flush()
    return "opened"


def sweep(
    db: Session,
    *,
    tenant_id: UUID,
    terms: SuccessTerms,
    accounts: ActiveAccountSelection,
    now: datetime | None = None,
) -> SweepResult:
    """Evaluate every rule for every active student; refresh the queue.

    Idempotent: open/acknowledged entries are refreshed, entries whose rule no
    longer fires are auto-resolved (actor None). Sweep does not audit.
    """
    now = now or datetime.now(UTC)
    boundary = _transaction(db)
    if accounts.tenant_id != tenant_id:
        raise ValueError("account population tenant scope does not match the command")
    cert_grace_hours = terms.certificate_grace_hours

    counts = {"opened": 0, "refreshed": 0, "auto_resolved": 0}
    rows = db.execute(
        select(Enrollment.person_id, Enrollment.cohort_id, Enrollment.created_at)
        .join(
            Party,
            (Party.id == Enrollment.person_id)
            & (Party.tenant_id == Enrollment.tenant_id),
        )
        .join(PartyPerson, PartyPerson.party_id == Party.id)
        .where(Enrollment.tenant_id == tenant_id)
        .where(Enrollment.role_in_cohort == "student")
        .where(Enrollment.status == "active")
        # Suspended/offboarded people keep their enrolment rows; without this a
        # rule (e.g. inactivity) would hold an unresolvable entry for them
        # forever and segment messaging would email them.
        .where(Party.id.in_(accounts.active_person_ids))
    ).all()
    primary_cohort: dict[UUID, UUID] = {}
    enrolled_since: dict[UUID, datetime] = {}
    for person_id, cohort_id, created_at in rows:
        primary_cohort.setdefault(person_id, cohort_id)
        anchor = created_at if created_at.tzinfo else created_at.replace(tzinfo=UTC)
        if person_id not in enrolled_since or anchor < enrolled_since[person_id]:
            enrolled_since[person_id] = anchor

    for person_id in primary_cohort:
        cohort_ids = _student_cohort_ids(db, tenant_id, person_id)
        course_ids = _active_course_ids(db, tenant_id, cohort_ids)
        results = {
            REASON_INACTIVITY: _rule_inactivity(
                db,
                tenant_id=tenant_id,
                person_id=person_id,
                enrolled_since=enrolled_since.get(person_id),
                now=now,
                terms=terms,
            ),
            REASON_OVERDUE_WORK: _rule_overdue(
                db, tenant_id=tenant_id, person_id=person_id, now=now, terms=terms
            ),
            REASON_BELOW_PASSING: _rule_below_passing(
                db,
                tenant_id=tenant_id,
                person_id=person_id,
                course_ids=course_ids,
                terms=terms,
            ),
            REASON_FAILED_FINAL: _rule_failed_final(
                db, tenant_id=tenant_id, person_id=person_id, course_ids=course_ids
            ),
            REASON_CERTIFICATE_BLOCKED: _rule_certificate_blocked(
                db,
                tenant_id=tenant_id,
                person_id=person_id,
                now=now,
                grace_hours=cert_grace_hours,
            ),
        }
        for reason, result in results.items():
            if result is not None:
                facts, severity, action = result
                outcome = _upsert(
                    db,
                    tenant_id=tenant_id,
                    person_id=person_id,
                    cohort_id=primary_cohort[person_id],
                    reason=reason,
                    facts=facts,
                    severity=severity,
                    action=action,
                    now=now,
                )
                counts[outcome] += 1
            else:
                stale = db.scalars(
                    select(SuccessQueueEntry)
                    .where(SuccessQueueEntry.tenant_id == tenant_id)
                    .where(SuccessQueueEntry.person_id == person_id)
                    .where(SuccessQueueEntry.reason_kind == reason)
                    .where(
                        SuccessQueueEntry.status.in_([STATUS_OPEN, STATUS_ACKNOWLEDGED])
                    )
                ).first()
                if stale is not None:
                    stale.status = STATUS_RESOLVED
                    stale.resolved_at = now
                    stale.resolved_by = None
                    stale.supporting_facts = {
                        **cast(dict[str, JsonValue], stale.supporting_facts),
                        "auto_resolved": "rule no longer fires",
                    }
                    counts["auto_resolved"] += 1
    db.flush()
    _unchanged(db, boundary)
    return SweepResult(**counts)


# ---------------------------------------------------------------------------
# Lifecycle transitions (instructor actions, audited)
# ---------------------------------------------------------------------------


def _get_entry(db: Session, tenant_id: UUID, entry_id: UUID) -> SuccessQueueEntry:
    result = db.execute(
        select(SuccessQueueEntry, *SuccessQueueEntry.__table__.c)
        .where(SuccessQueueEntry.tenant_id == tenant_id)
        .where(SuccessQueueEntry.id == entry_id)
        .with_for_update()
    ).first()
    if result is None:
        raise SuccessNotFound("queue entry not found")
    entry = cast(SuccessQueueEntry, result[0])
    untouched = inspect(entry).unmodified
    for column, fresh_value in zip(
        SuccessQueueEntry.__table__.columns, result[1:], strict=True
    ):
        if column.key in untouched:
            set_committed_value(entry, column.key, fresh_value)
    return entry


def transition(
    db: Session,
    *,
    tenant_id: UUID,
    entry_id: UUID,
    action: str,
    actor_person_id: UUID,
    audit_context: AttemptAuditContext,
    assigned_to: UUID | None = None,
) -> QueueFact:
    return _transition(
        db,
        tenant_id=tenant_id,
        entry_id=entry_id,
        action=action,
        actor_person_id=actor_person_id,
        audit_context=audit_context,
        assigned_to=assigned_to,
    ).entry


def transition_with_receipt(
    db: Session,
    *,
    tenant_id: UUID,
    entry_id: UUID,
    action: str,
    actor_person_id: UUID,
    audit_context: AttemptAuditContext,
    assigned_to: UUID | None = None,
) -> SuccessTransitionEffect:
    """Return the observed entry and audit UUID after the existing guards."""
    return _transition(
        db,
        tenant_id=tenant_id,
        entry_id=entry_id,
        action=action,
        actor_person_id=actor_person_id,
        audit_context=audit_context,
        assigned_to=assigned_to,
    )


def _transition(
    db: Session,
    *,
    tenant_id: UUID,
    entry_id: UUID,
    action: str,
    actor_person_id: UUID,
    audit_context: AttemptAuditContext,
    assigned_to: UUID | None = None,
) -> SuccessTransitionEffect:
    boundary = _transaction(db)
    if audit_context.tenant_id != tenant_id:
        raise ValueError("Audit context tenant scope does not match the command.")
    entry = _get_entry(db, tenant_id, entry_id)
    now = datetime.now(UTC)
    if action == "acknowledge" and entry.status == STATUS_OPEN:
        entry.status = STATUS_ACKNOWLEDGED
    elif action == "assign":
        entry.assigned_to = assigned_to or actor_person_id
        if entry.status == STATUS_OPEN:
            entry.status = STATUS_ACKNOWLEDGED
    elif action == "resolve" and entry.status in (STATUS_OPEN, STATUS_ACKNOWLEDGED):
        entry.status = STATUS_RESOLVED
        entry.resolved_at = now
        entry.resolved_by = actor_person_id
    else:
        raise SuccessBadRequest(f"invalid transition {action!r} from {entry.status!r}")
    audit_event = write_audit_event(
        db,
        tenant_id=tenant_id,
        actor_type=audit_context.actor_type,
        actor_id=audit_context.actor_id,
        actor_label=audit_context.actor_label,
        actor_party_id=actor_person_id,
        source_application=audit_context.source_application,
        action=f"success_queue.{action}",
        entity_type="success_queue_entry",
        entity_id=str(entry.id),
        details={"reason": entry.reason_kind, "person_id": str(entry.person_id)},
    )
    db.flush()
    _unchanged(db, boundary)
    return SuccessTransitionEffect(_fact(entry), audit_event.id)


def list_entries(
    db: Session,
    *,
    tenant_id: UUID,
    status: str | None = None,
    severity: str | None = None,
    cohort_id: UUID | None = None,
) -> tuple[QueueListFact, ...]:
    q = (
        select(SuccessQueueEntry, Party, PartyPerson, Cohort.name)
        .join(
            Party,
            (Party.id == SuccessQueueEntry.person_id)
            & (Party.tenant_id == SuccessQueueEntry.tenant_id),
        )
        .join(PartyPerson, PartyPerson.party_id == Party.id)
        .join(
            Cohort,
            (Cohort.id == SuccessQueueEntry.cohort_id)
            & (Cohort.tenant_id == SuccessQueueEntry.tenant_id),
            isouter=True,
        )
        .where(SuccessQueueEntry.tenant_id == tenant_id)
        .order_by(severity_order(), SuccessQueueEntry.detected_at.desc())
    )
    if status:
        q = q.where(SuccessQueueEntry.status == status)
    if severity:
        q = q.where(SuccessQueueEntry.severity == severity)
    if cohort_id:
        q = q.where(SuccessQueueEntry.cohort_id == cohort_id)
    return tuple(
        QueueListFact(_fact(entry), _person(party, profile), cohort_name or "")
        for entry, party, profile, cohort_name in db.execute(q).all()
    )


# ---------------------------------------------------------------------------
# Deterministic segments + message action (item 22)
# ---------------------------------------------------------------------------


def segment_members(
    db: Session,
    *,
    tenant_id: UUID,
    cohort_id: UUID,
    segment: str,
    terms: SuccessTerms,
    accounts: ActiveAccountSelection,
    now: datetime | None = None,
) -> tuple[SuccessPersonFact, ...]:
    """People in the cohort matching a deterministic segment rule."""
    if accounts.tenant_id != tenant_id:
        raise ValueError("account population tenant scope does not match the command")
    now = now or datetime.now(UTC)
    if segment not in SEGMENT_KEYS:
        raise SuccessBadRequest(f"unknown segment {segment!r}")

    people = db.execute(
        select(Party, PartyPerson)
        .join(PartyPerson, PartyPerson.party_id == Party.id)
        .join(
            Enrollment,
            (Enrollment.person_id == Party.id)
            & (Enrollment.tenant_id == Party.tenant_id),
        )
        .where(Enrollment.tenant_id == tenant_id)
        .where(Enrollment.cohort_id == cohort_id)
        .where(Enrollment.role_in_cohort == "student")
        .where(Enrollment.status == "active")
        .where(Party.id.in_(accounts.active_person_ids))
        .order_by(PartyPerson.last_name)
    ).all()

    members: list[SuccessPersonFact] = []
    for party, profile in people:
        person = _person(party, profile)
        cohort_ids = [cohort_id]
        course_ids = _active_course_ids(db, tenant_id, cohort_ids)
        if segment == "inactive":
            enrolled_since = db.scalar(
                select(func.min(Enrollment.created_at))
                .where(Enrollment.tenant_id == tenant_id)
                .where(Enrollment.person_id == person.id)
                .where(Enrollment.status == "active")
            )
            if enrolled_since is not None and enrolled_since.tzinfo is None:
                enrolled_since = enrolled_since.replace(tzinfo=UTC)
            hit = _rule_inactivity(
                db,
                tenant_id=tenant_id,
                person_id=person.id,
                enrolled_since=enrolled_since,
                now=now,
                terms=terms,
            )
        elif segment == "overdue_work":
            hit = _rule_overdue(
                db, tenant_id=tenant_id, person_id=person.id, now=now, terms=terms
            )
        elif segment == "below_passing":
            hit = _rule_below_passing(
                db,
                tenant_id=tenant_id,
                person_id=person.id,
                course_ids=course_ids,
                terms=terms,
            )
        elif segment == "failed_final":
            hit = _rule_failed_final(
                db, tenant_id=tenant_id, person_id=person.id, course_ids=course_ids
            )
        else:  # almost_complete
            comp = db.scalars(
                select(CourseCompletion)
                .where(CourseCompletion.tenant_id == tenant_id)
                .where(CourseCompletion.person_id == person.id)
                .where(CourseCompletion.course_id.in_(course_ids or [uuid4()]))
                .where(CourseCompletion.pct >= terms.almost_complete_fraction)
                .where(CourseCompletion.status != "completed")
            ).first()
            hit = ({"pct": comp.pct}, "low", "") if comp is not None else None
        if hit is not None:
            members.append(person)
    return tuple(members)


def message_segment(
    db: Session,
    *,
    tenant_id: UUID,
    cohort_id: UUID,
    segment: str,
    subject: str,
    body: str,
    actor_person_id: UUID,
    terms: SuccessTerms,
    accounts: ActiveAccountSelection,
    audit_context: AttemptAuditContext,
    delivery: SegmentDeliveryProtocol,
    now: datetime | None = None,
) -> SegmentMessageResult:
    """Sequential required product delivery, then required audit and flush."""
    return _message_segment(
        db,
        tenant_id=tenant_id,
        cohort_id=cohort_id,
        segment=segment,
        subject=subject,
        body=body,
        actor_person_id=actor_person_id,
        terms=terms,
        accounts=accounts,
        audit_context=audit_context,
        delivery=delivery,
        now=now,
    ).result


def message_segment_with_receipt(
    db: Session,
    *,
    tenant_id: UUID,
    cohort_id: UUID,
    segment: str,
    subject: str,
    body: str,
    actor_person_id: UUID,
    terms: SuccessTerms,
    accounts: ActiveAccountSelection,
    audit_context: AttemptAuditContext,
    delivery: SegmentDeliveryProtocol,
    now: datetime | None = None,
) -> SegmentMessageEffect:
    """Return the observed delivery result and actual audit UUID."""
    return _message_segment(
        db,
        tenant_id=tenant_id,
        cohort_id=cohort_id,
        segment=segment,
        subject=subject,
        body=body,
        actor_person_id=actor_person_id,
        terms=terms,
        accounts=accounts,
        audit_context=audit_context,
        delivery=delivery,
        now=now,
    )


def _message_segment(
    db: Session,
    *,
    tenant_id: UUID,
    cohort_id: UUID,
    segment: str,
    subject: str,
    body: str,
    actor_person_id: UUID,
    terms: SuccessTerms,
    accounts: ActiveAccountSelection,
    audit_context: AttemptAuditContext,
    delivery: SegmentDeliveryProtocol,
    now: datetime | None = None,
) -> SegmentMessageEffect:
    """Sequential required product delivery, then required audit and flush."""
    boundary = _transaction(db)
    if audit_context.tenant_id != tenant_id:
        raise ValueError("Audit context tenant scope does not match the command.")
    subject = (subject or "").strip()
    body = (body or "").strip()
    if not subject or not body:
        raise SuccessBadRequest("subject and message are both required")
    if accounts.tenant_id != tenant_id:
        raise ValueError("account population tenant scope does not match the command")
    members = segment_members(
        db,
        tenant_id=tenant_id,
        cohort_id=cohort_id,
        segment=segment,
        terms=terms,
        accounts=accounts,
        now=now,
    )
    action_id = uuid4().hex[:12]
    message = SegmentMessageFact(tenant_id, cohort_id, segment, subject, body)
    for person in members:
        delivery(db, person, message, action_id)
        _unchanged(db, boundary)
    audit_event = write_audit_event(
        db,
        tenant_id=tenant_id,
        actor_type=audit_context.actor_type,
        actor_id=audit_context.actor_id,
        actor_label=audit_context.actor_label,
        actor_party_id=actor_person_id,
        source_application=audit_context.source_application,
        action="success_queue.message_segment",
        entity_type="cohort",
        entity_id=str(cohort_id),
        details={
            "segment": segment,
            "recipients": len(members),
            "subject": subject,
            "action_id": action_id,
        },
    )
    db.flush()
    _unchanged(db, boundary)
    return SegmentMessageEffect(
        SegmentMessageResult(len(members), action_id), audit_event.id
    )


__all__ = [
    "SEGMENT_KEYS",
    "SEGMENTS_DEFERRED",
    "severity_order",
    "sweep",
    "transition",
    "transition_with_receipt",
    "list_entries",
    "segment_members",
    "message_segment",
    "message_segment_with_receipt",
]
