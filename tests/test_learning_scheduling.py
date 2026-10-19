"""Hosted PostgreSQL scheduling parity, audit and non-bypass isolation canaries.

Uses the real Alembic learning@head fixture. Delivery and tenant-audit revisions
must be integrated before execution; no metadata creation or stamps substitute.
"""

from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta
from threading import Event
from time import monotonic, sleep
from uuid import UUID, uuid4

import pytest
from dotmac_kernel import audit_actions
from dotmac_kernel.audit import AuditEvent, MissingAuditActorError
from dotmac_learning import scheduling
from dotmac_learning.attempt_contracts import AttemptAuditContext
from dotmac_learning.delivery_contracts import (
    CancelSessionCommand,
    CreateSessionCommand,
    SchedulingBadRequest,
    SchedulingNotFound,
    SetDeliveryModeCommand,
    UpdateSessionCommand,
)
from dotmac_learning.delivery_models import ClassSession
from dotmac_learning.manifest import module
from dotmac_learning.participation_models import Cohort, Enrollment
from sqlalchemy import create_engine, event, func, inspect, select, text
from sqlalchemy.exc import DBAPIError, IntegrityError, ProgrammingError
from sqlalchemy.orm import defer
from sqlalchemy.orm.attributes import NO_VALUE

from tests import test_learning_catalog as learning_catalog
from tests.test_learning_catalog import _person, _scope, _tenant
from tests.test_learning_participation_catalog import _cohort
from tests.test_learning_results import scoped

learning_database = learning_catalog.learning_database
NOW = datetime(2026, 8, 1, tzinfo=UTC)


@pytest.fixture(autouse=True)
def declared_audit(monkeypatch):
    monkeypatch.setattr(
        audit_actions,
        "_active_registry",
        audit_actions.AuditActionRegistry.from_manifests([module]),
    )


def context(tenant, **values):
    return AttemptAuditContext(
        tenant_id=tenant,
        actor_type=values.get("actor_type", "user"),
        actor_id=values.get("actor_id", "principal-7"),
        actor_label="Scheduler",
        source_application="academy",
    )


@pytest.fixture
def scheduling_database(learning_database):
    admin, app = (create_engine(url) for url in learning_database)
    tenant, other = uuid4(), uuid4()
    try:
        with admin.begin() as conn:
            _tenant(conn, tenant)
            _tenant(conn, other)
            person, outsider = _person(conn, tenant), _person(conn, tenant)
        with app.begin() as conn:
            _scope(conn, tenant)
            cohort = _cohort(conn, tenant)
            conn.execute(
                Enrollment.__table__.insert().values(
                    id=uuid4(),
                    tenant_id=tenant,
                    cohort_id=cohort,
                    person_id=person,
                    role_in_cohort="instructor",
                    status="active",
                )
            )
        yield admin, app, tenant, other, cohort, person, outsider
    finally:
        app.dispose()
        admin.dispose()


def create(db, tenant, cohort, **values):
    return scheduling.create_session(
        db,
        CreateSessionCommand(
            **(
                {
                    "tenant_id": tenant,
                    "cohort_id": cohort,
                    "title": "  Class  ",
                    "starts_at": NOW,
                    "session_type": "live_class",
                    "ends_at": NOW + timedelta(hours=1),
                    "join_url": " https://example.test/join ",
                }
                | values
            )
        ),
    )


def update(db, tenant, session_id, **values):
    return scheduling.update_session(
        db,
        UpdateSessionCommand(tenant_id=tenant, session_id=session_id, **values),
        audit_context=context(tenant),
    )


