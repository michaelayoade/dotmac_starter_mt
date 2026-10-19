"""Hosted bulk policy privilege boundary and permitted sibling effects."""

from uuid import uuid4

import pytest
from dotmac_kernel.models import Party, PartyRoleGrant, Role
from dotmac_learning import instructor_reads, roster
from dotmac_learning.account_contracts import AccountPresenceSelection
from dotmac_learning.instructor_contracts import (
    InstructorAuthority,
    InstructorCourseForbidden,
)
from dotmac_learning.participation_contracts import (
    BulkEnrollCommand,
    BulkEnrollmentInitialState,
    RosterAuthority,
)
from dotmac_learning.participation_contracts import (
    ParticipationNotFound as NotFoundError,
)
from dotmac_learning.participation_models import CourseOffering, Enrollment
from sqlalchemy import select

from tests import test_learning_roster_commands as roster_tests
from tests.test_learning_catalog import _course, _person, _scope
from tests.test_learning_participation_catalog import _row, _track
from tests.test_learning_roster_commands import scoped

learning_database = roster_tests.learning_database
roster_database = roster_tests.roster_database


@pytest.fixture
def bulk_role_database(roster_database):
    admin, app, tenant, _, cohort, people, _, _ = roster_database
    with admin.begin() as conn:
        held = _person(conn, tenant)
        conn.execute(
            Party.__table__.update().where(Party.id == held).values(email="held@x")
        )
        role_id = uuid4()
        conn.execute(
            Role.__table__.insert().values(
                id=role_id, tenant_id=tenant, slug="instructor", name="Instructor"
            )
        )
        conn.execute(
            PartyRoleGrant.__table__.insert().values(
                id=uuid4(), tenant_id=tenant, party_id=people[0], role_id=role_id
            )
        )
    with app.begin() as conn:
        _scope(conn, tenant)
        track = _track(conn, tenant)
        courses = (_course(conn, tenant), _course(conn, tenant))
        _row(conn, "cohort_tracks", tenant, cohort_id=cohort, track_id=track)
        for course in courses:
            _row(conn, "track_courses", tenant, track_id=track, course_id=course)
        _row(
            conn,
            "course_offerings",
            tenant,
            cohort_id=cohort,
            course_id=courses[0],
            status="dropped",
        )
    return app, tenant, cohort, people, held, track, courses


def _bulk(db, data, emails, *, admin=False, track=None, role="instructor"):
    _, tenant, cohort, _, _, _, _ = data
    return roster.bulk_enroll(
        db,
        BulkEnrollCommand(
            tenant_id=tenant, cohort_id=cohort, emails=emails, track_id=track
        ),
        authority=RosterAuthority(tenant, admin),
        accounts=AccountPresenceSelection(
            tenant, select(Party.id).where(Party.tenant_id == tenant)
        ),
        initial_state=BulkEnrollmentInitialState(role, "active"),
    )


def _offerings(db, cohort):
    return tuple(
        db.execute(
            select(CourseOffering.id, CourseOffering.course_id, CourseOffering.status)
            .where(CourseOffering.cohort_id == cohort)
            .order_by(CourseOffering.id)
        ).all()
    )


def test_global_instructor_cannot_self_enroll_into_course_authoring(bulk_role_database):
    app, tenant, cohort, people, _, track, courses = bulk_role_database
    with scoped(app, tenant) as db:
        # Genuine Kernel global instructor grant, with no admin grant.
        grants = db.scalars(
            select(Role.slug)
            .join(PartyRoleGrant, PartyRoleGrant.role_id == Role.id)
            .where(
                PartyRoleGrant.tenant_id == tenant, PartyRoleGrant.party_id == people[0]
            )
        ).all()
        assert grants == ["instructor"]
        before = _offerings(db, cohort)
        result = _bulk(
            db, bulk_role_database, (" PEER@X ", "peer@x", "missing@x"), track=track
        )
        assert result.enrolled == result.reactivated == result.already_active == ()
        assert result.admin_required == ("peer@x",)
        assert result.not_found == ("missing@x",)
        assert (
            db.scalar(select(Enrollment).where(Enrollment.person_id == people[0]))
            is None
        )
        assert _offerings(db, cohort) == before
        assert (
            instructor_reads.assigned_course_ids(
                db, tenant_id=tenant, instructor_id=people[0]
            )
            == frozenset()
        )
        authority = InstructorAuthority(tenant, people[0], False)
        assert (
            instructor_reads.authorable_courses(
                db, tenant_id=tenant, person_id=people[0], authority=authority
            )
            == ()
        )
        with pytest.raises(InstructorCourseForbidden):
            instructor_reads.authorable_course(
                db,
                tenant_id=tenant,
                person_id=people[0],
                course_id=courses[0],
                authority=authority,
            )
        allowed = _bulk(db, bulk_role_database, ("peer@x",), admin=True, track=track)
        assert allowed.enrolled == ("peer@x",)
        row = db.scalar(select(Enrollment).where(Enrollment.person_id == people[0]))
        assert (row.role_in_cohort, row.status) == ("instructor", "active")
        assert instructor_reads.assigned_course_ids(
            db, tenant_id=tenant, instructor_id=people[0]
        ) == frozenset(courses)
        assert (
            instructor_reads.authorable_course(
                db,
                tenant_id=tenant,
                person_id=people[0],
                course_id=courses[0],
                authority=authority,
            ).id
            == courses[0]
        )


