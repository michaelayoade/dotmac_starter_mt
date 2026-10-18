"""Hosted-only real migration support selection and non-bypass RLS canaries."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from dotmac_kernel.models import Party
from dotmac_learning import support_reads as reads
from dotmac_learning.curriculum_models import Chapter
from dotmac_learning.models import CourseCompletion, Score, Submission
from dotmac_learning.participation_contracts import ParticipationNotFound
from dotmac_learning.participation_models import Cohort, CourseOffering, Enrollment
from dotmac_learning.references import (
    ActivityWrite,
    CourseWrite,
    create_activity,
    create_course,
)
from dotmac_learning.support_contracts import SearchAuthority
from sqlalchemy import event as sa_event
from sqlalchemy import select, text

from tests import test_learning_results as learning_results
from tests.test_learning_catalog import _person
from tests.test_learning_results import scoped

learning_database = learning_results.learning_database
result_database = learning_results.result_database
NOW = datetime(2026, 10, 17, 12, tzinfo=UTC)


def course(db, tenant, title, *, status="published"):
    cid = uuid4()
    create_course(
        db,
        CourseWrite(
            id=cid,
            tenant_id=tenant,
            slug=cid.hex,
            title=title,
            discipline="test",
            source_ref="test",
            status=status,
            listed=False,
        ),
    )
    return cid


def chapter(db, tenant, cid, number, title, body):
    db.add(
        Chapter(
            tenant_id=tenant,
            course_id=cid,
            number=number,
            order_index=number,
            title=title,
            body_md="",
            body_html=body,
            part="",
            source_hash="test",
        )
    )


def score(db, tenant, person, activity, stamp):
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
            score=1,
            max_score=1,
            fraction=1,
            passed=True,
            source="auto",
            per_item=[],
            created_at=stamp,
        )
    )


@pytest.fixture
def support_data(result_database):
    admin, app, tenant, person, cid, aid, *_ = result_database
    with admin.begin() as conn:
        instructor, waitlisted = _person(conn, tenant), _person(conn, tenant)
        conn.execute(
            Party.__table__.update().where(Party.id == person).values(is_active=False)
        )
    with scoped(app, tenant) as db:
        cohort, other_cohort = (
            Cohort(tenant_id=tenant, name="A", discipline="test"),
            Cohort(tenant_id=tenant, name="Dropped", discipline="test"),
        )
        db.add_all([cohort, other_cohort])
        db.flush()
        db.add_all(
            [
                Enrollment(tenant_id=tenant, cohort_id=cohort.id, person_id=person),
                Enrollment(
                    tenant_id=tenant,
                    cohort_id=cohort.id,
                    person_id=instructor,
                    role_in_cohort="instructor",
                ),
                Enrollment(
                    tenant_id=tenant,
                    cohort_id=cohort.id,
                    person_id=waitlisted,
                    status="waitlisted",
                ),
                Enrollment(
                    tenant_id=tenant,
                    cohort_id=other_cohort.id,
                    person_id=person,
                    status="dropped",
                ),
            ]
        )
        second = course(db, tenant, "Second")
        unrelated = course(db, tenant, "Needle Hidden", status="draft")
        behind = course(db, tenant, "Behind")
        db.add_all(
            [
                CourseOffering(
                    tenant_id=tenant,
                    cohort_id=cohort.id,
                    course_id=cid,
                    ends_at=NOW - timedelta(seconds=1),
                ),
                CourseOffering(tenant_id=tenant, cohort_id=cohort.id, course_id=second),
                CourseOffering(
                    tenant_id=tenant,
                    cohort_id=other_cohort.id,
                    course_id=behind,
                    starts_at=NOW - timedelta(days=1),
                    ends_at=NOW + timedelta(days=1),
                ),
            ]
        )
        db.add(
            CourseCompletion(
                tenant_id=tenant,
                person_id=person,
                course_id=cid,
                pct=0.8,
                status="in_progress",
            )
        )
        score(db, tenant, person, aid, NOW - timedelta(days=15))
        other_activity = uuid4()
        create_activity(
            db,
            ActivityWrite(
                id=other_activity,
                tenant_id=tenant,
                course_id=unrelated,
                title="Recent unrelated",
                type="mcq_test",
                pass_threshold=0.0,
                weight=1.0,
                assessment_mode="graded",
                grading="auto",
            ),
        )
        score(db, tenant, person, other_activity, NOW)
        chapter(db, tenant, cid, 1, "Needle title", "<p>Needle raw HTML</p>")
        chapter(db, tenant, unrelated, 1, "Hidden", "<p>Needle secret</p>")
        db.flush()
        cohort_id, dropped_id = cohort.id, other_cohort.id
    return result_database, cohort_id, dropped_id, behind, unrelated


def test_populated_all_projections_no_account_filter_and_actual_two_tenant_rls(
    support_data,
):
    fixture, cohort, _, behind, hidden = support_data
    _, app, tenant, person, cid, _, other, other_person, *_ = fixture
    with scoped(app, tenant) as db:
        assert db.scalar(
            text(
                "SELECT NOT rolsuper AND NOT rolbypassrls "
                "FROM pg_roles WHERE rolname=current_user"
            )
        )
        root = db.get_transaction()
        dashboard = reads.cohort_overview(
            db,
            tenant_id=tenant,
            cohort_id=cohort,
            now=NOW,
            at_risk_pct=0.5,
            stale_days=14,
        )
        assert len(dashboard.rows) == 1
        row = dashboard.rows[0]
        assert row.person.id == person and row.completion_pct == 0.4
        assert row.last_activity_at == NOW - timedelta(days=15) and row.at_risk
        risks = reads.at_risk_for_person(
            db, tenant_id=tenant, person_id=person, now=NOW, behind_threshold=0.2
        )
        assert {(r.course_id, r.reason) for r in risks} == {
            (cid, "overdue"),
            (behind, "behind"),
        }
        learner = reads.search(
            db,
            tenant_id=tenant,
            person_id=person,
            q="  nEeDlE  ",
            authority=SearchAuthority(tenant, person, False),
            limit=20,
        )
        assert learner.term == "nEeDlE" and learner.courses == ()
        assert (
            len(learner.chapters) == 1 and learner.chapters[0].chapter.course_id == cid
        )
        assert learner.chapters[0].chapter.body_html == "<p>Needle raw HTML</p>"
        staff = reads.search(
            db,
            tenant_id=tenant,
            person_id=person,
            q="needle",
            authority=SearchAuthority(tenant, person, True),
            limit=20,
        )
        assert tuple(c.id for c in staff.courses) == (hidden,)
        assert len(staff.chapters) == 2
        assert db.get_transaction() is root
    assert not hasattr(dashboard.cohort, "_sa_instance_state")
    assert not hasattr(staff.chapters[0].chapter, "_sa_instance_state")
    with scoped(app, other) as db:
        assert db.scalar(
            text(
                "SELECT NOT rolsuper AND NOT rolbypassrls "
                "FROM pg_roles WHERE rolname=current_user"
            )
        )
        with pytest.raises(ParticipationNotFound):
            reads.cohort_overview(
                db,
                tenant_id=tenant,
                cohort_id=cohort,
                now=NOW,
                at_risk_pct=0.5,
                stale_days=14,
            )
        assert (
            reads.at_risk_for_person(
                db, tenant_id=tenant, person_id=person, now=NOW, behind_threshold=0.2
            )
            == ()
        )
        evil = reads.search(
            db,
            tenant_id=tenant,
            person_id=person,
            q="needle",
            authority=SearchAuthority(tenant, person, True),
            limit=20,
        )
        assert evil.courses == evil.chapters == ()
        own = Cohort(tenant_id=other, name="B", discipline="test")
        db.add(own)
        db.flush()
        db.add(Enrollment(tenant_id=other, cohort_id=own.id, person_id=other_person))
        assert (
            reads.cohort_overview(
                db,
                tenant_id=other,
                cohort_id=own.id,
                now=NOW,
                at_risk_pct=0.5,
                stale_days=14,
            )
            .rows[0]
            .at_risk
        )


def test_search_single_character_sql_wildcards_independent_caps_and_empty_input(
    result_database, monkeypatch
):
    _, app, tenant, person, *_ = result_database
    authority = SearchAuthority(tenant, person, True)
    with scoped(app, tenant) as db:
        for i in range(22):
            cid = course(db, tenant, f"Needle {i}", status="draft")
            chapter(db, tenant, cid, 1, f"Needle {i}", "<p>needle</p>")
        db.flush()
        statements = []

        def capture(conn, cursor, statement, parameters, context, executemany):
            statements.append(statement)

        sa_event.listen(app, "before_cursor_execute", capture)
        try:
            result = reads.search(
                db,
                tenant_id=tenant,
                person_id=person,
                q="n",
                authority=authority,
                limit=20,
            )
        finally:
            sa_event.remove(app, "before_cursor_execute", capture)
        assert len(statements) == 2
        assert all("ORDER BY" not in statement.upper() for statement in statements)
        assert len(result.courses) == len(result.chapters) == 20
        wildcards = reads.search(
            db, tenant_id=tenant, person_id=person, q="%", authority=authority, limit=20
        )
        assert len(wildcards.courses) == len(wildcards.chapters) == 20
        with monkeypatch.context() as patch:

            def no_sql(*args, **kwargs):
                pytest.fail("blank/foreign authority must not issue SQL")

            patch.setattr(db, "execute", no_sql)
            patch.setattr(db, "scalars", no_sql)
            blank = reads.search(
                db,
                tenant_id=tenant,
                person_id=person,
                q="  ",
                authority=authority,
                limit=20,
            )
            assert blank.courses == blank.chapters == ()
            with pytest.raises(ValueError):
                reads.search(
                    db,
                    tenant_id=tenant,
                    person_id=person,
                    q="needle",
                    authority=SearchAuthority(uuid4(), person, True),
                    limit=20,
                )


@pytest.mark.parametrize("mismatch", ["tenant", "person"])
def test_search_scope_refusal_precedes_real_sql_and_pending_autoflush(
    support_data, mismatch
):
    fixture, *_ = support_data
    _, app, tenant, person, *_ = fixture
    with scoped(app, tenant) as db:
        pending = Cohort(tenant_id=tenant, name="Unflushed", discipline="test")
        db.add(pending)
        connection = db.connection()
        statements = []

        def observed(conn, cursor, statement, parameters, context, executemany):
            statements.append(statement)

        sa_event.listen(connection, "before_cursor_execute", observed)
        try:
            authority = SearchAuthority(
                uuid4() if mismatch == "tenant" else tenant,
                uuid4() if mismatch == "person" else person,
                True,
            )
            with pytest.raises(ValueError):
                reads.search(
                    db,
                    tenant_id=tenant,
                    person_id=person,
                    q="needle",
                    authority=authority,
                    limit=20,
                )
            assert statements == []
            assert pending in db.new
        finally:
            sa_event.remove(connection, "before_cursor_execute", observed)
            db.expunge(pending)


def test_at_risk_role_status_completed_and_active_offering_asymmetry(support_data):
    fixture, cohort, _, behind, _ = support_data
    _, app, tenant, person, cid, *_ = fixture
    with scoped(app, tenant) as db:
        instructor = db.scalar(
            select(Enrollment.person_id)
            .where(Enrollment.cohort_id == cohort)
            .where(Enrollment.role_in_cohort == "instructor")
        )
        waitlisted = db.scalar(
            select(Enrollment.person_id)
            .where(Enrollment.cohort_id == cohort)
            .where(Enrollment.status == "waitlisted")
        )
        assert (
            reads.at_risk_for_person(
                db,
                tenant_id=tenant,
                person_id=instructor,
                now=NOW,
                behind_threshold=0.2,
            )
            == ()
        )
        assert tuple(
            r.course_id
            for r in reads.at_risk_for_person(
                db,
                tenant_id=tenant,
                person_id=waitlisted,
                now=NOW,
                behind_threshold=0.2,
            )
        ) == (cid,)
        completion = db.scalar(
            select(CourseCompletion).where(CourseCompletion.person_id == person)
        )
        completion.status = "completed"
        assert tuple(
            r.course_id
            for r in reads.at_risk_for_person(
                db, tenant_id=tenant, person_id=person, now=NOW, behind_threshold=0.2
            )
        ) == (behind,)
        offering = db.scalar(
            select(CourseOffering).where(CourseOffering.course_id == behind)
        )
        offering.status = "inactive"
        assert (
            reads.at_risk_for_person(
                db, tenant_id=tenant, person_id=person, now=NOW, behind_threshold=0.2
            )
            == ()
        )


def test_configured_search_cap_and_dashboard_thresholds(support_data):
    fixture, cohort, *_ = support_data
    _, app, tenant, person, *_ = fixture
    with scoped(app, tenant) as db:
        root = db.get_transaction()
        result = reads.search(
            db,
            tenant_id=tenant,
            person_id=person,
            q="needle",
            authority=SearchAuthority(tenant, person, True),
            limit=1,
        )
        assert len(result.courses) <= 1 and len(result.chapters) == 1
        dashboard = reads.cohort_overview(
            db,
            tenant_id=tenant,
            cohort_id=cohort,
            now=NOW,
            at_risk_pct=0.3,
            stale_days=2,
        )
        assert dashboard.rows[0].completion_pct == 0.4
        assert not dashboard.rows[0].at_risk
        assert db.get_transaction() is root
