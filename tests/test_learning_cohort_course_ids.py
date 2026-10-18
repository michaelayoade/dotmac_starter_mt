"""Hosted-only PostgreSQL coverage for the public active-offering course read.

Uses genuine Kernel/learning migrations and an ordinary non-bypass caller Session.
Canonical offering uniqueness forbids duplicate tenant/cohort/course rows; the
reader's absence of deduplication is preserved by delegation to its source helper.
"""

from collections import Counter
from uuid import uuid4

import dotmac_learning as learning
import pytest
from dotmac_learning.models import Activity
from dotmac_learning.participation_models import Cohort, CourseOffering
from dotmac_learning.references import CourseWrite, create_course
from sqlalchemy import event, select, text
from sqlalchemy.orm import Session

from tests import test_learning_results as learning_results
from tests.test_learning_results import scoped

learning_database = learning_results.learning_database
result_database = learning_results.result_database


def _course(db, tenant, title):
    course = uuid4()
    create_course(
        db,
        CourseWrite(
            id=course,
            tenant_id=tenant,
            slug=course.hex,
            title=title,
            discipline="test",
            source_ref="test",
        ),
    )
    return course


@pytest.fixture
def offered_courses(result_database):
    _, app, tenant, _, course, _, other, *_ = result_database
    with scoped(app, tenant) as db:
        # Cohort status is deliberately inactive: only offering status matters.
        cohort = Cohort(
            tenant_id=tenant, name="Target", discipline="test", status="inactive"
        )
        elsewhere = Cohort(tenant_id=tenant, name="Elsewhere", discipline="test")
        db.add_all([cohort, elsewhere])
        db.flush()
        empty_course = _course(db, tenant, "No activities")
        inactive_course = _course(db, tenant, "Inactive offering")
        elsewhere_course = _course(db, tenant, "Other cohort")
        db.add_all(
            [
                CourseOffering(tenant_id=tenant, cohort_id=cohort.id, course_id=course),
                CourseOffering(
                    tenant_id=tenant, cohort_id=cohort.id, course_id=empty_course
                ),
                CourseOffering(
                    tenant_id=tenant,
                    cohort_id=cohort.id,
                    course_id=inactive_course,
                    status="inactive",
                ),
                CourseOffering(
                    tenant_id=tenant,
                    cohort_id=elsewhere.id,
                    course_id=elsewhere_course,
                ),
            ]
        )
        cohort_id = cohort.id
    with scoped(app, other) as db:
        other_cohort = Cohort(tenant_id=other, name="Other tenant", discipline="test")
        db.add(other_cohort)
        db.flush()
        other_course = _course(db, other, "Other tenant course")
        db.add(
            CourseOffering(
                tenant_id=other, cohort_id=other_cohort.id, course_id=other_course
            )
        )
        other_cohort_id = other_cohort.id
    return app, tenant, other, cohort_id, course, empty_course, other_cohort_id


def _assert_nonbypass(db):
    assert db.scalar(
        text(
            "SELECT NOT rolsuper AND NOT rolbypassrls "
            "FROM pg_roles WHERE rolname=current_user"
        )
    )


def test_public_read_matches_source_membership_in_ordinary_caller_session(
    offered_courses,
):
    app, tenant, _, cohort, course, empty_course, _ = offered_courses
    with scoped(app, tenant) as db:
        assert isinstance(db, Session)
        _assert_nonbypass(db)
        assert (
            db.scalar(select(Activity.id).where(Activity.course_id == empty_course))
            is None
        )
        root = db.get_transaction()
        conn = db.connection()
        statements = []

        def executed(connection, cursor, statement, parameters, context, executemany):
            statements.append(statement)

        event.listen(conn, "before_cursor_execute", executed)
        try:
            result = learning.cohort_course_ids(db, tenant_id=tenant, cohort_id=cohort)
        finally:
            event.remove(conn, "before_cursor_execute", executed)
        assert isinstance(result, list)
        assert Counter(result) == Counter([course, empty_course])
        assert len(statements) == 1
        assert statements[0].lstrip().startswith("SELECT ")
        assert "JOIN" not in statements[0].upper()
        assert "ORDER BY" not in statements[0].upper()
        assert "DISTINCT" not in statements[0].upper()
        assert db.get_transaction() is root
        assert db.connection() is conn
        assert not db.new and not db.dirty and not db.deleted
        source = list(
            db.scalars(
                select(CourseOffering.course_id)
                .where(CourseOffering.tenant_id == tenant)
                .where(CourseOffering.cohort_id == cohort)
                .where(CourseOffering.status == "active")
            ).all()
        )
        assert Counter(result) == Counter(source)
        assert learning.cohort_course_ids(db, tenant_id=tenant, cohort_id=uuid4()) == []


def test_public_read_keeps_explicit_tenant_cohort_and_nonbypass_rls(offered_courses):
    app, tenant, other, cohort, _, _, other_cohort = offered_courses
    with scoped(app, tenant) as db:
        _assert_nonbypass(db)
        assert learning.cohort_course_ids(db, tenant_id=other, cohort_id=cohort) == []
        assert (
            learning.cohort_course_ids(db, tenant_id=tenant, cohort_id=other_cohort)
            == []
        )
        assert (
            learning.cohort_course_ids(db, tenant_id=other, cohort_id=other_cohort)
            == []
        )
    with scoped(app, other) as db:
        _assert_nonbypass(db)
        assert learning.cohort_course_ids(db, tenant_id=tenant, cohort_id=cohort) == []
        assert (
            len(learning.cohort_course_ids(db, tenant_id=other, cohort_id=other_cohort))
            == 1
        )
