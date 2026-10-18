"""Hosted digest source counters, timestamps, query order and RLS proof."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from dotmac_learning import digest_reads
from dotmac_learning.models import CourseCompletion, LearningEvent, Score, Submission
from dotmac_learning.references import CourseWrite, create_course
from sqlalchemy import event as sa_event
from sqlalchemy import text

from tests import test_learning_results as learning_results
from tests.test_learning_catalog import _person
from tests.test_learning_results import scoped

learning_database = learning_results.learning_database
result_database = learning_results.result_database
SINCE = datetime(2026, 10, 10, 12, tzinfo=UTC)


def test_populated_staff_and_learner_windows_exact_source_counts_and_rls(
    result_database,
):
    admin, app, tenant, person, cid, aid, other, other_person, *_ = result_database
    with admin.begin() as conn:
        peer, outsider = _person(conn, tenant), _person(conn, tenant)
    with scoped(app, tenant) as db:
        assert db.scalar(
            text(
                "SELECT NOT rolsuper AND NOT rolbypassrls "
                "FROM pg_roles WHERE rolname=current_user"
            )
        )
        extra, null_course = uuid4(), uuid4()
        for course in (extra, null_course):
            create_course(
                db,
                CourseWrite(
                    status="published",
                    listed=False,
                    id=course,
                    tenant_id=tenant,
                    slug=course.hex,
                    title="Extra",
                    discipline="test",
                    source_ref="test",
                ),
            )
        subject, second = uuid4(), uuid4()
        for pid, kind, subject_id, stamp in (
            (person, "chapter_viewed", subject, SINCE),
            (person, "chapter_viewed", subject, SINCE + timedelta(days=1)),
            (person, "chapter_viewed", second, SINCE + timedelta(days=100)),
            (person, "chapter_viewed", None, SINCE),
            (person, "chapter_completed", uuid4(), SINCE),
            (person, "chapter_viewed", uuid4(), SINCE - timedelta(microseconds=1)),
            (peer, "work_graded", None, SINCE - timedelta(days=1)),
            (outsider, "work_graded", None, SINCE),
        ):
            db.add(
                LearningEvent(
                    tenant_id=tenant,
                    person_id=pid,
                    kind=kind,
                    course_id=cid,
                    subject_id=subject_id,
                    detail={},
                    occurred_at=stamp,
                )
            )
        for pid, course, status, stamp in (
            (person, cid, "in_progress", SINCE),
            (person, extra, "in_progress", SINCE + timedelta(days=100)),
            (person, null_course, "completed", None),
            (peer, cid, "completed", SINCE - timedelta(microseconds=1)),
            (peer, extra, "in_progress", SINCE),
            (outsider, cid, "completed", SINCE),
        ):
            db.add(
                CourseCompletion(
                    tenant_id=tenant,
                    person_id=pid,
                    course_id=course,
                    status=status,
                    pct=0.2,
                    completed_at=stamp,
                )
            )
        old = Submission(
            tenant_id=tenant,
            person_id=person,
            activity_id=aid,
            attempt_no=1,
            answers={},
            created_at=SINCE - timedelta(days=1),
        )
        current = Submission(
            tenant_id=tenant,
            person_id=person,
            activity_id=aid,
            attempt_no=2,
            answers={},
            created_at=SINCE,
        )
        future = Submission(
            tenant_id=tenant,
            person_id=person,
            activity_id=aid,
            attempt_no=3,
            answers={},
            created_at=SINCE + timedelta(days=100),
        )
        db.add_all([old, current, future])
        db.flush()
        # Score timestamps, not Submission timestamps, define passed volume.
        # Multiple passing scores on one submission each count as a row.
        for sub, passed, stamp in (
            (old, True, SINCE),
            (old, True, SINCE + timedelta(days=100)),
            (current, False, SINCE),
            (current, True, SINCE - timedelta(microseconds=1)),
        ):
            db.add(
                Score(
                    tenant_id=tenant,
                    submission_id=sub.id,
                    score=1,
                    max_score=1,
                    fraction=1,
                    passed=passed,
                    source="auto",
                    per_item=[],
                    created_at=stamp,
                )
            )
        db.flush()
        statements = []

        def capture(conn, cursor, statement, parameters, context, executemany):
            statements.append(statement)

        sa_event.listen(app, "before_cursor_execute", capture)
        try:
            staff = digest_reads.staff_learning_window(
                db, tenant_id=tenant, staff_ids=(person, peer), since=SINCE
            )
        finally:
            sa_event.remove(app, "before_cursor_execute", capture)
        assert len(statements) == 3
        assert "occurred_at >=" in statements[0]
        assert "occurred_at >=" not in statements[1]
        assert "course_completions" in statements[2]
        assert staff.active_ids == frozenset({person})
        assert staff.ever_active_ids == frozenset({person, peer})
        assert staff.completion_count == 3
        root = db.get_transaction()
        learner = digest_reads.learner_window_activity(
            db, tenant_id=tenant, person_id=person, since=SINCE
        )
        assert (learner.chapters, learner.submissions, learner.passed) == (2, 2, 2)
        assert db.get_transaction() is root
    with scoped(app, other) as db:
        assert db.scalar(
            text(
                "SELECT NOT rolsuper AND NOT rolbypassrls "
                "FROM pg_roles WHERE rolname=current_user"
            )
        )
        hidden = digest_reads.staff_learning_window(
            db, tenant_id=tenant, staff_ids=(person, peer), since=SINCE
        )
        assert hidden.active_ids == hidden.ever_active_ids == frozenset()
        assert hidden.completion_count == 0
        invisible = digest_reads.learner_window_activity(
            db, tenant_id=tenant, person_id=person, since=SINCE
        )
        assert (invisible.chapters, invisible.submissions, invisible.passed) == (
            0,
            0,
            0,
        )
        db.add(
            LearningEvent(
                tenant_id=other,
                person_id=other_person,
                kind="work_graded",
                detail={},
                occurred_at=SINCE,
            )
        )
        own = digest_reads.staff_learning_window(
            db, tenant_id=other, staff_ids=(other_person,), since=SINCE
        )
        assert own.active_ids == frozenset({other_person})


def test_empty_staff_guard_preserves_source_zero_sql(result_database, monkeypatch):
    _, app, tenant, *_ = result_database
    with scoped(app, tenant) as db:

        def no_sql(*args, **kwargs):
            pytest.fail("source empty-staff guard must issue no queries")

        monkeypatch.setattr(db, "execute", no_sql)
        monkeypatch.setattr(db, "scalars", no_sql)
        monkeypatch.setattr(db, "scalar", no_sql)
        fact = digest_reads.staff_learning_window(
            db, tenant_id=tenant, staff_ids=(), since=SINCE
        )
        assert fact.active_ids == fact.ever_active_ids == frozenset()
        assert fact.completion_count == 0
