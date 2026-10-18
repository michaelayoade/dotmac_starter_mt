"""Hosted learner dashboard qualification on real learning@head app-role RLS."""

from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta

import pytest
from dotmac_learning import learner_dashboard
from dotmac_learning.attempt_models import ActivityAttempt, AttemptGrant
from dotmac_learning.curriculum_models import Chapter, ChapterRead
from dotmac_learning.delivery_models import ClassSession, OfferingActivity
from dotmac_learning.models import (
    Activity,
    Certificate,
    Course,
    CourseCompletion,
    Score,
    Submission,
)
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
NOW = datetime(2026, 8, 1, 12, tzinfo=UTC)


def seed(db, tenant, person, course, activity):
    cohort = Cohort(tenant_id=tenant, name="Dashboard", discipline="engineering")
    db.add(cohort)
    db.flush()
    off = CourseOffering(
        tenant_id=tenant,
        cohort_id=cohort.id,
        course_id=course,
        ends_at=NOW - timedelta(days=1),
    )
    enrollment = Enrollment(
        tenant_id=tenant,
        cohort_id=cohort.id,
        person_id=person,
        role_in_cohort="instructor",
        access_ends_at=NOW + timedelta(days=5),
    )
    chapters = [
        Chapter(
            tenant_id=tenant,
            course_id=course,
            number=i,
            title=str(i),
            order_index=4 - i,
        )
        for i in (1, 2, 3)
    ]
    first = db.scalar(select(Activity).where(Activity.id == activity))
    first.chapter_number = 1
    second = Activity(
        tenant_id=tenant,
        course_id=course,
        title="Second",
        chapter_number=2,
        type="mcq_test",
        max_attempts=1,
        pass_threshold=0.7,
    )
    third = Activity(
        tenant_id=tenant,
        course_id=course,
        title="Third",
        chapter_number=3,
        type="lab",
        pass_threshold=0.8,
    )
    db.add_all([off, enrollment, second, third, *chapters])
    db.flush()
    db.add(
        ChapterRead(
            tenant_id=tenant,
            person_id=person,
            chapter_id=chapters[0].id,
            created_at=NOW - timedelta(days=4),
        )
    )
    for i, act, fraction, passed in (
        (1, first, 1.0, True),
        (2, second, 0.2, False),
        (3, second, 0.3, False),
    ):
        submission = Submission(
            tenant_id=tenant,
            person_id=person,
            activity_id=act.id,
            attempt_no=i,
            answers={},
            created_at=NOW - timedelta(days=4 - i),
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
                created_at=NOW - timedelta(days=4 - i),
            )
        )
    db.add_all(
        [
            ActivityAttempt(
                tenant_id=tenant,
                person_id=person,
                activity_id=third.id,
                question_ext_ids=[],
                started_at=NOW,
            ),
            AttemptGrant(
                tenant_id=tenant,
                person_id=person,
                activity_id=second.id,
                extra_attempts=2,
                reason="Grant",
            ),
            CourseCompletion(
                tenant_id=tenant,
                person_id=person,
                course_id=course,
                pct=0.5,
                status="in_progress",
            ),
            Certificate(
                tenant_id=tenant,
                person_id=person,
                course_id=course,
                serial="DASHBOARD",
                issued_at=NOW,
            ),
            OfferingActivity(
                tenant_id=tenant,
                offering_id=off.id,
                activity_id=first.id,
                due_at=NOW + timedelta(days=2),
            ),
            OfferingActivity(
                tenant_id=tenant,
                offering_id=off.id,
                activity_id=second.id,
                due_at=NOW + timedelta(days=1),
            ),
            ClassSession(
                tenant_id=tenant,
                cohort_id=cohort.id,
                title="Live",
                starts_at=NOW,
                ends_at=NOW + timedelta(hours=1),
            ),
        ]
    )
    db.flush()
    return off, enrollment, chapters, second, third


