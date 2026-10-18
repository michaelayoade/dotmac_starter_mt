"""Hosted real Alembic report policies and non-bypass tenant isolation."""

from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from dotmac_kernel.models import Party, PartyPerson
from dotmac_learning import support_report_reads as reads
from dotmac_learning.delivery_models import SuccessQueueEntry
from dotmac_learning.models import (
    Activity,
    Course,
    CourseCompletion,
    LearningEvent,
    Score,
    Submission,
)
from dotmac_learning.participation_models import Cohort, CourseOffering, Enrollment
from sqlalchemy import delete, event, func, select, text
from sqlalchemy.exc import ProgrammingError

from tests import test_learning_results as result_tests
from tests.test_learning_catalog import _person, _scope
from tests.test_learning_results import scoped

learning_database = result_tests.learning_database
result_database = result_tests.result_database
NOW = datetime(2026, 8, 1, 12, tzinfo=UTC)
SINCE = NOW - timedelta(days=1)


def add_event(db, tenant, person, *, when=NOW, kind="activity_started"):
    db.add(
        LearningEvent(
            tenant_id=tenant, person_id=person, kind=kind, occurred_at=when, detail={}
        )
    )


def add_score(db, tenant, person, activity, *, fraction=0, attempt=1, when=NOW):
    submission = Submission(
        tenant_id=tenant,
        person_id=person,
        activity_id=activity,
        attempt_no=attempt,
        answers={},
        created_at=NOW,
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
            passed=fraction >= 0.7,
            per_item=[],
            created_at=when,
        )
    )
    db.flush()
    return submission.id


@pytest.fixture
def report_database(result_database, request):
    event_case = getattr(request, "param", "active")
    assert event_case in {"active", "historical-person", "never-active-second"}
    admin, app, tenant, person, course, activity, other, *_ = result_database
    with admin.begin() as conn:
        second, dropped = _person(conn, tenant), _person(conn, tenant)
    with scoped(app, tenant) as db:
        cohort = Cohort(tenant_id=tenant, name="Zeta", discipline="engineering")
        alternate = Cohort(tenant_id=tenant, name="Alpha", discipline="engineering")
        db.add_all([cohort, alternate])
        db.flush()
        cohort_id, alternate_id = cohort.id, alternate.id
        for who, cid, role, status in (
            (person, cohort_id, "student", "active"),
            (second, cohort_id, "student", "active"),
            (dropped, cohort_id, "student", "dropped"),
            (person, alternate_id, "instructor", "active"),
            (second, alternate_id, "student", "active"),
        ):
            db.add(
                Enrollment(
                    tenant_id=tenant,
                    person_id=who,
                    cohort_id=cid,
                    role_in_cohort=role,
                    status=status,
                    created_at=NOW,
                )
            )
        db.get(Party, person).is_active = False
        db.get(PartyPerson, person).last_name = "A"
        db.get(PartyPerson, second).last_name = "B"
        courses = [course]
        activities = [activity]
        for index in range(2):
            c = Course(
                tenant_id=tenant,
                slug=uuid4().hex,
                title=f"Course {index}",
                discipline="engineering",
                source_ref="source",
                status="published",
                listed=False,
            )
            db.add(c)
            db.flush()
            a = Activity(
                tenant_id=tenant,
                course_id=c.id,
                title=f"Work {index}",
                type="mcq_test",
                pass_threshold=0.0,
                weight=1.0,
                assessment_mode="graded",
                grading="auto",
            )
            db.add(a)
            db.flush()
            courses.append(c.id)
            activities.append(a.id)
        for cid in courses:
            db.add(CourseOffering(tenant_id=tenant, cohort_id=cohort_id, course_id=cid))
        db.add(
            CourseCompletion(
                tenant_id=tenant,
                person_id=person,
                course_id=course,
                pct=0.75,
                status="in_progress",
                completed_at=NOW,
            )
        )
        add_score(db, tenant, person, activity, fraction=0)
        add_score(db, tenant, person, activities[1], fraction=1)
        person_when = (
            SINCE - timedelta(seconds=1) if event_case == "historical-person" else NOW
        )
        for kind in ("activity_started", "submission_made", "work_graded"):
            add_event(db, tenant, person, when=person_when, kind=kind)
        add_event(db, tenant, person, when=SINCE - timedelta(microseconds=1))
        # Events are append-only for app_user: seed the desired history directly.
        if event_case == "active":
            for _ in range(2):
                add_event(db, tenant, second)
        for _ in range(10):
            add_event(db, tenant, dropped)
        for index in range(10):
            db.add(
                SuccessQueueEntry(
                    tenant_id=tenant,
                    person_id=person,
                    cohort_id=cohort_id,
                    reason_kind="inactivity" if index == 0 else f"reason{index}",
                    status="open",
                    severity="medium",
                    detected_at=NOW - timedelta(minutes=index),
                    supporting_facts={"never_active": index == 0, "days_inactive": 20},
                )
            )
        db.add_all(
            [
                SuccessQueueEntry(
                    tenant_id=tenant,
                    person_id=person,
                    cohort_id=None,
                    reason_kind="ack_history",
                    status="acknowledged",
                    severity="high",
                    detected_at=NOW,
                    supporting_facts=["arbitrary history"],
                ),
                SuccessQueueEntry(
                    tenant_id=tenant,
                    person_id=person,
                    cohort_id=uuid4(),
                    reason_kind="other_state",
                    status="investigating",
                    severity="low",
                    detected_at=NOW,
                    supporting_facts={},
                ),
                SuccessQueueEntry(
                    tenant_id=tenant,
                    person_id=person,
                    reason_kind="resolved_history",
                    status="resolved",
                    severity="high",
                    detected_at=NOW,
                    supporting_facts={},
                ),
            ]
        )
        db.flush()
    return {
        "admin": admin,
        "app": app,
        "tenant": tenant,
        "other": other,
        "person": person,
        "second": second,
        "dropped": dropped,
        "cohort": cohort_id,
        "alternate": alternate_id,
        "courses": tuple(courses),
        "activities": tuple(activities),
    }


