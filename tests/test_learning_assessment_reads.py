"""Hosted source queue, question projection and grant-aware feedback canaries."""

from dotmac_kernel.models import Party
from dotmac_learning import assessment_reads
from dotmac_learning.models import Activity, Submission
from sqlalchemy import select

from tests import test_learning_results as result_tests
from tests.test_learning_results import scoped

learning_database = result_tests.learning_database
result_database = result_tests.result_database


def test_grouped_attempt_counts_include_unscored_submissions_and_keep_scope(
    result_database,
):
    from dotmac_learning.attempt_models import AttemptGrant

    from tests.test_learning_catalog import _person

    admin, app, tenant, person, course, activity, other, *_ = result_database
    with admin.begin() as conn:
        peer = _person(conn, tenant)
    with scoped(app, tenant) as db:
        second = Activity(
            assessment_mode="graded",
            grading="auto",
            pass_threshold=0.0,
            weight=1.0,
            tenant_id=tenant,
            course_id=course,
            title="Other",
            type="lab",
        )
        db.add(second)
        db.flush()
        db.add_all(
            [
                Submission(
                    tenant_id=tenant,
                    person_id=who,
                    activity_id=aid,
                    attempt_no=number,
                    answers={},
                )
                for who, aid, number in (
                    (person, activity, 1),
                    (person, activity, 2),
                    (person, second.id, 1),
                    (peer, activity, 1),
                )
            ]
        )
        db.add(
            AttemptGrant(
                tenant_id=tenant,
                person_id=person,
                activity_id=activity,
                extra_attempts=100,
                reason="Count submissions, not entitlement",
            )
        )
        # The read retains ordinary source autoflush in the caller transaction.
        root = db.get_transaction()
        assert assessment_reads.attempts_used_by_person(
            db, tenant_id=tenant, person_id=person
        ) == {activity: 2, second.id: 1}
        assert db.get_transaction() is root
        assert assessment_reads.attempts_used_by_person(
            db, tenant_id=tenant, person_id=peer
        ) == {activity: 1}
        assert (
            assessment_reads.attempts_used_by_person(
                db, tenant_id=other, person_id=person
            )
            == {}
        )
    with scoped(app, other) as db:
        assert (
            assessment_reads.attempts_used_by_person(
                db, tenant_id=tenant, person_id=person
            )
            == {}
        )


def test_pending_queue_keeps_auto_and_non_mcq_rows_without_type_or_manual_filter(
    result_database,
):
    _, app, tenant, person, _, activity, other, *_ = result_database
    with scoped(app, tenant) as db:
        row = db.scalar(select(Activity).where(Activity.id == activity))
        row.type = "lab"
        db.scalar(select(Party).where(Party.id == person)).email = "learner@x"
        db.add(
            Submission(
                tenant_id=tenant,
                person_id=person,
                activity_id=activity,
                answers={"a": [1]},
                attempt_no=1,
            )
        )
        db.flush()
        queue = assessment_reads.pending_grading(db, tenant_id=tenant)
        assert len(queue) == 1
        assert queue[0].activity.type == "lab" and queue[0].activity.grading == "auto"
        assert queue[0].submission.answers == {"a": [1]}
        assert queue[0].person_email
        assert assessment_reads.pending_grading(db, tenant_id=other) == []
        assert (
            assessment_reads.attempts_used(
                db, tenant_id=tenant, person_id=person, activity_id=activity
            )
            == 1
        )


def test_pending_queue_outer_score_exclusion_oldest_first_detached_and_scoped(
    result_database,
):
    from datetime import UTC, datetime, timedelta

    from dotmac_learning.models import Score

    _, app, tenant, person, _, activity, other, *_ = result_database
    clock = datetime(2001, 1, 1, tzinfo=UTC)
    with scoped(app, tenant) as db:
        rows = [
            Submission(
                tenant_id=tenant,
                person_id=person,
                activity_id=activity,
                answers={"old": [i]},
                attempt_no=i + 1,
                created_at=clock + timedelta(days=i),
            )
            for i in (2, 0, 1)
        ]
        db.add_all(rows)
        db.flush()
        db.add(
            Score(
                tenant_id=tenant,
                submission_id=rows[2].id,
                score=0,
                max_score=1,
                fraction=0,
                passed=False,
                per_item=[],
                source="auto",
            )
        )
        db.flush()
        queue = assessment_reads.pending_grading(db, tenant_id=tenant)
        assert [item.submission.id for item in queue] == [rows[1].id, rows[0].id]
        queue[0].submission.answers["old"].append(99)
        assert rows[1].answers == {"old": [0]}
        assert assessment_reads.pending_grading(db, tenant_id=other) == []


