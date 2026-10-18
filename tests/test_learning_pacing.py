"""Hosted PostgreSQL pacing canaries using real migrated, non-bypass actors.

The real Learning lineage must include the delivery migration. No metadata
creation, stamping or catalog substitute is used here.
"""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from dotmac_kernel.models import Party
from dotmac_learning import pacing
from dotmac_learning.delivery_models import OfferingActivity
from dotmac_learning.models import Activity
from dotmac_learning.pacing_contracts import PacingFact, PacingForbidden
from dotmac_learning.participation_models import Cohort, CourseOffering, Enrollment
from sqlalchemy import event, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from tests import test_learning_results as result_tests
from tests.test_learning_catalog import _course, _scope
from tests.test_learning_results import scoped

learning_database = result_tests.learning_database
result_database = result_tests.result_database
CLOCK = datetime(2001, 1, 1, tzinfo=UTC)


def _offering(db, tenant, person, course, activity, **override):
    cohort = Cohort(tenant_id=tenant, name="Pacing", discipline="engineering")
    db.add(cohort)
    db.flush()
    enrollment = Enrollment(
        tenant_id=tenant,
        cohort_id=cohort.id,
        person_id=person,
        role_in_cohort="instructor",
        status="active",
    )
    offering = CourseOffering(
        tenant_id=tenant,
        cohort_id=cohort.id,
        course_id=course,
        status="active",
        starts_at=CLOCK + timedelta(days=10),
        ends_at=CLOCK - timedelta(days=10),
    )
    db.add_all([enrollment, offering])
    db.flush()
    row = OfferingActivity(
        tenant_id=tenant, offering_id=offering.id, activity_id=activity, **override
    )
    db.add(row)
    return cohort, enrollment, offering, row


def _criteria(tenant, person, course, activity):
    return {
        "tenant_id": tenant,
        "person_id": person,
        "course_id": course,
        "activity_id": activity,
        "now": CLOCK,
    }


def test_actual_non_bypass_two_tenant_query_and_explicit_scope(result_database):
    _, app, tenant, person, course, activity, other, other_person, *_ = result_database
    with scoped(app, tenant) as db:
        actor = db.execute(
            text(
                "SELECT rolsuper, rolbypassrls FROM pg_roles "
                "WHERE rolname = current_user"
            )
        ).one()
        assert actor == (False, False)
        _offering(
            db, tenant, person, course, activity, release_at=CLOCK + timedelta(days=1)
        )
    with app.begin() as conn:
        _scope(conn, other)
        other_course = _course(conn, other)
    with scoped(app, other) as db:
        other_activity = Activity(
            tenant_id=other, course_id=other_course, title="Other", type="lab"
        )
        db.add(other_activity)
        db.flush()
        _offering(db, other, other_person, other_course, other_activity.id)
        assert pacing.activity_pacing(
            db, **_criteria(other, other_person, other_course, other_activity.id)
        ) == PacingFact(True, True, True)
        # Foreign tenant and complete foreign UUIDs cannot bypass RLS.
        assert pacing.activity_pacing(
            db, **_criteria(tenant, person, course, activity)
        ) == PacingFact(True, True, False)
        assert (
            db.scalars(
                select(OfferingActivity).where(OfferingActivity.tenant_id == tenant)
            ).all()
            == []
        )
    with scoped(app, tenant) as db:
        assert pacing.activity_pacing(
            db, **_criteria(tenant, person, course, activity)
        ) == PacingFact(False, False, True)
        assert pacing.activity_pacing(
            db, **_criteria(other, person, course, activity)
        ) == PacingFact(True, True, False)
        for changed in ("person_id", "course_id", "activity_id"):
            criteria = _criteria(tenant, person, course, activity)
            criteria[changed] = uuid4()
            assert pacing.activity_pacing(db, **criteria) == PacingFact(
                True, True, False
            )