def test_all_projections_positive_before_evil_a_parameters_under_b_non_bypass(
    report_database,
):
    f = report_database
    with scoped(f["app"], f["tenant"]) as db:
        engagement = reads.engagement_snapshot(db, tenant_id=f["tenant"], since=SINCE)
        assert (
            engagement.enrollees,
            engagement.ever_active,
            engagement.active_in_window,
        ) == (2, 2, 2)
        movers = reads.top_movers(db, tenant_id=f["tenant"], since=SINCE, limit=5)
        assert [row.person.id for row in movers] == [f["person"], f["second"]]
        assert movers[0].cohort_name == "Alpha" and movers[0].avg_score == 0.5
        assert movers[1].avg_score is None
        counts = reads.activity_window_counts(db, tenant_id=f["tenant"], since=SINCE)
        assert (counts.new_enrollments, counts.submissions, counts.completions) == (
            5,
            2,
            1,
        )
        attention = reads.attention_list(db, tenant_id=f["tenant"], limit=8)
        assert attention.open_total == 10 and len(attention.top) == 8
        assert (
            sum(row.count for row in attention.by_reason) == 10
            and attention.never_started == 1
        )
        roster = reads.cohort_roster_progress(
            db, tenant_id=f["tenant"], cohort_id=f["cohort"], now=NOW
        )
        assert [row.person.id for row in roster.rows] == [f["person"], f["second"]]
        first = roster.rows[0]
        assert (first.courses, first.avg_completion_pct, first.avg_grade_pct) == (
            3,
            75,
            50,
        )
        assert first.last_activity_at == NOW and first.days_inactive == 0
        assert (
            roster.rows[1].last_activity_at == NOW and roster.rows[1].days_inactive == 0
        )
        with pytest.raises(FrozenInstanceError):
            first.avg_grade_pct = 99
        summary = reads.queue_summary(db, tenant_id=f["tenant"])
        assert len(summary) == 12 and summary[0].entry.status == "acknowledged"
        assert summary[0].entry.supporting_facts == ["arbitrary history"]
        assert (
            next(
                row for row in summary if row.entry.status == "investigating"
            ).cohort_name
            == ""
        )
    with scoped(f["app"], f["other"]) as db:
        assert db.execute(
            text(
                "SELECT rolsuper,rolbypassrls FROM pg_roles WHERE rolname=current_user"
            )
        ).one() == (False, False)
        assert (
            reads.engagement_snapshot(db, tenant_id=f["tenant"], since=SINCE).enrollees
            == 0
        )
        assert reads.top_movers(db, tenant_id=f["tenant"], since=SINCE, limit=5) == ()
        counts = reads.activity_window_counts(db, tenant_id=f["tenant"], since=SINCE)
        assert (counts.new_enrollments, counts.submissions, counts.completions) == (
            0,
            0,
            0,
        )
        assert reads.attention_list(db, tenant_id=f["tenant"], limit=8).open_total == 0
        assert reads.queue_summary(db, tenant_id=f["tenant"]) == ()
        with pytest.raises(ValueError, match="^cohort not found$"):
            reads.cohort_roster_progress(
                db, tenant_id=f["tenant"], cohort_id=f["cohort"], now=NOW
            )


