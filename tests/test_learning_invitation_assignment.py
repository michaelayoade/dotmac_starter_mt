"""Hosted PostgreSQL invitation-assignment parity; product keeps identity/auth."""

from contextlib import contextmanager
from uuid import uuid4

import pytest
from dotmac_learning import roster
from dotmac_learning.participation_contracts import (
    EnsureInvitationEnrollmentCommand,
    ParticipationBadRequest,
    ParticipationForbidden,
    RosterAuthority,
)
from dotmac_learning.participation_models import (
    CourseOffering,
    Enrollment,
    TrackCourse,
)
from dotmac_learning.result_contracts import TransactionChanged
from sqlalchemy import create_engine, event, select, text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session

from tests import test_learning_catalog as learning_catalog
from tests.test_learning_catalog import _course, _person, _scope, _tenant
from tests.test_learning_participation_catalog import _cohort, _row, _track

learning_database = learning_catalog.learning_database


@contextmanager
def scoped(app, tenant, *, autoflush=True):
    with Session(bind=app, autoflush=autoflush) as db, db.begin():
        _scope(db.connection(), tenant)
        yield db


@pytest.fixture
def assignment_database(learning_database):
    admin, app = (create_engine(url) for url in learning_database)
    tenant, other = uuid4(), uuid4()
    try:
        with admin.begin() as conn:
            _tenant(conn, tenant)
            _tenant(conn, other)
            people = tuple(_person(conn, tenant) for _ in range(4))
        with app.begin() as conn:
            _scope(conn, tenant)
            cohort = _cohort(conn, tenant)
            track = _track(conn, tenant)
            courses = tuple(_course(conn, tenant) for _ in range(3))
            _row(conn, "cohort_tracks", tenant, cohort_id=cohort, track_id=track)
            _row(conn, "track_courses", tenant, track_id=track, course_id=courses[0])
        yield app, tenant, other, cohort, track, courses, people
    finally:
        app.dispose()
        admin.dispose()


def command(data, **values):
    _, tenant, _, cohort, _, _, people = data
    return EnsureInvitationEnrollmentCommand(
        tenant_id=tenant,
        cohort_id=cohort,
        person_id=people[0],
        global_role="student",
        **values,
    )


def member(db, data, *, role="student", status="active", track=None):
    _, tenant, _, cohort, _, _, people = data
    row = Enrollment(
        tenant_id=tenant,
        cohort_id=cohort,
        person_id=people[0],
        role_in_cohort=role,
        status=status,
        track_id=track,
    )
    db.add(row)
    db.flush()
    return row


def test_invitation_assignment_restricted_actor_a_positive_b_negative(
    assignment_database,
):
    app, tenant, other, _, _, courses, _ = assignment_database
    with scoped(app, tenant) as db:
        assert db.scalar(text("SELECT current_user")) == "app_user"
        result = roster.ensure_invitation_enrollment(
            db,
            command(assignment_database, selected_course_ids=courses[:1]),
            authority=RosterAuthority(tenant, False),
        )
        assert result.activation is None and not result.preserved_instructor_role
    with scoped(app, other) as db:
        assert db.scalars(select(Enrollment)).all() == []
        assert db.scalars(select(CourseOffering)).all() == []
        assert (
            db.execute(
                Enrollment.__table__.update()
                .values(status="dropped")
                .returning(Enrollment.id)
            ).all()
            == []
        )
    with pytest.raises(DBAPIError) as refused, scoped(app, other) as db:
        # Matching command authority cannot bypass the actual database tenant.
        roster.ensure_invitation_enrollment(
            db, command(assignment_database), authority=RosterAuthority(tenant, False)
        )
        db.flush()
    assert refused.value.orig.sqlstate == "42501"
    with scoped(app, tenant) as db:
        row = db.scalar(select(Enrollment))
        assert row.tenant_id == tenant and row.status == "active"


def test_authority_mismatch_refuses_before_sql(assignment_database):
    app, tenant, other, *_ = assignment_database
    with scoped(app, tenant) as db:
        statements = []

        def observe(_conn, _cursor, sql, _parameters, _context, _many):
            statements.append(sql)

        conn = db.connection()
        event.listen(conn, "before_cursor_execute", observe)
        try:
            with pytest.raises(ParticipationForbidden):
                roster.ensure_invitation_enrollment(
                    db,
                    command(assignment_database),
                    authority=RosterAuthority(other, True),
                )
            assert statements == []
        finally:
            event.remove(conn, "before_cursor_execute", observe)


