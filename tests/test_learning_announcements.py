"""Hosted source-audience, optional fanout and required audit canaries."""

from dataclasses import FrozenInstanceError
from datetime import timedelta
from uuid import uuid4

import pytest
from dotmac_kernel import audit_actions
from dotmac_kernel.audit import AuditEvent, MissingAuditActorError
from dotmac_learning import announcements
from dotmac_learning.announcement_contracts import (
    AnnouncementFact,
    CreateAnnouncementCommand,
    DeleteAnnouncementCommand,
)
from dotmac_learning.attempt_contracts import AttemptAuditContext
from dotmac_learning.delivery_models import Announcement
from dotmac_learning.manifest import module
from dotmac_learning.participation_models import Cohort, Enrollment
from sqlalchemy import event, func, select, text
from sqlalchemy.exc import DBAPIError

from tests import test_learning_results as result_tests
from tests.test_learning_catalog import _person
from tests.test_learning_results import scoped

learning_database = result_tests.learning_database
result_database = result_tests.result_database


@pytest.fixture(autouse=True)
def declared_audit(monkeypatch):
    monkeypatch.setattr(
        audit_actions,
        "_active_registry",
        audit_actions.AuditActionRegistry.from_manifests([module]),
    )


@pytest.fixture
def announcement_database(result_database):
    admin, app, tenant, author, _, _, other, other_author, *_ = result_database
    with admin.begin() as conn:
        student = _person(conn, tenant)
        instructor = _person(conn, tenant)
        elsewhere = _person(conn, tenant)
        dropped = _person(conn, tenant)
        unenrolled = _person(conn, tenant)
    with scoped(app, tenant) as db:
        target = Cohort(tenant_id=tenant, name="Target", discipline="engineering")
        alternate = Cohort(tenant_id=tenant, name="Other", discipline="engineering")
        db.add_all([target, alternate])
        db.flush()
        target_id, alternate_id = target.id, alternate.id
        for person, cohort, role, status in (
            (student, target_id, "student", "active"),
            (instructor, target_id, "instructor", "active"),
            (elsewhere, alternate_id, "student", "active"),
            (dropped, target_id, "student", "dropped"),
            (student, alternate_id, "student", "active"),
        ):
            db.add(
                Enrollment(
                    tenant_id=tenant,
                    person_id=person,
                    cohort_id=cohort,
                    role_in_cohort=role,
                    status=status,
                )
            )
        db.flush()
    return {
        "app": app,
        "tenant": tenant,
        "author": author,
        "target": target_id,
        "alternate": alternate_id,
        "student": student,
        "instructor": instructor,
        "elsewhere": elsewhere,
        "dropped": dropped,
        "unenrolled": unenrolled,
        "other": other,
        "other_author": other_author,
    }


def command(fixture, *, cohort_id=None, title="  raw title  "):
    return CreateAnnouncementCommand(
        tenant_id=fixture["tenant"],
        author_person_id=fixture["author"],
        title=title,
        body_md="\n# raw Markdown\n",
        body_html="<h1>already rendered</h1>\n",
        cohort_id=cohort_id,
    )


def context(tenant, *, actor_type="system", actor_id=None):
    return AttemptAuditContext(
        tenant_id=tenant,
        actor_type=actor_type,
        actor_id=actor_id,
        actor_label="Announcement operator",
        source_application="academy",
    )


def no_fanout(_db, _fact, _recipients):
    pass


@pytest.mark.parametrize("targeted", [True, False])
def test_source_audience_and_detached_content(announcement_database, targeted):
    fixture = announcement_database
    calls = []
    with scoped(fixture["app"], fixture["tenant"]) as db:
        boundary = db.get_transaction()

        def fanout(caller, fact, recipients):
            assert caller is db
            assert isinstance(fact, AnnouncementFact)
            assert caller.get_transaction() is boundary
            assert caller.get_nested_transaction() is not None
            assert caller.scalar(select(func.count()).select_from(Announcement)) == 1
            assert caller.scalar(select(func.count()).select_from(AuditEvent)) == 0
            calls.append((fact, recipients))

        fact = announcements.create(
            db,
            command(fixture, cohort_id=fixture["target"] if targeted else None),
            audit_context=context(fixture["tenant"], actor_type="user", actor_id="p7"),
            fanout=fanout,
        )
        assert len(calls) == 1
        expected = (
            {fixture["student"]}
            if targeted
            else {fixture["student"], fixture["instructor"], fixture["elsewhere"]}
        )
        assert set(calls[0][1]) == expected
        assert len(calls[0][1]) == len(expected)
        assert fact.title == "  raw title  "
        assert fact.body_md == "\n# raw Markdown\n"
        assert fact.body_html == "<h1>already rendered</h1>\n"
        assert db.get_transaction() is boundary
        assert db.get_nested_transaction() is None
        audit = db.scalar(select(AuditEvent))
        assert audit.action == "announcement.created"
        assert audit.entity_type == "announcement" and audit.entity_id == str(fact.id)
        assert audit.actor_type == "user" and audit.actor_id == "p7"
        assert audit.actor_party_id == fixture["author"]
        assert audit.actor_label == "Announcement operator"
        assert audit.source_application == "academy"
        with pytest.raises(FrozenInstanceError):
            fact.title = "mutated"


