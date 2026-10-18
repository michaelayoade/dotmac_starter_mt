"""Hosted real learning@head reminder input characterization and app-role RLS."""

from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from dotmac_learning import reminder_reads
from dotmac_learning.models import (
    Certificate,
    Course,
    CourseCompletion,
    Score,
    Submission,
)
from dotmac_learning.participation_models import Cohort, CourseOffering, Enrollment
from sqlalchemy import event, func, literal, select, text

from tests import test_learning_results as result_tests
from tests.test_learning_results import scoped

learning_database = result_tests.learning_database
result_database = result_tests.result_database
SINCE = datetime(2026, 8, 1, 12, tzinfo=UTC)
THROUGH = SINCE + timedelta(hours=72)


def enroll(db, tenant, person, *, created_at=SINCE, role="instructor", status="active"):
    cohort = Cohort(
        tenant_id=tenant, name="Cohort", discipline="engineering", status="inactive"
    )
    db.add(cohort)
    db.flush()
    enrollment = Enrollment(
        tenant_id=tenant,
        person_id=person,
        cohort_id=cohort.id,
        role_in_cohort=role,
        status=status,
        created_at=created_at,
    )
    db.add(enrollment)
    db.flush()
    return cohort, enrollment


def seed_all(db, tenant, person, course, activity):
    cohort, enrollment = enroll(db, tenant, person)
    offering = CourseOffering(
        tenant_id=tenant, cohort_id=cohort.id, course_id=course, starts_at=THROUGH
    )
    submission = Submission(
        tenant_id=tenant,
        person_id=person,
        activity_id=activity,
        attempt_no=1,
        answers={},
    )
    completion = CourseCompletion(
        tenant_id=tenant,
        person_id=person,
        course_id=course,
        status="completed",
        completed_at=SINCE,
        pct=0,
    )
    certificate = Certificate(
        tenant_id=tenant,
        person_id=person,
        course_id=course,
        serial="REMINDER",
        issued_at=SINCE,
    )
    db.add_all([offering, submission, completion, certificate])
    db.flush()
    score = Score(
        tenant_id=tenant,
        submission_id=submission.id,
        score=0,
        max_score=1,
        fraction=0,
        passed=False,
        per_item=[],
        source="manual",
        created_at=SINCE,
    )
    db.add(score)
    db.flush()
    return cohort, enrollment, offering, submission, score, completion, certificate


def facts(db, tenant, person):
    return (
        reminder_reads.recent_assignments(
            db, tenant_id=tenant, person_id=person, since=SINCE
        ),
        reminder_reads.opening_offerings(
            db, tenant_id=tenant, person_id=person, after=SINCE, through=THROUGH
        ),
        reminder_reads.recent_manual_results(
            db, tenant_id=tenant, person_id=person, since=SINCE
        ),
        reminder_reads.recent_completed_courses(
            db, tenant_id=tenant, person_id=person, since=SINCE
        ),
        reminder_reads.recent_certificates(
            db, tenant_id=tenant, person_id=person, since=SINCE
        ),
        reminder_reads.earliest_active_enrollment_at(
            db, tenant_id=tenant, person_id=person
        ),
    )


def test_every_input_populated_before_actual_non_bypass_wrong_scope_probe(
    result_database,
):
    _, app, tenant, person, course, activity, other, *_ = result_database
    with scoped(app, tenant) as db:
        cohort, enrollment, offering, submission, score, completion, certificate = (
            seed_all(db, tenant, person, course, activity)
        )
        assignments, openings, grades, completions, certificates, earliest = facts(
            db, tenant, person
        )
        assert [(f.enrollment_id, f.cohort_name) for f in assignments] == [
            (enrollment.id, cohort.name)
        ]
        assert [(f.offering_id, f.course_title, f.starts_at) for f in openings] == [
            (offering.id, "Course", THROUGH)
        ]
        assert [(f.score_id, f.activity_title) for f in grades] == [
            (score.id, "Assessment")
        ]
        assert [(f.completion_id, f.course_title) for f in completions] == [
            (completion.id, "Course")
        ]
        assert [(f.certificate_id, f.course_title) for f in certificates] == [
            (certificate.id, "Course")
        ]
        assert earliest == SINCE and isinstance(assignments, tuple)
        with pytest.raises(FrozenInstanceError):
            assignments[0].cohort_name = "Changed"
    with scoped(app, other) as db:
        assert db.execute(
            text(
                "SELECT rolsuper, rolbypassrls FROM pg_catalog.pg_roles "
                "WHERE rolname=current_user"
            )
        ).one() == (False, False)
        for model in (Enrollment, CourseOffering, Score, CourseCompletion, Certificate):
            assert db.scalar(select(func.count()).select_from(model)) == 0
        assert facts(db, tenant, person) == ((), (), (), (), (), None)
    with scoped(app, tenant) as db:
        assert facts(db, other, person) == ((), (), (), (), (), None)
        assert facts(db, tenant, uuid4()) == ((), (), (), (), (), None)


def test_assignments_any_active_role_inclusive_since_no_cohort_status_or_upper_bound(
    result_database,
):
    _, app, tenant, person, *_ = result_database
    with scoped(app, tenant) as db:
        rows = [
            enroll(db, tenant, person, created_at=at, role=role, status=status)[1]
            for at, role, status in (
                (SINCE, "instructor", "active"),
                (SINCE + timedelta(days=10), "student", "active"),
                (SINCE - timedelta(microseconds=1), "instructor", "active"),
                (SINCE, "student", "inactive"),
            )
        ]
        found = reminder_reads.recent_assignments(
            db, tenant_id=tenant, person_id=person, since=SINCE
        )
        assert {f.enrollment_id for f in found} == {rows[0].id, rows[1].id}
        assert len(found) == 2 and all(f.cohort_name == "Cohort" for f in found)
        assert (
            reminder_reads.earliest_active_enrollment_at(
                db, tenant_id=tenant, person_id=person
            )
            == rows[2].created_at
        )
        rows[2].status = "inactive"
        assert (
            reminder_reads.earliest_active_enrollment_at(
                db, tenant_id=tenant, person_id=person
            )
            == SINCE
        )