def test_real_catalog_and_non_bypass_rls(scheduling_database):
    admin, app, tenant, other, cohort, person, _ = scheduling_database
    with admin.connect() as conn:
        assert conn.execute(
            text(
                "SELECT relrowsecurity, relforcerowsecurity "
                "FROM pg_catalog.pg_class "
                "WHERE oid='mod_learning.class_sessions'::regclass"
            )
        ).one() == (True, True)
    with scoped(app, tenant) as db:
        assert db.execute(
            text(
                "SELECT rolsuper, rolbypassrls FROM pg_catalog.pg_roles "
                "WHERE rolname=current_user"
            )
        ).one() == (False, False)
        session = create(db, tenant, cohort)
    with scoped(app, other) as db:
        assert db.scalar(select(func.count()).select_from(ClassSession)) == 0
        assert scheduling.list_for_cohort(db, tenant_id=tenant, cohort_id=cohort) == ()
        assert (
            scheduling.list_for_person(
                db, tenant_id=tenant, person_id=person, now=NOW
            ).upcoming
            == ()
        )
        for read in (
            lambda: scheduling.get_session(db, tenant_id=tenant, session_id=session.id),
            lambda: scheduling.get_session_for_person(
                db, tenant_id=tenant, person_id=person, session_id=session.id
            ),
            lambda: scheduling.cancel_session(
                db, CancelSessionCommand(tenant_id=tenant, session_id=session.id)
            ),
        ):
            with pytest.raises(SchedulingNotFound, match="Session not found"):
                read()
        assert (
            db.execute(
                ClassSession.__table__.update()
                .values(title="cross")
                .returning(ClassSession.id)
            ).all()
            == []
        )
        assert (
            db.execute(ClassSession.__table__.delete().returning(ClassSession.id)).all()
            == []
        )
        with pytest.raises(DBAPIError) as caught, db.begin_nested():
            db.execute(
                ClassSession.__table__.insert().values(
                    id=uuid4(),
                    tenant_id=tenant,
                    cohort_id=cohort,
                    title="forbidden",
                    starts_at=NOW,
                )
            )
        assert caught.value.orig.sqlstate == "42501"
    with scoped(app, tenant) as db:
        with pytest.raises(SchedulingNotFound):
            scheduling.get_session(db, tenant_id=other, session_id=session.id)
    with app.begin() as conn:
        assert conn.scalar(select(func.count()).select_from(ClassSession)) == 0


def test_source_validation_order_promotion_and_detached_fields(scheduling_database):
    _, app, tenant, _, cohort, *_ = scheduling_database
    with scoped(app, tenant) as db:
        missing = uuid4()
        for values, message in (
            (
                {"session_type": "bad", "ends_at": NOW, "join_url": "bad"},
                "Unknown session type: bad",
            ),
            ({"ends_at": NOW, "join_url": "bad"}, "ends_at must be after starts_at."),
            ({"join_url": "bad"}, "Join URL must start with http:// or https://."),
        ):
            with pytest.raises(SchedulingBadRequest) as caught:
                create(db, tenant, missing, **values)
            assert str(caught.value) == message
        with pytest.raises(SchedulingNotFound, match="Cohort not found"):
            create(db, tenant, missing)
        with pytest.raises(SchedulingBadRequest, match="Unknown delivery mode"):
            scheduling.set_delivery_mode(
                db,
                SetDeliveryModeCommand(tenant_id=tenant, cohort_id=missing, mode="bad"),
            )
        session = create(
            db,
            tenant,
            cohort,
            offering_id=uuid4(),
            instructor_person_id=uuid4(),
            location=" ",
            notes="",
        )
        assert (
            session.title,
            session.join_url,
            session.location,
            session.notes,
            session.status,
        ) == ("Class", "https://example.test/join", " ", None, "scheduled")
        assert (
            db.scalar(select(Cohort.delivery_mode).where(Cohort.id == cohort))
            == "blended"
        )
        assert session.created_at is not None and session.updated_at is not None
        with pytest.raises(FrozenInstanceError):
            session.title = "changed"
        assert (
            scheduling.set_delivery_mode(
                db,
                SetDeliveryModeCommand(
                    tenant_id=tenant, cohort_id=cohort, mode="self_paced"
                ),
            ).delivery_mode
            == "self_paced"
        )
        assert (
            scheduling.cancel_session(
                db, CancelSessionCommand(tenant_id=tenant, session_id=session.id)
            ).status
            == "cancelled"
        )
        assert db.scalar(select(func.count()).select_from(AuditEvent)) == 0