def test_empty_audience_skips_adapter_but_requires_audit(result_database):
    _, app, tenant, author, *_ = result_database
    called = []
    with scoped(app, tenant) as db:
        fact = announcements.create(
            db,
            CreateAnnouncementCommand(
                tenant_id=tenant,
                author_person_id=author,
                title="empty",
                body_md="",
                body_html="",
            ),
            audit_context=context(tenant),
            fanout=lambda *args: called.append(args),
        )
        assert called == []
        assert db.scalar(select(AuditEvent.entity_id)) == str(fact.id)


def test_poisoned_partial_fanout_rolls_back_then_required_audit_succeeds(
    announcement_database,
):
    fixture = announcement_database
    with scoped(fixture["app"], fixture["tenant"]) as db:
        db.execute(
            text("CREATE TEMP TABLE announcement_delivery_probe (id uuid PRIMARY KEY)")
        )
        boundary = db.get_transaction()

        def poisoned(caller, _fact, recipients):
            assert recipients
            marker = uuid4()
            caller.execute(
                text("INSERT INTO announcement_delivery_probe VALUES (:id)"),
                {"id": marker},
            )
            # Real PostgreSQL failure poisons the child transaction.
            caller.execute(
                text("INSERT INTO announcement_delivery_probe VALUES (:id)"),
                {"id": marker},
            )

        fact = announcements.create(
            db,
            command(fixture),
            audit_context=context(fixture["tenant"]),
            fanout=poisoned,
        )
        assert db.is_active
        assert db.get_transaction() is boundary and db.get_nested_transaction() is None
        assert db.scalar(text("SELECT count(*) FROM announcement_delivery_probe")) == 0
        assert db.scalar(select(func.count()).select_from(Announcement)) == 1
        assert db.scalar(select(AuditEvent.entity_id)) == str(fact.id)
        db.rollback()
    with scoped(fixture["app"], fixture["tenant"]) as db:
        assert db.scalar(select(func.count()).select_from(Announcement)) == 0
        assert db.scalar(select(func.count()).select_from(AuditEvent)) == 0


@pytest.mark.parametrize("failure", ["undeclared", "actor"])
def test_required_audit_failure_propagates_after_announcement_flush(
    announcement_database,
    monkeypatch,
    failure,
):
    fixture = announcement_database
    if failure == "undeclared":
        monkeypatch.setattr(
            audit_actions, "_active_registry", audit_actions.AuditActionRegistry(())
        )
        error = audit_actions.UndeclaredAuditActionError
        authority = context(fixture["tenant"])
    else:
        error = MissingAuditActorError
        authority = context(fixture["tenant"], actor_type="user")
    with scoped(fixture["app"], fixture["tenant"]) as db:
        with pytest.raises(error):
            announcements.create(
                db, command(fixture), audit_context=authority, fanout=no_fanout
            )
        assert db.scalar(select(func.count()).select_from(Announcement)) == 1
        assert db.scalar(select(func.count()).select_from(AuditEvent)) == 0
        db.rollback()
    with scoped(fixture["app"], fixture["tenant"]) as db:
        assert db.scalar(select(func.count()).select_from(Announcement)) == 0


def test_scope_binding_precedes_any_sql(announcement_database):
    fixture = announcement_database
    with scoped(fixture["app"], fixture["tenant"]) as db:
        connection = db.connection()
        statements = []

        def observe(_conn, _cursor, statement, *_):
            statements.append(statement)

        event.listen(connection, "before_cursor_execute", observe)
        try:
            with pytest.raises(ValueError, match="scope"):
                announcements.create(
                    db,
                    command(fixture),
                    audit_context=context(fixture["other"]),
                    fanout=no_fanout,
                )
            assert statements == []
        finally:
            event.remove(connection, "before_cursor_execute", observe)


def test_initial_flush_failure_is_required_not_optional(result_database):
    _, app, tenant, _, _, _, _, _, root_only, *_ = result_database
    called = []
    with scoped(app, tenant) as db:
        with pytest.raises(DBAPIError):
            announcements.create(
                db,
                CreateAnnouncementCommand(
                    tenant_id=tenant,
                    author_person_id=root_only,
                    title="missing profile",
                    body_md="",
                    body_html="",
                ),
                audit_context=context(tenant),
                fanout=lambda *args: called.append(args),
            )
        assert called == []
        assert not db.is_active
        db.rollback()