def test_denied_new_and_inactive_nonstudent_only_batch_preserves_offerings(
    bulk_role_database,
):
    app, tenant, cohort, _, held, track, _ = bulk_role_database
    with scoped(app, tenant) as db:
        db.add(
            Enrollment(
                tenant_id=tenant,
                cohort_id=cohort,
                person_id=held,
                role_in_cohort="instructor",
                status="dropped",
            )
        )
        db.flush()
        before = _offerings(db, cohort)
        result = _bulk(
            db, bulk_role_database, ("held@x", "peer@x", "missing@x"), track=track
        )
        assert result.admin_required == ("held@x", "peer@x")
        assert result.not_found == ("missing@x",)
        assert result.enrolled == result.reactivated == result.already_active == ()
        assert _offerings(db, cohort) == before
        row = db.scalar(select(Enrollment).where(Enrollment.person_id == held))
        assert (row.status, row.track_id) == ("dropped", None)


@pytest.mark.parametrize("existing_status", ["dropped", "active"])
def test_mixed_batch_continues_student_and_ensures_same_track_offerings(
    bulk_role_database, existing_status
):
    app, tenant, cohort, people, _, track, courses = bulk_role_database
    with scoped(app, tenant) as db:
        db.add(
            Enrollment(
                tenant_id=tenant,
                cohort_id=cohort,
                person_id=people[1],
                track_id=track,
                role_in_cohort="student",
                status=existing_status,
            )
        )
        db.flush()
        result = _bulk(
            db,
            bulk_role_database,
            ("missing@x", " PEER@X ", "peer@x", " FRESH@X ", "fresh@x"),
            track=track,
        )
        assert result.enrolled == ()
        assert result.not_found == ("missing@x",)
        assert result.admin_required == ("peer@x",)
        assert result.reactivated == (
            ("fresh@x",) if existing_status == "dropped" else ()
        )
        assert result.already_active == (
            ("fresh@x",) if existing_status == "active" else ()
        )
        row = db.scalar(select(Enrollment).where(Enrollment.person_id == people[1]))
        assert (row.role_in_cohort, row.status, row.track_id) == (
            "student",
            "active",
            track,
        )
        assert {(r.course_id, r.status) for r in _offerings(db, cohort)} == {
            (course, "active") for course in courses
        }
        assert (
            db.scalar(select(Enrollment).where(Enrollment.person_id == people[0]))
            is None
        )


def test_invalid_track_refuses_before_email_lookup_without_offering_writes(
    bulk_role_database, monkeypatch
):
    app, tenant, cohort, _, _, _, _ = bulk_role_database

    def forbidden(*args, **kwargs):
        pytest.fail("invalid track reached a mutation or enrollment lookup")

    monkeypatch.setattr(roster.tracks, "ensure_track_offerings", forbidden)
    monkeypatch.setattr(roster, "_enrollment", forbidden)
    with scoped(app, tenant) as db:
        before = _offerings(db, cohort)
        with pytest.raises(NotFoundError, match="track not found for cohort"):
            _bulk(db, bulk_role_database, ("peer@x",), track=uuid4())
        assert _offerings(db, cohort) == before


def test_admin_can_create_opaque_configured_role(bulk_role_database):
    app, tenant, cohort, people, _, _, _ = bulk_role_database
    with scoped(app, tenant) as db:
        result = _bulk(
            db, bulk_role_database, ("peer@x",), admin=True, role="opaque-business"
        )
        assert result.enrolled == ("peer@x",)
        row = db.scalar(select(Enrollment).where(Enrollment.person_id == people[0]))
        assert (row.role_in_cohort, row.status) == ("opaque-business", "active")