def test_learner_any_active_role_exact_split_order_and_access(scheduling_database):
    _, app, tenant, _, cohort, person, outsider = scheduling_database
    with scoped(app, tenant) as db:
        old = create(
            db,
            tenant,
            cohort,
            title="old",
            starts_at=NOW - timedelta(days=2),
            ends_at=None,
        )
        completed = create(
            db,
            tenant,
            cohort,
            title="completed future",
            starts_at=NOW + timedelta(days=2),
            ends_at=None,
        )
        update(db, tenant, completed.id, status="completed")
        edge = create(
            db,
            tenant,
            cohort,
            title="ending now",
            starts_at=NOW - timedelta(hours=1),
            ends_at=NOW,
        )
        point = create(db, tenant, cohort, title="point", ends_at=None)
        future = create(
            db,
            tenant,
            cohort,
            title="future",
            starts_at=NOW + timedelta(days=1),
            ends_at=None,
        )
        cancelled = create(db, tenant, cohort, title="cancelled")
        scheduling.cancel_session(
            db, CancelSessionCommand(tenant_id=tenant, session_id=cancelled.id)
        )
        table = scheduling.list_for_person(
            db, tenant_id=tenant, person_id=person, now=NOW
        )
        assert [entry.session.id for entry in table.upcoming] == [
            edge.id,
            point.id,
            future.id,
        ]
        assert [entry.session.id for entry in table.past] == [completed.id, old.id]
        assert (
            scheduling.get_session_for_person(
                db, tenant_id=tenant, person_id=person, session_id=cancelled.id
            ).session.status
            == "cancelled"
        )
        assert [
            row.id
            for row in scheduling.list_for_cohort(
                db, tenant_id=tenant, cohort_id=cohort, upcoming_only=True, now=NOW
            )
        ] == [future.id]
        with pytest.raises(SchedulingNotFound):
            scheduling.get_session_for_person(
                db, tenant_id=tenant, person_id=outsider, session_id=edge.id
            )
        enrollment = db.scalar(select(Enrollment).where(Enrollment.person_id == person))
        enrollment.status = "inactive"
        assert (
            scheduling.list_for_person(
                db, tenant_id=tenant, person_id=person, now=NOW
            ).upcoming
            == ()
        )


def test_source_update_empty_clearing_iso_audit_sql_order_and_boundaries(
    scheduling_database,
):
    _, app, tenant, _, cohort, *_ = scheduling_database
    statements = []

    def observe(conn, cursor, statement, parameters, execution_context, executemany):
        statements.append(statement.lower())

    with scoped(app, tenant) as db:
        session = create(db, tenant, cohort, location="Room", notes="Notes")
        root = db.get_transaction()
        with db.begin_nested():
            nested = db.get_nested_transaction()
            event.listen(app, "before_cursor_execute", observe)
            try:
                result = update(
                    db,
                    tenant,
                    session.id,
                    title=None,
                    ends_at=None,
                    join_url="",
                    location="",
                    notes=None,
                    starts_at=NOW + timedelta(hours=2),
                )
            finally:
                event.remove(app, "before_cursor_execute", observe)
            assert result.session.title == "" and result.session.ends_at is None
            assert (
                result.session.join_url is None
                and result.session.location is None
                and result.session.notes is None
            )
            audit = db.scalar(
                select(AuditEvent).where(AuditEvent.entity_id == str(session.id))
            )
            assert isinstance(result.audit_event_id, UUID)
            assert result.audit_event_id == audit.id
            assert audit.is_success is None and audit.occurred_at is not None
            assert audit.details["changes"]["starts_at"] == {
                "from": NOW.isoformat(),
                "to": (NOW + timedelta(hours=2)).isoformat(),
            }
            assert (
                audit.action,
                audit.entity_type,
                audit.actor_type,
                audit.actor_id,
                audit.source_application,
                audit.actor_party_id,
            ) == (
                "scheduling.update_session",
                "class_session",
                "user",
                "principal-7",
                "academy",
                None,
            )
            assert next(
                i
                for i, s in enumerate(statements)
                if "update mod_learning.class_sessions" in s
            ) < next(
                i
                for i, s in enumerate(statements)
                if "insert into audit_events" in s
                or "insert into public.audit_events" in s
            )
            assert (
                db.get_transaction() is root and db.get_nested_transaction() is nested
            )
        db.rollback()
    with scoped(app, tenant) as db:
        assert (
            db.scalar(select(ClassSession)) is None
            and db.scalar(select(AuditEvent)) is None
        )