@pytest.mark.parametrize("report_database", ["historical-person"], indirect=True)
def test_engagement_distinct_students_ever_vs_window_and_no_identity_activity_filter(
    report_database,
):
    f = report_database
    with scoped(f["app"], f["tenant"]) as db:
        fact = reads.engagement_snapshot(db, tenant_id=f["tenant"], since=SINCE)
        assert (
            fact.enrollees,
            fact.ever_active,
            fact.active_in_window,
            fact.never_started,
        ) == (2, 1, 0, 1)
        assert db.get(Party, f["person"]).is_active is False


def test_top_movers_all_window_score_rows_not_best_and_uuid_tie_order(report_database):
    f = report_database
    with scoped(f["app"], f["tenant"]) as db:
        submission = db.scalar(
            select(Submission)
            .where(Submission.person_id == f["person"])
            .order_by(Submission.created_at, Submission.id)
        )
        db.add(
            Score(
                tenant_id=f["tenant"],
                submission_id=submission.id,
                score=1,
                max_score=1,
                fraction=1,
                passed=True,
                per_item=[],
                created_at=SINCE,
            )
        )
        db.add(
            Score(
                tenant_id=f["tenant"],
                submission_id=submission.id,
                score=0,
                max_score=1,
                fraction=0,
                passed=False,
                per_item=[],
                created_at=SINCE - timedelta(microseconds=1),
            )
        )
        add_event(db, f["tenant"], f["second"])
        db.flush()
        rows = reads.top_movers(db, tenant_id=f["tenant"], since=SINCE, limit=5)
        assert [row.person.id for row in rows] == sorted([f["person"], f["second"]])
        first = next(row for row in rows if row.person.id == f["person"])
        assert first.avg_score == pytest.approx(2 / 3) and first.submissions == 1
        assert reads.top_movers(db, tenant_id=f["tenant"], since=SINCE, limit=0) == ()


def test_top_mover_real_delete_after_ranking_causes_underfill_without_refill(
    report_database,
):
    f = report_database
    seen = []

    def remove_ranked_person(conn, cursor, statement, parameters, context, executemany):
        if seen or "GROUP BY mod_learning.learning_events.person_id" not in statement:
            return
        seen.append(statement)
        with f["admin"].begin() as admin_conn:
            _scope(admin_conn, f["tenant"])
            assert admin_conn.scalar(text("SELECT pg_backend_pid()")) != read_backend
            admin_conn.execute(
                delete(Party).where(
                    Party.id == f["person"], Party.tenant_id == f["tenant"]
                )
            )

    event.listen(f["app"], "after_cursor_execute", remove_ranked_person)
    try:
        with scoped(f["app"], f["tenant"]) as db:
            assert db.scalar(text("SHOW transaction_isolation")) == "read committed"
            read_backend = db.scalar(text("SELECT pg_backend_pid()"))
            assert (
                reads.top_movers(db, tenant_id=f["tenant"], since=SINCE, limit=1) == ()
            )
            assert len(seen) == 1
            assert db.get(Party, f["second"]) is not None
            assert (
                db.scalar(
                    select(func.count())
                    .select_from(LearningEvent)
                    .where(LearningEvent.person_id == f["second"])
                )
                == 2
            )
    finally:
        event.remove(f["app"], "after_cursor_execute", remove_ranked_person)


