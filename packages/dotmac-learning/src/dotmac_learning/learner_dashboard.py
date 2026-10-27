"""Learner dashboard projections (roadmap P1a).

This service OWNS the learner-facing dashboard, resume, and progress
projections: each course card carries ONE derived state and ONE action, the
resume target is computed server-side from the last meaningful touch (so it
follows the learner across devices), and the progress overview aggregates
grades, attempts, deadlines and certificate eligibility per course. Routes
render these facts; they do not re-derive state.

States (mutually exclusive, in precedence order):
    locked > upcoming > completed > expired > in_progress
``in_progress`` covers not-yet-started courses too — the card's action
(Start vs Resume) is what distinguishes them.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TypedDict
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import attempt_policy, scheduling
from .assessment_reads import (
    FeedbackRevealTerms,
    attempts_used_by_person,
    reveal_feedback,
)
from .attempt_models import ActivityAttempt
from .curriculum import _fact as _chapter_fact
from .curriculum_contracts import ChapterFact
from .curriculum_models import Chapter, ChapterRead
from .dashboard_contracts import (
    CardState,
    CertificateStatus,
    ContinueTarget,
    CourseCard,
    CourseCards,
    CourseProgress,
    DashboardAction,
    LastRead,
    LearnerHome,
    ProgressActivity,
    ProgressCertificate,
    StateCount,
    Trend,
)
from .delivery_contracts import ClassSessionFact
from .delivery_models import OfferingActivity
from .entitlements import CourseAccessState, course_access_states, visible_course_ids
from .gradebook import grade_from
from .insight_contracts import ProgressTrendTerms
from .models import Activity, Certificate, Course, CourseCompletion, Score, Submission
from .participation_models import CourseOffering, Enrollment
from .progress_contracts import CertificateFact, CompletionFact, ScoreFact
from .projections import best_scores_for, best_scores_for_person
from .references import ActivityFact, CourseFact, _activity_fact, _course_fact

_STATES: tuple[CardState, ...] = (
    "in_progress",
    "upcoming",
    "completed",
    "expired",
    "locked",
)


@dataclass(frozen=True, slots=True)
class _Offering:
    id: UUID
    cohort_id: UUID
    starts_at: datetime | None
    ends_at: datetime | None
    effective_ends_at: datetime | None


class _TouchBundle(TypedDict):
    last_touch: dict[UUID, tuple[datetime, int]]
    last_read: dict[UUID, tuple[int, str]]
    read_numbers: dict[UUID, set[int]]


class _DashboardContext(TypedDict):
    now: datetime
    pairs: list[tuple[CourseFact, _Offering]]
    course_ids: list[UUID]
    offering_ids: list[UUID]
    access: dict[UUID, CourseAccessState]
    completions: dict[UUID, CompletionFact]
    certificates: dict[UUID, CertificateFact]
    next_session_by_cohort: dict[UUID, ClassSessionFact]
    activities_by_course: dict[UUID, list[ActivityFact]]
    best: dict[UUID, ScoreFact]
    bundle: _TouchBundle
    chapters_by_course: dict[UUID, list[ChapterFact]]
    due_by_offering: dict[UUID, list[tuple[UUID, datetime | None]]]


def _completion_fact(row: CourseCompletion) -> CompletionFact:
    return CompletionFact(
        **{name: getattr(row, name) for name in CompletionFact.__dataclass_fields__}
    )


def _certificate_fact(row: Certificate) -> CertificateFact:
    return CertificateFact(
        **{name: getattr(row, name) for name in CertificateFact.__dataclass_fields__}
    )


def _entitled_courses(
    db: Session, tenant_id: UUID, person_id: UUID
) -> list[tuple[CourseFact, _Offering]]:
    """(course, offering) pairs the person can see, one row per course.

    Visibility is OWNED by entitlements.visible_course_ids — this query only
    attaches the binding offering window to each visible course. When several
    active offerings cover the same course, the one with the earliest end
    (soonest deadline) wins.
    """
    allowed = visible_course_ids(db, tenant_id=tenant_id, person_id=person_id)
    if not allowed:
        return []
    rows = db.execute(
        select(Course, CourseOffering, Enrollment.access_ends_at)
        .join(
            CourseOffering,
            (CourseOffering.course_id == Course.id)
            & (CourseOffering.tenant_id == Course.tenant_id),
        )
        .join(
            Enrollment,
            (Enrollment.cohort_id == CourseOffering.cohort_id)
            & (Enrollment.tenant_id == CourseOffering.tenant_id),
        )
        .where(Course.tenant_id == tenant_id)
        .where(Course.id.in_(allowed))
        .where(CourseOffering.status == "active")
        .where(Enrollment.person_id == person_id)
        .where(Enrollment.status == "active")
        .order_by(Course.title)
    ).all()
    chosen: dict[UUID, tuple[CourseFact, _Offering]] = {}
    for course, offering, access_ends_at in rows:
        effective_end = (
            access_ends_at
            if access_ends_at is not None
            and (offering.ends_at is None or access_ends_at > offering.ends_at)
            else offering.ends_at
        )
        fact = _Offering(
            offering.id,
            offering.cohort_id,
            offering.starts_at,
            offering.ends_at,
            effective_end,
        )
        held = chosen.get(course.id)
        if held is None:
            chosen[course.id] = (_course_fact(course), fact)
            continue
        held_end = held[1].effective_ends_at
        if effective_end is not None and (held_end is None or effective_end < held_end):
            chosen[course.id] = (_course_fact(course), fact)
    return sorted(chosen.values(), key=lambda pair: pair[0].title)


def _chapters(db: Session, tenant_id: UUID, course_id: UUID) -> list[ChapterFact]:
    return [
        _chapter_fact(row)
        for row in db.scalars(
            select(Chapter)
            .where(Chapter.tenant_id == tenant_id)
            .where(Chapter.course_id == course_id)
            .order_by(Chapter.number)
        ).all()
    ]


def _activities(db: Session, tenant_id: UUID, course_id: UUID) -> list[ActivityFact]:
    return [
        _activity_fact(row)
        for row in db.scalars(
            select(Activity)
            .where(Activity.tenant_id == tenant_id)
            .where(Activity.course_id == course_id)
            .order_by(Activity.chapter_number, Activity.type)
        ).all()
    ]


# ---------------------------------------------------------------------------
# Batched primitives — each computes across ALL of the person's courses in a
# bounded number of queries, so the dashboard/progress render cost does not
# scale with the number of enrolled courses (roadmap review item 5).
# ---------------------------------------------------------------------------


def _activities_by_course(
    db: Session, tenant_id: UUID, course_ids: Sequence[UUID]
) -> dict[UUID, list[ActivityFact]]:
    if not course_ids:
        return {}
    acts = db.scalars(
        select(Activity)
        .where(Activity.tenant_id == tenant_id)
        .where(Activity.course_id.in_(course_ids))
        .order_by(Activity.chapter_number, Activity.type)
    ).all()
    out: dict[UUID, list[ActivityFact]] = {}
    for a in acts:
        out.setdefault(a.course_id, []).append(_activity_fact(a))
    return out


def _chapters_by_course(
    db: Session, tenant_id: UUID, course_ids: Sequence[UUID]
) -> dict[UUID, list[ChapterFact]]:
    if not course_ids:
        return {}
    chs = db.scalars(
        select(Chapter)
        .where(Chapter.tenant_id == tenant_id)
        .where(Chapter.course_id.in_(course_ids))
        .order_by(Chapter.number)
    ).all()
    out: dict[UUID, list[ChapterFact]] = {}
    for c in chs:
        out.setdefault(c.course_id, []).append(_chapter_fact(c))
    return out


def _touch_bundle(
    db: Session, tenant_id: UUID, person_id: UUID, course_ids: Sequence[UUID]
) -> _TouchBundle:
    """Per-course last-touch + last-read + read chapter numbers, in 3 queries.

    Returns {"last_touch": {course_id: (when, chapter_number)},
             "last_read": {course_id: (number, title)},
             "read_numbers": {course_id: set[int]}}.
    'when' is the max across chapter reads, submissions, and opened attempts.
    """
    last_touch: dict[UUID, tuple[datetime, int]] = {}
    last_read: dict[UUID, tuple[int, str]] = {}
    read_numbers: dict[UUID, set[int]] = {}
    if not course_ids:
        return {"last_touch": {}, "last_read": {}, "read_numbers": {}}

    def _offer(course_id: UUID, when: datetime, chapter_number: int | None) -> None:
        if chapter_number is None:
            return
        held = last_touch.get(course_id)
        if held is None or when > held[0]:
            last_touch[course_id] = (when, chapter_number)

    reads = db.execute(
        select(Chapter.course_id, ChapterRead.created_at, Chapter.number, Chapter.title)
        .join(Chapter, Chapter.id == ChapterRead.chapter_id)
        .where(ChapterRead.tenant_id == tenant_id)
        .where(ChapterRead.person_id == person_id)
        .where(Chapter.course_id.in_(course_ids))
        .order_by(ChapterRead.created_at.desc())
    ).all()
    for course_id, when, number, title in reads:
        read_numbers.setdefault(course_id, set()).add(number)
        if course_id not in last_read:  # rows are newest-first
            last_read[course_id] = (number, title)
        _offer(course_id, when, number)

    subs = db.execute(
        select(Activity.course_id, Submission.created_at, Activity.chapter_number)
        .join(
            Activity,
            (Activity.id == Submission.activity_id)
            & (Activity.tenant_id == Submission.tenant_id),
        )
        .where(Submission.tenant_id == tenant_id)
        .where(Submission.person_id == person_id)
        .where(Activity.course_id.in_(course_ids))
    ).all()
    for course_id, when, chapter_number in subs:
        _offer(course_id, when, chapter_number)

    atts = db.execute(
        select(Activity.course_id, ActivityAttempt.started_at, Activity.chapter_number)
        .join(
            Activity,
            (Activity.id == ActivityAttempt.activity_id)
            & (Activity.tenant_id == ActivityAttempt.tenant_id),
        )
        .where(ActivityAttempt.tenant_id == tenant_id)
        .where(ActivityAttempt.person_id == person_id)
        .where(Activity.course_id.in_(course_ids))
    ).all()
    for course_id, when, chapter_number in atts:
        _offer(course_id, when, chapter_number)

    return {
        "last_touch": last_touch,
        "last_read": last_read,
        "read_numbers": read_numbers,
    }


def _due_by_offering(
    db: Session, tenant_id: UUID, offering_ids: Sequence[UUID]
) -> dict[UUID, list[tuple[UUID, datetime | None]]]:
    """All (activity_id, due_at) pacing rows grouped by offering, in ONE query."""
    if not offering_ids:
        return {}
    rows = db.execute(
        select(
            OfferingActivity.offering_id,
            OfferingActivity.activity_id,
            OfferingActivity.due_at,
        )
        .where(OfferingActivity.tenant_id == tenant_id)
        .where(OfferingActivity.offering_id.in_(offering_ids))
    ).all()
    out: dict[UUID, list[tuple[UUID, datetime | None]]] = {}
    for offering_id, activity_id, due in rows:
        out.setdefault(offering_id, []).append((activity_id, due))
    return out


def _resume_from(
    chapters: list[ChapterFact],
    activities: list[ActivityFact],
    best: dict[UUID, ScoreFact],
    read_numbers: set[int],
    last_touch: tuple[datetime, int] | None,
) -> ChapterFact | None:
    """Pure resume computation from already-fetched data (no queries)."""
    if not chapters:
        return None
    passed_acts = {aid for aid, s in best.items() if s.passed}
    acts_by_chapter: dict[int, list[ActivityFact]] = {}
    for a in activities:
        if a.chapter_number is None:
            continue
        acts_by_chapter.setdefault(a.chapter_number, []).append(a)

    def incomplete(ch: ChapterFact) -> bool:
        acts = acts_by_chapter.get(ch.number, [])
        acts_done = all(a.id in passed_acts for a in acts) if acts else True
        return not (acts_done and ch.number in read_numbers)

    start_number = last_touch[1] if last_touch is not None else chapters[0].number
    ordered = [c for c in chapters if c.number >= start_number] + [
        c for c in chapters if c.number < start_number
    ]
    for ch in ordered:
        if incomplete(ch):
            return ch
    return None


def resume_chapter(
    db: Session, *, tenant_id: UUID, person_id: UUID, course_id: UUID
) -> ChapterFact | None:
    """The chapter to resume at: the first incomplete chapter at or after the
    last touched one (wrapping to the start), or None when nothing remains.

    Public single-course entry point (unchanged signature); the batched
    dashboard path uses :func:`_resume_from` with already-fetched data.
    """
    chapters = _chapters(db, tenant_id, course_id)
    if not chapters:
        return None
    best = best_scores_for(
        db, tenant_id=tenant_id, person_id=person_id, course_id=course_id
    )
    bundle = _touch_bundle(db, tenant_id, person_id, [course_id])
    return _resume_from(
        chapters,
        _activities(db, tenant_id, course_id),
        best,
        bundle["read_numbers"].get(course_id, set()),
        bundle["last_touch"].get(course_id),
    )


def continue_target(
    db: Session,
    *,
    tenant_id: UUID,
    person_id: UUID,
    _ctx: _DashboardContext | None = None,
) -> ContinueTarget | None:
    """Cross-course resume: the most recently touched course's resume chapter.

    ``_ctx`` (from :func:`_dashboard_context`) lets ``learner_home`` share the
    already-computed touch/activity data so the ``/`` render fans out once, not
    twice (roadmap review item 5). Standalone callers pass nothing.
    """
    if _ctx is not None:
        pairs = _ctx["pairs"]
    else:
        pairs = _entitled_courses(db, tenant_id, person_id)
    if not pairs:
        return None
    courses_by_id = {c.id: c for c, _ in pairs}
    course_ids = [c.id for c, _ in pairs]
    bundle = (
        _ctx["bundle"]
        if _ctx is not None
        else _touch_bundle(db, tenant_id, person_id, course_ids)
    )
    last_touch = bundle["last_touch"]
    latest_course_id = None
    latest_when = None
    for cid in course_ids:
        t = last_touch.get(cid)
        if t is not None and (latest_when is None or t[0] > latest_when):
            latest_when, latest_course_id = t[0], cid
    if latest_course_id is None:
        return None
    if _ctx is not None:
        chapter = _resume_from(
            _ctx["chapters_by_course"].get(latest_course_id, []),
            _ctx["activities_by_course"].get(latest_course_id, []),
            _ctx["best"],
            bundle["read_numbers"].get(latest_course_id, set()),
            last_touch.get(latest_course_id),
        )
    else:
        chapter = resume_chapter(
            db, tenant_id=tenant_id, person_id=person_id, course_id=latest_course_id
        )
    if chapter is None:
        return None
    return ContinueTarget(courses_by_id[latest_course_id], chapter)


def _next_deadline_from(
    due_rows: list[tuple[UUID, datetime | None]],
    ends_at: datetime | None,
    now: datetime,
) -> datetime | None:
    """Earliest future deadline from already-fetched (activity_id, due) rows."""
    deadlines = [due for _aid, due in due_rows if due is not None and due > now]
    if ends_at is not None and ends_at > now:
        deadlines.append(ends_at)
    return min(deadlines) if deadlines else None


def _dashboard_context(
    db: Session, tenant_id: UUID, person_id: UUID, now: datetime | None
) -> _DashboardContext:
    """All batched inputs the dashboard + cross-course resume share, fetched
    once (bounded query count) so ``home()`` never recomputes per course."""
    now = now or datetime.now(UTC)
    pairs = _entitled_courses(db, tenant_id, person_id)
    course_ids = [c.id for c, _ in pairs]
    offering_ids = [o.id for _, o in pairs]
    sessions = scheduling.list_for_person(
        db, tenant_id=tenant_id, person_id=person_id, now=now
    )
    next_session_by_cohort: dict[UUID, ClassSessionFact] = {}
    for entry in sessions.upcoming:
        session = entry.session
        next_session_by_cohort.setdefault(session.cohort_id, session)
    return {
        "now": now,
        "pairs": pairs,
        "course_ids": course_ids,
        "offering_ids": offering_ids,
        "access": course_access_states(db, tenant_id=tenant_id, person_id=person_id),
        "completions": {
            c.course_id: _completion_fact(c)
            for c in db.scalars(
                select(CourseCompletion)
                .where(CourseCompletion.tenant_id == tenant_id)
                .where(CourseCompletion.person_id == person_id)
            ).all()
        },
        "certificates": {
            c.course_id: _certificate_fact(c)
            for c in db.scalars(
                select(Certificate)
                .where(Certificate.tenant_id == tenant_id)
                .where(Certificate.person_id == person_id)
            ).all()
        },
        "next_session_by_cohort": next_session_by_cohort,
        "activities_by_course": _activities_by_course(db, tenant_id, course_ids),
        "best": best_scores_for_person(
            db, tenant_id=tenant_id, person_id=person_id, course_ids=course_ids
        ),
        "bundle": _touch_bundle(db, tenant_id, person_id, course_ids),
        "chapters_by_course": _chapters_by_course(db, tenant_id, course_ids),
        "due_by_offering": _due_by_offering(db, tenant_id, offering_ids),
    }


def learner_home(
    db: Session, *, tenant_id: UUID, person_id: UUID, now: datetime | None = None
) -> LearnerHome:
    """Shared batched home projection with cross-course resume."""
    ctx = _dashboard_context(db, tenant_id, person_id, now)
    result = course_cards(db, tenant_id=tenant_id, person_id=person_id, _ctx=ctx)
    return LearnerHome(
        result.cards,
        result.counts,
        result.filters,
        continue_target(db, tenant_id=tenant_id, person_id=person_id, _ctx=ctx),
    )


def course_cards(
    db: Session,
    *,
    tenant_id: UUID,
    person_id: UUID,
    now: datetime | None = None,
    _ctx: _DashboardContext | None = None,
) -> CourseCards:
    """The My Courses projection: cards with one state and one action each.

    Returns detached cards, ordered state counts and semantic filter codes.
    """
    ctx = (
        _ctx if _ctx is not None else _dashboard_context(db, tenant_id, person_id, now)
    )
    now = ctx["now"]
    pairs = ctx["pairs"]
    access = ctx["access"]
    completions = ctx["completions"]
    certificates = ctx["certificates"]
    next_session_by_cohort = ctx["next_session_by_cohort"]
    activities_by_course = ctx["activities_by_course"]
    best = ctx["best"]
    bundle = ctx["bundle"]
    chapters_by_course = ctx["chapters_by_course"]
    due_by_offering = ctx["due_by_offering"]
    last_touch = bundle["last_touch"]
    last_read = bundle["last_read"]
    read_numbers = bundle["read_numbers"]

    cards: list[CourseCard] = []
    counts: dict[CardState, int] = dict.fromkeys(_STATES, 0)
    for course, offering in pairs:
        activities = activities_by_course.get(course.id, [])
        total = len(activities)
        passed = sum(1 for a in activities if a.id in best and best[a.id].passed)
        pct = round(100 * passed / total) if total else 0
        grade = grade_from(activities, best)
        final_threshold = max((a.pass_threshold for a in activities), default=0.0)

        completion = completions.get(course.id)
        state_info = access.get(course.id)
        touch = last_touch.get(course.id)

        state: CardState
        if state_info is not None and state_info.locked:
            state = "locked"
        elif offering.starts_at is not None and offering.starts_at > now:
            state = "upcoming"
        elif completion is not None and completion.status == "completed":
            state = "completed"
        else:
            effective_ends_at = offering.effective_ends_at
            state = (
                "expired"
                if effective_ends_at is not None and effective_ends_at < now
                else "in_progress"
            )
        counts[state] += 1

        next_activity = next(
            (a for a in activities if a.id not in best or not best[a.id].passed), None
        )
        read_row = last_read.get(course.id)

        certificate = certificates.get(course.id)
        cert_status: CertificateStatus | None
        if certificate is not None:
            cert_status = "issued"
        elif state == "completed":
            cert_status = "eligible"
        else:
            cert_status = None

        action: DashboardAction | None = None
        if state == "in_progress":
            if touch is None:
                action = DashboardAction("start", course.id, 1)
            else:
                target = _resume_from(
                    chapters_by_course.get(course.id, []),
                    activities,
                    best,
                    read_numbers.get(course.id, set()),
                    touch,
                )
                number = target.number if target is not None else 1
                action = DashboardAction("resume", course.id, number)
        elif state == "completed":
            action = DashboardAction("review_feedback", course.id)

        cards.append(
            CourseCard(
                course,
                state,
                pct,
                passed,
                total,
                grade.pct,
                int(round(final_threshold * 100)),
                next_activity,
                _next_deadline_from(
                    due_by_offering.get(offering.id, []),
                    offering.effective_ends_at,
                    now,
                ),
                offering.starts_at,
                LastRead(*read_row) if read_row is not None else None,
                next_session_by_cohort.get(offering.cohort_id),
                cert_status,
                state_info.locked_reason
                if state_info is not None and state_info.locked
                else None,
                action,
            )
        )
    return CourseCards(
        tuple(cards), tuple(StateCount(s, counts[s]) for s in _STATES), _STATES
    )


def _trend(points: list[int], terms: ProgressTrendTerms) -> Trend:
    if len(points) < terms.min_samples:
        return "flat"
    half = len(points) // 2
    earlier = sum(points[:half]) / half
    later = sum(points[half:]) / (len(points) - half)
    if later - earlier > terms.min_delta_fraction * 100:
        return "up"
    if earlier - later > terms.min_delta_fraction * 100:
        return "down"
    return "flat"


def _due_key(row: ProgressActivity) -> datetime:
    # Only rows with future, non-null due dates enter the upcoming collection.
    if row.due_at is None:
        raise AssertionError
    return row.due_at


def progress_overview(
    db: Session,
    *,
    tenant_id: UUID,
    person_id: UUID,
    progress_trend: ProgressTrendTerms,
    feedback_terms: FeedbackRevealTerms,
    now: datetime | None = None,
) -> tuple[CourseProgress, ...]:
    """The rich per-course progress projection for /progress."""
    now = now or datetime.now(UTC)
    pairs = _entitled_courses(db, tenant_id, person_id)
    course_ids = [c.id for c, _ in pairs]
    offering_ids = [o.id for _, o in pairs]

    # Batched fan-out (roadmap review item 5).
    activities_by_course = _activities_by_course(db, tenant_id, course_ids)
    best = best_scores_for_person(
        db, tenant_id=tenant_id, person_id=person_id, course_ids=course_ids
    )
    attempts_by_activity = attempts_used_by_person(
        db, tenant_id=tenant_id, person_id=person_id
    )
    grants_by_activity = attempt_policy.granted_by_activity(
        db, tenant_id=tenant_id, person_id=person_id
    )
    due_rows_by_offering = _due_by_offering(db, tenant_id, offering_ids)
    completions = {
        c.course_id: _completion_fact(c)
        for c in db.scalars(
            select(CourseCompletion)
            .where(CourseCompletion.tenant_id == tenant_id)
            .where(CourseCompletion.person_id == person_id)
        ).all()
    }
    certificates = {
        c.course_id: _certificate_fact(c)
        for c in db.scalars(
            select(Certificate)
            .where(Certificate.tenant_id == tenant_id)
            .where(Certificate.person_id == person_id)
        ).all()
    }
    # Trend points: every score across the person's courses, ordered, grouped.
    trend_by_course: dict[UUID, list[int]] = {}
    for course_id, fraction in db.execute(
        select(Activity.course_id, Score.fraction)
        .join(
            Submission,
            (Submission.id == Score.submission_id)
            & (Submission.tenant_id == Score.tenant_id),
        )
        .join(
            Activity,
            (Activity.id == Submission.activity_id)
            & (Activity.tenant_id == Submission.tenant_id),
        )
        .where(Score.tenant_id == tenant_id)
        .where(Submission.person_id == person_id)
        .where(
            Activity.course_id.in_(course_ids)
            if course_ids
            else Activity.course_id.is_(None)
        )
        .order_by(Score.created_at.asc())
    ).all():
        trend_by_course.setdefault(course_id, []).append(round(100 * fraction))

    out: list[CourseProgress] = []
    for course, offering in pairs:
        activities = activities_by_course.get(course.id, [])
        grade = grade_from(activities, best)
        due_by_activity = dict(due_rows_by_offering.get(offering.id, []))

        rows: list[ProgressActivity] = []
        upcoming: list[ProgressActivity] = []
        overdue: list[ProgressActivity] = []
        for a in activities:
            score = best.get(a.id)
            used = attempts_by_activity.get(a.id, 0)
            ent = attempt_policy.entitlement(
                a, used=used, granted=grants_by_activity.get(a.id, 0)
            )
            remaining = ent.remaining
            due = due_by_activity.get(a.id) or offering.ends_at
            is_passed = score is not None and score.passed
            is_overdue = due is not None and due < now and not is_passed
            row = ProgressActivity(
                a,
                a.chapter_number,
                score,
                round(100 * score.fraction) if score is not None else None,
                is_passed,
                int(round(a.pass_threshold * 100)),
                used,
                ent.limit,
                remaining,
                ent.granted,
                score is not None
                and reveal_feedback(
                    a,
                    terms=feedback_terms,
                    passed=score.passed,
                    attempts_used=used,
                    attempts_granted=ent.granted,
                ),
                due,
                is_overdue,
            )
            rows.append(row)
            if is_overdue:
                overdue.append(row)
            elif due is not None and due > now and not is_passed:
                upcoming.append(row)

        score_points = trend_by_course.get(course.id, [])[
            -progress_trend.sample_limit :
        ]

        completion = completions.get(course.id)
        certificate = certificates.get(course.id)
        completed = completion is not None and completion.status == "completed"

        out.append(
            CourseProgress(
                course,
                grade.pct,
                round(100 * completion.pct) if completion is not None else 0,
                int(
                    round(
                        max((a.pass_threshold for a in activities), default=0.0) * 100
                    )
                ),
                tuple(rows),
                tuple(sorted(upcoming, key=_due_key)),
                tuple(overdue),
                tuple(score_points),
                _trend(score_points, progress_trend),
                ProgressCertificate(
                    "issued"
                    if certificate is not None
                    else ("eligible" if completed else None),
                    certificate.serial if certificate is not None else None,
                    course.id if completed else None,
                ),
            )
        )
    return tuple(out)


__all__ = [
    "resume_chapter",
    "continue_target",
    "learner_home",
    "course_cards",
    "progress_overview",
]
