"""Hosted migrated instructor read characterization and non-bypass RLS proof."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from dotmac_kernel.models import Party, PartyPerson
from dotmac_learning import instructor_reads as reads
from dotmac_learning.account_contracts import ActiveAccountSelection
from dotmac_learning.curriculum_models import Chapter
from dotmac_learning.instructor_contracts import (
    InstructorAuthority,
    InstructorCourseForbidden,
    InstructorCourseNotFound,
)
from dotmac_learning.models import CourseCompletion, Score, Submission
from dotmac_learning.participation_contracts import ParticipationNotFound
from dotmac_learning.participation_models import Cohort, CourseOffering, Enrollment
from dotmac_learning.references import (
    ActivityWrite,
    CourseWrite,
    create_activity,
    create_course,
)
from sqlalchemy import select, text

from tests import test_learning_results as learning_results
from tests.test_learning_catalog import _person
from tests.test_learning_results import scoped

learning_database = learning_results.learning_database
result_database = learning_results.result_database
NOW = datetime(2026, 10, 17, 12, tzinfo=UTC)


def selection(tenant, *people):
    """Explicit fixture account IDs, never inferred from Party.is_active."""
    return ActiveAccountSelection(
        tenant, select(Party.id).where(Party.tenant_id == tenant, Party.id.in_(people))
    )


@pytest.fixture
def instructor_data(result_database):
    admin, app, tenant, person, course, activity, *_ = result_database
    with admin.begin() as conn:
        conn.execute(
            Party.__table__.update().where(Party.id == person).values(is_active=False)
        )
        waitlisted, suspended, instructor = (_person(conn, tenant) for _ in range(3))
        for pid, last in (
            (person, "Zulu"),
            (waitlisted, "Alpha"),
            (suspended, "Beta"),
            (instructor, "Instructor"),
        ):
            conn.execute(
                PartyPerson.__table__.update()
                .where(PartyPerson.party_id == pid)
                .values(last_name=last)
            )
    with scoped(app, tenant) as db:
        cohort = Cohort(tenant_id=tenant, name="C", discipline="test")
        db.add(cohort)
        db.flush()
        db.add_all(
            [
                Enrollment(tenant_id=tenant, cohort_id=cohort.id, person_id=person),
                Enrollment(
                    tenant_id=tenant,
                    cohort_id=cohort.id,
                    person_id=waitlisted,
                    status="waitlisted",
                ),
                Enrollment(tenant_id=tenant, cohort_id=cohort.id, person_id=suspended),
                Enrollment(
                    tenant_id=tenant,
                    cohort_id=cohort.id,
                    person_id=instructor,
                    role_in_cohort="instructor",
                ),
            ]
        )
        extra, disabled, draft = uuid4(), uuid4(), uuid4()
        for cid, title, status in (
            (extra, "A Extra", "completed"),
            (disabled, "Disabled", "draft"),
            (draft, "Unassigned", "draft"),
        ):
            create_course(
                db,
                CourseWrite(
                    id=cid,
                    tenant_id=tenant,
                    slug=cid.hex,
                    title=title,
                    discipline="test",
                    status=status,
                    source_ref="test",
                    listed=False,
                ),
            )
        for cid, status in (
            (course, "active"),
            (extra, "active"),
            (disabled, "inactive"),
        ):
            db.add(
                CourseOffering(
                    tenant_id=tenant, cohort_id=cohort.id, course_id=cid, status=status
                )
            )
        db.add(
            CourseCompletion(
                tenant_id=tenant,
                person_id=person,
                course_id=course,
                pct=0.8,
                status="in_progress",
            )
        )
        # Latest score must use active offered courses, never an unrelated one.
        score_ids = set()
        for cid, stamp in ((course, NOW - timedelta(days=15)), (draft, NOW)):
            aid = activity if cid == course else uuid4()
            if cid != course:
                create_activity(
                    db,
                    ActivityWrite(
                        id=aid,
                        tenant_id=tenant,
                        course_id=cid,
                        title="Unrelated",
                        type="mcq_test",
                        pass_threshold=0.0,
                        weight=1.0,
                        assessment_mode="graded",
                        grading="auto",
                    ),
                )
            sub = Submission(
                tenant_id=tenant,
                person_id=person,
                activity_id=aid,
                attempt_no=1,
                answers={},
            )
            db.add(sub)
            db.flush()
            score = Score(
                tenant_id=tenant,
                submission_id=sub.id,
                score=1,
                max_score=1,
                fraction=1,
                passed=True,
                source="auto",
                per_item=[],
                created_at=stamp,
            )
            db.add(score)
            db.flush()
            score_ids.add(score.id)
            if cid == course:
                correction = Score(
                    tenant_id=tenant,
                    submission_id=sub.id,
                    score=0.5,
                    max_score=1,
                    fraction=0.5,
                    passed=False,
                    source="override",
                    override_reason="Retain both scores for the same submission",
                    per_item=[],
                    created_at=stamp,
                )
                db.add(correction)
                db.flush()
                score_ids.add(correction.id)
        for number, position in ((1, 2), (2, 1)):
            db.add(
                Chapter(
                    tenant_id=tenant,
                    course_id=disabled,
                    number=number,
                    order_index=position,
                    title=f"Chapter{number}",
                    part="",
                    body_md="",
                    body_html="",
                    source_hash="test",
                )
            )
        preview_activity_ids = set()
        for title, chapter in (("Z", 2), ("A", 2), ("Standalone", 99)):
            aid = uuid4()
            if chapter == 2:
                preview_activity_ids.add(aid)
            create_activity(
                db,
                ActivityWrite(
                    id=aid,
                    tenant_id=tenant,
                    course_id=disabled,
                    title=title,
                    chapter_number=chapter,
                    type="mcq_test",
                    pass_threshold=0.0,
                    weight=1.0,
                    assessment_mode="graded",
                    grading="auto",
                ),
            )
        db.flush()
        cohort_id = cohort.id
    return (
        result_database,
        cohort_id,
        instructor,
        extra,
        disabled,
        draft,
        {
            "score_ids": frozenset(score_ids),
            "preview_activity_ids": frozenset(preview_activity_ids),
        },
    )


def test_populated_summary_workspace_authoring_results_and_actual_rls(instructor_data):
    fixture, cohort, instructor, extra, disabled, draft, expected = instructor_data
    _, app, tenant, person, course, _, other, other_person, *_ = fixture
    accounts = selection(tenant, person, instructor)
    authority = InstructorAuthority(tenant, instructor, False)
    with scoped(app, tenant) as db:
        assert db.scalar(
            text(
                "SELECT NOT rolsuper AND NOT rolbypassrls "
                "FROM pg_roles WHERE rolname=current_user"
            )
        )
        root = db.get_transaction()
        assert reads.cohort_exists(db, tenant_id=tenant, cohort_id=cohort)
        summary = reads.cohorts_summary(db, tenant_id=tenant, accounts=accounts)
        assert len(summary.cohorts[0].roster) == 4
        assert summary.cohorts[0].active_student_count == 1
        assert tuple(p.id for p in summary.cohorts[0].facilitators) == (instructor,)
        assert tuple(c.id for c in summary.cohorts[0].active_courses) == (extra, course)
        assert summary.draft_course_count == 2
        assert len(summary.selectable_courses) == 2
        detail = reads.cohort_workspace(
            db,
            tenant_id=tenant,
            cohort_id=cohort,
            accounts=accounts,
            now=NOW,
            limit=1,
            at_risk_pct=0.5,
            stale_days=14,
        )
        assert detail.student_total == 3 and detail.active_student_count == 1
        assert detail.students[0].person.last_name == "Alpha"
        assert detail.average_progress == 0.4 and detail.at_risk_count == 1
        full = reads.cohort_workspace(
            db,
            tenant_id=tenant,
            cohort_id=cohort,
            accounts=accounts,
            now=NOW,
            at_risk_pct=0.5,
            stale_days=14,
            limit=12,
        )
        zulu = next(s for s in full.students if s.person.id == person)
        assert zulu.last_activity_at == NOW - timedelta(days=15)
        assert zulu.at_risk
        inactive_course = next(c for c in full.courses if c.course.id == disabled)
        assert tuple(c.chapter.number for c in inactive_course.chapters) == (2, 1)
        assert tuple(a.title for a in inactive_course.chapters[0].activities) == (
            "A",
            "Z",
        )
        assert tuple(a.title for a in inactive_course.standalone_activities) == (
            "Standalone",
        )
        assert len(full.facilitators) == 1
        assigned = reads.assigned_course_ids(
            db, tenant_id=tenant, instructor_id=instructor
        )
        assert assigned == frozenset({course, extra})
        assert {
            c.id
            for c in reads.authorable_courses(
                db, tenant_id=tenant, person_id=instructor, authority=authority
            )
        } == {course, extra}
        admin = InstructorAuthority(tenant, person, True)
        assert (
            len(
                reads.authorable_courses(
                    db, tenant_id=tenant, person_id=person, authority=admin
                )
            )
            == 4
        )
        assert (
            reads.authorable_course(
                db, tenant_id=tenant, person_id=person, course_id=draft, authority=admin
            ).id
            == draft
        )
        with pytest.raises(InstructorCourseForbidden):
            reads.authorable_course(
                db,
                tenant_id=tenant,
                person_id=instructor,
                course_id=draft,
                authority=authority,
            )
        with pytest.raises(InstructorCourseNotFound):
            reads.authorable_course(
                db,
                tenant_id=tenant,
                person_id=instructor,
                course_id=uuid4(),
                authority=authority,
            )
        assert tuple(
            c.number
            for c in reads.authoring_chapters(db, tenant_id=tenant, course_id=disabled)
        ) == (1, 2)
        preview = reads.course_preview(
            db, tenant_id=tenant, person_id=person, course_id=disabled, authority=admin
        )
        assert preview.chapter.number == 1 and preview.activity is None
        assert preview.previous_chapter is None and preview.next_chapter.number == 2
        populated_preview = reads.course_preview(
            db,
            tenant_id=tenant,
            person_id=person,
            course_id=disabled,
            authority=admin,
            chapter_number=2,
        )
        assert len(expected["preview_activity_ids"]) == 2
        assert populated_preview.activity is not None
        assert populated_preview.activity.id in expected["preview_activity_ids"]
        assert populated_preview.previous_chapter.number == 1
        assert populated_preview.next_chapter is None
        assert {c.id for c in reads.cohort_choices(db, tenant_id=tenant)} == {cohort}
        results = reads.assessment_results(db, tenant_id=tenant)
        assert len(results) == 3 and all(r.score.submission_id for r in results)
        assert {r.score.id for r in results} == expected["score_ids"]
        assert db.get_transaction() is root
    assert not hasattr(detail.cohort, "_sa_instance_state")
    assert not hasattr(results[0].score, "_sa_instance_state")
    with scoped(app, other) as db:
        assert db.scalar(
            text(
                "SELECT NOT rolsuper AND NOT rolbypassrls "
                "FROM pg_roles WHERE rolname=current_user"
            )
        )
        assert (
            reads.cohorts_summary(db, tenant_id=tenant, accounts=accounts).cohorts == ()
        )
        with pytest.raises(ParticipationNotFound):
            reads.cohort_workspace(
                db,
                tenant_id=tenant,
                cohort_id=cohort,
                accounts=accounts,
                now=NOW,
                at_risk_pct=0.5,
                stale_days=14,
                limit=12,
            )
        assert (
            reads.assigned_course_ids(db, tenant_id=tenant, instructor_id=instructor)
            == frozenset()
        )
        assert (
            reads.authorable_courses(
                db, tenant_id=tenant, person_id=instructor, authority=authority
            )
            == ()
        )
        assert reads.assessment_results(db, tenant_id=tenant) == ()
        assert reads.authoring_chapters(db, tenant_id=tenant, course_id=disabled) == ()
        assert reads.cohort_choices(db, tenant_id=tenant) == ()
        assert not reads.cohort_exists(db, tenant_id=tenant, cohort_id=cohort)
        with pytest.raises(InstructorCourseNotFound):
            reads.course_preview(
                db,
                tenant_id=tenant,
                person_id=person,
                course_id=disabled,
                authority=InstructorAuthority(tenant, person, True),
                chapter_number=2,
            )

        with pytest.raises(InstructorCourseNotFound):
            reads.authorable_course(
                db,
                tenant_id=tenant,
                person_id=instructor,
                course_id=course,
                authority=authority,
            )
        own = Cohort(tenant_id=other, name="B", discipline="test")
        db.add(own)
        db.flush()
        db.add(Enrollment(tenant_id=other, cohort_id=own.id, person_id=other_person))
        assert (
            reads.cohorts_summary(
                db,
                tenant_id=other,
                accounts=selection(other, other_person),
            )
            .cohorts[0]
            .active_student_count
            == 1
        )


def test_missing_foreign_population_refused_before_sql_and_assignment_filters(
    instructor_data, monkeypatch
):
    fixture, cohort, instructor, *_ = instructor_data
    _, app, tenant, person, _, _, other, *_ = fixture
    with scoped(app, tenant) as db:
        with monkeypatch.context() as patch:

            def no_sql(*args, **kwargs):
                pytest.fail("population validation must precede any SQL")

            patch.setattr(db, "execute", no_sql)
            patch.setattr(db, "scalars", no_sql)
            patch.setattr(db, "scalar", no_sql)
            with pytest.raises(TypeError):
                reads.cohorts_summary(db, tenant_id=tenant)
            foreign = selection(other, person)
            with pytest.raises(ValueError):
                reads.cohorts_summary(db, tenant_id=tenant, accounts=foreign)
            with pytest.raises(ValueError):
                reads.cohort_workspace(
                    db,
                    tenant_id=tenant,
                    cohort_id=cohort,
                    accounts=foreign,
                    at_risk_pct=0.5,
                    stale_days=14,
                    limit=12,
                )
        enrollment = db.scalar(
            select(Enrollment).where(Enrollment.person_id == instructor)
        )
        enrollment.status = "dropped"
        assert (
            reads.assigned_course_ids(db, tenant_id=tenant, instructor_id=instructor)
            == frozenset()
        )
        enrollment.status = "active"
        enrollment.role_in_cohort = "student"
        assert (
            reads.assigned_course_ids(db, tenant_id=tenant, instructor_id=instructor)
            == frozenset()
        )


def test_inactive_identity_with_available_account_stays_current(instructor_data):
    fixture, cohort, *_ = instructor_data
    _, app, tenant, person, *_ = fixture
    with scoped(app, tenant) as db:
        assert db.scalar(select(Party.is_active).where(Party.id == person)) is False
        facts = reads.cohort_workspace(
            db,
            tenant_id=tenant,
            cohort_id=cohort,
            accounts=selection(tenant, person),
            now=NOW,
            at_risk_pct=0.5,
            stale_days=14,
            limit=12,
        )
        assert facts.active_student_count == 1
        assert next(s for s in facts.students if s.person.id == person).at_risk


def test_active_identity_with_unavailable_account_remains_visible_not_current(
    instructor_data,
):
    fixture, cohort, *_ = instructor_data
    _, app, tenant, *_ = fixture
    with scoped(app, tenant) as db:
        facts = reads.cohort_workspace(
            db,
            tenant_id=tenant,
            cohort_id=cohort,
            accounts=selection(tenant),
            now=NOW,
            at_risk_pct=0.5,
            stale_days=14,
            limit=12,
        )
        beta = next(s for s in facts.students if s.person.last_name == "Beta")
        assert (
            db.scalar(select(Party.is_active).where(Party.id == beta.person.id)) is True
        )
        assert beta.enrollment.status == "active"
        assert not beta.at_risk
        assert facts.student_total == 3 and facts.active_student_count == 0
