"""Hosted PostgreSQL roster authorization on migrated Kernel/Learning and app_user.

These canaries use real cached ORM identities and concurrent database connections.
They require the existing integration lane; no SQLite or mocked freshness proof.
"""

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager, nullcontext
from threading import Event
from time import monotonic, sleep
from uuid import uuid4

import pytest
from dotmac_learning import roster
from dotmac_learning.participation_contracts import (
    ParticipationForbidden,
    ParticipationNotFound,
    RosterAuthority,
    RosterStateCommand,
)
from dotmac_learning.participation_models import Cohort, Enrollment
from dotmac_learning.result_contracts import TransactionChanged
from sqlalchemy import inspect, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from tests import test_learning_roster_commands as roster_tests
from tests.test_learning_catalog import _scope
from tests.test_learning_cohort_commands import observed

learning_database = roster_tests.learning_database
roster_database = roster_tests.roster_database


@pytest.fixture
def state_database(roster_database):
    _, app, tenant, _, cohort, people, _, _ = roster_database
    with app.begin() as conn:
        _scope(conn, tenant)
        enrollment = conn.scalar(
            Enrollment.__table__.insert()
            .values(
                tenant_id=tenant,
                cohort_id=cohort,
                person_id=people[0],
                role_in_cohort="student",
                status="active",
            )
            .returning(Enrollment.id)
        )
    return roster_database, enrollment


@contextmanager
def caller(app, tenant, *, autoflush=False):
    with Session(bind=app, autoflush=autoflush) as db, db.begin():
        _scope(db.connection(), tenant)
        yield db


def state_command(data, state="dropped", *, tenant=None):
    _, _, original_tenant, _, cohort, people, _, _ = data
    return RosterStateCommand(
        tenant_id=original_tenant if tenant is None else tenant,
        cohort_id=cohort,
        person_id=people[0],
        state=state,
    )


def change_role(app, tenant, enrollment, role):
    with app.begin() as conn:
        _scope(conn, tenant)
        conn.execute(text("SET LOCAL lock_timeout='500ms'"))
        conn.execute(
            Enrollment.__table__.update()
            .where(Enrollment.id == enrollment)
            .values(role_in_cohort=role)
        )


def stored(app, tenant, enrollment):
    with app.connect() as conn:
        _scope(conn, tenant)
        return conn.execute(
            select(Enrollment.role_in_cohort, Enrollment.status).where(
                Enrollment.id == enrollment
            )
        ).one()


@pytest.mark.parametrize("autoflush", [False, True])
@pytest.mark.parametrize("requested", ["dropped", "invalid"])
def test_cached_student_cannot_bypass_fresh_instructor_permission(
    state_database, autoflush, requested
):
    data, enrollment = state_database
    _, app, tenant, _, _, _, _, _ = data
    with caller(app, tenant, autoflush=autoflush) as db:
        cached = db.get(Enrollment, enrollment)
        assert cached.role_in_cohort == "student"
        change_role(app, tenant, enrollment, "instructor")
        with observed(db) as (statements, flushes):
            with pytest.raises(ParticipationForbidden, match="^Forbidden$"):
                roster.set_roster_state(
                    db,
                    state_command(data, requested),
                    authority=RosterAuthority(tenant, False),
                )
            assert len(statements) == 1
            assert "FOR UPDATE" in statements[0].upper()
            assert flushes == []
        assert db.get(Enrollment, enrollment) is cached
        assert cached.role_in_cohort == "student" and cached.status == "active"
    assert stored(app, tenant, enrollment) == ("instructor", "active")


@pytest.mark.parametrize("autoflush", [False, True])
def test_admin_fact_uses_locked_role_without_refreshing_cached_identity(
    state_database, autoflush
):
    data, enrollment = state_database
    _, app, tenant, _, cohort, people, _, _ = data
    with caller(app, tenant, autoflush=autoflush) as db:
        cached = db.get(Enrollment, enrollment)
        change_role(app, tenant, enrollment, "instructor")
        fact = roster.set_roster_state(
            db, state_command(data), authority=RosterAuthority(tenant, True)
        )
        assert fact.role_in_cohort == "instructor" and fact.status == "dropped"
        assert (fact.tenant_id, fact.cohort_id, fact.person_id) == (
            tenant,
            cohort,
            people[0],
        )
        assert db.get(Enrollment, enrollment) is cached
        assert cached.role_in_cohort == "student"
        assert stored(app, tenant, enrollment) == ("instructor", "active")
        db.rollback()
    assert stored(app, tenant, enrollment) == ("instructor", "active")


