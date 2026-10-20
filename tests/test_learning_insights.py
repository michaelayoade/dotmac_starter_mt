"""Hosted-only populated projections and actual non-bypass tenant isolation."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from dotmac_learning import insights
from dotmac_learning.attempt_models import AttemptGrant
from dotmac_learning.delivery_models import OfferingActivity
from dotmac_learning.insight_contracts import (
    CohortInsightTerms,
    GradeTrendTerms,
    LearnerInsightTerms,
)
from dotmac_learning.models import (
    Activity,
    Certificate,
    CourseCompletion,
    LearningEvent,
    Score,
    Submission,
)
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


def add_score(db, tenant, person, activity, fraction, *, passed=False, stamp=NOW):
    sub = Submission(
        tenant_id=tenant,
        person_id=person,
        activity_id=activity,
        attempt_no=1,
        answers={},
    )
    db.add(sub)
    db.flush()
    db.add(
        Score(
            tenant_id=tenant,
            submission_id=sub.id,
            score=fraction,
            max_score=1,
            fraction=fraction,
            passed=passed,
            source="auto",
            per_item=[],
            created_at=stamp,
        )
    )
    db.flush()


def event(db, tenant, person, course, stamp, kind="work_graded"):
    db.add(
        LearningEvent(
            tenant_id=tenant,
            person_id=person,
            course_id=course,
            kind=kind,
            detail={},
            occurred_at=stamp,
        )
    )


@pytest.fixture
def populated(result_database):
    admin, app, tenant, person, course, activity, other, other_person, *_ = (
        result_database
    )
    with admin.begin() as conn:
        instructor = _person(conn, tenant)
    with scoped(app, tenant) as db:
        cohort = Cohort(tenant_id=tenant, name="Zulu", discipline="unrelated")
        db.add(cohort)
        db.flush()
        db.add_all(
            [
                Enrollment(
                    tenant_id=tenant,
                    cohort_id=cohort.id,
                    person_id=person,
                    role_in_cohort="student",
                    status="active",
                ),
                Enrollment(
                    tenant_id=tenant,
                    cohort_id=cohort.id,
                    person_id=instructor,
                    role_in_cohort="instructor",
                    status="active",
                ),
            ]
        )
        offering = CourseOffering(
            tenant_id=tenant, cohort_id=cohort.id, course_id=course
        )
        db.add(offering)
        db.flush()
        db.add(
            OfferingActivity(
                tenant_id=tenant,
                offering_id=offering.id,
                activity_id=activity,
                due_at=NOW - timedelta(seconds=1),
            )
        )
        second_course = uuid4()
        create_course(
            db,
            CourseWrite(
                id=second_course,
                tenant_id=tenant,
                slug=second_course.hex,
                title="Second",
                discipline="second",
                source_ref="test",
                status="published",
                listed=False,
            ),
        )
        db.add(
            CourseOffering(
                tenant_id=tenant, cohort_id=cohort.id, course_id=second_course
            )
        )
        extra, missing, other_activity = uuid4(), uuid4(), uuid4()
        for aid, cid in (
            (extra, course),
            (missing, course),
            (other_activity, second_course),
        ):
            create_activity(
                db,
                ActivityWrite(
                    id=aid,
                    tenant_id=tenant,
                    course_id=cid,
                    title="A",
                    type="mcq_test",
                    pass_threshold=0.0,
                    assessment_mode="graded",
                    grading="auto",
                    weight=1.0,
                ),
            )
        for i, f in enumerate((0.1, 0.2, 0.4, 0.8)):
            # Same activity may have several Score rows; trend consumes all,
            # cohort average consumes only each activity's assessment best.
            if i == 0:
                sub = Submission(
                    tenant_id=tenant,
                    person_id=person,
                    activity_id=activity,
                    attempt_no=1,
                    answers={},
                )
                db.add(sub)
                db.flush()
            db.add(
                Score(
                    tenant_id=tenant,
                    submission_id=sub.id,
                    score=f,
                    max_score=1,
                    fraction=f,
                    passed=False,
                    source="auto",
                    per_item=[],
                    created_at=NOW - timedelta(days=4 - i),
                )
            )
        add_score(db, tenant, person, extra, 0.8, passed=True)
        add_score(db, tenant, person, other_activity, 1, passed=True)
        db.add_all(
            [
                CourseCompletion(
                    tenant_id=tenant,
                    person_id=person,
                    course_id=course,
                    status="completed",
                    pct=1,
                ),
                Certificate(
                    tenant_id=tenant,
                    person_id=person,
                    course_id=course,
                    serial=uuid4().hex,
                    issued_at=NOW,
                ),
            ]
        )
        event(db, tenant, person, course, NOW - timedelta(days=1))
        event(db, tenant, person, course, NOW - timedelta(days=7))
        event(db, tenant, person, course, NOW - timedelta(days=35))
        event(db, tenant, person, course, NOW - timedelta(days=35, seconds=1))
        event(db, tenant, person, course, NOW - timedelta(days=1), "lab_check_failed")
        event(
            db, tenant, instructor, course, NOW - timedelta(days=1), "lab_check_failed"
        )
        db.flush()
        cid, oid = cohort.id, offering.id
    return result_database, cid, oid, missing


def test_all_populated_projections_and_nonbypass_rls(populated):
    fixture, cohort, _, _ = populated
    _, app, tenant, person, _, _, other, other_person, *_ = fixture
    with scoped(app, tenant) as db:
        assert db.scalar(
            text(
                "SELECT NOT rolsuper AND NOT rolbypassrls "
                "FROM pg_roles WHERE rolname=current_user"
            )
        )
        root = db.get_transaction()
        learner = insights.learner_activity(
            db,
            tenant_id=tenant,
            person_id=person,
            now=NOW,
            terms=LearnerInsightTerms(8, 4, 7, GradeTrendTerms(10, 4, 0.05)),
        )
        assert learner.blocker.reason == learner.recommendation == "overdue"
        assert learner.days_since_last == 1 and learner.attendance is None
        assert len(learner.weekly) == 8 and learner.max_week > 0
        assert learner.velocity_per_week == 0.5
        assert learner.grade_trend.direction == "up"
        assert learner.disciplines.strong == "second"
        overview = insights.cohort_overview(
            db,
            tenant_id=tenant,
            cohort_id=cohort,
            now=NOW,
            terms=CohortInsightTerms(7, 4),
        )
        assert (overview.learners, overview.active, overview.inactive) == (1, 1, 0)
        assert (overview.submitted, overview.overdue, overview.not_attempted) == (
            3,
            1,
            1,
        )
        assert overview.avg_score_pct == 87
        assert (
            overview.funnel.enrolled,
            overview.funnel.started,
            overview.funnel.half,
            overview.funnel.completed,
            overview.funnel.certified,
        ) == (1, 1, 1, 1, 1)
        assert overview.week_events == 3 and overview.prior_week_avg == 0.2
        assert tuple(f.failed_checks for f in overview.lab_failures) == (1,)
        comparison = insights.cohorts_comparison(
            db, tenant_id=tenant, now=NOW, active_window_days=7
        )
        assert comparison[0].cohort.id == cohort
        assert comparison[0].completion_pct == 100
        assert db.get_transaction() is root
    assert not hasattr(comparison[0].cohort, "_sa_instance_state")
    with scoped(app, other) as db:
        assert db.scalar(
            text(
                "SELECT NOT rolsuper AND NOT rolbypassrls "
                "FROM pg_roles WHERE rolname=current_user"
            )
        )
        evil = insights.learner_activity(
            db,
            tenant_id=tenant,
            person_id=person,
            now=NOW,
            terms=LearnerInsightTerms(8, 4, 7, GradeTrendTerms(10, 4, 0.05)),
        )
        assert evil.max_week == 0 and evil.grade_trend.points == ()
        assert evil.disciplines.all == () and evil.blocker is None
        hidden = insights.cohort_overview(
            db,
            tenant_id=tenant,
            cohort_id=cohort,
            now=NOW,
            terms=CohortInsightTerms(7, 4),
        )
        assert hidden.learners == hidden.submitted == hidden.overdue == 0
        assert hidden.avg_score_pct is None and hidden.lab_failures == ()
        assert (
            insights.cohorts_comparison(
                db, tenant_id=tenant, now=NOW, active_window_days=7
            )
            == ()
        )
        own = Cohort(tenant_id=other, name="Own", discipline="B")
        db.add(own)
        db.flush()
        db.add(
            Enrollment(
                tenant_id=other,
                cohort_id=own.id,
                person_id=other_person,
                role_in_cohort="student",
                status="active",
            )
        )
        event(db, other, other_person, None, NOW - timedelta(days=1))
        assert (
            insights.cohort_overview(
                db,
                tenant_id=other,
                cohort_id=own.id,
                now=NOW,
                terms=CohortInsightTerms(7, 4),
            ).active
            == 1
        )
        assert (
            insights.cohorts_comparison(
                db, tenant_id=other, now=NOW, active_window_days=7
            )[0].cohort.id
            == own.id
        )


def test_blocker_exact_deadline_and_attempt_grants(populated):
    fixture, cohort, offering, missing = populated
    _, app, tenant, person, _, activity, *_ = fixture
    with scoped(app, tenant) as db:
        override = db.scalar(
            select(OfferingActivity).where(OfferingActivity.offering_id == offering)
        )
        override.due_at = NOW
        row = db.get(Activity, activity)
        row.max_attempts = 1
        assert (
            insights.learner_activity(
                db,
                tenant_id=tenant,
                person_id=person,
                now=NOW,
                terms=LearnerInsightTerms(8, 4, 7, GradeTrendTerms(10, 4, 0.05)),
            ).blocker.reason
            == "attempts_exhausted"
        )
        db.add(
            AttemptGrant(
                tenant_id=tenant,
                person_id=person,
                activity_id=activity,
                extra_attempts=1,
                reason="extra",
            )
        )
        assert (
            insights.learner_activity(
                db,
                tenant_id=tenant,
                person_id=person,
                now=NOW,
                terms=LearnerInsightTerms(8, 4, 7, GradeTrendTerms(10, 4, 0.05)),
            ).blocker
            is None
        )
        # Source overview includes overdue overrides on inactive offerings;
        # learner blocker requires an active offering. Keep that difference.
        disabled_course, disabled_activity = uuid4(), uuid4()
        create_course(
            db,
            CourseWrite(
                id=disabled_course,
                tenant_id=tenant,
                slug=disabled_course.hex,
                title="Disabled",
                discipline="test",
                source_ref="test",
                status="published",
                listed=False,
            ),
        )
        create_activity(
            db,
            ActivityWrite(
                id=disabled_activity,
                tenant_id=tenant,
                course_id=disabled_course,
                title="Disabled",
                type="mcq_test",
                pass_threshold=0.0,
                assessment_mode="graded",
                grading="auto",
                weight=1.0,
            ),
        )
        inactive = CourseOffering(
            tenant_id=tenant,
            cohort_id=cohort,
            course_id=disabled_course,
            status="inactive",
        )
        db.add(inactive)
        db.flush()
        db.add(
            OfferingActivity(
                tenant_id=tenant,
                offering_id=inactive.id,
                activity_id=disabled_activity,
                due_at=NOW - timedelta(seconds=1),
            )
        )
        assert (
            insights.learner_activity(
                db,
                tenant_id=tenant,
                person_id=person,
                now=NOW,
                terms=LearnerInsightTerms(8, 4, 7, GradeTrendTerms(10, 4, 0.05)),
            ).blocker
            is None
        )
        assert (
            insights.cohort_overview(
                db,
                tenant_id=tenant,
                cohort_id=cohort,
                now=NOW,
                terms=CohortInsightTerms(7, 4),
            ).overdue
            == 1
        )


def test_trend_latest_ten_and_comparison_all_statuses_stable_name_sort(result_database):
    _, app, tenant, person, course, activity, *_ = result_database
    with scoped(app, tenant) as db:
        sub = Submission(
            tenant_id=tenant,
            person_id=person,
            activity_id=activity,
            attempt_no=1,
            answers={},
        )
        db.add(sub)
        db.flush()
        for i in range(12):
            db.add(
                Score(
                    tenant_id=tenant,
                    submission_id=sub.id,
                    score=i / 20,
                    max_score=1,
                    fraction=i / 20,
                    passed=False,
                    per_item=[],
                    source="auto",
                    created_at=NOW - timedelta(days=12 - i),
                )
            )
        assert insights._grade_trend(
            db, tenant, person, terms=GradeTrendTerms(10, 4, 0.05)
        ).points == tuple(range(10, 60, 5))
        db.add_all(
            [
                Cohort(
                    tenant_id=tenant, name="Zulu", discipline="test", status="active"
                ),
                Cohort(
                    tenant_id=tenant, name="alpha", discipline="test", status="inactive"
                ),
            ]
        )
        rows = insights.cohorts_comparison(
            db, tenant_id=tenant, now=NOW, active_window_days=7
        )
        assert tuple(r.cohort.name for r in rows) == ("alpha", "Zulu")
        assert tuple(r.completion_pct for r in rows) == (0, 0)
        for r in rows:
            ov = insights.cohort_overview(
                db,
                tenant_id=tenant,
                cohort_id=r.cohort.id,
                now=NOW,
                terms=CohortInsightTerms(7, 4),
            )
            assert ov.avg_score_pct is None
            assert ov.lab_failures == ()
            assert ov.funnel.enrolled == ov.funnel.started == ov.funnel.half == 0


def test_configured_insight_windows_keep_real_rls_and_transaction(populated):
    fixture, cohort, _, _ = populated
    _, app, tenant, person, _, _, other, *_ = fixture
    learner_terms = LearnerInsightTerms(3, 2, 11, GradeTrendTerms(2, 2, 0.25))
    cohort_terms = CohortInsightTerms(0, 1)
    with scoped(app, tenant) as db:
        root = db.get_transaction()
        learner = insights.learner_activity(
            db, tenant_id=tenant, person_id=person, terms=learner_terms, now=NOW
        )
        assert len(learner.weekly) == 3
        assert learner.velocity_per_week == 1.0
        assert len(learner.grade_trend.points) == 2
        overview = insights.cohort_overview(
            db, tenant_id=tenant, cohort_id=cohort, terms=cohort_terms, now=NOW
        )
        assert overview.active_window_days == 0 and overview.active == 0
        # The event at exactly seven days belongs to this week; only the prior
        # half-open 7..14 day range enters the one-week comparison denominator.
        assert overview.week_events == 3 and overview.prior_week_avg == 0
        rows = insights.cohorts_comparison(
            db, tenant_id=tenant, active_window_days=0, now=NOW
        )
        assert rows[0].active == 0
        assert db.get_transaction() is root
    with scoped(app, other) as db:
        hidden = insights.cohort_overview(
            db, tenant_id=tenant, cohort_id=cohort, terms=cohort_terms, now=NOW
        )
        assert hidden.learners == hidden.active == hidden.week_events == 0
        assert hidden.prior_week_avg == 0