def test_audience_query_failure_propagates_outside_optional_fanout(
    result_database,
):
    admin, app, tenant, author, *_ = result_database
    calls = []
    with admin.begin() as conn:
        conn.execute(text("REVOKE SELECT ON mod_learning.enrollments FROM app_user"))
    try:
        with scoped(app, tenant) as db:
            with pytest.raises(DBAPIError, match="permission denied"):
                announcements.create(
                    db,
                    CreateAnnouncementCommand(
                        tenant_id=tenant,
                        author_person_id=author,
                        title="required audience",
                        body_md="",
                        body_html="",
                    ),
                    audit_context=context(tenant),
                    fanout=lambda *args: calls.append(args),
                )
            assert calls == []
            db.rollback()
        with scoped(app, tenant) as db:
            assert db.scalar(select(func.count()).select_from(Announcement)) == 0
            assert db.scalar(select(func.count()).select_from(AuditEvent)) == 0
    finally:
        with admin.begin() as conn:
            conn.execute(text("GRANT SELECT ON mod_learning.enrollments TO app_user"))


def test_visibility_allows_any_active_role_and_tenant_wide_unenrolled(
    announcement_database,
):
    fixture = announcement_database
    with scoped(fixture["app"], fixture["tenant"]) as db:
        wide = announcements.create(
            db,
            command(fixture, title="wide"),
            audit_context=context(fixture["tenant"]),
            fanout=no_fanout,
        )
        targeted = announcements.create(
            db,
            command(fixture, cohort_id=fixture["target"], title="targeted"),
            audit_context=context(fixture["tenant"]),
            fanout=no_fanout,
        )
        assert {
            f.id
            for f in announcements.for_person(
                db,
                tenant_id=fixture["tenant"],
                person_id=fixture["instructor"],
                limit=20,
            )
        } == {wide.id, targeted.id}
        for person in (fixture["unenrolled"], fixture["dropped"], fixture["elsewhere"]):
            assert [
                f.id
                for f in announcements.for_person(
                    db, tenant_id=fixture["tenant"], person_id=person, limit=20
                )
            ] == [wide.id]


def test_nonbypass_tenant_reads_and_delete_are_scoped(announcement_database):
    fixture = announcement_database
    with scoped(fixture["app"], fixture["other"]) as db:
        other = announcements.create(
            db,
            CreateAnnouncementCommand(
                tenant_id=fixture["other"],
                author_person_id=fixture["other_author"],
                title="other",
                body_md="",
                body_html="",
            ),
            audit_context=context(fixture["other"]),
            fanout=no_fanout,
        )
    with scoped(fixture["app"], fixture["tenant"]) as db:
        assert db.execute(
            text(
                "SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname=current_user"
            )
        ).one() == (False, False)
        assert (
            announcements.list_for_tenant(db, tenant_id=fixture["other"], limit=50)
            == ()
        )
        announcements.delete(
            db,
            DeleteAnnouncementCommand(
                tenant_id=fixture["other"], announcement_id=other.id
            ),
        )
        assert db.scalar(select(func.count()).select_from(AuditEvent)) == 0
    with scoped(fixture["app"], fixture["other"]) as db:
        assert [
            f.id
            for f in announcements.list_for_tenant(
                db, tenant_id=fixture["other"], limit=50
            )
        ] == [other.id]


def test_order_limit_delete_no_audit_and_nested_caller_continuity(
    announcement_database,
):
    fixture = announcement_database
    with scoped(fixture["app"], fixture["tenant"]) as db:
        first = announcements.create(
            db,
            command(fixture, title="first"),
            audit_context=context(fixture["tenant"]),
            fanout=no_fanout,
        )
        second = announcements.create(
            db,
            command(fixture, title="second"),
            audit_context=context(fixture["tenant"]),
            fanout=no_fanout,
        )
        row = db.get(Announcement, first.id)
        row.created_at = second.created_at - timedelta(seconds=1)
        db.flush()
        assert [
            f.id
            for f in announcements.list_for_tenant(
                db, tenant_id=fixture["tenant"], limit=1
            )
        ] == [second.id]
        assert [
            f.id
            for f in announcements.for_person(
                db, tenant_id=fixture["tenant"], person_id=fixture["student"], limit=1
            )
        ] == [second.id]
        audit_count = db.scalar(select(func.count()).select_from(AuditEvent))
        root = db.get_transaction()
        with db.begin_nested():
            nested = db.get_nested_transaction()
            announcements.delete(
                db,
                DeleteAnnouncementCommand(
                    tenant_id=fixture["tenant"], announcement_id=first.id
                ),
            )
            announcements.delete(
                db,
                DeleteAnnouncementCommand(
                    tenant_id=fixture["tenant"], announcement_id=uuid4()
                ),
            )
            assert (
                db.get_transaction() is root and db.get_nested_transaction() is nested
            )
            assert (
                db.scalar(select(func.count()).select_from(AuditEvent)) == audit_count
            )
        assert [
            f.id
            for f in announcements.list_for_tenant(
                db, tenant_id=fixture["tenant"], limit=50
            )
        ] == [second.id]
