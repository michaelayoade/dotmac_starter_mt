"""Hosted real learning@head source parity and non-bypass isolation canaries."""

from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta

import pytest
from dotmac_learning import agenda, todo
from dotmac_learning.attempt_models import AttemptGrant
from dotmac_learning.delivery_models import ClassSession, OfferingActivity
from dotmac_learning.models import Activity, Score, Submission
from dotmac_learning.participation_models import Cohort, CourseOffering, Enrollment
from sqlalchemy import func, select, text

from tests import test_learning_results as result_tests
from tests.test_learning_results import scoped

learning_database = result_tests.learning_database
result_database = result_tests.result_database
NOW = datetime(2026, 8, 1, 12, tzinfo=UTC)


def seed(db, tenant, person, course, activity, *, role="student", status="active"):
    cohort = Cohort(tenant_id=tenant, name="C", discipline="engineering")
    db.add(cohort)
    db.flush()
    offering = CourseOffering(tenant_id=tenant, cohort_id=cohort.id, course_id=course)
    db.add_all(
        [
            offering,
            Enrollment(
                tenant_id=tenant,
                cohort_id=cohort.id,
                person_id=person,
                role_in_cohort=role,
                status=status,
            ),
        ]
    )
    db.flush()
    pacing = OfferingActivity(
        tenant_id=tenant,
        offering_id=offering.id,
        activity_id=activity,
        due_at=NOW + timedelta(days=1),
    )
    db.add(pacing)
    db.flush()
    return cohort, offering, pacing


def test_non_bypass_wrong_scope_and_detached_deadline(result_database):
    _, app, tenant, person, course, activity, other, *_ = result_database
    with scoped(app, tenant) as db:
        cohort, offering, _ = seed(db, tenant, person, course, activity)
        offering.starts_at = NOW + timedelta(days=2)
        db.get(Activity, activity).assessment_mode = "practice"
        db.add(
            ClassSession(
                tenant_id=tenant,
                cohort_id=cohort.id,
                title="Visible only in A",
                starts_at=NOW + timedelta(minutes=5),
                join_url="https://example.org/class",
            )
        )
        submission = Submission(
            tenant_id=tenant,
            person_id=person,
            activity_id=activity,
            answers={},
            attempt_no=1,
        )
        db.add(submission)
        db.flush()
        db.add(
            Score(
                tenant_id=tenant,
                submission_id=submission.id,
                score=0,
                max_score=1,
                fraction=0,
                passed=False,
                per_item=[],
                created_at=NOW,
            )
        )
        db.flush()
        rows = todo.deadline_rows(db, tenant_id=tenant, person_id=person)
        assert isinstance(rows, tuple) and rows[0].activity.id == activity
        with pytest.raises(FrozenInstanceError):
            rows[0].activity.title = "changed"
        positive = todo.timeline_for_person(
            db, tenant_id=tenant, person_id=person, now=NOW, local_zone=UTC
        )
        assert len(positive.next_7) == 1
        assert (
            len(positive.sessions)
            == len(positive.graded)
            == len(positive.feedback)
            == 1
        )
        assert len(positive.course_windows) == len(positive.joinable) == 1
        assert agenda.upcoming_for_person(
            db, tenant_id=tenant, person_id=person, now=NOW
        )
    with scoped(app, other) as db:
        assert db.execute(
            text(
                "SELECT rolsuper, rolbypassrls FROM pg_catalog.pg_roles "
                "WHERE rolname=current_user"
            )
        ).one() == (False, False)
        assert db.scalar(select(func.count()).select_from(OfferingActivity)) == 0
        assert db.scalar(select(func.count()).select_from(CourseOffering)) == 0
        assert todo.deadline_rows(db, tenant_id=tenant, person_id=person) == ()
        assert (
            agenda.upcoming_for_person(db, tenant_id=tenant, person_id=person, now=NOW)
            == ()
        )
        timeline = todo.timeline_for_person(
            db, tenant_id=tenant, person_id=person, now=NOW, local_zone=UTC
        )
        assert (
            timeline.overdue
            == timeline.due_today
            == timeline.next_7
            == timeline.next_30
            == timeline.sessions
            == timeline.graded
            == timeline.feedback
            == timeline.course_windows
            == ()
        )
        assert timeline.joinable == frozenset()


