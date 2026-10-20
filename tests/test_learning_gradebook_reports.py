"""Real migrated, non-bypass two-tenant reporting canaries; hosted only."""

from uuid import uuid4

import pytest
from dotmac_kernel.models import Party
from dotmac_learning import gradebook, reports
from dotmac_learning.models import Score, Submission
from dotmac_learning.participation_contracts import ParticipationNotFound
from dotmac_learning.participation_models import Cohort, CourseOffering, Enrollment
from dotmac_learning.references import (
    ActivityWrite,
    CourseWrite,
    create_activity,
    create_course,
)
from sqlalchemy import text

from tests import test_learning_results as learning_results
from tests.test_learning_catalog import _person
from tests.test_learning_results import scoped

learning_database = learning_results.learning_database
result_database = learning_results.result_database


def seed(db, tenant, person, course, activity):
    cohort = Cohort(tenant_id=tenant, name="C", discipline="unrelated")
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
            CourseOffering(tenant_id=tenant, cohort_id=cohort.id, course_id=course),
        ]
    )
    sub = Submission(
        tenant_id=tenant,
        activity_id=activity,
        person_id=person,
        attempt_no=1,
        answers={},
    )
    db.add(sub)
    db.flush()
    low = Score(
        tenant_id=tenant,
        submission_id=sub.id,
        score=0,
        max_score=1,
        fraction=0,
        passed=False,
        per_item=[{"id": "q", "correct": False}],
        source="auto",
    )
    db.add(low)
    db.flush()
    return cohort.id, sub.id, low.id


def test_gradebook_reports_real_app_role_isolation_and_detachment(result_database):
    _, app, tenant, person, course, activity, other, other_person, *_ = result_database
    with scoped(app, tenant) as db:
        assert db.scalar(
            text(
                "SELECT NOT rolsuper AND NOT rolbypassrls "
                "FROM pg_roles WHERE rolname=current_user"
            )
        )
        cohort, _, _ = seed(db, tenant, person, course, activity)
        missing = uuid4()
        create_activity(
            db,
            ActivityWrite(
                type="mcq_test",
                pass_threshold=0.0,
                assessment_mode="graded",
                grading="auto",
                id=missing,
                tenant_id=tenant,
                course_id=course,
                title="Missing",
                weight=3,
            ),
        )
        gb = gradebook.cohort_gradebook(db, tenant_id=tenant, cohort_id=cohort)
        matrix = reports.cohort_matrix(db, tenant_id=tenant, cohort_id=cohort)
        assert len(gb.rows) == len(matrix.rows) == 1
        assert gb.rows[0].person.id == person
        assert gb.rows[0].final_pct == 0
        assert matrix.rows[0].completion == 0
        assert (
            gradebook.attempted_grade(
                db, tenant_id=tenant, person_id=person, course_id=course
            ).graded_weight_fraction
            == 0.25
        )
        root = db.get_transaction()
        transcript = reports.student_transcript(db, tenant_id=tenant, person_id=person)
        assert db.get_transaction() is root
        assert len(transcript.rows) == 1
    # Facts remain usable after the Session closes; no ORM capability escapes.
    assert transcript.rows[0].activity.id == activity
    assert not hasattr(transcript.person, "_sa_instance_state")
    assert not hasattr(transcript.rows[0].score, "_sa_instance_state")
    with scoped(app, other) as db:
        assert db.scalar(
            text(
                "SELECT NOT rolsuper AND NOT rolbypassrls "
                "FROM pg_roles WHERE rolname=current_user"
            )
        )
        # Deliberately pass A's tenant while connected as B: explicit filtering
        # alone cannot make this proof pass; actual FORCE RLS must hide A.
        for reader in (reports.cohort_matrix, gradebook.cohort_gradebook):
            with pytest.raises(
                ParticipationNotFound, match="cohort not found for tenant"
            ):
                reader(db, tenant_id=tenant, cohort_id=cohort)
        with pytest.raises(reports.ReportNotFound, match="person not found for tenant"):
            reports.student_transcript(db, tenant_id=tenant, person_id=person)
        assert (
            gradebook.course_grade(
                db, tenant_id=tenant, person_id=person, course_id=course
            ).per_activity
            == ()
        )
        assert (
            reports.student_transcript(db, tenant_id=other, person_id=other_person).rows
            == ()
        )