@pytest.mark.parametrize("autoflush", [False, True])
@pytest.mark.parametrize("requested", ["dropped", "invalid"])
@pytest.mark.parametrize(
    "pending", ["role", "status", "metadata", "tenant", "cohort", "person", "delete"]
)
def test_pending_target_refused_without_discarding_values_or_history(
    state_database, autoflush, requested, pending
):
    data, enrollment = state_database
    _, app, tenant, other, _, people, _, _ = data
    with caller(app, tenant, autoflush=autoflush) as db:
        cached = db.get(Enrollment, enrollment)
        root = db.get_transaction()
        nested = db.begin_nested()
        db.connection()  # Materialize the clean savepoint before observing owner SQL.
        changes = {
            "role": ("role_in_cohort", "instructor"),
            "status": ("status", "waitlisted"),
            "metadata": ("employee_ref", "pending caller metadata"),
            "tenant": ("tenant_id", other),
            "cohort": ("cohort_id", uuid4()),
            "person": ("person_id", people[1]),
        }
        if pending == "delete":
            db.delete(cached)
            attribute, value = "status", "active"
        else:
            attribute, value = changes[pending]
            setattr(cached, attribute, value)
        history = inspect(cached).attrs[attribute].history
        # True compatibility is tested in a caller no_autoflush scope; actual
        # Kernel False needs no scope to expose the production pending-state case.
        with db.no_autoflush if autoflush else nullcontext():
            with observed(db) as (statements, flushes):
                with pytest.raises(
                    TransactionChanged,
                    match=(
                        "^clean target enrollment required; "
                        "caller rollback required$"
                    ),
                ):
                    roster.set_roster_state(
                        db,
                        state_command(data, requested),
                        authority=RosterAuthority(tenant, False),
                    )
                assert len(statements) == 1 and "FOR UPDATE" in statements[0].upper()
                assert flushes == []
            assert getattr(cached, attribute) == value
            assert inspect(cached).attrs[attribute].history == history
            assert (cached in db.deleted) is (pending == "delete")
            assert db.get_transaction() is root
            assert db.get_nested_transaction() is nested
            assert db.is_active
        db.rollback()
    assert stored(app, tenant, enrollment) == ("student", "active")


@pytest.mark.parametrize("autoflush", [False, True])
def test_unrelated_pending_state_retained_and_included_in_final_full_flush(
    state_database, autoflush
):
    data, enrollment = state_database
    _, app, tenant, _, cohort, _, _, _ = data
    with caller(app, tenant, autoflush=autoflush) as db:
        unrelated = db.get(Cohort, cohort)
        old_name = unrelated.name
        unrelated.name = "caller pending name"
        with db.no_autoflush, observed(db) as (statements, flushes):
            fact = roster.set_roster_state(
                db, state_command(data), authority=RosterAuthority(tenant, False)
            )
            assert fact.status == "dropped"
            assert len(flushes) == 1 and flushes[0] is db
            assert "FOR UPDATE" in statements[0].upper()
            assert any(
                statement.lstrip().upper().startswith("UPDATE")
                for statement in statements
            )
        assert unrelated.name == "caller pending name"
        with app.connect() as other:
            _scope(other, tenant)
            assert (
                other.scalar(select(Cohort.name).where(Cohort.id == cohort)) == old_name
            )
        assert stored(app, tenant, enrollment) == ("student", "active")
        db.rollback()
    with caller(app, tenant) as db:
        assert db.get(Cohort, cohort).name == old_name
    assert stored(app, tenant, enrollment) == ("student", "active")


def test_permission_denial_locks_before_any_status_update_and_caller_releases_it(
    state_database,
):
    data, enrollment = state_database
    _, app, tenant, _, _, _, _, _ = data
    change_role(app, tenant, enrollment, "instructor")
    with caller(app, tenant) as db:
        root = db.get_transaction()
        with observed(db) as (statements, flushes):
            with pytest.raises(ParticipationForbidden, match="^Forbidden$"):
                roster.set_roster_state(
                    db, state_command(data), authority=RosterAuthority(tenant, False)
                )
            assert len(statements) == 1 and "FOR UPDATE" in statements[0].upper()
            assert flushes == []
        assert db.get_transaction() is root and db.is_active
        # A denied command issues no UPDATE. Only its SELECT lock can block this.
        with pytest.raises(DBAPIError) as caught:
            with app.begin() as contender:
                _scope(contender, tenant)
                contender.execute(text("SET LOCAL lock_timeout='150ms'"))
                contender.execute(
                    Enrollment.__table__.update()
                    .where(Enrollment.id == enrollment)
                    .values(role_in_cohort="student")
                )
        assert caught.value.orig.sqlstate == "55P03"
        db.rollback()
        change_role(app, tenant, enrollment, "student")
    assert stored(app, tenant, enrollment) == ("student", "active")