def test_unchanged_update_flushes_without_audit_and_tenant_binding_before_sql(
    scheduling_database, monkeypatch
):
    _, app, tenant, other, cohort, *_ = scheduling_database
    with scoped(app, tenant) as db:
        session = create(db, tenant, cohort)
        flushes = []
        event.listen(db, "after_flush", lambda *args: flushes.append(True))
        # The source explicit flush must persist unrelated pending changes too.
        pending = db.scalar(select(Cohort).where(Cohort.id == cohort))
        pending.name = "pending"
        with db.no_autoflush:
            result = update(db, tenant, session.id)
            assert result.changes == () and result.audit_event_id is None
        assert flushes
        assert db.scalar(select(func.count()).select_from(AuditEvent)) == 0

        def forbidden(*args, **kwargs):
            raise AssertionError("tenant mismatch must precede SQL")

        monkeypatch.setattr(db, "scalar", forbidden)
        monkeypatch.setattr(db, "execute", forbidden)
        with pytest.raises(SchedulingBadRequest, match="another tenant"):
            scheduling.update_session(
                db,
                UpdateSessionCommand(tenant_id=tenant, session_id=session.id),
                audit_context=context(other),
            )


def test_invalid_update_retains_source_dirty_state_until_caller_rollback(
    scheduling_database,
):
    _, app, tenant, _, cohort, *_ = scheduling_database
    with scoped(app, tenant) as db:
        session = create(db, tenant, cohort)
    with scoped(app, tenant) as db:
        root = db.get_transaction()
        with pytest.raises(
            SchedulingBadRequest, match="ends_at must be after starts_at"
        ):
            update(
                db,
                tenant,
                session.id,
                title="bad interval",
                starts_at=NOW + timedelta(days=1),
            )
        row = next(row for row in db.dirty if isinstance(row, ClassSession))
        assert row.title == "bad interval" and row.starts_at == NOW + timedelta(days=1)
        assert db.get_transaction() is root
        # Source validation precedes flush but a later read can autoflush bad state.
        assert (
            db.scalar(select(ClassSession.title).where(ClassSession.id == session.id))
            == "bad interval"
        )
        assert db.scalar(select(func.count()).select_from(AuditEvent)) == 0
        db.rollback()
    with scoped(app, tenant) as db:
        assert (
            scheduling.get_session(db, tenant_id=tenant, session_id=session.id).title
            == "Class"
        )


@pytest.mark.parametrize("nested", [False, True])
@pytest.mark.parametrize("failure", ["registry", "actor", "storage"])
def test_actual_required_kernel_audit_failure_after_flush_needs_caller_rollback(
    scheduling_database, monkeypatch, failure, nested
):
    _, app, tenant, _, cohort, *_ = scheduling_database
    with scoped(app, tenant) as db:
        session = create(db, tenant, cohort)
    with scoped(app, tenant) as db:
        authority = context(tenant)
        error = IntegrityError
        if failure == "registry":
            monkeypatch.setattr(
                audit_actions, "_active_registry", audit_actions.AuditActionRegistry(())
            )
            error = audit_actions.UndeclaredAuditActionError
        elif failure == "actor":
            authority = context(tenant, actor_id=None)
            error = MissingAuditActorError
        else:
            authority = context(tenant, actor_id="x" * 121)
            from sqlalchemy.exc import DataError

            error = DataError
        root = db.get_transaction()
        savepoint = db.begin_nested() if nested else None
        with pytest.raises(error):
            scheduling.update_session(
                db,
                UpdateSessionCommand(
                    tenant_id=tenant, session_id=session.id, title="flushed"
                ),
                audit_context=authority,
            )
        assert db.get_transaction() is root
        assert db.get_nested_transaction() is savepoint
        if failure != "storage":
            assert (
                db.scalar(
                    select(ClassSession.title).where(ClassSession.id == session.id)
                )
                == "flushed"
            )
        db.rollback()
    with scoped(app, tenant) as db:
        assert (
            scheduling.get_session(db, tenant_id=tenant, session_id=session.id).title
            == "Class"
        )
        assert db.scalar(select(func.count()).select_from(AuditEvent)) == 0


def test_update_lookup_type_status_url_validation_order_before_mutation(
    scheduling_database,
):
    _, app, tenant, _, cohort, *_ = scheduling_database
    with scoped(app, tenant) as db:
        with pytest.raises(SchedulingNotFound, match="Session not found"):
            update(db, tenant, uuid4(), session_type="bad", status="bad")
        session = create(db, tenant, cohort)
        for values, message in (
            ({"session_type": None, "status": "bad"}, "Unknown session type: None"),
            ({"status": "bad", "join_url": "bad"}, "Unknown status: bad"),
            ({"title": "pending", "join_url": "bad"}, "Join URL must start"),
        ):
            with pytest.raises(SchedulingBadRequest, match=message):
                update(db, tenant, session.id, **values)
            assert (
                scheduling.get_session(
                    db, tenant_id=tenant, session_id=session.id
                ).title
                == "Class"
            )
        assert db.scalar(select(func.count()).select_from(AuditEvent)) == 0