def test_every_dashboard_projection_populated_before_non_bypass_wrong_tenant_probe(
    result_database,
):
    _, app, tenant, person, course, activity, other, *_ = result_database
    with scoped(app, tenant) as db:
        off, enrollment, chapters, second, third = seed(
            db, tenant, person, course, activity
        )
        home = learner_dashboard.learner_home(
            db, tenant_id=tenant, person_id=person, now=NOW
        )
        assert len(home.cards) == 1 and home.continue_to.chapter.id == chapters[2].id
        card = home.cards[0]
        assert (card.state, card.pct, card.grade_pct) == ("in_progress", 33, 43)
        assert card.next_activity.id == second.id and card.last_read.number == 1
        assert card.next_session.title == "Live" and card.action.code == "resume"
        assert card.action.chapter_number == 3 and card.certificate_status == "issued"
        assert card.next_deadline == NOW + timedelta(days=1)
        assert (
            learner_dashboard.resume_chapter(
                db, tenant_id=tenant, person_id=person, course_id=course
            ).id
            == chapters[2].id
        )
        assert (
            learner_dashboard.continue_target(
                db, tenant_id=tenant, person_id=person
            ).chapter.id
            == chapters[2].id
        )
        overview = learner_dashboard.progress_overview(
            db, tenant_id=tenant, person_id=person, now=NOW
        )
        progress = overview[0]
        assert progress.completion_pct == 50 and progress.grade_pct == 43
        assert len(progress.rows) == 3 and progress.upcoming[0].activity.id == second.id
        assert progress.overdue[0].activity.id == third.id
        assert progress.overdue[0].due_at == off.ends_at
        assert progress.trend_points == (100, 20, 30) and progress.trend == "down"
        assert (
            progress.certificate.status == "issued"
            and progress.certificate.serial == "DASHBOARD"
        )
        row = progress.upcoming[0]
        assert (
            row.attempts_used,
            row.attempts_granted,
            row.max_attempts,
            row.attempts_left,
        ) == (2, 2, 3, 1)
        assert not row.feedback_available
        assert progress.rows[0].feedback_available and progress.rows[0].passed
        with pytest.raises(FrozenInstanceError):
            card.state = "expired"
    with scoped(app, other) as db:
        assert db.execute(
            text(
                "SELECT rolsuper, rolbypassrls FROM pg_catalog.pg_roles "
                "WHERE rolname=current_user"
            )
        ).one() == (False, False)
        for model in (
            ChapterRead,
            ActivityAttempt,
            AttemptGrant,
            CourseCompletion,
            Certificate,
            Score,
            ClassSession,
        ):
            assert db.scalar(select(func.count()).select_from(model)) == 0
        home = learner_dashboard.learner_home(
            db, tenant_id=tenant, person_id=person, now=NOW
        )
        assert home.cards == () and home.continue_to is None
        assert all(c.count == 0 for c in home.counts)
        assert (
            learner_dashboard.course_cards(
                db, tenant_id=tenant, person_id=person, now=NOW
            ).cards
            == ()
        )
        assert (
            learner_dashboard.continue_target(db, tenant_id=tenant, person_id=person)
            is None
        )
        assert (
            learner_dashboard.resume_chapter(
                db, tenant_id=tenant, person_id=person, course_id=course
            )
            is None
        )
        assert (
            learner_dashboard.progress_overview(
                db, tenant_id=tenant, person_id=person, now=NOW
            )
            == ()
        )


def test_card_state_precedence_exact_edges_actions_and_raw_progress_deadline(
    result_database,
):
    _, app, tenant, person, course, activity, *_ = result_database
    with scoped(app, tenant) as db:
        off, enrollment, *_ = seed(db, tenant, person, course, activity)

        def card():
            return learner_dashboard.course_cards(
                db, tenant_id=tenant, person_id=person, now=NOW
            ).cards[0]

        assert card().state == "in_progress"
        enrollment.access_ends_at = NOW
        assert card().state == "in_progress"
        enrollment.access_ends_at = NOW - timedelta(microseconds=1)
        assert card().state == "expired" and card().action is None
        completion = db.scalar(
            select(CourseCompletion).where(CourseCompletion.course_id == course)
        )
        completion.status = "completed"
        assert card().state == "completed" and card().action.code == "review_feedback"
        off.starts_at = NOW + timedelta(microseconds=1)
        assert card().state == "upcoming" and card().action is None
        required = Course(
            tenant_id=tenant,
            slug="required",
            title="Required",
            discipline="engineering",
            source_ref="x",
        )
        db.add(required)
        db.flush()
        db.add(
            CoursePrerequisite(
                tenant_id=tenant, course_id=course, requires_course_id=required.id
            )
        )
        assert card().state == "locked" and card().locked_reason is not None
        assert card().certificate_status == "issued"