def test_owner_waits_for_uncommitted_role_change_then_uses_committed_role(
    state_database,
):
    data, enrollment = state_database
    _, app, tenant, _, _, _, _, _ = data
    cached, changed, started, release = Event(), Event(), Event(), Event()
    pids = {}

    def winner():
        assert cached.wait(10)
        with app.begin() as conn:
            _scope(conn, tenant)
            conn.execute(text("SET LOCAL statement_timeout='15s'"))
            pids["winner"] = conn.scalar(text("SELECT pg_backend_pid()"))
            conn.execute(
                Enrollment.__table__.update()
                .where(Enrollment.id == enrollment)
                .values(role_in_cohort="instructor")
            )
            changed.set()
            assert release.wait(10)
        return "committed"

    def contender():
        with caller(app, tenant) as db:
            db.execute(text("SET LOCAL statement_timeout='15s'"))
            row = db.get(Enrollment, enrollment)
            assert row.role_in_cohort == "student"
            pids["contender"] = db.scalar(text("SELECT pg_backend_pid()"))
            cached.set()
            assert changed.wait(10)
            started.set()
            with pytest.raises(ParticipationForbidden, match="^Forbidden$"):
                roster.set_roster_state(
                    db, state_command(data), authority=RosterAuthority(tenant, False)
                )
            assert row.role_in_cohort == "student" and row.status == "active"
        return "denied"

    with ThreadPoolExecutor(max_workers=2) as pool:
        one, two = pool.submit(winner), pool.submit(contender)
        try:
            assert started.wait(10)
            with app.connect().execution_options(
                isolation_level="AUTOCOMMIT"
            ) as observer:
                deadline = monotonic() + 5
                while monotonic() < deadline:
                    waiting = observer.execute(
                        text(
                            "SELECT wait_event_type, pg_blocking_pids(pid) "
                            "FROM pg_stat_activity WHERE pid=:pid"
                        ),
                        {"pid": pids["contender"]},
                    ).one_or_none()
                    if (
                        waiting is not None
                        and waiting[0] == "Lock"
                        and pids["winner"] in waiting[1]
                    ):
                        break
                    sleep(0.01)  # Pacing only; actual PostgreSQL lock state is proof.
                else:
                    pytest.fail("roster owner did not wait on uncommitted role writer")
        finally:
            release.set()
            cached.set()
            changed.set()
        assert one.result(timeout=20) == "committed"
        assert two.result(timeout=20) == "denied"
    assert stored(app, tenant, enrollment) == ("instructor", "active")


@pytest.mark.parametrize("misleading", [False, True])
def test_wrong_tenant_rls_neither_finds_modifies_nor_locks_target(
    state_database, misleading
):
    data, enrollment = state_database
    _, app, tenant, other, _, _, _, _ = data
    with caller(app, other) as db:
        assert db.scalar(text("SELECT current_user")) == "app_user"
        assert db.get(Enrollment, enrollment) is None
        command_tenant = tenant if misleading else other
        with pytest.raises(ParticipationNotFound, match="^enrollment not found$"):
            roster.set_roster_state(
                db,
                state_command(data, tenant=command_tenant),
                authority=RosterAuthority(command_tenant, True),
            )
        assert (
            db.execute(
                Enrollment.__table__.update()
                .where(Enrollment.id == enrollment)
                .values(status="dropped")
            ).rowcount
            == 0
        )
        # This role update must succeed while the wrong-tenant transaction stays open.
        with app.begin() as permitted:
            _scope(permitted, tenant)
            permitted.execute(text("SET LOCAL lock_timeout='150ms'"))
            assert (
                permitted.execute(
                    Enrollment.__table__.update()
                    .where(Enrollment.id == enrollment)
                    .values(role_in_cohort="instructor")
                ).rowcount
                == 1
            )
    assert stored(app, tenant, enrollment) == ("instructor", "active")


def test_caller_root_and_nested_boundaries_visibility_and_rollback(state_database):
    data, enrollment = state_database
    _, app, tenant, _, _, _, _, _ = data
    with caller(app, tenant) as db:
        root = db.get_transaction()
        nested = db.begin_nested()
        fact = roster.set_roster_state(
            db, state_command(data), authority=RosterAuthority(tenant, False)
        )
        assert fact.status == "dropped"
        assert db.get_transaction() is root
        assert db.get_nested_transaction() is nested
        assert stored(app, tenant, enrollment) == ("student", "active")
        db.rollback()
        change_role(app, tenant, enrollment, "instructor")
    assert stored(app, tenant, enrollment) == ("instructor", "active")
