"""Hosted PostgreSQL scheduling parity, audit and non-bypass isolation canaries.

Uses the real Alembic learning@head fixture. Delivery and tenant-audit revisions
must be integrated before execution; no metadata creation or stamps substitute.
"""

from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta
from uuid import uuid4

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
from sqlalchemy import create_engine, event, func, select, text
from sqlalchemy.exc import DBAPIError, IntegrityError

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
            assert update(db, tenant, session.id).changes == ()
        assert flushes
        assert db.scalar(select(func.count()).select_from(AuditEvent)) == 0

        def forbidden(*args, **kwargs):
            raise AssertionError("tenant mismatch must precede SQL")

        monkeypatch.setattr(db, "scalar", forbidden)
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