def test_existing_bad_interval_and_explicit_null_start_preserve_source_errors(
    scheduling_database,
):
    _, app, tenant, _, cohort, *_ = scheduling_database
    with scoped(app, tenant) as db:
        session = create(db, tenant, cohort)
        # Source storage has no interval CHECK; historical bad state remains legal.
        db.execute(
            ClassSession.__table__.update()
            .where(ClassSession.id == session.id)
            .values(ends_at=NOW)
        )
    with scoped(app, tenant) as db:
        with pytest.raises(
            SchedulingBadRequest, match="ends_at must be after starts_at"
        ):
            update(db, tenant, session.id)
        db.rollback()
    with scoped(app, tenant) as db:
        with pytest.raises(TypeError):
            update(db, tenant, session.id, starts_at=None)
        row = next(row for row in db.dirty if isinstance(row, ClassSession))
        assert row.starts_at is None
        db.rollback()


def test_commands_require_existing_caller_transaction(scheduling_database):
    from dotmac_learning.result_contracts import TransactionChanged
    from sqlalchemy.orm import Session

    _, app, tenant, _, cohort, *_ = scheduling_database
    with Session(bind=app) as db:
        with pytest.raises(TransactionChanged, match="active caller root"):
            create(db, tenant, cohort)
        assert db.get_transaction() is None


def test_stale_identity_update_uses_current_row_for_noop(scheduling_database):
    _, app, tenant, _, cohort, *_ = scheduling_database
    with scoped(app, tenant) as db:
        session = create(db, tenant, cohort)
    with scoped(app, tenant) as db:
        cached = db.get(ClassSession, session.id)
        assert cached.title == "Class"
        with app.begin() as conn:
            _scope(conn, tenant)
            conn.execute(
                ClassSession.__table__.update()
                .where(ClassSession.id == session.id)
                .values(title="current")
            )
        # Read behavior stays unchanged; the update alone refreshes and locks.
        assert (
            scheduling.get_session(db, tenant_id=tenant, session_id=session.id).title
            == "Class"
        )
        root = db.get_transaction()
        with db.begin_nested():
            nested = db.get_nested_transaction()
            result = update(db, tenant, session.id, title="current")
            assert cached.title == result.session.title == "current"
            assert result.changes == () and result.audit_event_id is None
            assert db.get_transaction() is root
            assert db.get_nested_transaction() is nested
        assert db.scalar(select(func.count()).select_from(AuditEvent)) == 0


def test_update_waits_for_row_writer_then_audits_current_values(scheduling_database):
    _, app, tenant, _, cohort, *_ = scheduling_database
    with scoped(app, tenant) as db:
        session = create(db, tenant, cohort)
    cached, written, started, release = Event(), Event(), Event(), Event()
    pids = {}

    def winner():
        assert cached.wait(10)
        with app.begin() as conn:
            _scope(conn, tenant)
            conn.execute(text("SET LOCAL statement_timeout='15s'"))
            pids["winner"] = conn.scalar(text("SELECT pg_backend_pid()"))
            conn.execute(
                ClassSession.__table__.update()
                .where(ClassSession.id == session.id)
                .values(title="winner")
            )
            written.set()
            assert release.wait(10)
        return "committed"

    def contender():
        with scoped(app, tenant) as db:
            db.execute(text("SET LOCAL statement_timeout='15s'"))
            row = db.get(ClassSession, session.id)
            assert row.title == "Class"
            pids["contender"] = db.scalar(text("SELECT pg_backend_pid()"))
            cached.set()
            assert written.wait(10)
            root = db.get_transaction()
            with db.begin_nested():
                nested = db.get_nested_transaction()
                started.set()
                result = update(db, tenant, session.id, title="contender")
                assert db.get_transaction() is root
                assert db.get_nested_transaction() is nested
                assert row.title == result.session.title == "contender"
                assert [
                    (c.field, c.from_value, c.to_value) for c in result.changes
                ] == [("title", "winner", "contender")]
                audit = db.get(AuditEvent, result.audit_event_id)
                assert audit is not None and audit.id == result.audit_event_id
                assert audit.details["changes"] == {
                    "title": {"from": "winner", "to": "contender"}
                }
                assert audit.is_success is None and audit.occurred_at is not None
                return result.audit_event_id

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
                    sleep(0.01)  # Pacing; the PostgreSQL lock state is the proof.
                else:
                    pytest.fail("session update did not wait for the row writer")
        finally:
            release.set()
            cached.set()
            written.set()
        assert one.result(timeout=20) == "committed"
        event_id = two.result(timeout=20)
    with scoped(app, tenant) as db:
        assert isinstance(event_id, UUID)
        audit = db.get(AuditEvent, event_id)
        assert audit is not None
        assert audit.tenant_id == tenant and audit.entity_id == str(session.id)
        assert audit.action == "scheduling.update_session"
        assert db.scalar(select(func.count()).select_from(AuditEvent)) == 1
        assert (
            scheduling.get_session(db, tenant_id=tenant, session_id=session.id).title
            == "contender"
        )
        unchanged = update(db, tenant, session.id, title="contender")
        assert unchanged.changes == () and unchanged.audit_event_id is None
        assert db.scalar(select(func.count()).select_from(AuditEvent)) == 1