def test_empty_course_cohort_uuid_sentinel_collision_preserved(report_database):
    f = report_database
    with scoped(f["app"], f["tenant"]) as db:
        empty = Cohort(tenant_id=f["tenant"], name="Empty", discipline="engineering")
        db.add(empty)
        db.flush()
        db.add(
            Course(
                id=empty.id,
                tenant_id=f["tenant"],
                slug=uuid4().hex,
                title="UUID collision",
                discipline="engineering",
                source_ref="source",
                status="published",
                listed=False,
            )
        )
        db.flush()
        db.add_all(
            [
                Enrollment(
                    tenant_id=f["tenant"], cohort_id=empty.id, person_id=f["person"]
                ),
                CourseCompletion(
                    tenant_id=f["tenant"],
                    person_id=f["person"],
                    course_id=empty.id,
                    pct=0.625,
                ),
            ]
        )
        db.flush()
        row = reads.cohort_roster_progress(
            db, tenant_id=f["tenant"], cohort_id=empty.id, now=NOW
        ).rows[0]
        assert (
            row.courses == 0 and row.avg_completion_pct == 62 and row.avg_grade_pct == 0
        )


def test_roster_existing_completion_denominator_zero_grade_and_no_email_tie(
    report_database,
):
    f = report_database
    seen = []

    def observe(conn, cursor, statement, parameters, context, executemany):
        if "party_persons.last_name" in statement and "ORDER BY" in statement:
            seen.append(statement.split("ORDER BY", 1)[1])

    with scoped(f["app"], f["tenant"]) as db:
        db.get(PartyPerson, f["second"]).first_name = db.get(
            PartyPerson, f["person"]
        ).first_name
        db.get(PartyPerson, f["second"]).last_name = "A"
        db.get(Party, f["person"]).email = "z@example.org"
        db.get(Party, f["second"]).email = "a@example.org"
        # Remove the 100% attempted course; the true graded 0 remains in average.
        db.execute(
            delete(Score).where(
                Score.submission_id.in_(
                    select(Submission.id).where(
                        Submission.activity_id == f["activities"][1]
                    )
                )
            )
        )
        event.listen(f["app"], "before_cursor_execute", observe)
        try:
            rows = reads.cohort_roster_progress(
                db, tenant_id=f["tenant"], cohort_id=f["cohort"], now=NOW
            ).rows
        finally:
            event.remove(f["app"], "before_cursor_execute", observe)
        row = next(r for r in rows if r.person.id == f["person"])
        assert row.avg_completion_pct == 75 and row.avg_grade_pct == 0
        assert seen and all("email" not in clause for clause in seen)
        assert [r.person.id for r in rows] == [
            r[0]
            for r in db.execute(
                select(Party.id)
                .join(PartyPerson, PartyPerson.party_id == Party.id)
                .join(
                    Enrollment,
                    (Enrollment.person_id == Party.id)
                    & (Enrollment.tenant_id == Party.tenant_id),
                )
                .where(
                    Enrollment.tenant_id == f["tenant"],
                    Enrollment.cohort_id == f["cohort"],
                    Enrollment.role_in_cohort == "student",
                    Enrollment.status == "active",
                )
                .order_by(PartyPerson.last_name, PartyPerson.first_name)
            ).all()
        ]


def test_window_counts_rows_include_roles_statuses_null_and_inclusive_timestamp_edges(
    report_database,
):
    f = report_database
    with scoped(f["app"], f["tenant"]) as db:
        db.execute(Enrollment.__table__.update().values(created_at=SINCE))
        db.execute(
            Submission.__table__.update().values(
                created_at=SINCE - timedelta(microseconds=1)
            )
        )
        completion = db.scalar(select(CourseCompletion))
        completion.status = "not_completed"
        completion.completed_at = SINCE
        fact = reads.activity_window_counts(db, tenant_id=f["tenant"], since=SINCE)
        assert (fact.new_enrollments, fact.submissions, fact.completions) == (5, 0, 1)
        completion.completed_at = None
        assert (
            reads.activity_window_counts(
                db, tenant_id=f["tenant"], since=SINCE
            ).completions
            == 0
        )