def test_soonest_effective_offering_selected_and_card_deadline_includes_passed_work(
    result_database,
):
    _, app, tenant, person, course, activity, *_ = result_database
    with scoped(app, tenant) as db:
        off, enrollment, *_ = seed(db, tenant, person, course, activity)
        off.starts_at = NOW - timedelta(days=2)
        cohort = Cohort(tenant_id=tenant, name="Alternative", discipline="engineering")
        db.add(cohort)
        db.flush()
        other_off = CourseOffering(
            tenant_id=tenant,
            cohort_id=cohort.id,
            course_id=course,
            starts_at=NOW - timedelta(days=1),
            ends_at=NOW + timedelta(days=2),
        )
        other_enrollment = Enrollment(
            tenant_id=tenant,
            person_id=person,
            cohort_id=cohort.id,
            role_in_cohort="student",
            access_ends_at=NOW + timedelta(days=10),
        )
        db.add_all([other_off, other_enrollment])
        db.flush()
        due = NOW + timedelta(hours=1)
        db.add(
            OfferingActivity(
                tenant_id=tenant,
                offering_id=other_off.id,
                activity_id=activity,
                due_at=due,
            )
        )
        assert (
            learner_dashboard.course_cards(
                db, tenant_id=tenant, person_id=person, now=NOW
            )
            .cards[0]
            .starts_at
            == off.starts_at
        )
        other_enrollment.access_ends_at = None
        card = learner_dashboard.course_cards(
            db, tenant_id=tenant, person_id=person, now=NOW
        ).cards[0]
        assert card.starts_at == other_off.starts_at and card.next_deadline == due
        # Passed activity deadlines still compete for the card's nearest deadline.
        overview = learner_dashboard.progress_overview(
            db, tenant_id=tenant, person_id=person, now=NOW
        )[0]
        assert overview.rows[0].passed and overview.rows[0].due_at == due
        assert all(row.activity.id != activity for row in overview.upcoming)
        assert overview.overdue == ()
        assert [row.due_at for row in overview.upcoming] == [
            other_off.ends_at,
            other_off.ends_at,
        ]
        other_off.ends_at = None
        assert (
            learner_dashboard.course_cards(
                db, tenant_id=tenant, person_id=person, now=NOW
            )
            .cards[0]
            .starts_at
            == off.starts_at
        )


def test_start_chapter_one_resume_numbers_and_due_now_neither_bucket(
    result_database,
):
    _, app, tenant, person, course, activity, *_ = result_database
    with scoped(app, tenant) as db:
        cohort = Cohort(tenant_id=tenant, name="Fresh", discipline="engineering")
        db.add(cohort)
        db.flush()
        off = CourseOffering(
            tenant_id=tenant,
            cohort_id=cohort.id,
            course_id=course,
            starts_at=NOW,
            ends_at=NOW,
        )
        chapter = Chapter(tenant_id=tenant, course_id=course, number=7, title="Seven")
        db.add_all(
            [
                off,
                chapter,
                Enrollment(tenant_id=tenant, cohort_id=cohort.id, person_id=person),
            ]
        )
        db.flush()
        home = learner_dashboard.learner_home(
            db, tenant_id=tenant, person_id=person, now=NOW
        )
        card = home.cards[0]
        assert card.state == "in_progress" and card.action.code == "start"
        assert card.action.chapter_number == 1 and card.next_deadline is None
        assert card.last_read is None and home.continue_to is None
        assert (
            learner_dashboard.resume_chapter(
                db, tenant_id=tenant, person_id=person, course_id=course
            ).number
            == 7
        )
        progress = learner_dashboard.progress_overview(
            db, tenant_id=tenant, person_id=person, now=NOW
        )[0]
        assert progress.rows[0].due_at == NOW and progress.rows[0].score is None
        assert progress.rows[0].pct is None and not progress.rows[0].feedback_available
        assert progress.upcoming == progress.overdue == ()
        assert progress.trend_points == () and progress.trend == "flat"
        assert progress.certificate.status is None