@pytest.mark.parametrize("mode", ["disabled", "suppressed", "native"])
@pytest.mark.parametrize("nested", [False, True])
@pytest.mark.parametrize("command", ["change", "noop", "invalid", "same_field"])
def test_pending_scheduling_notes_and_fresh_clean_audit_values(
    scheduling_database, mode, nested, command
):
    _, app, tenant, _, cohort, *_ = scheduling_database
    with scoped(app, tenant) as db:
        session_id = create(db, tenant, cohort, notes="old", location="old room").id
    with scoped(app, tenant) as db:
        row = db.get(ClassSession, session_id)
        pending_cohort = db.get(Cohort, cohort)
        with app.begin() as conn:
            _scope(conn, tenant)
            conn.execute(
                ClassSession.__table__.update()
                .where(ClassSession.id == session_id)
                .values(title="SQL title", location="SQL room")
            )
        root = db.get_transaction()
        savepoint = db.begin_nested() if nested else None
        # SQLAlchemy materializes a caller savepoint lazily on the first SELECT.
        expected_read_sql = (["savepoint"] if nested else []) + ["select"]
        db.autoflush = mode != "disabled"
        row.notes = "caller pending"
        pending_cohort.name = "unrelated pending"
        if command == "same_field":
            row.title = "caller title"
        history = inspect(row).attrs.notes.history
        seen = []

        def observe(conn, cursor, statement, parameters, execution_context, many):
            seen.append(statement.lstrip().split()[0].lower())

        event.listen(app, "before_cursor_execute", observe)
        try:
            with db.no_autoflush if mode == "suppressed" else nullcontext():
                if command == "invalid":
                    with pytest.raises(SchedulingBadRequest, match="ends_at"):
                        update(
                            db,
                            tenant,
                            session_id,
                            title="command title",
                            starts_at=NOW + timedelta(days=1),
                        )
                    assert row.title == "command title"
                    assert row.notes == "caller pending"
                    if mode != "native":
                        assert inspect(row).attrs.notes.history == history
                        assert seen == expected_read_sql
                    assert db.scalar(select(func.count()).select_from(AuditEvent)) == 0
                else:
                    title = "SQL title" if command == "noop" else "command title"
                    result = update(db, tenant, session_id, title=title)
                    assert result.session.notes == row.notes == "caller pending"
                    assert row.location == result.session.location == "SQL room"
                    if command == "noop":
                        assert result.changes == () and result.audit_event_id is None
                        assert (
                            db.scalar(select(func.count()).select_from(AuditEvent)) == 0
                        )
                    else:
                        old = "caller title" if command == "same_field" else "SQL title"
                        assert [
                            (c.field, c.from_value, c.to_value) for c in result.changes
                        ] == [("title", old, title)]
                        (audit,) = db.scalars(select(AuditEvent)).all()
                        assert audit.id == result.audit_event_id
                        assert audit.details["changes"] == {
                            "title": {"from": old, "to": title}
                        }
                    assert db.scalar(select(ClassSession.notes)) == "caller pending"
                    assert (
                        db.scalar(select(Cohort.name).where(Cohort.id == cohort))
                        == "unrelated pending"
                    )
            if mode != "native":
                assert seen[: len(expected_read_sql)] == expected_read_sql
            assert db.get(ClassSession, session_id) is row
            assert db.get_transaction() is root
            assert db.get_nested_transaction() is savepoint
        finally:
            event.remove(app, "before_cursor_execute", observe)
        db.rollback()
    with scoped(app, tenant) as db:
        row = db.get(ClassSession, session_id)
        assert (row.title, row.location, row.notes) == ("SQL title", "SQL room", "old")
        assert db.scalar(select(func.count()).select_from(AuditEvent)) == 0


