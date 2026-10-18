"""Learner and instructor insights — read-only projections of canonical
state (roadmap P3a, items 19-20).

Everything here derives from the learning-event ledger plus existing owned
records (scores, completions, enrollments). Deterministic, explainable rules
only — every derived flag states its rule; nothing here writes, decides, or
notifies (consequences belong to the reminder service and, later, the
Success Queue). Question-level item analysis stays in
``dotmac_learning.analytics`` (reused, not duplicated).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from typing import Literal
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import attempt_policy
from . import events as learning_events
from .assessment_reads import attempts_used
from .delivery_models import OfferingActivity
from .entitlements import visible_course_ids
from .insight_contracts import (
    BlockerReason,
    CohortComparison,
    CohortFunnel,
    CohortOverview,
    CurrentBlocker,
    DisciplineScore,
    DisciplineStrengths,
    GradeTrend,
    LabFailures,
    LearnerActivity,
    Recommendation,
)
from .models import (
    KIND_CHAPTER_COMPLETED,
    KIND_LAB_CHECK_FAILED,
    KIND_WORK_GRADED,
    Activity,
    Certificate,
    Course,
    CourseCompletion,
    LearningEvent,
    Score,
    Submission,
)
from .participation_models import Cohort, CourseOffering, Enrollment
from .progress_contracts import ScoreFact
from .projections import best_scores_by_course_for_person, best_scores_for
from .references import _activity_fact
from .reports import _best_for_person
from .reports_contracts import ReportCohort

ACTIVE_WINDOW_DAYS = 7  # rule: any ledger event in the last 7 days = active


# --------------------------------------------------------------------------
# Learner insights (item 19)
# --------------------------------------------------------------------------


def _grade_trend(db: Session, tenant_id: UUID, person_id: UUID) -> GradeTrend:
    rows = list(
        db.execute(
            select(Score.fraction, Score.created_at)
            .join(
                Submission,
                (Submission.id == Score.submission_id)
                & (Submission.tenant_id == Score.tenant_id),
            )
            .where(Score.tenant_id == tenant_id)
            .where(Submission.person_id == person_id)
            .order_by(Score.created_at.desc())
            .limit(10)
        ).all()
    )
    fractions = [float(f) for f, _ in reversed(rows)]
    return _trend_from(fractions)


def _trend_from(fractions: Sequence[float]) -> GradeTrend:
    direction: Literal["flat", "up", "down"] = "flat"
    if len(fractions) >= 4:
        half = len(fractions) // 2
        older = sum(fractions[:half]) / half
        newer = sum(fractions[half:]) / (len(fractions) - half)
        if newer - older > 0.05:
            direction = "up"
        elif older - newer > 0.05:
            direction = "down"
    return GradeTrend(tuple(round(100 * f) for f in fractions), direction)


def _discipline_strengths(
    db: Session, tenant_id: UUID, person_id: UUID
) -> DisciplineStrengths:
    """Average best-score fraction per course discipline. Competency-level
    areas arrive with roadmap P5; discipline is the honest available grain."""
    course_ids = list(visible_course_ids(db, tenant_id=tenant_id, person_id=person_id))
    per_discipline: dict[str, list[float]] = {}
    if not course_ids:
        return DisciplineStrengths(None, None, ())
    disciplines = {  # noqa: C416 - retain source-shaped row selection
        cid: disc
        for cid, disc in db.execute(
            select(Course.id, Course.discipline)
            .where(Course.tenant_id == tenant_id)
            .where(Course.id.in_(course_ids))
        ).all()
    }
    best_by_course = best_scores_by_course_for_person(
        db, tenant_id=tenant_id, person_id=person_id, course_ids=course_ids
    )
    for course_id, best in best_by_course.items():
        discipline = disciplines.get(course_id)
        if discipline is None or not best:
            continue
        avg = sum(float(s.fraction) for s in best.values()) / len(best)
        per_discipline.setdefault(discipline, []).append(avg)
    return _discipline_summary(per_discipline)


def _discipline_summary(
    per_discipline: Mapping[str, Sequence[float]],
) -> DisciplineStrengths:
    scored = {d: round(100 * sum(v) / len(v)) for d, v in per_discipline.items() if v}
    if not scored:
        return DisciplineStrengths(None, None, ())
    strong = max(scored, key=lambda d: scored[d])
    weak = min(scored, key=lambda d: scored[d])
    return DisciplineStrengths(
        strong,
        weak if weak != strong else None,
        tuple(DisciplineScore(d, pct) for d, pct in scored.items()),
    )


def _current_blocker(
    db: Session, tenant_id: UUID, person_id: UUID, now: datetime
) -> CurrentBlocker | None:
    """First blocking activity, by explicit rule:

    - overdue: ``OfferingActivity.due_at`` in the past without a passing best
      score;
    - attempts_exhausted: ``max_attempts`` used without a pass.
    """
    course_ids = visible_course_ids(db, tenant_id=tenant_id, person_id=person_id)
    if not course_ids:
        return None
    best_by_activity: dict[UUID, ScoreFact] = {}
    for course_id in course_ids:
        best_by_activity.update(
            best_scores_for(
                db, tenant_id=tenant_id, person_id=person_id, course_id=course_id
            )
        )

    # Scope deadlines to the offerings this person is actually enrolled in.
    # Filtering only by Activity.course_id would let another cohort's offering
    # of the same course contribute a due_at that this learner never had.
    overdue_rows = db.execute(
        select(OfferingActivity.due_at, Activity)
        .join(
            Activity,
            (Activity.id == OfferingActivity.activity_id)
            & (Activity.tenant_id == OfferingActivity.tenant_id),
        )
        .join(
            CourseOffering,
            (CourseOffering.id == OfferingActivity.offering_id)
            & (CourseOffering.tenant_id == OfferingActivity.tenant_id),
        )
        .join(
            Enrollment,
            (Enrollment.cohort_id == CourseOffering.cohort_id)
            & (Enrollment.tenant_id == CourseOffering.tenant_id),
        )
        .where(OfferingActivity.tenant_id == tenant_id)
        .where(OfferingActivity.due_at.is_not(None))
        .where(OfferingActivity.due_at < now)
        .where(CourseOffering.status == "active")
        .where(Enrollment.person_id == person_id)
        .where(Enrollment.status == "active")
        .order_by(OfferingActivity.due_at)
    ).all()
    for due_at, activity in overdue_rows:
        score = best_by_activity.get(activity.id)
        if score is None or not score.passed:
            return CurrentBlocker(
                activity.id, activity.title, activity.course_id, "overdue", due_at
            )

    for activity_id, score in best_by_activity.items():
        if score.passed:
            continue
        activity = db.get(Activity, activity_id)
        if activity is None or activity.max_attempts is None:
            continue
        used = attempts_used(
            db, tenant_id=tenant_id, person_id=person_id, activity_id=activity_id
        )
        if attempt_policy.for_learner(
            db,
            tenant_id=tenant_id,
            person_id=person_id,
            activity=_activity_fact(activity),
            used=used,
        ).exhausted:
            return CurrentBlocker(
                activity_id,
                activity.title,
                activity.course_id,
                "attempts_exhausted",
                None,
            )
    return None


def learner_activity(
    db: Session,
    *,
    tenant_id: UUID,
    person_id: UUID,
    now: datetime | None = None,
) -> LearnerActivity:
    """The 'My learning activity' projection for the progress page."""
    now = now or datetime.now(UTC)
    weekly = learning_events.weekly_buckets(
        db, tenant_id=tenant_id, person_id=person_id, weeks=8, now=now
    )
    velocity = learning_events.weekly_buckets(
        db,
        tenant_id=tenant_id,
        person_id=person_id,
        weeks=4,
        kinds=(KIND_CHAPTER_COMPLETED, KIND_WORK_GRADED),
        now=now,
    )
    last_at = learning_events.last_activity_at(
        db, tenant_id=tenant_id, person_id=person_id
    )
    days_since = (now - last_at).days if last_at else None
    blocker = _current_blocker(db, tenant_id, person_id, now)

    return LearnerActivity(
        tuple(weekly),
        max((b.count for b in weekly), default=0),
        round(sum(b.count for b in velocity) / max(len(velocity), 1), 1),
        _grade_trend(db, tenant_id, person_id),
        days_since,
        _discipline_strengths(db, tenant_id, person_id),
        None,
        blocker,
        _recommendation(blocker.reason if blocker is not None else None, days_since),
    )


def _recommendation(
    reason: BlockerReason | None, days_since: int | None
) -> Recommendation:
    if reason is not None:
        return reason
    if days_since is None:
        return "start_first_chapter"
    if days_since >= 7:
        return "resume_after_week"
    return "continue_unfinished"


# --------------------------------------------------------------------------
# Instructor insights (item 20)
# --------------------------------------------------------------------------


def _cohort_people(db: Session, tenant_id: UUID, cohort_id: UUID) -> list[UUID]:
    """Active *students* in the cohort. Instructors must not inflate learner
    counts, deflate active-% and completion, or land in the not-attempted
    bucket (they never 'start' coursework)."""
    return list(
        db.scalars(
            select(Enrollment.person_id)
            .where(Enrollment.tenant_id == tenant_id)
            .where(Enrollment.cohort_id == cohort_id)
            .where(Enrollment.status == "active")
            .where(Enrollment.role_in_cohort == "student")
        ).all()
    )


def _cohort_course_ids(db: Session, tenant_id: UUID, cohort_id: UUID) -> list[UUID]:
    return list(
        db.scalars(
            select(CourseOffering.course_id)
            .where(CourseOffering.tenant_id == tenant_id)
            .where(CourseOffering.cohort_id == cohort_id)
            .where(CourseOffering.status == "active")
        ).all()
    )


def cohort_course_ids(db: Session, *, tenant_id: UUID, cohort_id: UUID) -> list[UUID]:
    """Active offering course IDs in the caller Session, without deduplication.

    No activity or cohort-existence requirement; result order is unspecified.
    """
    return _cohort_course_ids(db, tenant_id, cohort_id)


def _person_best(
    db: Session,
    tenant_id: UUID,
    person_id: UUID,
    course_ids: list[UUID],
) -> dict[UUID, ScoreFact]:
    return _best_for_person(db, tenant_id, person_id, course_ids)


def cohort_overview(
    db: Session,
    *,
    tenant_id: UUID,
    cohort_id: UUID,
    now: datetime | None = None,
) -> CohortOverview:
    now = now or datetime.now(UTC)
    people = _cohort_people(db, tenant_id, cohort_id)
    course_ids = _cohort_course_ids(db, tenant_id, cohort_id)

    active = learning_events.active_person_ids(
        db,
        tenant_id=tenant_id,
        person_ids=people,
        since=now - timedelta(days=ACTIVE_WINDOW_DAYS),
    )

    total_activities = 0
    if course_ids:
        total_activities = int(
            db.scalar(
                select(func.count())
                .select_from(Activity)
                .where(Activity.tenant_id == tenant_id)
                .where(Activity.course_id.in_(course_ids))
            )
            or 0
        )

    overdue_activity_ids: set[UUID] = set()
    if course_ids:
        overdue_activity_ids = {
            r[0]
            for r in db.execute(
                select(OfferingActivity.activity_id)
                .join(
                    CourseOffering,
                    (CourseOffering.id == OfferingActivity.offering_id)
                    & (CourseOffering.tenant_id == OfferingActivity.tenant_id),
                )
                .where(OfferingActivity.tenant_id == tenant_id)
                .where(CourseOffering.cohort_id == cohort_id)
                .where(OfferingActivity.due_at.is_not(None))
                .where(OfferingActivity.due_at < now)
            ).all()
        }

    submitted = overdue = not_attempted = 0
    fractions: list[float] = []
    started: set[UUID] = set()
    half: set[UUID] = set()
    for person_id in people:
        best = _person_best(db, tenant_id, person_id, course_ids)
        if best:
            started.add(person_id)
        fractions.extend(float(s.fraction) for s in best.values())
        submitted += len(best)
        if total_activities:
            not_attempted += max(total_activities - len(best), 0)
            passed = sum(1 for s in best.values() if s.passed)
            if passed >= total_activities / 2:
                half.add(person_id)
        overdue += sum(
            1 for aid in overdue_activity_ids if aid not in best or not best[aid].passed
        )

    completed_count = certified_count = 0
    if people and course_ids:
        completed_count = int(
            db.scalar(
                select(func.count(func.distinct(CourseCompletion.person_id)))
                .where(CourseCompletion.tenant_id == tenant_id)
                .where(CourseCompletion.person_id.in_(people))
                .where(CourseCompletion.course_id.in_(course_ids))
                .where(CourseCompletion.status == "completed")
            )
            or 0
        )
        certified_count = int(
            db.scalar(
                select(func.count(func.distinct(Certificate.person_id)))
                .where(Certificate.tenant_id == tenant_id)
                .where(Certificate.person_id.in_(people))
                .where(Certificate.course_id.in_(course_ids))
            )
            or 0
        )

    # Scope lab failures to this cohort's students and courses. Without the
    # person filter an empty course list would fall back to tenant-wide counts.
    lab_failures_raw = (
        learning_events.kind_counts(
            db,
            tenant_id=tenant_id,
            kinds=(KIND_LAB_CHECK_FAILED,),
            course_ids=course_ids,
            person_ids=people,
        )
        if course_ids and people
        else {}
    )
    course_titles = (
        {
            c.id: c.title
            for c in db.scalars(
                select(Course)
                .where(Course.tenant_id == tenant_id)
                .where(Course.id.in_(course_ids))
            ).all()
        }
        if course_ids
        else {}
    )
    lab_failures = tuple(
        LabFailures(
            cid,
            course_titles.get(cid) if cid is not None else None,
            counts.get(KIND_LAB_CHECK_FAILED, 0),
        )
        for cid, counts in lab_failures_raw.items()
        if counts.get(KIND_LAB_CHECK_FAILED, 0)
    )

    this_week = 0
    prior_avg = 0.0
    if people:
        this_week = int(
            db.scalar(
                select(func.count())
                .select_from(LearningEvent)
                .where(LearningEvent.tenant_id == tenant_id)
                .where(LearningEvent.person_id.in_(people))
                .where(LearningEvent.occurred_at >= now - timedelta(days=7))
            )
            or 0
        )
        prior = int(
            db.scalar(
                select(func.count())
                .select_from(LearningEvent)
                .where(LearningEvent.tenant_id == tenant_id)
                .where(LearningEvent.person_id.in_(people))
                .where(LearningEvent.occurred_at >= now - timedelta(days=35))
                .where(LearningEvent.occurred_at < now - timedelta(days=7))
            )
            or 0
        )
        prior_avg = prior / 4.0

    return CohortOverview(
        len(people),
        len(active),
        len(people) - len(active),
        ACTIVE_WINDOW_DAYS,
        submitted,
        overdue,
        not_attempted,
        round(100 * sum(fractions) / len(fractions)) if fractions else None,
        CohortFunnel(
            len(people), len(started), len(half), completed_count, certified_count
        ),
        lab_failures,
        this_week,
        round(prior_avg, 1),
    )


def cohorts_comparison(
    db: Session, *, tenant_id: UUID, now: datetime | None = None
) -> tuple[CohortComparison, ...]:
    """One comparison row per cohort for the reports index."""
    now = now or datetime.now(UTC)
    rows: list[CohortComparison] = []
    for cohort in db.scalars(select(Cohort).where(Cohort.tenant_id == tenant_id)).all():
        people = _cohort_people(db, tenant_id, cohort.id)
        active = learning_events.active_person_ids(
            db,
            tenant_id=tenant_id,
            person_ids=people,
            since=now - timedelta(days=ACTIVE_WINDOW_DAYS),
        )
        course_ids = _cohort_course_ids(db, tenant_id, cohort.id)
        completed = 0
        if people and course_ids:
            completed = int(
                db.scalar(
                    select(func.count(func.distinct(CourseCompletion.person_id)))
                    .where(CourseCompletion.tenant_id == tenant_id)
                    .where(CourseCompletion.person_id.in_(people))
                    .where(CourseCompletion.course_id.in_(course_ids))
                    .where(CourseCompletion.status == "completed")
                )
                or 0
            )
        rows.append(
            CohortComparison(
                ReportCohort(
                    **{
                        field: getattr(cohort, field)
                        for field in ReportCohort.__dataclass_fields__
                    }
                ),
                len(people),
                len(active),
                completed,
                round(100 * completed / len(people)) if people else 0,
            )
        )
    rows.sort(key=lambda r: r.cohort.name.lower())
    return tuple(rows)
