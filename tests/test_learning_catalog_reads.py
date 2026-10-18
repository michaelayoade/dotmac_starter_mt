"""Hosted real learning@head catalog characterization and non-bypass canaries."""

from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from dotmac_learning import catalog_reads
from dotmac_learning.curriculum_models import Chapter
from dotmac_learning.models import Activity, Course, CourseCompletion, Score, Submission
from dotmac_learning.participation_models import (
    Cohort,
    CourseOffering,
    CoursePrerequisite,
    Enrollment,
)
from sqlalchemy import func, select, text

from tests import test_learning_results as result_tests
from tests.test_learning_results import scoped

learning_database = result_tests.learning_database
result_database = result_tests.result_database


def score(db, tenant, person, activity, *, passed=True, fraction=1.0, attempt=1):
    submission = Submission(
        tenant_id=tenant,
        person_id=person,
        activity_id=activity,
        attempt_no=attempt,
        answers={},
    )
    db.add(submission)
    db.flush()
    db.add(
        Score(
            tenant_id=tenant,
            submission_id=submission.id,
            score=fraction,
            max_score=1,
            fraction=fraction,
            passed=passed,
            per_item=[],
        )
    )
    db.flush()


def enroll(db, tenant, person, course, *, role="student"):
    cohort = Cohort(tenant_id=tenant, name="Catalog", discipline="engineering")
    db.add(cohort)
    db.flush()
    offering = CourseOffering(
        tenant_id=tenant,
        cohort_id=cohort.id,
        course_id=course,
        ends_at=datetime.now(UTC) - timedelta(days=1),
    )
    enrollment = Enrollment(
        tenant_id=tenant, cohort_id=cohort.id, person_id=person, role_in_cohort=role
    )
    db.add_all([offering, enrollment])
    db.flush()
    return offering, enrollment


def test_populated_catalog_non_bypass_cross_tenant_isolation(result_database):
    _, app, tenant, person, course, activity, other, *_ = result_database
    with scoped(app, tenant) as db:
        enroll(db, tenant, person, course)
        db.scalar(select(Activity).where(Activity.id == activity)).chapter_number = 1
        chapter = Chapter(
            tenant_id=tenant,
            course_id=course,
            number=1,
            order_index=1,
            title="Chapter",
            part="A",
        )
        empty_chapter = Chapter(
            tenant_id=tenant,
            course_id=course,
            number=2,
            order_index=2,
            title="Next",
            part="A",
        )
        course_activity = Activity(
            assessment_mode="graded",
            grading="auto",
            weight=1.0,
            tenant_id=tenant,
            course_id=course,
            title="Final",
            type="mcq_test",
            pass_threshold=0.7,
        )
        db.add_all([chapter, empty_chapter, course_activity])
        prerequisite = Course(
            listed=False,
            status="published",
            id=uuid4(),
            tenant_id=tenant,
            slug="required",
            title="Required",
            discipline="engineering",
            source_ref="x",
        )
        db.add(prerequisite)
        db.flush()
        db.add(
            CoursePrerequisite(
                tenant_id=tenant, course_id=course, requires_course_id=prerequisite.id
            )
        )
        score(db, tenant, person, activity)
        score(db, tenant, person, course_activity.id)
        assert [c.id for c in catalog_reads.all_courses(db, tenant_id=tenant)] == [
            course,
            prerequisite.id,
        ]
        assert [
            c.id
            for c in catalog_reads.my_courses(db, tenant_id=tenant, person_id=person)
        ] == [course]
        assert (
            catalog_reads.course_completion(
                db, tenant_id=tenant, person_id=person, course_id=course
            )
            == 100
        )
        structure = catalog_reads.course_structure(
            db, tenant_id=tenant, person_id=person, course_id=course
        )
        assert (
            structure.locked and structure.parts[0].chapters[0].chapter.id == chapter.id
        )
        assert structure.parts[0].chapters[0].activities[0].passed
        assert structure.continue_target.id == empty_chapter.id
        assert structure.continue_target is next(
            item.chapter
            for part in structure.parts
            for item in part.chapters
            if item.chapter.id == empty_chapter.id
        )
        assert structure.course_activities[0].activity.id == course_activity.id
        assert structure.course_activities[0].passed
        with pytest.raises(FrozenInstanceError):
            structure.parts[0].chapters[0].chapter.title = "changed"
    with scoped(app, other) as db:
        assert db.execute(
            text(
                "SELECT rolsuper, rolbypassrls FROM pg_catalog.pg_roles "
                "WHERE rolname=current_user"
            )
        ).one() == (False, False)
        assert db.scalar(select(func.count()).select_from(Chapter)) == 0
        assert db.scalar(select(func.count()).select_from(Score)) == 0
        assert catalog_reads.all_courses(db, tenant_id=tenant) == ()
        assert catalog_reads.my_courses(db, tenant_id=tenant, person_id=person) == ()
        assert (
            catalog_reads.course_completion(
                db, tenant_id=tenant, person_id=person, course_id=course
            )
            == 0
        )
        structure = catalog_reads.course_structure(
            db, tenant_id=tenant, person_id=person, course_id=course
        )
        assert structure.parts == structure.course_activities == ()
        assert not structure.locked and structure.continue_target is None