@pytest.mark.parametrize("admin", [False, True])
def test_existing_active_instructor_is_never_downgraded(assignment_database, admin):
    app, tenant, *_ = assignment_database
    with scoped(app, tenant) as db:
        row = member(db, assignment_database, role="instructor")
        result = roster.ensure_invitation_enrollment(
            db, command(assignment_database), authority=RosterAuthority(tenant, admin)
        )
        assert row.role_in_cohort == "instructor"
        assert result.preserved_instructor_role
        assert (result.activation.outcome, result.activation.previous_status) == (
            "already_active",
            "active",
        )


@pytest.mark.parametrize("status", ["dropped", "waitlisted"])
@pytest.mark.parametrize(
    "old_role,new_role", [("instructor", "student"), ("student", "instructor")]
)
def test_nonadmin_refusal_preserves_source_partial_role_write_and_outer_rollback(
    assignment_database,
    status,
    old_role,
    new_role,
):
    app, tenant, *_ = assignment_database
    with scoped(app, tenant) as db:
        member(db, assignment_database, role=old_role, status=status)
    with pytest.raises(ParticipationBadRequest), scoped(app, tenant) as db:
        row = db.scalar(select(Enrollment))
        try:
            roster.ensure_invitation_enrollment(
                db,
                command(assignment_database).model_copy(
                    update={"global_role": new_role}
                ),
                authority=RosterAuthority(tenant, False),
            )
        except ParticipationBadRequest:
            assert row.role_in_cohort == "instructor" and row.status == status
            assert db.get_transaction().is_active
            raise
    with scoped(app, tenant) as db:
        row = db.scalar(select(Enrollment))
        assert (row.role_in_cohort, row.status) == (old_role, status)


@pytest.mark.parametrize(
    "global_role,member_role",
    [
        ("admin", "instructor"),
        ("instructor", "instructor"),
        ("student", "student"),
        ("observer", "student"),
    ],
)
def test_new_role_mapping_stages_without_extra_flush(
    assignment_database, global_role, member_role
):
    app, tenant, *_ = assignment_database
    with scoped(app, tenant) as db:
        flushes = []

        def observe(_session, _context, _instances):
            flushes.append(True)

        event.listen(db, "before_flush", observe)
        root = db.get_transaction()
        try:
            result = roster.ensure_invitation_enrollment(
                db,
                command(assignment_database).model_copy(
                    update={"global_role": global_role}
                ),
                authority=RosterAuthority(tenant, False),
            )
            assert result.activation is None
            assert result.member_role == member_role
            assert flushes == [] and db.get_transaction() is root
            assert len(db.new) == 1
            assert next(iter(db.new)).role_in_cohort == member_role
        finally:
            event.remove(db, "before_flush", observe)


@pytest.mark.parametrize("status", ["active", "dropped", "waitlisted", "historic"])
def test_existing_student_promotion_and_canonical_activation(
    assignment_database, status
):
    app, tenant, *_ = assignment_database
    with scoped(app, tenant) as db:
        row = member(db, assignment_database, status=status)
        result = roster.ensure_invitation_enrollment(
            db,
            command(assignment_database).model_copy(update={"global_role": "admin"}),
            authority=RosterAuthority(tenant, True),
        )
        assert row.role_in_cohort == "instructor" and row.status == "active"
        assert not result.preserved_instructor_role
        assert result.activation.previous_status == status
        assert result.activation.outcome == (
            "already_active" if status == "active" else "reactivated"
        )


def test_omitted_track_is_preserved_and_selected_courses_reactivate_in_order(
    assignment_database,
):
    app, tenant, _, _, track, courses, _ = assignment_database
    with scoped(app, tenant) as db:
        row = member(db, assignment_database, track=track)
        selected, flushes = [], []

        def observe_query(execution):
            statement = execution.statement
            if statement.column_descriptions[0]["entity"] is CourseOffering:
                selected.append(statement.compile().params["course_id_1"])

        def observe_flush(_session, _context, _instances):
            staged = tuple(
                item.course_id for item in db.new if isinstance(item, CourseOffering)
            )
            if staged:
                flushes.append(staged)

        event.listen(db, "do_orm_execute", observe_query)
        event.listen(db, "before_flush", observe_flush)
        try:
            roster.ensure_invitation_enrollment(
                db,
                command(
                    assignment_database,
                    selected_course_ids=(courses[2], courses[1], courses[2]),
                ),
                authority=RosterAuthority(tenant, False),
            )
            assert selected == [courses[2], courses[1], courses[2]]
            assert flushes == [(courses[2],), (courses[1],)]
        finally:
            event.remove(db, "do_orm_execute", observe_query)
            event.remove(db, "before_flush", observe_flush)
        assert row.track_id == track
        # Source queries autoflush earlier staged work; the last repeated lookup
        # sees the earlier offering rather than staging a duplicate.
        offerings = db.scalars(select(CourseOffering)).all()
        assert {item.course_id for item in offerings} == {courses[1], courses[2]}
        offerings[0].status = "historic"
        roster.ensure_invitation_enrollment(
            db,
            command(assignment_database, selected_course_ids=(offerings[0].course_id,)),
            authority=RosterAuthority(tenant, False),
        )
        assert offerings[0].status == "active"


