"""Hosted canonical admission enrollment intent with real non-bypass fixtures."""

import inspect
from dataclasses import FrozenInstanceError, asdict
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from dotmac_kernel.audit import AuditEvent
from dotmac_kernel.models import Party
from dotmac_learning import roster
from dotmac_learning.models import Course
from dotmac_learning.participation_contracts import (
    EnsureAdmissionEnrollmentCommand,
    enrollment_fact,
)
from dotmac_learning.participation_models import (
    Cohort,
    CohortTrack,
    CourseOffering,
    Enrollment,
    Track,
    TrackCourse,
)
from dotmac_learning.result_contracts import TransactionChanged
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session

from tests import test_learning_roster_commands as roster_tests
from tests.test_learning_roster_commands import scoped

learning_database = roster_tests.learning_database
roster_database = roster_tests.roster_database
NOW = datetime(2026, 8, 1, tzinfo=UTC)


def command(tenant, cohort, person, track=None):
    return EnsureAdmissionEnrollmentCommand(
        tenant_id=tenant, cohort_id=cohort, person_id=person, track_id=track
    )


def tracks(db, tenant, cohort):
    ids = []
    course = Course(
        tenant_id=tenant,
        slug=uuid4().hex,
        title="Admission course",
        discipline="engineering",
        source_ref="source",
    )
    db.add(course)
    db.flush()
    for _ in range(2):
        track = Track(tenant_id=tenant, slug=uuid4().hex, name="Admission track")
        db.add(track)
        db.flush()
        db.add_all(
            [
                CohortTrack(tenant_id=tenant, cohort_id=cohort, track_id=track.id),
                TrackCourse(tenant_id=tenant, track_id=track.id, course_id=course.id),
            ]
        )
        ids.append(track.id)
    db.flush()
    return tuple(ids)


def flush_calls(db, monkeypatch):
    calls = []
    original = db.flush

    def spy(*args, **kwargs):
        calls.append(inspect.currentframe().f_back.f_code.co_name)
        return original(*args, **kwargs)

    monkeypatch.setattr(db, "flush", spy)
    return calls


@pytest.mark.parametrize("person_is_active", [True, False])
def test_new_active_student_genuine_person_and_no_offerings_audit_or_boundary_change(
    roster_database,
    monkeypatch,
    person_is_active,
):
    _, app, tenant, _, cohort, people, _, _ = roster_database
    with scoped(app, tenant) as db, db.begin_nested():
        track, _ = tracks(db, tenant, cohort)
        db.get(Party, people[0]).is_active = person_is_active
        db.flush()
        root, nested = db.get_transaction(), db.get_nested_transaction()
        calls = flush_calls(db, monkeypatch)
        fact = roster.ensure_admission_enrollment(
            db, command(tenant, cohort, people[0], track)
        )
        assert (fact.tenant_id, fact.cohort_id, fact.person_id, fact.track_id) == (
            tenant,
            cohort,
            people[0],
            track,
        )
        assert (fact.role_in_cohort, fact.status, fact.audience) == (
            "student",
            "active",
            None,
        )
        assert calls.count("ensure_admission_enrollment") == 1
        assert db.get_transaction() is root and db.get_nested_transaction() is nested
        assert db.scalar(select(func.count()).select_from(CourseOffering)) == 0
        assert db.scalar(select(func.count()).select_from(AuditEvent)) == 0
        with pytest.raises(FrozenInstanceError):
            fact.status = "changed"
    with scoped(app, tenant) as db:
        stored = db.get(Enrollment, fact.id)
        assert stored.status == "active" and stored.track_id == track


@pytest.mark.parametrize("status", ["active", "waitlisted", "dropped", "inactive"])
@pytest.mark.parametrize("role", ["student", "instructor"])
@pytest.mark.parametrize("target", ["different", "null", "from_null"])
def test_existing_only_changes_track_preserves_status_role_audience_and_metadata(
    roster_database,
    monkeypatch,
    status,
    role,
    target,
):
    _, app, tenant, _, cohort, people, _, _ = roster_database
    with scoped(app, tenant) as db:
        first, second = tracks(db, tenant, cohort)
        row = Enrollment(
            tenant_id=tenant,
            cohort_id=cohort,
            person_id=people[0],
            track_id=None if target == "from_null" else first,
            role_in_cohort=role,
            status=status,
            audience="staff",
            employee_ref="Employee-1",
            access_ends_at=NOW,
        )
        db.add(row)
        db.flush()
        before = asdict(enrollment_fact(row))
        calls = flush_calls(db, monkeypatch)
        selected = None if target == "null" else second
        fact = roster.ensure_admission_enrollment(
            db, command(tenant, cohort, people[0], selected)
        )
        after = asdict(fact)
        assert fact.track_id == selected
        assert {
            k: v for k, v in after.items() if k not in ("track_id", "updated_at")
        } == {k: v for k, v in before.items() if k not in ("track_id", "updated_at")}
        assert calls.count("ensure_admission_enrollment") == 1
        assert db.scalar(select(func.count()).select_from(CourseOffering)) == 0
        assert db.scalar(select(func.count()).select_from(AuditEvent)) == 0