def test_student_deadlines_any_role_windows_and_exact_buckets(result_database):
    _, app, tenant, person, course, activity, *_ = result_database
    with scoped(app, tenant) as db:
        _, off, pacing = seed(db, tenant, person, course, activity, role="instructor")
        off.starts_at, off.ends_at = NOW, NOW + timedelta(days=30)
        assert todo.deadline_rows(db, tenant_id=tenant, person_id=person) == ()
        timeline = todo.timeline_for_person(
            db, tenant_id=tenant, person_id=person, now=NOW, local_zone=UTC
        )
        assert [(w.kind, w.at) for w in timeline.course_windows] == [
            ("closes", off.ends_at)
        ]
        enrollment = db.scalar(select(Enrollment).where(Enrollment.person_id == person))
        enrollment.role_in_cohort = "student"
        for due, bucket in (
            (NOW - timedelta(microseconds=1), "overdue"),
            (NOW, "due_today"),
            (NOW.replace(hour=23, minute=59, second=59), "due_today"),
            (NOW.replace(hour=23, minute=59, second=59, microsecond=1), "next_7"),
            (NOW + timedelta(days=7), "next_7"),
            (NOW + timedelta(days=7, microseconds=1), "next_30"),
            (NOW + timedelta(days=30), "next_30"),
        ):
            pacing.due_at = due
            timeline = todo.timeline_for_person(
                db, tenant_id=tenant, person_id=person, now=NOW, local_zone=UTC
            )
            assert len(getattr(timeline, bucket)) == 1
        pacing.due_at = NOW + timedelta(days=30, microseconds=1)
        timeline = todo.timeline_for_person(
            db, tenant_id=tenant, person_id=person, now=NOW, local_zone=UTC
        )
        assert timeline.next_30 == ()


def test_agenda_other_cohort_offering_and_inactive_session_membership(result_database):
    _, app, tenant, person, course, activity, *_ = result_database
    with scoped(app, tenant) as db:
        cohort, _, _ = seed(db, tenant, person, course, activity)
        other_cohort = Cohort(tenant_id=tenant, name="Other", discipline="engineering")
        db.add(other_cohort)
        db.flush()
        other_off = CourseOffering(
            tenant_id=tenant,
            cohort_id=other_cohort.id,
            course_id=course,
            starts_at=NOW + timedelta(days=2),
        )
        db.add(other_off)
        db.flush()
        db.add(
            OfferingActivity(
                tenant_id=tenant,
                offering_id=other_off.id,
                activity_id=activity,
                due_at=NOW + timedelta(days=3),
            )
        )
        session = ClassSession(
            tenant_id=tenant,
            cohort_id=cohort.id,
            title="Lab",
            session_type="lab",
            starts_at=NOW - timedelta(hours=1),
            ends_at=NOW,
            join_url="https://example.test/join",
        )
        db.add(session)
        db.flush()
        items = agenda.upcoming_for_person(
            db, tenant_id=tenant, person_id=person, now=NOW
        )
        assert len([i for i in items if i.kind == "due"]) == 2
        assert any(i.kind == "opens" for i in items)
        assert items[0].session.id == session.id and items[0].joinable
        assert (
            agenda.upcoming_for_person(
                db, tenant_id=tenant, person_id=person, now=NOW, limit=1
            )
            == items[:1]
        )
        db.scalar(
            select(Enrollment).where(Enrollment.person_id == person)
        ).status = "inactive"
        items = agenda.upcoming_for_person(
            db, tenant_id=tenant, person_id=person, now=NOW
        )
        assert [i.session.id for i in items if i.kind == "session"] == [session.id]
        assert (
            todo.timeline_for_person(
                db, tenant_id=tenant, person_id=person, now=NOW, local_zone=UTC
            ).sessions
            == ()
        )