def test_touch_ties_keep_read_then_first_course_and_completed_latest_does_not_fall_back(
    result_database,
):
    _, app, tenant, person, course, activity, *_ = result_database
    with scoped(app, tenant) as db:
        _, _, chapters, second, third = seed(db, tenant, person, course, activity)
        # At equal timestamps reads are offered first, then submissions, then attempts.
        read = db.scalar(select(ChapterRead).where(ChapterRead.person_id == person))
        read.created_at = NOW
        assert (
            learner_dashboard.continue_target(
                db, tenant_id=tenant, person_id=person
            ).chapter.id
            == chapters[1].id
        )
        # Course-level submissions have no chapter target and cannot displace a touch.
        course_act = Activity(
            tenant_id=tenant,
            course_id=course,
            title="Final",
            type="mcq_test",
            pass_threshold=0.7,
        )
        db.add(course_act)
        db.flush()
        db.add(
            Submission(
                tenant_id=tenant,
                person_id=person,
                activity_id=course_act.id,
                answers={},
                attempt_no=1,
                created_at=NOW + timedelta(days=1),
            )
        )
        assert (
            learner_dashboard.continue_target(
                db, tenant_id=tenant, person_id=person
            ).chapter.id
            == chapters[1].id
        )
        other_course = Course(
            tenant_id=tenant,
            slug="a-first",
            title="A first",
            discipline="engineering",
            source_ref="x",
        )
        db.add(other_course)
        db.flush()
        cohort = Cohort(tenant_id=tenant, name="Other", discipline="engineering")
        db.add(cohort)
        db.flush()
        ch = Chapter(
            tenant_id=tenant, course_id=other_course.id, number=1, title="Other"
        )
        db.add_all(
            [
                ch,
                CourseOffering(
                    tenant_id=tenant, cohort_id=cohort.id, course_id=other_course.id
                ),
                Enrollment(tenant_id=tenant, cohort_id=cohort.id, person_id=person),
            ]
        )
        db.flush()
        db.add(
            ChapterRead(
                tenant_id=tenant, person_id=person, chapter_id=ch.id, created_at=NOW
            )
        )
        # Equal latest times select the first course by title. It is fully read with
        # no activities, so cross-course Continue is absent, without trying Course.
        assert (
            learner_dashboard.continue_target(db, tenant_id=tenant, person_id=person)
            is None
        )
        assert (
            learner_dashboard.learner_home(
                db, tenant_id=tenant, person_id=person, now=NOW
            ).continue_to
            is None
        )


def test_progress_last_eight_scores_unbounded_by_now_and_eligible_certificate_target(
    result_database,
):
    _, app, tenant, person, course, activity, *_ = result_database
    with scoped(app, tenant) as db:
        seed(db, tenant, person, course, activity)
        for i in range(10):
            submission = Submission(
                tenant_id=tenant,
                person_id=person,
                activity_id=activity,
                attempt_no=10 + i,
                answers={},
            )
            db.add(submission)
            db.flush()
            db.add(
                Score(
                    tenant_id=tenant,
                    submission_id=submission.id,
                    score=i / 10,
                    max_score=1,
                    fraction=i / 10,
                    passed=False,
                    per_item=[],
                    created_at=NOW + timedelta(seconds=i),
                )
            )
        certificate = db.scalar(
            select(Certificate).where(Certificate.course_id == course)
        )
        db.delete(certificate)
        completion = db.scalar(
            select(CourseCompletion).where(CourseCompletion.course_id == course)
        )
        completion.status = "completed"
        completion.pct = 0.125
        progress = learner_dashboard.progress_overview(
            db, tenant_id=tenant, person_id=person, now=NOW
        )[0]
        assert progress.trend_points == (20, 30, 40, 50, 60, 70, 80, 90)
        assert progress.trend == "up" and progress.completion_pct == 12
        assert progress.certificate.status == "eligible"
        assert (
            progress.certificate.serial is None
            and progress.certificate.target_course_id == course
        )
        card = learner_dashboard.course_cards(
            db, tenant_id=tenant, person_id=person, now=NOW
        ).cards[0]
        assert card.certificate_status == "eligible" and card.state == "completed"
        assert card.action.code == "review_feedback"


def test_read_projection_autoflushes_without_changing_caller_transaction(
    result_database,
):
    _, app, tenant, person, course, activity, *_ = result_database
    with scoped(app, tenant) as db:
        seed(db, tenant, person, course, activity)
        root = db.get_transaction()
        row = db.scalar(select(Course).where(Course.id == course))
        row.title = "Pending title"
        home = learner_dashboard.learner_home(
            db, tenant_id=tenant, person_id=person, now=NOW
        )
        assert home.cards[0].course.title == "Pending title"
        assert not db.dirty and db.get_transaction() is root
        assert db.get_nested_transaction() is None
