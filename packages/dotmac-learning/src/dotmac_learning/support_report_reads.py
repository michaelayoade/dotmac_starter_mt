"""Source admin learning metrics and CSV inputs in the caller's read scope.

These reports intentionally retain independent denominators and score policies.
No account availability is inferred from Kernel identity activity. Products own
admissions metrics, recipients, labels, CSV/email rendering and delivery.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import cast
from uuid import UUID

from dotmac_kernel.models import Party, PartyPerson
from pydantic import JsonValue
from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from . import events, insights, success_queue
from .delivery_models import (
    REASON_INACTIVITY,
    STATUS_OPEN,
    STATUS_RESOLVED,
    SuccessQueueEntry,
)
from .gradebook import course_grade
from .models import (
    KIND_SUBMISSION_MADE,
    CourseCompletion,
    LearningEvent,
    Score,
    Submission,
)
from .participation_models import Cohort, Enrollment
from .reports import _person
from .reports_contracts import ReportCohort, ReportPerson
from .success_contracts import QueueListFact, SuccessPersonFact
from .support_report_contracts import (
    ActivityWindowCounts,
    AttentionLearner,
    AttentionReasonCount,
    AttentionSnapshot,
    CohortRosterProgress,
    EngagementSnapshot,
    RosterProgressRow,
    TopMover,
)

TOP_MOVERS_LIMIT = 5
ATTENTION_LIMIT = 8


def _student_roster(tenant_id: UUID) -> Select[tuple[UUID]]:
    return (
        select(Enrollment.person_id)
        .where(
            Enrollment.tenant_id == tenant_id,
            Enrollment.status == "active",
            Enrollment.role_in_cohort == "student",
        )
        .distinct()
    )


def engagement_snapshot(
    db: Session, *, tenant_id: UUID, since: datetime
) -> EngagementSnapshot:
    roster = _student_roster(tenant_id)
    enrollees = int(db.scalar(select(func.count()).select_from(roster.subquery())) or 0)
    active = select(func.count(func.distinct(LearningEvent.person_id))).where(
        LearningEvent.tenant_id == tenant_id,
        LearningEvent.person_id.in_(roster),
    )
    ever_active = int(db.scalar(active) or 0)
    active_in_window = int(
        db.scalar(active.where(LearningEvent.occurred_at >= since)) or 0
    )
    return EngagementSnapshot(
        enrollees, ever_active, active_in_window, max(enrollees - ever_active, 0)
    )


def top_movers(
    db: Session, *, tenant_id: UUID, since: datetime, limit: int = TOP_MOVERS_LIMIT
) -> tuple[TopMover, ...]:
    ranked = db.execute(
        select(
            LearningEvent.person_id,
            func.count().label("events"),
            func.count()
            .filter(LearningEvent.kind == KIND_SUBMISSION_MADE)
            .label("submissions"),
            func.max(LearningEvent.occurred_at).label("last_seen"),
        )
        .where(LearningEvent.tenant_id == tenant_id)
        .where(LearningEvent.occurred_at >= since)
        .where(LearningEvent.person_id.in_(_student_roster(tenant_id)))
        .group_by(LearningEvent.person_id)
        .order_by(func.count().desc(), LearningEvent.person_id)
        .limit(limit)
    ).all()
    if not ranked:
        return ()
    ids = [row.person_id for row in ranked]
    people = {
        party.id: _person(party, profile)
        for party, profile in db.execute(
            select(Party, PartyPerson)
            .join(PartyPerson, PartyPerson.party_id == Party.id)
            .where(Party.tenant_id == tenant_id, Party.id.in_(ids))
        ).all()
    }
    cohorts: dict[UUID, str] = {}
    for person_id, name in db.execute(
        select(Enrollment.person_id, Cohort.name)
        .join(
            Cohort,
            (Cohort.id == Enrollment.cohort_id)
            & (Cohort.tenant_id == Enrollment.tenant_id),
        )
        .where(
            Enrollment.tenant_id == tenant_id,
            Enrollment.person_id.in_(ids),
            Enrollment.status == "active",
        )
        .order_by(Cohort.name)
    ).all():
        cohorts.setdefault(person_id, name)
    graded = {
        person_id: float(avg)
        for person_id, avg in db.execute(
            select(Submission.person_id, func.avg(Score.fraction))
            .join(
                Score,
                (Score.submission_id == Submission.id)
                & (Score.tenant_id == Submission.tenant_id),
            )
            .where(
                Submission.tenant_id == tenant_id,
                Submission.person_id.in_(ids),
                Score.created_at >= since,
            )
            .group_by(Submission.person_id)
        ).all()
        if avg is not None
    }
    out = []
    for row in ranked:
        person = people.get(row.person_id)
        if person is None:
            continue
        out.append(
            TopMover(
                person,
                cohorts.get(row.person_id, ""),
                int(row.events),
                int(row.submissions),
                graded.get(row.person_id),
            )
        )
    return tuple(out)


def attention_list(
    db: Session, *, tenant_id: UUID, limit: int = ATTENTION_LIMIT
) -> AttentionSnapshot:
    entries = success_queue.list_entries(db, tenant_id=tenant_id, status=STATUS_OPEN)
    by_reason: dict[str, int] = {}
    never_started = 0
    for item in entries:
        entry = item.entry
        by_reason[entry.reason_kind] = by_reason.get(entry.reason_kind, 0) + 1
        facts = cast(dict[str, JsonValue], entry.supporting_facts)
        if entry.reason_kind == REASON_INACTIVITY and facts.get("never_active"):
            never_started += 1
    top = []
    for item in entries[:limit]:
        entry, person = item.entry, item.person
        facts = cast(dict[str, JsonValue], entry.supporting_facts)
        top.append(
            AttentionLearner(
                _report_person(person),
                item.cohort_name,
                entry.reason_kind,
                entry.severity,
                facts.get("days_inactive"),
                bool(facts.get("never_active")),
            )
        )
    return AttentionSnapshot(
        len(entries),
        tuple(AttentionReasonCount(k, v) for k, v in by_reason.items()),
        never_started,
        tuple(top),
    )


def _report_person(person: SuccessPersonFact) -> ReportPerson:
    # Queue owns identity selection; converting its detached fact adds no query.
    return ReportPerson(
        person.id, person.tenant_id, person.first_name, person.last_name, person.email
    )


def activity_window_counts(
    db: Session, *, tenant_id: UUID, since: datetime
) -> ActivityWindowCounts:
    enrollments = int(
        db.scalar(
            select(func.count())
            .select_from(Enrollment)
            .where(Enrollment.tenant_id == tenant_id, Enrollment.created_at >= since)
        )
        or 0
    )
    submissions = int(
        db.scalar(
            select(func.count())
            .select_from(Submission)
            .where(Submission.tenant_id == tenant_id, Submission.created_at >= since)
        )
        or 0
    )
    completions = int(
        db.scalar(
            select(func.count())
            .select_from(CourseCompletion)
            .where(
                CourseCompletion.tenant_id == tenant_id,
                CourseCompletion.completed_at >= since,
            )
        )
        or 0
    )
    return ActivityWindowCounts(since, enrollments, submissions, completions)


def cohort_roster_progress(
    db: Session, *, tenant_id: UUID, cohort_id: UUID, now: datetime | None = None
) -> CohortRosterProgress:
    now = now or datetime.now(UTC)
    cohort = db.scalars(
        select(Cohort).where(Cohort.tenant_id == tenant_id, Cohort.id == cohort_id)
    ).first()
    if cohort is None:
        raise ValueError("cohort not found")
    cohort_fact = ReportCohort(
        **{field: getattr(cohort, field) for field in ReportCohort.__dataclass_fields__}
    )
    course_ids = insights._cohort_course_ids(db, tenant_id, cohort_id)
    people = db.execute(
        select(Party, PartyPerson)
        .join(PartyPerson, PartyPerson.party_id == Party.id)
        .join(
            Enrollment,
            (Enrollment.person_id == Party.id)
            & (Enrollment.tenant_id == Party.tenant_id),
        )
        .where(
            Enrollment.tenant_id == tenant_id,
            Enrollment.cohort_id == cohort_id,
            Enrollment.role_in_cohort == "student",
            Enrollment.status == "active",
        )
        .order_by(PartyPerson.last_name, PartyPerson.first_name)
    ).all()
    rows = []
    for party, profile in people:
        person = _person(party, profile)
        completions = db.scalars(
            select(CourseCompletion).where(
                CourseCompletion.tenant_id == tenant_id,
                CourseCompletion.person_id == person.id,
                CourseCompletion.course_id.in_(course_ids or [cohort_id]),
            )
        ).all()
        avg_completion = (
            round(100 * sum(c.pct for c in completions) / len(completions))
            if completions
            else 0
        )
        grade_docs = [
            course_grade(db, tenant_id=tenant_id, person_id=person.id, course_id=cid)
            for cid in course_ids
        ]
        attempted_pcts = [
            g.pct for g in grade_docs if any(a.graded for a in g.per_activity)
        ]
        avg_grade = (
            round(sum(attempted_pcts) / len(attempted_pcts)) if attempted_pcts else 0
        )
        last = events.last_activity_at(db, tenant_id=tenant_id, person_id=person.id)
        reasons = db.scalars(
            select(SuccessQueueEntry.reason_kind).where(
                SuccessQueueEntry.tenant_id == tenant_id,
                SuccessQueueEntry.person_id == person.id,
                SuccessQueueEntry.status != STATUS_RESOLVED,
            )
        ).all()
        rows.append(
            RosterProgressRow(
                person,
                len(course_ids),
                avg_completion,
                avg_grade,
                last,
                (now - last).days if last else None,
                tuple(sorted(reasons)),
            )
        )
    return CohortRosterProgress(cohort_fact, tuple(rows))


def queue_summary(
    db: Session, *, tenant_id: UUID, cohort_id: UUID | None = None
) -> tuple[QueueListFact, ...]:
    return tuple(
        item
        for item in success_queue.list_entries(
            db, tenant_id=tenant_id, cohort_id=cohort_id
        )
        if item.entry.status != STATUS_RESOLVED
    )


__all__ = [
    "engagement_snapshot",
    "top_movers",
    "attention_list",
    "activity_window_counts",
    "cohort_roster_progress",
    "queue_summary",
    "TOP_MOVERS_LIMIT",
    "ATTENTION_LIMIT",
]