def test_any_active_role_no_identity_or_cohort_or_window_filter_and_autoflush(
    result_database,
):
    _, app, tenant, person, course, activity, *_ = result_database
    with scoped(app, tenant) as db:
        cohort, enrollment, offering, row = _offering(
            db, tenant, person, course, activity, release_at=CLOCK + timedelta(days=1)
        )
        cohort.status = "inactive"
        db.scalar(select(Party).where(Party.id == person)).is_active = False
        enrollment.role_in_cohort = "custom-source-role"
        root = db.get_transaction()
        flushed = []

        def before_flush(session, context, instances):
            flushed.append(session)

        event.listen(db, "before_flush", before_flush)
        try:
            assert pacing.activity_pacing(
                db, **_criteria(tenant, person, course, activity)
            ) == PacingFact(False, False, True)
            assert flushed == [db]
            assert row.id is not None
            assert db.get_transaction() is root
            # Dirty override values are flushed by the ordinary ORM query.
            row.release_at = CLOCK
            row.due_at = CLOCK
            assert pacing.activity_pacing(
                db, **_criteria(tenant, person, course, activity)
            ) == PacingFact(True, True, True)
        finally:
            event.remove(db, "before_flush", before_flush)


@pytest.mark.parametrize("excluded", ["offering", "enrollment"])
def test_inactive_offering_or_enrollment_excluded(result_database, excluded):
    _, app, tenant, person, course, activity, *_ = result_database
    with scoped(app, tenant) as db:
        _, enrollment, offering, _ = _offering(
            db, tenant, person, course, activity, release_at=CLOCK + timedelta(days=1)
        )
        (offering if excluded == "offering" else enrollment).status = "inactive"
        assert pacing.activity_pacing(
            db, **_criteria(tenant, person, course, activity)
        ) == PacingFact(True, True, False)


def test_source_join_uses_offering_course_not_activity_course_and_permissive_or(
    result_database,
):
    _, app, tenant, person, course, activity, *_ = result_database
    with app.begin() as conn:
        _scope(conn, tenant)
        different_course = _course(conn, tenant)
    with scoped(app, tenant) as db:
        # The source joins offering/activity UUIDs, not Activity.course_id.
        _offering(
            db,
            tenant,
            person,
            different_course,
            activity,
            release_at=CLOCK + timedelta(days=1),
        )
        criteria = _criteria(tenant, person, different_course, activity)
        assert pacing.activity_pacing(db, **criteria) == PacingFact(False, False, True)
        with pytest.raises(PacingForbidden, match="^Activity not yet released$"):
            pacing.require_activity_readable(db, **criteria)
        with pytest.raises(PacingForbidden) as caught:
            pacing.require_activity_submittable(db, **criteria)
        assert (
            str(caught.value)
            == "Activity is not open for submission (not released or past due)"
        )
        _, _, _, overdue = _offering(
            db,
            tenant,
            person,
            different_course,
            activity,
            due_at=CLOCK - timedelta(microseconds=1),
        )
        assert pacing.activity_pacing(db, **criteria) == PacingFact(True, False, True)
        assert pacing.require_activity_readable(db, **criteria) is None
        overdue.due_at = CLOCK
        overdue.release_at = CLOCK
        assert pacing.activity_pacing(db, **criteria) == PacingFact(True, True, True)
        assert pacing.require_activity_submittable(db, **criteria) is None
        overdue.release_at = overdue.due_at = None
        fact = pacing.activity_pacing(db, **criteria)
    assert fact == PacingFact(True, True, True)
    assert not hasattr(fact, "_sa_instance_state")


def test_read_autobegin_allowed_and_source_autoflush_failure_propagates(
    result_database,
):
    _, app, tenant, person, course, activity, *_ = result_database
    with Session(bind=app) as db:
        assert db.get_transaction() is None
        # No writer-only active transaction guard; source reads allow autobegin.
        assert pacing.activity_pacing(
            db, **_criteria(tenant, person, course, activity)
        ) == PacingFact(True, True, False)
        assert db.get_transaction() is not None
    with scoped(app, tenant) as db:
        db.add(
            OfferingActivity(
                tenant_id=tenant, offering_id=uuid4(), activity_id=activity
            )
        )
        root = db.get_transaction()
        with pytest.raises(IntegrityError):
            pacing.activity_pacing(db, **_criteria(tenant, person, course, activity))
        assert not root.is_active
        db.rollback()  # Caller, rather than the read owner, restores the Session.