@pytest.mark.parametrize("clean", ["expired", "deferred"])
@pytest.mark.parametrize(
    "field,value",
    [
        ("notes", "old"),
        ("notes", None),
        ("notes", "DELETE"),
        ("id", "KEY"),
        ("tenant_id", "KEY"),
    ],
)
def test_scheduling_refusal_preserves_scalar_history_and_pending_keys(
    scheduling_database, clean, field, value
):
    _, app, tenant, _, cohort, *_ = scheduling_database
    with scoped(app, tenant) as db:
        session_id = create(db, tenant, cohort, notes="old", location="old room").id
    with scoped(app, tenant) as db:
        row = db.scalars(
            select(ClassSession)
            .options(defer(ClassSession.location))
            .where(ClassSession.id == session_id)
        ).one()
        if clean == "expired":
            db.expire(row, ["location"])
        assert inspect(row).attrs.location.loaded_value is NO_VALUE
        with app.begin() as conn:
            _scope(conn, tenant)
            conn.execute(
                ClassSession.__table__.update()
                .where(ClassSession.id == session_id)
                .values(location="SQL room")
            )
        db.autoflush = False
        if value == "DELETE":
            delattr(row, field)
        else:
            setattr(row, field, uuid4() if value == "KEY" else value)
        attribute = inspect(row).attrs[field]
        before = (attribute.loaded_value, attribute.history)
        assert field not in inspect(row).unmodified
        with pytest.raises(SchedulingBadRequest, match="Unknown status"):
            update(db, tenant, session_id, status="invalid")
        assert (attribute.loaded_value, attribute.history) == before
        assert row.location == "SQL room"
        assert inspect(row).identity == (session_id,)
        db.rollback()


def test_scheduling_native_explicit_flush_failure_is_not_reset(scheduling_database):
    _, app, tenant, _, cohort, *_ = scheduling_database
    with scoped(app, tenant) as db:
        session_id = create(db, tenant, cohort).id
    with scoped(app, tenant) as db:
        row = db.get(ClassSession, session_id)
        db.autoflush = False
        row.tenant_id = None
        with pytest.raises(ProgrammingError) as denied:
            update(db, tenant, session_id, title="native failure")
        assert denied.value.orig.sqlstate == "42501"
        assert not db.is_active
        db.rollback()
    with scoped(app, tenant) as db:
        assert db.get(ClassSession, session_id).title == "Class"
        assert db.scalar(select(func.count()).select_from(AuditEvent)) == 0