def test_transcript_does_not_share_assessment_override_tie_policy(result_database):
    _, app, tenant, person, course, activity, *_ = result_database
    with scoped(app, tenant) as db:
        cohort, sub, first = seed(db, tenant, person, course, activity)
        db.add(
            Score(
                tenant_id=tenant,
                submission_id=sub,
                score=0,
                max_score=1,
                fraction=0,
                passed=True,
                source="override",
                per_item=[{"id": "q", "correct": True}],
            )
        )
        db.flush()
        # Source leaves equal-fraction SQL ties unspecified, but its strict
        # comparison must select the same first input row for the transcript algorithm.
        pairs = db.execute(reports._transcript_query(tenant, person)).all()
        expected = pairs[0][1].id
        assert (
            reports.student_transcript(db, tenant_id=tenant, person_id=person)
            .rows[0]
            .score.id
            == expected
        )
        matrix = reports.cohort_matrix(db, tenant_id=tenant, cohort_id=cohort)
        assert matrix.rows[0].completion == 1
        assert matrix.rows[0].cells[0].score.source == "override"


def test_only_active_offerings_and_active_students_without_person_status_filter(
    result_database,
):
    admin, app, tenant, person, course, activity, other, other_person, *_ = (
        result_database
    )
    with admin.begin() as conn:
        conn.execute(
            Party.__table__.update().where(Party.id == person).values(is_active=False)
        )
        instructor = _person(conn, tenant)
        dropped = _person(conn, tenant)
    with scoped(app, tenant) as db:
        cohort, _, _ = seed(db, tenant, person, course, activity)
        db.add_all(
            [
                Enrollment(
                    tenant_id=tenant,
                    cohort_id=cohort,
                    person_id=instructor,
                    role_in_cohort="instructor",
                    status="active",
                ),
                Enrollment(
                    tenant_id=tenant,
                    cohort_id=cohort,
                    person_id=dropped,
                    role_in_cohort="student",
                    status="dropped",
                ),
            ]
        )
        # Discipline and course publication status are not report selectors.
        for status in (None, "inactive"):
            cid, aid = uuid4(), uuid4()
            create_course(
                db,
                CourseWrite(
                    listed=False,
                    id=cid,
                    tenant_id=tenant,
                    slug=cid.hex,
                    title="Excluded",
                    discipline="unrelated",
                    status="published",
                    source_ref="test",
                ),
            )
            create_activity(
                db,
                ActivityWrite(
                    type="mcq_test",
                    pass_threshold=0.0,
                    assessment_mode="graded",
                    grading="auto",
                    weight=1.0,
                    id=aid,
                    tenant_id=tenant,
                    course_id=cid,
                    title="Excluded",
                ),
            )
            if status is not None:
                db.add(
                    CourseOffering(
                        tenant_id=tenant, cohort_id=cohort, course_id=cid, status=status
                    )
                )
        for reader in (gradebook.cohort_gradebook, reports.cohort_matrix):
            result = reader(db, tenant_id=tenant, cohort_id=cohort)
            assert tuple(a.id for a in result.activities) == (activity,)
            assert len(result.rows) == 1
        assert (
            reports.cohort_matrix(db, tenant_id=tenant, cohort_id=cohort)
            .rows[0]
            .person_id
            == person
        )
    with scoped(app, other) as db:
        cid, aid = uuid4(), uuid4()
        create_course(
            db,
            CourseWrite(
                status="published",
                listed=False,
                id=cid,
                tenant_id=other,
                slug=cid.hex,
                title="B",
                discipline="other",
                source_ref="test",
            ),
        )
        create_activity(
            db,
            ActivityWrite(
                type="mcq_test",
                pass_threshold=0.0,
                assessment_mode="graded",
                grading="auto",
                weight=1.0,
                id=aid,
                tenant_id=other,
                course_id=cid,
                title="B",
            ),
        )
        own_cohort, _, _ = seed(db, other, other_person, cid, aid)
        assert (
            reports.student_transcript(db, tenant_id=other, person_id=other_person)
            .rows[0]
            .activity.id
            == aid
        )
        assert (
            reports.cohort_matrix(db, tenant_id=other, cohort_id=own_cohort)
            .rows[0]
            .person_id
            == other_person
        )
        with pytest.raises(ParticipationNotFound):
            reports.cohort_matrix(db, tenant_id=tenant, cohort_id=cohort)