def test_question_projection_filter_empty_and_detachment(result_database):
    from dotmac_learning.curriculum_models import Question, QuestionBank

    _, app, tenant, _, course, _, other, *_ = result_database
    with scoped(app, tenant) as db:
        bank = QuestionBank(
            tenant_id=tenant, course_id=course, kind="practice", version=1
        )
        db.add(bank)
        db.flush()
        questions = [
            Question(
                tenant_id=tenant,
                bank_id=bank.id,
                ext_id=ext_id,
                stem="S",
                type="single",
                rubric_category="recall",
                correct=["yes"],
                options=["yes", "no"],
                explanation="Because",
                weight=2,
            )
            for ext_id in ("b", "a")
        ]
        db.add_all(questions)
        db.flush()
        all_questions = assessment_reads.questions_for(
            db, tenant_id=tenant, bank_id=bank.id
        )
        assert {q["ext_id"] for q in all_questions} == {"a", "b"}
        kept = assessment_reads.questions_for(
            db, tenant_id=tenant, bank_id=bank.id, only_ext_ids=["a", "missing"]
        )
        assert kept == [
            {
                "ext_id": "a",
                "type": "single",
                "correct": ["yes"],
                "weight": 2,
                "explanation": "Because",
                "options": ["yes", "no"],
            }
        ]
        kept[0]["correct"].append("changed")
        assert questions[1].correct == ["yes"]
        assert (
            assessment_reads.questions_for(
                db, tenant_id=tenant, bank_id=bank.id, only_ext_ids=[]
            )
            == []
        )
        assert (
            assessment_reads.questions_for(db, tenant_id=other, bank_id=bank.id) == []
        )


def test_other_passing_score_exclusion_ignores_time_and_retains_tenant_scope(
    result_database,
):
    from datetime import UTC, datetime, timedelta
    from uuid import uuid4

    from dotmac_learning.models import Score
    from sqlalchemy import text

    _, app, tenant, person, _, activity, other, *_ = result_database
    clock = datetime(2026, 10, 17, tzinfo=UTC)
    with scoped(app, tenant) as db:
        assert db.scalar(
            text(
                "SELECT NOT rolsuper AND NOT rolbypassrls "
                "FROM pg_roles WHERE rolname=current_user"
            )
        )
        sub = Submission(
            tenant_id=tenant,
            person_id=person,
            activity_id=activity,
            answers={},
            attempt_no=1,
        )
        db.add(sub)
        db.flush()
        excluded = Score(
            tenant_id=tenant,
            submission_id=sub.id,
            score=1,
            max_score=1,
            fraction=1,
            passed=True,
            source="auto",
            per_item=[],
            created_at=clock,
        )
        db.add(excluded)
        db.flush()
        assert not assessment_reads.has_other_passing_score(
            db,
            tenant_id=tenant,
            person_id=person,
            activity_id=activity,
            excluded_score_id=excluded.id,
        )
        for stamp in (clock, clock + timedelta(days=1)):
            later = Score(
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
            db.add(later)
            db.flush()
            assert assessment_reads.has_other_passing_score(
                db,
                tenant_id=tenant,
                person_id=person,
                activity_id=activity,
                excluded_score_id=excluded.id,
            )
            db.delete(later)
            db.flush()
        false = Score(
            tenant_id=tenant,
            submission_id=sub.id,
            score=0,
            max_score=1,
            fraction=0,
            passed=False,
            source="override",
            per_item=[],
        )
        db.add(false)
        assert not assessment_reads.has_other_passing_score(
            db,
            tenant_id=tenant,
            person_id=person,
            activity_id=activity,
            excluded_score_id=excluded.id,
        )
        assert assessment_reads.has_other_passing_score(
            db,
            tenant_id=tenant,
            person_id=person,
            activity_id=activity,
            excluded_score_id=uuid4(),
        )
        assert not assessment_reads.has_other_passing_score(
            db,
            tenant_id=tenant,
            person_id=uuid4(),
            activity_id=activity,
            excluded_score_id=uuid4(),
        )
        saved_id = excluded.id
    with scoped(app, other) as db:
        assert db.scalar(
            text(
                "SELECT NOT rolsuper AND NOT rolbypassrls "
                "FROM pg_roles WHERE rolname=current_user"
            )
        )
        assert not assessment_reads.has_other_passing_score(
            db,
            tenant_id=tenant,
            person_id=person,
            activity_id=activity,
            excluded_score_id=saved_id,
        )
        assert not assessment_reads.has_other_passing_score(
            db,
            tenant_id=tenant,
            person_id=person,
            activity_id=activity,
            excluded_score_id=uuid4(),
        )