def test_attention_aggregate_before_limit_and_queue_global_one_query_autoflush(
    report_database,
):
    f = report_database
    seen = []

    def observe(conn, cursor, statement, parameters, context, executemany):
        if statement.lstrip().startswith("SELECT"):
            seen.append(statement)

    with scoped(f["app"], f["tenant"]) as db:
        caller_cohort = db.get(Cohort, f["cohort"])
        caller_cohort.name = "Caller autoflush"
        root = db.get_transaction()
        event.listen(f["app"], "before_cursor_execute", observe)
        try:
            rows = reads.queue_summary(db, tenant_id=f["tenant"])
        finally:
            event.remove(f["app"], "before_cursor_execute", observe)
        assert len(seen) == 1 and "ORDER BY CASE" in seen[0]
        assert rows[0].entry.status == "acknowledged" and len(rows) == 12
        assert not db.is_modified(caller_cohort) and db.get_transaction() is root
        attention = reads.attention_list(db, tenant_id=f["tenant"], limit=1)
        assert (
            attention.open_total == 10
            and len(attention.by_reason) == 10
            and len(attention.top) == 1
        )
        assert attention.top[0].reason == "inactivity" and attention.never_started == 1
        assert attention.top[0].cohort_name == "Caller autoflush"


@pytest.mark.parametrize("report_database", ["never-active-second"], indirect=True)
def test_roster_never_active_nulls_and_python_half_even_rounding(report_database):
    f = report_database
    with scoped(f["app"], f["tenant"]) as db:
        for activity, fraction in zip(f["activities"][:2], (0.20, 0.21), strict=True):
            db.execute(
                Score.__table__.update()
                .where(
                    Score.submission_id.in_(
                        select(Submission.id).where(Submission.activity_id == activity)
                    )
                )
                .values(fraction=fraction)
            )
        db.scalar(select(CourseCompletion)).pct = 0.625
        rows = reads.cohort_roster_progress(
            db, tenant_id=f["tenant"], cohort_id=f["cohort"], now=NOW
        ).rows
        person = next(row for row in rows if row.person.id == f["person"])
        second = next(row for row in rows if row.person.id == f["second"])
        assert person.avg_grade_pct == 20 and person.avg_completion_pct == 62
        assert second.last_activity_at is None and second.days_inactive is None
        assert second.avg_grade_pct == second.avg_completion_pct == 0


def test_configured_report_caps_keep_full_attention_counts(report_database):
    f = report_database
    with scoped(f["app"], f["tenant"]) as db:
        root = db.get_transaction()
        movers = reads.top_movers(db, tenant_id=f["tenant"], since=SINCE, limit=1)
        assert [r.person.id for r in movers] == [f["person"]]
        attention = reads.attention_list(db, tenant_id=f["tenant"], limit=2)
        assert len(attention.top) == 2
        assert attention.open_total == 10
        assert sum(row.count for row in attention.by_reason) == 10
        assert db.get_transaction() is root


@pytest.mark.parametrize("mutation", ["delete", "update"])
def test_report_event_history_remains_append_only_for_app_user(
    report_database, mutation
):
    f = report_database
    with scoped(f["app"], f["tenant"]) as db:
        root = db.get_transaction()
        predicate = LearningEvent.person_id == f["second"]
        statement = (
            delete(LearningEvent).where(predicate)
            if mutation == "delete"
            else LearningEvent.__table__.update()
            .where(predicate)
            .values(occurred_at=SINCE)
        )
        with pytest.raises(ProgrammingError) as refused, db.begin_nested():
            db.execute(statement)
        assert refused.value.orig.sqlstate == "42501"
        assert db.get_transaction() is root and root.is_active
        assert (
            db.scalar(select(func.count()).select_from(LearningEvent).where(predicate))
            == 2
        )