def test_track_owner_runs_before_selected_courses_on_same_session(
    assignment_database, monkeypatch
):
    app, tenant, _, _, track, courses, _ = assignment_database
    real_ensure = roster.tracks.ensure_track_offerings
    observed = []

    def ensure(db, **kwargs):
        row = db.scalar(select(Enrollment))
        assert row.track_id == track
        observed.append((db, kwargs))
        real_ensure(db, **kwargs)

    monkeypatch.setattr(roster.tracks, "ensure_track_offerings", ensure)
    with scoped(app, tenant) as db:
        root = db.get_transaction()
        roster.ensure_invitation_enrollment(
            db,
            command(
                assignment_database, track_id=track, selected_course_ids=courses[1:]
            ),
            authority=RosterAuthority(tenant, False),
        )
        assert observed[0][0] is db and db.get_transaction() is root
        assert db.scalar(select(TrackCourse.course_id)) == courses[0]
        assert {item.course_id for item in db.scalars(select(CourseOffering))} == set(
            courses
        )


def test_caller_autoflush_disabled_leaves_source_duplicate_work_staged(
    assignment_database,
):
    app, tenant, _, _, _, courses, _ = assignment_database
    with pytest.raises(IntegrityError), scoped(app, tenant, autoflush=False) as db:
        roster.ensure_invitation_enrollment(
            db,
            command(assignment_database, selected_course_ids=(courses[1], courses[1])),
            authority=RosterAuthority(tenant, False),
        )
        assert len(db.new) == 3  # enrollment and both source-order offerings
        # The source does not deduplicate or catch this; caller owns rollback.
        db.flush()
    with scoped(app, tenant) as db:
        assert db.scalars(select(Enrollment)).all() == []
        assert db.scalars(select(CourseOffering)).all() == []


def test_propagated_offering_failure_rolls_back_prior_source_writes(
    assignment_database,
):
    app, tenant, *_ = assignment_database
    with pytest.raises(IntegrityError), scoped(app, tenant) as db:
        roster.ensure_invitation_enrollment(
            db,
            command(assignment_database, selected_course_ids=(uuid4(), uuid4())),
            authority=RosterAuthority(tenant, False),
        )
    with scoped(app, tenant) as db:
        assert db.scalars(select(Enrollment)).all() == []
        assert db.scalars(select(CourseOffering)).all() == []


@pytest.mark.parametrize(
    "role,requested_role",
    [("student", "student"), ("instructor", "instructor"), ("instructor", "student")],
)
@pytest.mark.parametrize("status", ["dropped", "waitlisted"])
def test_reactivation_returns_source_observation_and_preserved_role(
    assignment_database, role, requested_role, status
):
    app, tenant, *_ = assignment_database
    with scoped(app, tenant) as db:
        row = member(db, assignment_database, role=role, status=status)
        result = roster.ensure_invitation_enrollment(
            db,
            command(assignment_database).model_copy(
                update={"global_role": requested_role}
            ),
            authority=RosterAuthority(tenant, role == "instructor"),
        )
        assert row.status == "active" and row.role_in_cohort == role
        assert result.activation.outcome == "reactivated"
        assert result.activation.previous_status == status
        assert result.preserved_instructor_role == (role != requested_role)


def test_existing_root_required_and_nested_boundary_preserved(assignment_database):
    app, tenant, *_ = assignment_database
    with Session(bind=app) as db:
        with pytest.raises(TransactionChanged):
            roster.ensure_invitation_enrollment(
                db,
                command(assignment_database),
                authority=RosterAuthority(tenant, True),
            )
        assert db.get_transaction() is None
    with pytest.raises(RuntimeError, match="caller rollback"):
        with scoped(app, tenant) as db, db.begin_nested():
            root, nested = db.get_transaction(), db.get_nested_transaction()
            roster.ensure_invitation_enrollment(
                db,
                command(assignment_database),
                authority=RosterAuthority(tenant, True),
            )
            assert (
                db.get_transaction() is root and db.get_nested_transaction() is nested
            )
            raise RuntimeError("caller rollback")
    with scoped(app, tenant) as db:
        assert db.scalars(select(Enrollment)).all() == []
