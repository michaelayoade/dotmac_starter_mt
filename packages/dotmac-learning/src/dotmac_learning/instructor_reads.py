"""Source instructor learning reads; global route guards remain in Academy.

Roster visibility does not filter account status. Academy supplies its trusted
active-account relation only for counts/risk. Candidate role resolution
and admin determination remain product/Kernel authorization responsibilities.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from uuid import UUID

from dotmac_kernel.models import Party, PartyPerson
from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from . import tracks
from .account_contracts import ActiveAccountSelection
from .curriculum import _fact as _chapter_fact
from .curriculum_contracts import ChapterFact
from .curriculum_models import Chapter
from .instructor_contracts import (
    InstructorAuthority,
    InstructorChapter,
    InstructorChapterNotFound,
    InstructorCohorts,
    InstructorCohortSummary,
    InstructorCourseForbidden,
    InstructorCourseNotFound,
    InstructorCourseWorkspace,
    InstructorOffering,
    InstructorPreview,
    InstructorResult,
    InstructorRosterMember,
    InstructorStudent,
    InstructorWorkspace,
)
from .models import Activity, Course, CourseCompletion, Score, Submission
from .participation_contracts import enrollment_fact
from .participation_models import Cohort, CourseOffering, Enrollment
from .references import CourseFact, _activity_fact, _course_fact
from .reports import _cohort, _person, _score
from .reports_contracts import ReportCohort, ReportPerson


def _require_accounts(tenant_id: UUID, accounts: ActiveAccountSelection) -> None:
    if accounts.tenant_id != tenant_id:
        raise ValueError("account observations do not cover tenant")


def _require_authority(
    tenant_id: UUID, person_id: UUID, authority: InstructorAuthority
) -> None:
    if (authority.tenant_id, authority.person_id) != (tenant_id, person_id):
        raise ValueError("instructor authority does not cover tenant/person")


def _cohort_fact(cohort: Cohort) -> ReportCohort:
    return ReportCohort(
        **{name: getattr(cohort, name) for name in ReportCohort.__dataclass_fields__}
    )


def _roster_query(tenant_id: UUID) -> Select[tuple[Enrollment, Party, PartyPerson]]:
    return (
        select(Enrollment, Party, PartyPerson)
        .join(
            Party,
            (Party.id == Enrollment.person_id)
            & (Party.tenant_id == Enrollment.tenant_id),
        )
        .join(PartyPerson, PartyPerson.party_id == Party.id)
        .where(Enrollment.tenant_id == tenant_id)
        .order_by(PartyPerson.last_name, PartyPerson.first_name, Party.email)
    )


def _member(
    enrollment: Enrollment, party: Party, profile: PartyPerson
) -> InstructorRosterMember:
    return InstructorRosterMember(enrollment_fact(enrollment), _person(party, profile))


def cohorts_summary(
    db: Session, *, tenant_id: UUID, accounts: ActiveAccountSelection
) -> InstructorCohorts:
    _require_accounts(tenant_id, accounts)
    cohorts = db.scalars(
        select(Cohort).where(Cohort.tenant_id == tenant_id).order_by(Cohort.name)
    ).all()
    roster_by_cohort: dict[UUID, list[InstructorRosterMember]] = {
        c.id: [] for c in cohorts
    }
    counts = dict.fromkeys(roster_by_cohort, 0)
    facilitators: dict[UUID, list[ReportPerson]] = {c.id: [] for c in cohorts}
    for enrollment, party, profile, account_active in db.execute(
        _roster_query(tenant_id).add_columns(Party.id.in_(accounts.active_person_ids))
    ).all():
        if enrollment.cohort_id not in roster_by_cohort:
            continue
        person = _person(party, profile)
        if (
            enrollment.status == "active"
            and enrollment.role_in_cohort == "student"
            and account_active
        ):
            counts[enrollment.cohort_id] += 1
        if enrollment.status == "active" and enrollment.role_in_cohort == "instructor":
            facilitators[enrollment.cohort_id].append(person)
        roster_by_cohort[enrollment.cohort_id].append(
            _member(enrollment, party, profile)
        )
    track_items_by_cohort = {
        c.id: tracks.list_cohort_tracks(db, tenant_id=tenant_id, cohort_id=c.id)
        for c in cohorts
    }
    selectable = db.scalars(
        select(Course)
        .where(Course.tenant_id == tenant_id)
        .where(Course.status.in_(("published", "completed")))
        .order_by(Course.title)
    ).all()
    draft_count = (
        db.scalar(
            select(func.count())
            .select_from(Course)
            .where(Course.tenant_id == tenant_id)
            .where(Course.status == "draft")
        )
        or 0
    )
    offerings = db.execute(
        select(CourseOffering.cohort_id, Course)
        .join(
            Course,
            (Course.id == CourseOffering.course_id)
            & (Course.tenant_id == CourseOffering.tenant_id),
        )
        .where(CourseOffering.tenant_id == tenant_id)
        .where(CourseOffering.status == "active")
        .order_by(Course.title)
    ).all()
    courses_by_cohort: dict[UUID, list[CourseFact]] = {c.id: [] for c in cohorts}
    seen: set[tuple[UUID, UUID]] = set()
    for cohort_id, course in offerings:
        key = (cohort_id, course.id)
        if cohort_id in courses_by_cohort and key not in seen:
            courses_by_cohort[cohort_id].append(_course_fact(course))
            seen.add(key)
    rows = tuple(
        InstructorCohortSummary(
            _cohort_fact(c),
            tuple(roster_by_cohort[c.id]),
            counts[c.id],
            tuple(facilitators[c.id]),
            tuple(courses_by_cohort[c.id]),
            track_items_by_cohort[c.id],
        )
        for c in cohorts
    )
    return InstructorCohorts(
        rows, tuple(_course_fact(c) for c in selectable), int(draft_count)
    )


def _progress(
    course_ids: Sequence[UUID],
    pcts: Mapping[UUID, float],
    is_current: bool,
    last_at: datetime | None,
    now: datetime,
    at_risk_pct: float,
    stale_days: int,
) -> tuple[float, bool]:
    pct = (
        sum(pcts.get(cid, 0.0) for cid in course_ids) / len(course_ids)
        if course_ids
        else 0.0
    )
    risk = (
        is_current
        and pct < at_risk_pct
        and (last_at is None or last_at < now - timedelta(days=stale_days))
    )
    return pct, risk


def cohort_workspace(
    db: Session,
    *,
    tenant_id: UUID,
    cohort_id: UUID,
    accounts: ActiveAccountSelection,
    now: datetime | None = None,
    at_risk_pct: float,
    stale_days: int,
    limit: int,
    offset: int = 0,
) -> InstructorWorkspace:
    _require_accounts(tenant_id, accounts)
    cohort = _cohort(db, tenant_id, cohort_id)
    roster = db.execute(
        _roster_query(tenant_id)
        .add_columns(Party.id.in_(accounts.active_person_ids))
        .where(Enrollment.cohort_id == cohort_id)
        .where(Enrollment.role_in_cohort == "student")
    ).all()
    student_ids = [p.id for _, p, _, _ in roster]
    offering_pairs = db.execute(
        select(CourseOffering, Course)
        .join(
            Course,
            (Course.id == CourseOffering.course_id)
            & (Course.tenant_id == CourseOffering.tenant_id),
        )
        .where(CourseOffering.tenant_id == tenant_id)
        .where(CourseOffering.cohort_id == cohort_id)
        .order_by(Course.title)
    ).all()
    active_courses = [o.course_id for o, _ in offering_pairs if o.status == "active"]
    completion: dict[UUID, dict[UUID, float]] = {}
    if student_ids and active_courses:
        for pid, cid, pct in db.execute(
            select(
                CourseCompletion.person_id,
                CourseCompletion.course_id,
                CourseCompletion.pct,
            )
            .where(CourseCompletion.tenant_id == tenant_id)
            .where(CourseCompletion.person_id.in_(student_ids))
            .where(CourseCompletion.course_id.in_(active_courses))
        ).all():
            completion.setdefault(pid, {})[cid] = pct
    last_by_student: dict[UUID, datetime] = {}
    if student_ids and active_courses:
        for pid, stamp in db.execute(
            select(Submission.person_id, func.max(Score.created_at))
            .join(
                Score,
                (Score.submission_id == Submission.id)
                & (Score.tenant_id == Submission.tenant_id),
            )
            .join(
                Activity,
                (Activity.id == Submission.activity_id)
                & (Activity.tenant_id == Submission.tenant_id),
            )
            .where(Submission.tenant_id == tenant_id)
            .where(Submission.person_id.in_(student_ids))
            .where(Activity.course_id.in_(active_courses))
            .group_by(Submission.person_id)
        ).all():
            last_by_student[pid] = stamp
    track_items = tracks.list_cohort_tracks(
        db, tenant_id=tenant_id, cohort_id=cohort_id
    )
    course_tracks: dict[UUID, list[UUID]] = {}
    for item in track_items:
        for course in item.courses:
            course_tracks.setdefault(course.id, []).append(item.track.id)
    now = now or datetime.now(UTC)
    students: list[InstructorStudent] = []
    active: list[InstructorStudent] = []
    for enrollment, party, profile, account_active in roster:
        current = enrollment.status == "active" and account_active
        pct, risk = _progress(
            active_courses,
            completion.get(party.id, {}),
            current,
            last_by_student.get(party.id),
            now,
            at_risk_pct,
            stale_days,
        )
        row = InstructorStudent(
            enrollment_fact(enrollment),
            _person(party, profile),
            pct,
            last_by_student.get(party.id),
            risk,
        )
        students.append(row)
        if current:
            active.append(row)
    facilitators = db.execute(
        _roster_query(tenant_id)
        .where(Enrollment.cohort_id == cohort_id)
        .where(Enrollment.role_in_cohort == "instructor")
        .where(Enrollment.status == "active")
    ).all()
    offered_ids = [c.id for _, c in offering_pairs]
    chapters: dict[UUID, list[Chapter]] = {cid: [] for cid in offered_ids}
    activities: dict[UUID, list[Activity]] = {cid: [] for cid in offered_ids}
    if offered_ids:
        for chapter in db.scalars(
            select(Chapter)
            .where(Chapter.tenant_id == tenant_id)
            .where(Chapter.course_id.in_(offered_ids))
            .order_by(Chapter.course_id, Chapter.order_index, Chapter.number)
        ).all():
            chapters.setdefault(chapter.course_id, []).append(chapter)
        for activity in db.scalars(
            select(Activity)
            .where(Activity.tenant_id == tenant_id)
            .where(Activity.course_id.in_(offered_ids))
            .order_by(Activity.course_id, Activity.chapter_number, Activity.title)
        ).all():
            activities.setdefault(activity.course_id, []).append(activity)
    course_rows: list[InstructorCourseWorkspace] = []
    for offering, course in offering_pairs:
        cc, aa = chapters.get(course.id, []), activities.get(course.id, [])
        numbers = {c.number for c in cc}
        chapter_rows = tuple(
            InstructorChapter(
                _chapter_fact(c),
                tuple(_activity_fact(a) for a in aa if a.chapter_number == c.number),
            )
            for c in cc
        )
        standalone = tuple(
            _activity_fact(a)
            for a in aa
            if a.chapter_number is None or a.chapter_number not in numbers
        )
        offering_fact = InstructorOffering(
            **{
                name: getattr(offering, name)
                for name in InstructorOffering.__dataclass_fields__
            }
        )
        course_rows.append(
            InstructorCourseWorkspace(
                offering_fact,
                _course_fact(course),
                chapter_rows,
                standalone,
                len(aa),
                tuple(course_tracks.get(course.id, [])),
            )
        )
    return InstructorWorkspace(
        cohort,
        tuple(students[offset : offset + limit]),
        len(students),
        len(active),
        sum(1 for r in active if r.at_risk),
        sum(r.completion_pct for r in active) / len(active) if active else 0.0,
        tuple(_member(r[0], r[1], r[2]) for r in facilitators),
        tuple(course_rows),
        track_items,
    )


def assigned_course_ids(
    db: Session, *, tenant_id: UUID, instructor_id: UUID
) -> frozenset[UUID]:
    rows = db.scalars(
        select(CourseOffering.course_id)
        .join(
            Enrollment,
            (Enrollment.cohort_id == CourseOffering.cohort_id)
            & (Enrollment.tenant_id == CourseOffering.tenant_id),
        )
        .where(CourseOffering.tenant_id == tenant_id)
        .where(CourseOffering.status == "active")
        .where(Enrollment.person_id == instructor_id)
        .where(Enrollment.role_in_cohort == "instructor")
        .where(Enrollment.status == "active")
    ).all()
    return frozenset(rows)


def authorable_courses(
    db: Session, *, tenant_id: UUID, person_id: UUID, authority: InstructorAuthority
) -> tuple[CourseFact, ...]:
    _require_authority(tenant_id, person_id, authority)
    stmt = select(Course).where(Course.tenant_id == tenant_id).order_by(Course.title)
    if not authority.actor_is_admin:
        assigned = assigned_course_ids(db, tenant_id=tenant_id, instructor_id=person_id)
        if not assigned:
            return ()
        stmt = stmt.where(Course.id.in_(assigned))
    return tuple(_course_fact(c) for c in db.scalars(stmt).all())


def authorable_course(
    db: Session,
    *,
    tenant_id: UUID,
    person_id: UUID,
    course_id: UUID,
    authority: InstructorAuthority,
) -> CourseFact:
    _require_authority(tenant_id, person_id, authority)
    course = db.scalars(
        select(Course)
        .where(Course.tenant_id == tenant_id)
        .where(Course.id == course_id)
    ).first()
    if course is None:
        raise InstructorCourseNotFound()
    if not authority.actor_is_admin and course.id not in assigned_course_ids(
        db, tenant_id=tenant_id, instructor_id=person_id
    ):
        raise InstructorCourseForbidden("Forbidden")
    return _course_fact(course)


def assessment_results(db: Session, *, tenant_id: UUID) -> tuple[InstructorResult, ...]:
    rows = db.execute(
        select(Party.email, Activity.title, Score)
        .join(PartyPerson, PartyPerson.party_id == Party.id)
        .join(
            Submission,
            (Submission.person_id == Party.id)
            & (Submission.tenant_id == Party.tenant_id),
        )
        .join(
            Activity,
            (Activity.id == Submission.activity_id)
            & (Activity.tenant_id == Submission.tenant_id),
        )
        .join(
            Score,
            (Score.submission_id == Submission.id)
            & (Score.tenant_id == Submission.tenant_id),
        )
        .where(Party.tenant_id == tenant_id)
    ).all()
    return tuple(
        InstructorResult(email, title, _score(score)) for email, title, score in rows
    )


def authoring_chapters(
    db: Session, *, tenant_id: UUID, course_id: UUID
) -> tuple[ChapterFact, ...]:
    """Number order for authoring/preview; distinct from workspace order_index."""
    rows = db.scalars(
        select(Chapter)
        .where(Chapter.tenant_id == tenant_id)
        .where(Chapter.course_id == course_id)
        .order_by(Chapter.number)
    ).all()
    return tuple(_chapter_fact(c) for c in rows)


def course_preview(
    db: Session,
    *,
    tenant_id: UUID,
    person_id: UUID,
    course_id: UUID,
    authority: InstructorAuthority,
    chapter_number: int | None = None,
) -> InstructorPreview:
    course = authorable_course(
        db,
        tenant_id=tenant_id,
        person_id=person_id,
        course_id=course_id,
        authority=authority,
    )
    chapters = authoring_chapters(db, tenant_id=tenant_id, course_id=course.id)
    if chapter_number is None:
        chapter = chapters[0] if chapters else None
    else:
        chapter = next((c for c in chapters if c.number == chapter_number), None)
        if chapter is None:
            raise InstructorChapterNotFound()
    activity = None
    previous = following = None
    if chapter is not None:
        row = db.scalars(
            select(Activity)
            .where(Activity.tenant_id == tenant_id)
            .where(Activity.course_id == course.id)
            .where(Activity.chapter_number == chapter.number)
        ).first()
        activity = _activity_fact(row) if row is not None else None
        previous = next(
            (c for c in reversed(chapters) if c.number < chapter.number), None
        )
        following = next((c for c in chapters if c.number > chapter.number), None)
    return InstructorPreview(course, chapters, chapter, activity, previous, following)


def cohort_choices(db: Session, *, tenant_id: UUID) -> tuple[ReportCohort, ...]:
    """The announcements page's tenant cohorts, with source unspecified order."""
    return tuple(
        _cohort_fact(c)
        for c in db.scalars(select(Cohort).where(Cohort.tenant_id == tenant_id)).all()
    )


def cohort_exists(db: Session, *, tenant_id: UUID, cohort_id: UUID) -> bool:
    """Source announcement-create preflight; Academy maps False to Unknown cohort."""
    return (
        db.scalar(
            select(Cohort.id)
            .where(Cohort.tenant_id == tenant_id)
            .where(Cohort.id == cohort_id)
        )
        is not None
    )