def test_opening_bounds_any_role_all_offerings_no_course_filter(
    result_database,
):
    _, app, tenant, person, course, *_ = result_database
    with scoped(app, tenant) as db:
        db.scalar(select(Course).where(Course.id == course)).status = "archived"
        offerings = []
        for at, role, status, off_status in (
            (SINCE, "student", "active", "active"),
            (SINCE + timedelta(microseconds=1), "instructor", "active", "active"),
            (THROUGH, "student", "active", "active"),
            (THROUGH + timedelta(microseconds=1), "student", "active", "active"),
            (None, "student", "active", "active"),
            (THROUGH, "student", "inactive", "active"),
            (THROUGH, "student", "active", "inactive"),
        ):
            cohort, _ = enroll(db, tenant, person, role=role, status=status)
            off = CourseOffering(
                tenant_id=tenant,
                course_id=course,
                cohort_id=cohort.id,
                starts_at=at,
                status=off_status,
            )
            db.add(off)
            db.flush()
            offerings.append(off)
        found = reminder_reads.opening_offerings(
            db, tenant_id=tenant, person_id=person, after=SINCE, through=THROUGH
        )
        assert {f.offering_id for f in found} == {offerings[1].id, offerings[2].id}
        assert len(found) == 2 and [f.course_title for f in found] == [
            "Course",
            "Course",
        ]
        assert (
            reminder_reads.opening_offerings(
                db, tenant_id=tenant, person_id=person, after=THROUGH, through=SINCE
            )
            == ()
        )


def test_manual_results_keep_multiple_scores_failed_and_unknown_nonauto_sources(
    result_database,
):
    _, app, tenant, person, course, activity, *_ = result_database
    with scoped(app, tenant) as db:
        _, _, _, submission, first, *_ = seed_all(db, tenant, person, course, activity)
        rows = []
        for source, created_at in (
            ("override", SINCE),
            ("manual", SINCE - timedelta(microseconds=1)),
            ("auto", SINCE),
            ("legacy", THROUGH),
        ):
            row = Score(
                tenant_id=tenant,
                submission_id=submission.id,
                score=0,
                max_score=1,
                fraction=0,
                passed=False,
                per_item=[],
                source=source,
                created_at=created_at,
            )
            db.add(row)
            db.flush()
            rows.append(row)
        found = reminder_reads.recent_manual_results(
            db, tenant_id=tenant, person_id=person, since=SINCE
        )
        assert {f.score_id for f in found} == {first.id, rows[0].id, rows[3].id}
        assert len(found) == 3
        # Source storage disallows null source. Preserve SQL != semantics anyway;
        # a future nullable row must remain excluded, rather than DISTINCT FROM.
        assert db.scalar(select(literal(None) != "auto")) is None


def test_completion_certificate_timestamps_without_publication_policy(
    result_database,
):
    _, app, tenant, person, course, activity, *_ = result_database
    with scoped(app, tenant) as db:
        _, _, _, _, _, completion, certificate = seed_all(
            db, tenant, person, course, activity
        )
        db.scalar(select(Course).where(Course.id == course)).status = "draft"
        for status, at, expected in (
            ("in_progress", SINCE, False),
            ("completed", None, False),
            ("completed", SINCE - timedelta(microseconds=1), False),
            ("completed", SINCE, True),
            ("completed", THROUGH, True),
        ):
            completion.status, completion.completed_at = status, at
            result = reminder_reads.recent_completed_courses(
                db, tenant_id=tenant, person_id=person, since=SINCE
            )
            assert bool(result) is expected
        for at, expected in (
            (SINCE - timedelta(microseconds=1), False),
            (SINCE, True),
            (THROUGH, True),
        ):
            certificate.issued_at = at
            certificate.created_at = SINCE - timedelta(days=30)
            result = reminder_reads.recent_certificates(
                db, tenant_id=tenant, person_id=person, since=SINCE
            )
            assert bool(result) is expected


def test_independent_reads_autoflush_and_preserve_root_and_nested_scope(
    result_database,
):
    _, app, tenant, person, course, activity, *_ = result_database
    with scoped(app, tenant) as db:
        cohort, *_ = seed_all(db, tenant, person, course, activity)
        root = db.get_transaction()
        with db.begin_nested():
            nested = db.get_nested_transaction()
            cohort.name = "Pending"
            assert (
                reminder_reads.recent_assignments(
                    db, tenant_id=tenant, person_id=person, since=SINCE
                )[0].cohort_name
                == "Pending"
            )
            assert not db.dirty
            for read in facts(db, tenant, person):
                assert read
            assert (
                db.get_transaction() is root and db.get_nested_transaction() is nested
            )


def test_read_queries_preserve_source_encounter_order_and_no_eager_bundle(
    result_database,
):
    _, app, tenant, person, course, activity, *_ = result_database
    statements = []

    def observe(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement.lower())

    with scoped(app, tenant) as db:
        seed_all(db, tenant, person, course, activity)
        event.listen(db.connection(), "before_cursor_execute", observe)
        try:
            result = facts(db, tenant, person)
        finally:
            event.remove(db.connection(), "before_cursor_execute", observe)
        assert all(result)
        assert len(statements) == 6
        assert all("order by" not in s and "distinct" not in s for s in statements)
        assert "scores.source !=" in statements[2]
        assert "min(" in statements[5]
