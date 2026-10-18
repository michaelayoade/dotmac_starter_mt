"""Hosted PostgreSQL canaries for the two remaining direct cohort writers.

Uses the genuine Kernel and current learning lineages, never create_all.
"""

from contextlib import contextmanager
from dataclasses import FrozenInstanceError
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from dotmac_learning import cohorts, roster
from dotmac_learning.cohort_contracts import (
    CreateCohortCommand,
    RemoveFacilitatorCommand,
)
from dotmac_learning.models import LearningEvent
from dotmac_learning.participation_contracts import (
    ParticipationForbidden,
    ParticipationNotFound,
    RosterAuthority,
)
from dotmac_learning.participation_models import Cohort, Enrollment
from dotmac_learning.result_contracts import TransactionChanged
from sqlalchemy import event, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from tests import test_learning_roster_commands as roster_tests
from tests.test_learning_catalog import _scope
from tests.test_learning_participation_catalog import _row, _track
from tests.test_learning_roster_commands import scoped

learning_database = roster_tests.learning_database
roster_database = roster_tests.roster_database


@contextmanager
def observed(db):
    """Observe actual caller SQL/flush without substituting the service owner."""
    statements, flushes = [], []
    conn = db.connection()

    def executed(connection, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    def flushed(session, context, instances):
        flushes.append(session)

    event.listen(conn, "before_cursor_execute", executed)
    event.listen(db, "before_flush", flushed)
    try:
        yield statements, flushes
    finally:
        event.remove(conn, "before_cursor_execute", executed)
        event.remove(db, "before_flush", flushed)


def removal(tenant, cohort, person):
    return RemoveFacilitatorCommand(
        tenant_id=tenant, cohort_id=cohort, person_id=person
    )


def test_cohort_cross_tenant_and_missing_cohort_exact_detail(roster_database):
    _, app, tenant, other, cohort, people, _, _ = roster_database
    for scope, cohort_id in ((other, cohort), (tenant, uuid4())):
        with scoped(app, scope) as db:
            with pytest.raises(
                ParticipationNotFound, match="^cohort not found for tenant$"
            ):
                cohorts.remove_facilitator(
                    db,
                    removal(scope, cohort_id, people[0]),
                    authority=RosterAuthority(scope, True),
                )
    with scoped(app, tenant) as db:
        cohorts.create_cohort(
            db, CreateCohortCommand(tenant_id=tenant, name="Only here", discipline="D")
        )
    with scoped(app, other) as db:
        assert db.scalar(select(Cohort.id).where(Cohort.name == "Only here")) is None


def test_create_stages_raw_values_without_sql_flush_or_fabricated_defaults(
    roster_database,
):
    _, app, tenant, _, _, _, _, _ = roster_database
    with scoped(app, tenant) as db:
        root = db.get_transaction()
        with db.begin_nested() as nested:
            # Even invalid pending caller state must not autoflush during create.
            invalid = Cohort(tenant_id=tenant, name=None, discipline="pending")
            db.add(invalid)
            with observed(db) as (statements, flushes):
                result = cohorts.create_cohort(
                    db,
                    CreateCohortCommand(
                        tenant_id=tenant, name="  raw name  ", discipline=" ANY "
                    ),
                )
                assert result is None and statements == [] and flushes == []
            row = next(item for item in db.new if item is not invalid)
            assert (row.name, row.discipline, row.status) == (
                "  raw name  ",
                " ANY ",
                "active",
            )
            assert row.id is None and row.delivery_mode is None
            assert row.created_at is None and row.updated_at is None
            assert db.get_transaction() is root
            assert db.get_nested_transaction() is nested
            db.expunge(invalid)
            db.flush()
            row_id = row.id
            assert row.delivery_mode == "self_paced"
            assert row.created_at is not None and row.updated_at is not None
            nested.rollback()
        assert db.scalar(select(Cohort.id).where(Cohort.id == row_id)) is None


def test_removal_admin_gate_and_authority_scope_precede_sql_and_autoflush(
    roster_database,
):
    _, app, tenant, other, cohort, people, _, _ = roster_database
    with scoped(app, tenant) as db:
        invalid = Cohort(tenant_id=tenant, name=None, discipline="pending")
        db.add(invalid)
        with observed(db) as (statements, flushes):
            with pytest.raises(ParticipationForbidden, match="^Forbidden$"):
                cohorts.remove_facilitator(
                    db,
                    removal(tenant, uuid4(), people[0]),
                    authority=RosterAuthority(tenant, False),
                )
            with pytest.raises(
                ParticipationForbidden, match="^roster authority does not cover tenant$"
            ):
                cohorts.remove_facilitator(
                    db,
                    removal(tenant, cohort, people[0]),
                    authority=RosterAuthority(other, True),
                )
            assert statements == [] and flushes == []
        db.expunge(invalid)


def test_missing_and_student_enrollment_cannot_be_removed_as_facilitator(
    roster_database,
):
    _, app, tenant, _, cohort, people, outsider, _ = roster_database
    with scoped(app, tenant) as db:
        student = Enrollment(
            tenant_id=tenant,
            cohort_id=cohort,
            person_id=people[0],
            role_in_cohort="student",
            status="active",
        )
        db.add(student)
        for person in (*people[:2], outsider):
            with pytest.raises(
                ParticipationNotFound, match="^facilitator enrollment not found$"
            ):
                cohorts.remove_facilitator(
                    db,
                    removal(tenant, cohort, person),
                    authority=RosterAuthority(tenant, True),
                )
        assert student.status == "active"


def test_removal_delegates_canonical_writer_preserves_fields_and_caller_rollback(
    roster_database,
    monkeypatch,
):
    _, app, tenant, _, cohort, people, _, _ = roster_database
    with app.begin() as conn:
        _scope(conn, tenant)
        track = _track(conn, tenant)
        _row(conn, "cohort_tracks", tenant, cohort_id=cohort, track_id=track)
    authority = RosterAuthority(tenant, True)
    delegated = []
    writer = roster.set_roster_state

    def capture(db, command, *, authority):
        delegated.append((db, command, authority))
        return writer(db, command, authority=authority)

    monkeypatch.setattr(roster, "set_roster_state", capture)
    with scoped(app, tenant) as db:
        end = datetime(2040, 1, 1, tzinfo=UTC)
        row = Enrollment(
            tenant_id=tenant,
            cohort_id=cohort,
            person_id=people[0],
            role_in_cohort="instructor",
            status="active",
            track_id=track,
            audience="staff",
            employee_ref=" Employee ",
            access_ends_at=end,
        )
        db.add(row)
        db.flush()
        original = (row.id, row.created_at, row.updated_at)
        before_events = db.scalar(select(func.count()).select_from(LearningEvent))
        root = db.get_transaction()
        with db.begin_nested() as nested:
            with observed(db) as (statements, flushes):
                fact = cohorts.remove_facilitator(
                    db, removal(tenant, cohort, people[0]), authority=authority
                )
                assert flushes == [db]
                assert "FROM mod_learning.cohorts" in statements[0]
                assert "FROM mod_learning.enrollments" in statements[1]
                assert "role_in_cohort =" in statements[1]
                assert "FROM mod_learning.enrollments" in statements[2]
                assert "role_in_cohort =" not in statements[2]
                assert statements[3].startswith("UPDATE mod_learning.enrollments")
            assert len(delegated) == 1
            assert delegated[0][0] is db and delegated[0][2] is authority
            command = delegated[0][1]
            assert (
                command.tenant_id,
                command.cohort_id,
                command.person_id,
                command.state,
            ) == (tenant, cohort, people[0], "dropped")
            assert (fact.id, fact.created_at) == original[:2]
            assert (fact.status, fact.role_in_cohort, fact.track_id) == (
                "dropped",
                "instructor",
                track,
            )
            assert (fact.audience, fact.employee_ref, fact.access_ends_at) == (
                "staff",
                " Employee ",
                end,
            )
            with pytest.raises(FrozenInstanceError):
                fact.status = "active"
            assert db.get_transaction() is root
            assert db.get_nested_transaction() is nested
            assert (
                db.scalar(select(func.count()).select_from(LearningEvent))
                == before_events
            )
            nested.rollback()
        db.refresh(row)
        assert row.status == "active" and row.updated_at == original[2]
        # Already-dropped instructors remain valid; no additional state policy.
        row.status = "dropped"
        db.flush()
        assert (
            cohorts.remove_facilitator(
                db, removal(tenant, cohort, people[0]), authority=authority
            ).status
            == "dropped"
        )


def test_writers_require_root_before_sql_and_removal_propagates_autoflush_failure(
    roster_database,
):
    _, app, tenant, _, cohort, people, _, _ = roster_database
    create = CreateCohortCommand(tenant_id=tenant, name="x", discipline="x")
    command = removal(tenant, cohort, people[0])
    authority = RosterAuthority(tenant, True)
    with Session(bind=app) as db:
        statements = []

        def executed(*args):
            statements.append(args)

        event.listen(app, "before_cursor_execute", executed)
        try:
            with pytest.raises(TransactionChanged):
                cohorts.create_cohort(db, create)
            with pytest.raises(TransactionChanged):
                cohorts.remove_facilitator(db, command, authority=authority)
            assert db.get_transaction() is None and statements == [] and not db.new
        finally:
            event.remove(app, "before_cursor_execute", executed)
    with scoped(app, tenant) as db:
        root = db.get_transaction()
        db.add(Enrollment(tenant_id=tenant, cohort_id=uuid4(), person_id=people[0]))
        with pytest.raises(IntegrityError):
            cohorts.remove_facilitator(db, command, authority=authority)
        assert not root.is_active
        db.rollback()


def test_create_preserves_root_caller_rollback_without_committing(roster_database):
    _, app, tenant, _, _, _, _, _ = roster_database
    with Session(bind=app) as db:
        with db.begin():
            _scope(db.connection(), tenant)
            root = db.get_transaction()
            cohorts.create_cohort(
                db,
                CreateCohortCommand(tenant_id=tenant, name="rollback", discipline="D"),
            )
            assert db.get_transaction() is root
            db.flush()
            row_id = db.scalar(select(Cohort.id).where(Cohort.name == "rollback"))
            db.rollback()
    with scoped(app, tenant) as db:
        assert db.scalar(select(Cohort.id).where(Cohort.id == row_id)) is None


def test_removal_preserves_root_caller_rollback_without_committing(roster_database):
    _, app, tenant, _, cohort, people, _, _ = roster_database
    with scoped(app, tenant) as db:
        db.add(
            Enrollment(
                tenant_id=tenant,
                cohort_id=cohort,
                person_id=people[0],
                role_in_cohort="instructor",
                status="active",
            )
        )
    with Session(bind=app) as db:
        with db.begin():
            _scope(db.connection(), tenant)
            root = db.get_transaction()
            fact = cohorts.remove_facilitator(
                db,
                removal(tenant, cohort, people[0]),
                authority=RosterAuthority(tenant, True),
            )
            assert fact.status == "dropped" and db.get_transaction() is root
            db.rollback()
    with scoped(app, tenant) as db:
        row = db.scalar(select(Enrollment).where(Enrollment.id == fact.id))
        assert row.status == "active"