def test_entitlement_visibility_publication_and_staff_inventory_are_distinct(
    result_database,
):
    _, app, tenant, person, course, _, *_ = result_database
    with scoped(app, tenant) as db:
        off, enrollment = enroll(db, tenant, person, course, role="instructor")
        row = db.scalar(select(Course).where(Course.id == course))
        row.listed = False
        assert [
            c.id
            for c in catalog_reads.my_courses(db, tenant_id=tenant, person_id=person)
        ] == [course]
        # Closed offering windows do not hide the entitled catalog.
        assert off.ends_at < datetime.now(UTC)
        row.status = "completed"
        assert (
            len(catalog_reads.my_courses(db, tenant_id=tenant, person_id=person)) == 1
        )
        for status in ("draft", "archived"):
            row.status = status
            assert (
                catalog_reads.my_courses(db, tenant_id=tenant, person_id=person) == ()
            )
            assert [
                c.status for c in catalog_reads.all_courses(db, tenant_id=tenant)
            ] == [status]
        row.status = "published"
        enrollment.status = "inactive"
        assert catalog_reads.my_courses(db, tenant_id=tenant, person_id=person) == ()
        enrollment.status = "active"
        off.status = "inactive"
        assert catalog_reads.my_courses(db, tenant_id=tenant, person_id=person) == ()


def test_structure_first_activity_only_continue_part_order_and_course_level_sort(
    result_database,
):
    _, app, tenant, person, course, activity, *_ = result_database
    with scoped(app, tenant) as db:
        original = db.scalar(select(Activity).where(Activity.id == activity))
        original.chapter_number = 7
        extra = Activity(
            assessment_mode="graded",
            grading="auto",
            weight=1.0,
            tenant_id=tenant,
            course_id=course,
            title="Second",
            chapter_number=7,
            type="mcq_test",
            pass_threshold=0.7,
        )
        course_acts = [
            Activity(
                assessment_mode="graded",
                grading="auto",
                weight=1.0,
                tenant_id=tenant,
                course_id=course,
                title=title,
                type="mcq_test",
                pass_threshold=0.7,
            )
            for title in ("Zulu", "Alpha")
        ]
        chapters = [
            Chapter(
                tenant_id=tenant,
                course_id=course,
                number=n,
                title=str(n),
                order_index=order,
                part=part,
            )
            for n, order, part in ((7, 1, "B"), (2, 2, "A"), (9, 3, "B"), (4, 4, ""))
        ]
        db.add_all([extra, *course_acts, *chapters])
        db.flush()
        # Source gives activities no ORDER BY. Observe that same encounter order,
        # then pass its first activity; later unpassed activity cannot select chapter 7.
        observed = [
            a
            for a in db.scalars(
                select(Activity).where(
                    Activity.tenant_id == tenant, Activity.course_id == course
                )
            ).all()
            if a.chapter_number == 7
        ]
        score(db, tenant, person, observed[0].id)
        score(db, tenant, person, observed[1].id, passed=False, fraction=0.625)
        structure = catalog_reads.course_structure(
            db, tenant_id=tenant, person_id=person, course_id=course
        )
        assert [p.part for p in structure.parts] == ["B", "A", ""]
        assert [c.chapter.number for c in structure.parts[0].chapters] == [7, 9]
        assert [a.activity.id for a in structure.parts[0].chapters[0].activities] == [
            a.id for a in observed
        ]
        assert [a.pct for a in structure.parts[0].chapters[0].activities] == [100, 62]
        assert structure.continue_target.id == chapters[1].id
        assert structure.continue_target is next(
            item.chapter
            for part in structure.parts
            for item in part.chapters
            if item.chapter.id == chapters[1].id
        )
        assert [a.activity.title for a in structure.course_activities] == [
            "Alpha",
            "Zulu",
        ]
        # All four activities count; the second chapter activity is still unpassed.
        assert (
            catalog_reads.course_completion(
                db, tenant_id=tenant, person_id=person, course_id=course
            )
            == 25
        )


def test_completion_uses_current_best_not_persisted_completion_or_any_pass(
    result_database,
):
    _, app, tenant, person, course, activity, *_ = result_database
    with scoped(app, tenant) as db:
        db.add(
            CourseCompletion(
                tenant_id=tenant,
                person_id=person,
                course_id=course,
                pct=1,
                status="completed",
            )
        )
        score(db, tenant, person, activity, passed=True, fraction=0.5)
        score(db, tenant, person, activity, passed=False, fraction=0.9, attempt=2)
        assert (
            catalog_reads.course_completion(
                db, tenant_id=tenant, person_id=person, course_id=course
            )
            == 0
        )
        empty = uuid4()
        assert (
            catalog_reads.course_completion(
                db, tenant_id=tenant, person_id=person, course_id=empty
            )
            == 0
        )
        empty_structure = catalog_reads.course_structure(
            db, tenant_id=tenant, person_id=person, course_id=empty
        )
        assert empty_structure.parts == empty_structure.course_activities == ()