@pytest.mark.parametrize("with_track", [True, False])
def test_same_track_no_explicit_flush_but_query_retains_caller_autoflush(
    roster_database,
    monkeypatch,
    with_track,
):
    _, app, tenant, _, cohort, people, _, _ = roster_database
    with scoped(app, tenant) as db:
        track, _ = tracks(db, tenant, cohort)
        selected = track if with_track else None
        row = Enrollment(
            tenant_id=tenant,
            cohort_id=cohort,
            person_id=people[0],
            track_id=selected,
            role_in_cohort="instructor",
            status="dropped",
            audience="external",
        )
        db.add(row)
        db.flush()
        before = enrollment_fact(row)
        caller_cohort = db.get(Cohort, cohort)
        caller_cohort.name = "Autoflushed caller mutation"
        calls = flush_calls(db, monkeypatch)
        fact = roster.ensure_admission_enrollment(
            db, command(tenant, cohort, people[0], selected)
        )
        assert fact == before
        assert "ensure_admission_enrollment" not in calls
        assert "_autoflush" in calls
        assert not db.is_modified(caller_cohort)
        assert (
            db.scalar(select(Cohort.name).where(Cohort.id == cohort))
            == caller_cohort.name
        )
        assert db.scalar(select(func.count()).select_from(AuditEvent)) == 0


def test_non_bypass_b_wrong_scope_cannot_create_or_change_a_enrollment(roster_database):
    _, app, tenant, other, cohort, people, outsider, _ = roster_database
    with scoped(app, tenant) as db:
        original = roster.ensure_admission_enrollment(
            db, command(tenant, cohort, people[0])
        )
    with pytest.raises(DBAPIError):
        with scoped(app, other) as db:
            assert db.execute(
                text(
                    "SELECT rolsuper,rolbypassrls FROM pg_roles "
                    "WHERE rolname=current_user"
                )
            ).one() == (False, False)
            assert db.scalar(select(func.count()).select_from(Enrollment)) == 0
            roster.ensure_admission_enrollment(db, command(tenant, cohort, people[0]))
    # A tenant-B write with A's cohort also fails its real composite source FK.
    with pytest.raises(IntegrityError):
        with scoped(app, other) as db:
            roster.ensure_admission_enrollment(db, command(other, cohort, outsider))
    with scoped(app, tenant) as db:
        assert enrollment_fact(db.get(Enrollment, original.id)) == original
        assert db.scalar(select(func.count()).select_from(Enrollment)) == 1


@pytest.mark.parametrize("person_kind", ["root_only", "foreign"])
def test_genuine_person_fk_refusal_propagates_and_caller_rolls_back(
    roster_database, person_kind
):
    _, app, tenant, _, cohort, _, outsider, root_only = roster_database
    person = root_only if person_kind == "root_only" else outsider
    with pytest.raises(IntegrityError):
        with scoped(app, tenant) as db:
            db.get(Cohort, cohort).name = "Must roll back with failed enrollment"
            roster.ensure_admission_enrollment(db, command(tenant, cohort, person))
    with scoped(app, tenant) as db:
        assert db.get(Cohort, cohort).name != "Must roll back with failed enrollment"
        assert db.scalar(select(func.count()).select_from(Enrollment)) == 0


@pytest.mark.parametrize("bad_reference", ["cohort", "track"])
def test_source_fk_errors_not_prevalidated_or_translated(
    roster_database, bad_reference
):
    _, app, tenant, _, cohort, people, _, _ = roster_database
    with pytest.raises(IntegrityError):
        with scoped(app, tenant) as db:
            roster.ensure_admission_enrollment(
                db,
                command(
                    tenant,
                    uuid4() if bad_reference == "cohort" else cohort,
                    people[0],
                    uuid4() if bad_reference == "track" else None,
                ),
            )
    with scoped(app, tenant) as db:
        assert db.scalar(select(func.count()).select_from(Enrollment)) == 0


def test_existing_track_fk_failure_rolls_back_track_and_caller_writes(roster_database):
    _, app, tenant, _, cohort, people, _, _ = roster_database
    with scoped(app, tenant) as db:
        first, _ = tracks(db, tenant, cohort)
        original = roster.ensure_admission_enrollment(
            db, command(tenant, cohort, people[0], first)
        )
    with pytest.raises(IntegrityError):
        with scoped(app, tenant) as db:
            db.get(Cohort, cohort).name = "Caller write to roll back"
            roster.ensure_admission_enrollment(
                db, command(tenant, cohort, people[0], uuid4())
            )
    with scoped(app, tenant) as db:
        assert enrollment_fact(db.get(Enrollment, original.id)) == original
        assert db.get(Cohort, cohort).name != "Caller write to roll back"


def test_requires_existing_root_transaction_without_starting_one(roster_database):
    _, app, tenant, _, cohort, people, _, _ = roster_database
    with Session(bind=app) as db:
        assert db.get_transaction() is None
        with pytest.raises(TransactionChanged, match="already active caller root"):
            roster.ensure_admission_enrollment(db, command(tenant, cohort, people[0]))
        assert db.get_transaction() is None


@pytest.mark.parametrize("operation", ["new", "changed", "unchanged"])
def test_caller_root_nested_continuity_and_caller_owned_success_rollback(
    roster_database, operation
):
    _, app, tenant, _, cohort, people, _, _ = roster_database
    with scoped(app, tenant) as db:
        first, second = tracks(db, tenant, cohort)
        if operation != "new":
            original = roster.ensure_admission_enrollment(
                db, command(tenant, cohort, people[0], first)
            )
    with pytest.raises(RuntimeError, match="caller chooses rollback"):
        with scoped(app, tenant) as db, db.begin_nested():
            root, nested = db.get_transaction(), db.get_nested_transaction()
            selected = first if operation == "unchanged" else second
            roster.ensure_admission_enrollment(
                db, command(tenant, cohort, people[0], selected)
            )
            assert (
                db.get_transaction() is root and db.get_nested_transaction() is nested
            )
            raise RuntimeError("caller chooses rollback")
    with scoped(app, tenant) as db:
        if operation == "new":
            assert db.scalar(select(func.count()).select_from(Enrollment)) == 0
        else:
            assert enrollment_fact(db.get(Enrollment, original.id)) == original
