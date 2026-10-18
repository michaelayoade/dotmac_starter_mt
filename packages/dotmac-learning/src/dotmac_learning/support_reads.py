"""Source legacy dashboard, behind-pace and search computations.

These policies intentionally differ from insights and instructor workspaces.
There is no account-status predicate in the legacy dashboard, and behind-pace
selection includes student enrollments regardless of enrollment status.
Search preserves ILIKE wildcard behavior and unspecified SQL ordering. Academy
owns snippet extraction from raw chapter HTML and all notification operations.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from .curriculum import _fact as _chapter_fact
from .curriculum_models import Chapter
from .entitlements import accessible_course_ids
from .insights import _cohort_course_ids
from .models import Activity, Course, CourseCompletion, Score, Submission
from .participation_models import CourseOffering, Enrollment
from .references import _course_fact
from .reports import _cohort, _students
from .support_contracts import (
    AtRiskCourse,
    CourseRiskReason,
    LegacyCohortOverview,
    LegacyCohortStudent,
    SearchAuthority,
    SearchChapter,
    SearchResults,
)

DEFAULT_BEHIND_THRESHOLD = 0.2
_CAP = 20


def _dashboard_state(
    course_ids: Sequence[UUID],
    pcts: Mapping[UUID, float],
    last_at: datetime | None,
    now: datetime,
    at_risk_pct: float,
    stale_days: int,
) -> tuple[float, bool]:
    completion_pct = (
        sum(pcts.get(cid, 0.0) for cid in course_ids) / len(course_ids)
        if course_ids
        else 0.0
    )
    stale_before = now - timedelta(days=stale_days)
    inactive = last_at is None or last_at < stale_before
    return completion_pct, completion_pct < at_risk_pct and inactive


def cohort_overview(
    db: Session,
    *,
    tenant_id: UUID,
    cohort_id: UUID,
    now: datetime | None = None,
    at_risk_pct: float = 0.5,
    stale_days: int = 14,
) -> LegacyCohortOverview:
    """Legacy completion mean and course-scoped score inactivity, not insights."""
    now = now or datetime.now(UTC)
    cohort = _cohort(db, tenant_id, cohort_id)
    course_ids = _cohort_course_ids(db, tenant_id, cohort_id)
    students = _students(db, tenant_id, cohort_id)
    rows = []
    for p in students:
        pcts: dict[UUID, float] = {}
        if course_ids:
            pcts = {  # noqa: C416 - retain source-shaped selected columns
                cid: pct
                for cid, pct in db.execute(
                    select(CourseCompletion.course_id, CourseCompletion.pct)
                    .where(CourseCompletion.tenant_id == tenant_id)
                    .where(CourseCompletion.person_id == p.id)
                    .where(CourseCompletion.course_id.in_(course_ids))
                ).all()
            }
            last_activity_at = db.scalar(
                select(func.max(Score.created_at))
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
                .where(Submission.person_id == p.id)
                .where(Activity.course_id.in_(course_ids))
            )
        else:
            last_activity_at = None
        pct, risk = _dashboard_state(
            course_ids, pcts, last_activity_at, now, at_risk_pct, stale_days
        )
        rows.append(LegacyCohortStudent(p, pct, last_activity_at, risk))
    return LegacyCohortOverview(cohort, tuple(rows))


def _risk_for_window(
    starts_at: datetime | None,
    ends_at: datetime | None,
    pct: float,
    completed: bool,
    now: datetime,
    behind_threshold: float,
) -> tuple[CourseRiskReason, float] | None:
    if completed:
        return None
    if ends_at is not None and ends_at < now:
        return "overdue", 1.0
    if starts_at is not None and ends_at is not None and starts_at < now < ends_at:
        span = (ends_at - starts_at).total_seconds()
        elapsed = (now - starts_at).total_seconds() / span if span > 0 else 0.0
        if pct < elapsed - behind_threshold:
            return "behind", elapsed
    return None


def at_risk_for_person(
    db: Session,
    *,
    tenant_id: UUID,
    person_id: UUID,
    behind_threshold: float = DEFAULT_BEHIND_THRESHOLD,
    now: datetime | None = None,
) -> tuple[AtRiskCourse, ...]:
    now = now or datetime.now(UTC)
    cohort_ids = list(
        db.scalars(
            select(Enrollment.cohort_id)
            .where(Enrollment.tenant_id == tenant_id)
            .where(Enrollment.person_id == person_id)
            .where(Enrollment.role_in_cohort == "student")
        ).all()
    )
    if not cohort_ids:
        return ()
    offerings = db.scalars(
        select(CourseOffering)
        .where(CourseOffering.tenant_id == tenant_id)
        .where(CourseOffering.status == "active")
        .where(CourseOffering.cohort_id.in_(cohort_ids))
    ).all()
    if not offerings:
        return ()
    completions = {
        c.course_id: c
        for c in db.scalars(
            select(CourseCompletion)
            .where(CourseCompletion.tenant_id == tenant_id)
            .where(CourseCompletion.person_id == person_id)
        ).all()
    }
    course_ids = {o.course_id for o in offerings}
    titles = {
        c.id: c.title
        for c in db.scalars(
            select(Course)
            .where(Course.tenant_id == tenant_id)
            .where(Course.id.in_(course_ids))
        ).all()
    }
    results = []
    for off in offerings:
        comp = completions.get(off.course_id)
        pct = comp.pct if comp else 0.0
        risk = _risk_for_window(
            off.starts_at,
            off.ends_at,
            pct,
            comp is not None and comp.status == "completed",
            now,
            behind_threshold,
        )
        if risk is not None:
            reason, expected = risk
            results.append(
                AtRiskCourse(
                    off.course_id, titles.get(off.course_id), reason, pct, expected
                )
            )
    return tuple(results)


def _search_authority(
    tenant_id: UUID, person_id: UUID, authority: SearchAuthority
) -> None:
    if (authority.tenant_id, authority.person_id) != (tenant_id, person_id):
        raise ValueError("search authority does not cover tenant/person")


def search(
    db: Session, *, tenant_id: UUID, person_id: UUID, q: str, authority: SearchAuthority
) -> SearchResults:
    _search_authority(tenant_id, person_id, authority)
    if not q or not q.strip():
        return SearchResults("", (), ())
    term = q.strip()
    pattern = f"%{term}%"
    if authority.is_staff:
        allowed_ids: set[UUID] | None = None
    else:
        allowed_ids = accessible_course_ids(
            db, tenant_id=tenant_id, person_id=person_id
        )
        if not allowed_ids:
            return SearchResults(term, (), ())
    course_q = (
        select(Course)
        .where(Course.tenant_id == tenant_id)
        .where(Course.title.ilike(pattern))
    )
    if allowed_ids is not None:
        course_q = course_q.where(Course.id.in_(allowed_ids))
    course_rows = db.scalars(course_q.limit(_CAP)).all()
    chapter_q = (
        select(Chapter, Course.slug)
        .join(
            Course,
            (Course.id == Chapter.course_id) & (Course.tenant_id == Chapter.tenant_id),
        )
        .where(Chapter.tenant_id == tenant_id)
        .where(or_(Chapter.title.ilike(pattern), Chapter.body_html.ilike(pattern)))
    )
    if allowed_ids is not None:
        chapter_q = chapter_q.where(Chapter.course_id.in_(allowed_ids))
    chapter_rows = db.execute(chapter_q.limit(_CAP)).all()
    return SearchResults(
        term,
        tuple(_course_fact(c) for c in course_rows),
        tuple(SearchChapter(_chapter_fact(c), slug) for c, slug in chapter_rows),
    )