def test_any_passing_score_removes_deadline_and_recent_limit_precedes_feedback(
    result_database,
):
    _, app, tenant, person, course, activity, *_ = result_database
    with scoped(app, tenant) as db:
        seed(db, tenant, person, course, activity)
        row = db.scalar(select(Activity).where(Activity.id == activity))
        row.assessment_mode = "exam"
        for i in range(11):
            submission = Submission(
                tenant_id=tenant,
                person_id=person,
                activity_id=activity,
                answers={},
                attempt_no=i + 1,
            )
            db.add(submission)
            db.flush()
            db.add(
                Score(
                    tenant_id=tenant,
                    submission_id=submission.id,
                    score=1,
                    max_score=1,
                    fraction=1,
                    passed=i == 10,
                    per_item=[],
                    created_at=NOW - timedelta(minutes=i),
                )
            )
        db.flush()
        assert todo.deadline_rows(db, tenant_id=tenant, person_id=person) == ()
        timeline = todo.timeline_for_person(
            db, tenant_id=tenant, person_id=person, now=NOW, local_zone=UTC
        )
        assert len(timeline.graded) == 10 and timeline.feedback == ()
        # The older revealable practice score must not backfill the recent cap.
        old_activity = Activity(
            tenant_id=tenant,
            course_id=course,
            title="Old practice",
            type="mcq_test",
            assessment_mode="practice",
            pass_threshold=0.7,
        )
        db.add(old_activity)
        db.flush()
        old_submission = Submission(
            tenant_id=tenant,
            person_id=person,
            activity_id=old_activity.id,
            answers={},
            attempt_no=1,
        )
        db.add(old_submission)
        db.flush()
        db.add(
            Score(
                tenant_id=tenant,
                submission_id=old_submission.id,
                score=0,
                max_score=1,
                fraction=0,
                passed=False,
                per_item=[],
                created_at=NOW - timedelta(hours=1),
            )
        )
        db.flush()
        assert (
            todo.timeline_for_person(
                db, tenant_id=tenant, person_id=person, now=NOW, local_zone=UTC
            ).feedback
            == ()
        )
        row.assessment_mode = "graded"
        # Raw max_attempts is exhausted, despite a grant leaving many tries.
        db.add(
            AttemptGrant(
                tenant_id=tenant,
                person_id=person,
                activity_id=activity,
                extra_attempts=100,
                reason="source feedback ignores grants",
            )
        )
        timeline = todo.timeline_for_person(
            db, tenant_id=tenant, person_id=person, now=NOW, local_zone=UTC
        )
        assert len(timeline.feedback) == 10


def test_strict_agenda_now_stable_category_ties_and_window_horizon(result_database):
    _, app, tenant, person, course, activity, *_ = result_database
    with scoped(app, tenant) as db:
        _, off, pacing = seed(db, tenant, person, course, activity)
        off.starts_at = off.ends_at = pacing.due_at = NOW
        assert (
            agenda.upcoming_for_person(db, tenant_id=tenant, person_id=person, now=NOW)
            == ()
        )
        edge = NOW + timedelta(days=30)
        off.starts_at = off.ends_at = pacing.due_at = edge
        items = agenda.upcoming_for_person(
            db, tenant_id=tenant, person_id=person, now=NOW
        )
        assert [item.kind for item in items] == ["opens", "closes", "due"]
        timeline = todo.timeline_for_person(
            db, tenant_id=tenant, person_id=person, now=NOW, local_zone=UTC
        )
        assert [w.kind for w in timeline.course_windows] == ["opens", "closes"]
        off.starts_at = off.ends_at = edge + timedelta(microseconds=1)
        assert (
            todo.timeline_for_person(
                db, tenant_id=tenant, person_id=person, now=NOW, local_zone=UTC
            ).course_windows
            == ()
        )
        assert [
            item.kind
            for item in agenda.upcoming_for_person(
                db, tenant_id=tenant, person_id=person, now=NOW
            )
        ] == ["due", "opens", "closes"]


def test_recent_lookback_inclusive_and_read_does_not_require_explicit_begin(
    result_database,
):
    from sqlalchemy.orm import Session

    from tests.test_learning_catalog import _scope

    _, app, tenant, person, course, activity, *_ = result_database
    with scoped(app, tenant) as db:
        row = db.scalar(select(Activity).where(Activity.id == activity))
        row.assessment_mode = "practice"
        for i, created_at in enumerate(
            (NOW - timedelta(days=14), NOW - timedelta(days=14, microseconds=1))
        ):
            submission = Submission(
                tenant_id=tenant,
                person_id=person,
                activity_id=activity,
                answers={},
                attempt_no=i + 1,
            )
            db.add(submission)
            db.flush()
            db.add(
                Score(
                    tenant_id=tenant,
                    submission_id=submission.id,
                    score=0,
                    max_score=1,
                    fraction=0,
                    passed=False,
                    per_item=[],
                    created_at=created_at,
                )
            )
    # The product scope statement autobegins. These are ordinary read services;
    # no explicit command transaction assertion may be introduced.
    with Session(bind=app) as db:
        _scope(db.connection(), tenant)
        root = db.get_transaction()
        timeline = todo.timeline_for_person(
            db, tenant_id=tenant, person_id=person, now=NOW, local_zone=UTC
        )
        assert len(timeline.graded) == 1 and timeline.feedback == timeline.graded
        assert timeline.graded[0].created_at == NOW - timedelta(days=14)
        assert db.get_transaction() is root
        assert not db.new and not db.dirty and not db.deleted