def test_pending_notes_wait_for_row_writer_and_use_current_audit_values(
    scheduling_database,
):
    _, app, tenant, _, cohort, *_ = scheduling_database
    with scoped(app, tenant) as db:
        session = create(db, tenant, cohort)
    cached, written, started, release = Event(), Event(), Event(), Event()
    pids = {}

    def winner():
        assert cached.wait(10)
        with app.begin() as conn:
            _scope(conn, tenant)
            conn.execute(text("SET LOCAL statement_timeout='15s'"))
            pids["winner"] = conn.scalar(text("SELECT pg_backend_pid()"))
            conn.execute(
                ClassSession.__table__.update()
                .where(ClassSession.id == session.id)
                .values(title="winner")
            )
            written.set()
            assert release.wait(10)
        return "committed"

    def contender():
        with scoped(app, tenant) as db:
            db.execute(text("SET LOCAL statement_timeout='15s'"))
            row = db.get(ClassSession, session.id)
            assert row.title == "Class"
            pids["contender"] = db.scalar(text("SELECT pg_backend_pid()"))
            cached.set()
            assert written.wait(10)
            root = db.get_transaction()
            with db.begin_nested():
                nested = db.get_nested_transaction()
                db.autoflush = False
                row.notes = "pending through lock wait"
                started.set()
                result = update(db, tenant, session.id, title="contender")
                assert db.get_transaction() is root
                assert db.get_nested_transaction() is nested
                assert row.title == result.session.title == "contender"
                assert row.notes == result.session.notes == "pending through lock wait"
                assert [
                    (c.field, c.from_value, c.to_value) for c in result.changes
                ] == [("title", "winner", "contender")]
                audit = db.get(AuditEvent, result.audit_event_id)
                assert audit is not None and audit.id == result.audit_event_id
                assert audit.details["changes"] == {
                    "title": {"from": "winner", "to": "contender"}
                }
                assert audit.is_success is None and audit.occurred_at is not None
                return result.audit_event_id

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
                    sleep(0.01)  # Pacing; the PostgreSQL lock state is the proof.
                else:
                    pytest.fail("session update did not wait for the row writer")
        finally:
            release.set()
            cached.set()
            written.set()
        assert one.result(timeout=20) == "committed"
        event_id = two.result(timeout=20)
    with scoped(app, tenant) as db:
        assert isinstance(event_id, UUID)
        audit = db.get(AuditEvent, event_id)
        assert audit is not None
        assert audit.tenant_id == tenant and audit.entity_id == str(session.id)
        assert audit.action == "scheduling.update_session"
        assert db.scalar(select(func.count()).select_from(AuditEvent)) == 1
        assert (
            scheduling.get_session(db, tenant_id=tenant, session_id=session.id).title
            == "contender"
        )
        unchanged = update(db, tenant, session.id, title="contender")
        assert unchanged.changes == () and unchanged.audit_event_id is None
        assert db.scalar(select(func.count()).select_from(AuditEvent)) == 1


@pytest.mark.parametrize(
    "state",
    ["transient", "pending", "external_deleted", "pending_delete", "flushed_delete"],
)
def test_scheduling_native_membership_and_missing_sql(scheduling_database, state):
    _, app, tenant, _, cohort, *_ = scheduling_database
    if state not in {"transient", "pending"}:
        with scoped(app, tenant) as db:
            row_id = create(db, tenant, cohort).id
    else:
        row_id = uuid4()
    with scoped(app, tenant) as db:
        db.autoflush = False
        if state in {"transient", "pending"}:
            row = ClassSession(
                id=row_id,
                tenant_id=tenant,
                cohort_id=cohort,
                title="unpersisted",
                starts_at=NOW,
                status="scheduled",
                session_type="live_class",
            )
            if state == "pending":
                db.add(row)
        else:
            row = db.get(ClassSession, row_id)
            if state == "external_deleted":
                with app.begin() as conn:
                    _scope(conn, tenant)
                    conn.execute(
                        ClassSession.__table__.delete().where(ClassSession.id == row_id)
                    )
            else:
                db.delete(row)
                if state == "flushed_delete":
                    db.flush()
        if state == "pending_delete":
            with pytest.raises(SchedulingBadRequest, match="Unknown status"):
                update(db, tenant, row_id, status="invalid")
            assert row in db.deleted and row not in db.new
        else:
            with pytest.raises(SchedulingNotFound):
                update(db, tenant, row_id)
        if state == "transient":
            assert inspect(row).transient
        elif state == "pending":
            assert row in db.new and inspect(row).pending
        elif state == "flushed_delete":
            assert inspect(row).deleted
        db.rollback()


def test_scheduling_early_guard_does_not_flush_invalid_unrelated_row(
    scheduling_database,
):
    _, app, tenant, _, cohort, *_ = scheduling_database
    with scoped(app, tenant) as db:
        row_id = create(db, tenant, cohort).id
    with scoped(app, tenant) as db:
        unrelated = db.get(Cohort, cohort)
        db.autoflush = False
        unrelated.name = None
        history = inspect(unrelated).attrs.name.history
        with pytest.raises(SchedulingBadRequest, match="Unknown status"):
            update(db, tenant, row_id, status="invalid")
        assert db.is_active and inspect(unrelated).attrs.name.history == history
        assert db.scalar(select(func.count()).select_from(AuditEvent)) == 0
        db.rollback()


def test_scheduling_scalar_projection_matches_actual_mapped_keys():
    mapper = inspect(ClassSession)
    assert set(mapper.column_attrs.keys()) == set(ClassSession.__table__.columns.keys())
    assert all(
        attribute.key == attribute.columns[0].key and len(attribute.columns) == 1
        for attribute in mapper.column_attrs
    )
