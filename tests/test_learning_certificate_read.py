"""Hosted genuine owner reads; authored only, never executed locally."""

from contextlib import nullcontext
from dataclasses import FrozenInstanceError
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from dotmac_kernel.audit import AuditEvent
from dotmac_learning import CertificateFact, get_certificate
from dotmac_learning.models import Certificate, LearningEvent
from sqlalchemy import event, func, inspect, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import defer
from sqlalchemy.orm.attributes import NO_VALUE

from tests import test_learning_results as fixtures
from tests.test_learning_catalog import _scope
from tests.test_learning_results import scoped

learning_database = fixtures.learning_database
result_database = fixtures.result_database


def test_public_record_read_is_scoped_detached_and_has_no_issuance_gate(
    result_database,
):
    admin, app, tenant, person, course, _, foreign, *_ = result_database
    issued = datetime(2026, 10, 1, tzinfo=UTC)
    with scoped(app, tenant) as db:
        root = db.get_transaction()
        assert (
            get_certificate(db, tenant_id=tenant, person_id=person, course_id=course)
            is None
        )
        assert not db.new and db.get_transaction() is root
        # Historical read fixture, deliberately with no completed aggregate.
        cert = Certificate(
            tenant_id=tenant,
            person_id=person,
            course_id=course,
            serial="HISTORICAL-READ",
            issued_at=issued,
        )
        db.add(cert)
        fact = get_certificate(db, tenant_id=tenant, person_id=person, course_id=course)
        assert isinstance(fact, CertificateFact) and fact.id == cert.id
        assert fact.serial == "HISTORICAL-READ" and fact.issued_at == issued
        assert (
            fact.tenant_id == tenant
            and fact.person_id == person
            and fact.course_id == course
        )
        assert db.get_transaction() is root
        with pytest.raises((FrozenInstanceError, AttributeError)):
            fact.serial = "mutated"
        for scope in [
            (foreign, person, course),
            (tenant, uuid4(), course),
            (tenant, person, uuid4()),
        ]:
            assert (
                get_certificate(
                    db, tenant_id=scope[0], person_id=scope[1], course_id=scope[2]
                )
                is None
            )
        cert_id = cert.id
        db.rollback()
    with admin.connect() as conn:
        assert (
            conn.scalar(select(Certificate.id).where(Certificate.id == cert_id)) is None
        )


def test_read_refreshes_existing_identity_without_committing(result_database):
    admin, app, tenant, person, course, *_ = result_database
    with scoped(app, tenant) as db:
        cert = Certificate(
            tenant_id=tenant,
            person_id=person,
            course_id=course,
            serial="BEFORE",
            issued_at=datetime.now(UTC),
        )
        db.add(cert)
        db.flush()
        cert_id = cert.id
    with scoped(app, tenant) as db:
        cached = db.get(Certificate, cert_id)
        with admin.begin() as conn:
            conn.execute(
                update(Certificate)
                .where(Certificate.id == cert_id)
                .values(serial="AFTER")
            )
        root = db.get_transaction()
        fact = get_certificate(db, tenant_id=tenant, person_id=person, course_id=course)
        assert fact.serial == "AFTER" and cached.serial == "AFTER"
        assert db.get_transaction() is root and not db.new and not db.dirty


@pytest.mark.parametrize("mode", ["disabled", "suppressed"])
@pytest.mark.parametrize("nested", [False, True])
@pytest.mark.parametrize("finish", ["flush", "rollback"])
def test_pending_serial_and_current_clean_issued_at_preserve_caller(
    result_database, mode, nested, finish
):
    _, app, tenant, person, course, *_ = result_database
    before = datetime(2026, 9, 1, tzinfo=UTC)
    after = datetime(2026, 10, 1, tzinfo=UTC)
    with scoped(app, tenant) as db:
        cert = Certificate(
            tenant_id=tenant,
            person_id=person,
            course_id=course,
            serial="PERSISTED",
            issued_at=before,
        )
        db.add(cert)
        db.flush()
        cert_id = cert.id
    with scoped(app, tenant) as db:
        cached = db.get(Certificate, cert_id)
        with app.begin() as conn:
            _scope(conn, tenant)
            conn.execute(
                update(Certificate)
                .where(Certificate.id == cert_id)
                .values(issued_at=after)
            )
        root = db.get_transaction()
        savepoint = db.begin_nested() if nested else None
        # SQLAlchemy materializes a caller savepoint lazily on the first SELECT.
        expected_read_sql = (["savepoint"] if nested else []) + ["select"]
        original_autoflush = db.autoflush
        db.autoflush = mode != "disabled"
        cached.serial = "CALLER"
        history = inspect(cached).attrs.serial.history
        seen, flushes = [], []

        def observe(conn, cursor, statement, parameters, context, many):
            seen.append(statement.lstrip().split()[0].lower())

        def flushed(*args):
            flushes.append(True)

        event.listen(app, "before_cursor_execute", observe)
        event.listen(db, "before_flush", flushed)
        try:
            with db.no_autoflush if mode == "suppressed" else nullcontext():
                fact = get_certificate(
                    db, tenant_id=tenant, person_id=person, course_id=course
                )
            assert fact.id == cert_id and fact.serial == "CALLER"
            assert fact.issued_at == cached.issued_at == after
            assert db.get(Certificate, cert_id) is cached
            assert inspect(cached).attrs.serial.history == history
            assert inspect(cached).attrs.issued_at.history.has_changes() is False
            assert seen == expected_read_sql and flushes == [] and not db.new
            assert db.get_transaction() is root
            assert db.get_nested_transaction() is savepoint
        finally:
            event.remove(app, "before_cursor_execute", observe)
            event.remove(db, "before_flush", flushed)
            db.autoflush = original_autoflush
        with db.no_autoflush:
            assert db.scalar(select(func.count()).select_from(LearningEvent)) == 0
            assert db.scalar(select(func.count()).select_from(AuditEvent)) == 0
        if finish == "flush":
            db.flush()
            assert db.scalar(select(Certificate.serial)) == "CALLER"
        db.rollback()
    with scoped(app, tenant) as db:
        assert db.scalar(select(Certificate.serial)) == "PERSISTED"
        assert db.scalar(select(Certificate.issued_at)) == after


@pytest.mark.parametrize("clean", ["expired", "deferred"])
@pytest.mark.parametrize(
    "field,value",
    [
        ("serial", "ORIGINAL"),
        ("serial", None),
        ("serial", "DELETE"),
        ("id", "KEY"),
        ("tenant_id", "KEY"),
        ("person_id", "KEY"),
        ("course_id", "KEY"),
    ],
)
def test_certificate_conservative_scalar_history_and_lookup_keys(
    result_database, clean, field, value
):
    _, app, tenant, person, course, *_ = result_database
    with scoped(app, tenant) as db:
        row = Certificate(
            tenant_id=tenant,
            person_id=person,
            course_id=course,
            serial="ORIGINAL",
            issued_at=datetime(2026, 9, 1, tzinfo=UTC),
        )
        db.add(row)
        db.flush()
        row_id = row.id
    with scoped(app, tenant) as db:
        row = db.scalars(
            select(Certificate)
            .options(defer(Certificate.issued_at))
            .where(Certificate.id == row_id)
        ).one()
        if clean == "expired":
            db.expire(row, ["issued_at"])
        assert inspect(row).attrs.issued_at.loaded_value is NO_VALUE
        with app.begin() as conn:
            _scope(conn, tenant)
            conn.execute(
                update(Certificate)
                .where(Certificate.id == row_id)
                .values(issued_at=datetime(2026, 10, 1, tzinfo=UTC))
            )
        db.autoflush = False
        if value == "DELETE":
            delattr(row, field)
        else:
            setattr(row, field, uuid4() if value == "KEY" else value)
        attribute = inspect(row).attrs[field]
        before = (attribute.loaded_value, attribute.history)
        assert field not in inspect(row).unmodified
        root = db.get_transaction()
        fact = get_certificate(db, tenant_id=tenant, person_id=person, course_id=course)
        assert fact is not None
        assert (attribute.loaded_value, attribute.history) == before
        assert fact.issued_at == datetime(2026, 10, 1, tzinfo=UTC)
        assert db.get_transaction() is root
        assert inspect(row).identity == (row_id,)
        db.rollback()


@pytest.mark.parametrize(
    "state",
    ["transient", "pending", "external_deleted", "pending_delete", "flushed_delete"],
)
def test_certificate_missing_rows_and_native_membership(result_database, state):
    _, app, tenant, person, course, *_ = result_database
    row = Certificate(
        tenant_id=tenant,
        person_id=person,
        course_id=course,
        serial="MEMBERSHIP",
        issued_at=datetime.now(UTC),
    )
    if state not in {"transient", "pending"}:
        with scoped(app, tenant) as db:
            db.add(row)
            db.flush()
            row_id = row.id
    with scoped(app, tenant) as db:
        db.autoflush = False
        if state == "pending":
            db.add(row)
        elif state not in {"transient", "pending"}:
            row = db.get(Certificate, row_id)
            if state == "external_deleted":
                with app.begin() as conn:
                    _scope(conn, tenant)
                    conn.execute(
                        Certificate.__table__.delete().where(Certificate.id == row_id)
                    )
            else:
                db.delete(row)
                if state == "flushed_delete":
                    db.flush()
        fact = get_certificate(db, tenant_id=tenant, person_id=person, course_id=course)
        if state == "pending_delete":
            assert fact.id == row_id and row in db.deleted
            assert row not in db.new
        else:
            assert fact is None
        if state == "transient":
            assert inspect(row).transient
        if state == "pending":
            assert row in db.new and inspect(row).pending
        if state == "flushed_delete":
            assert inspect(row).deleted
        db.rollback()


def test_certificate_native_autoflush_and_failure_remain_caller_owned(result_database):
    _, app, tenant, person, course, *_ = result_database
    with scoped(app, tenant) as db:
        row = Certificate(
            tenant_id=tenant,
            person_id=person,
            course_id=course,
            serial="AUTOFLSH",
            issued_at=datetime.now(UTC),
        )
        db.add(row)
        assert db.autoflush
        fact = get_certificate(db, tenant_id=tenant, person_id=person, course_id=course)
        assert fact.id == row.id and not db.new
        row.serial = None
        db.autoflush = False
        fact = get_certificate(db, tenant_id=tenant, person_id=person, course_id=course)
        assert fact.serial is None and db.is_active
        with pytest.raises(IntegrityError):
            db.flush()
        assert not db.is_active
        db.rollback()
    with scoped(app, tenant) as db:
        assert (
            get_certificate(db, tenant_id=tenant, person_id=person, course_id=course)
            is None
        )


@pytest.mark.parametrize("mode", ["disabled", "suppressed"])
def test_certificate_lookup_does_not_flush_unrelated_invalid_pending_row(
    result_database, mode
):
    _, app, tenant, person, course, *_ = result_database
    with scoped(app, tenant) as db:
        cert = Certificate(
            tenant_id=tenant,
            person_id=person,
            course_id=course,
            serial="VALID",
            issued_at=datetime.now(UTC),
        )
        db.add(cert)
        db.flush()
        cert_id = cert.id
        invalid = Certificate(
            tenant_id=None,
            person_id=person,
            course_id=course,
            serial=None,
            issued_at=datetime.now(UTC),
        )
        db.add(invalid)
        db.autoflush = mode != "disabled"
        with db.no_autoflush if mode == "suppressed" else nullcontext():
            fact = get_certificate(
                db, tenant_id=tenant, person_id=person, course_id=course
            )
            assert fact.id == cert_id and fact.serial == "VALID"
            assert invalid in db.new and db.is_active
        db.rollback()


def test_certificate_scalar_projection_matches_actual_mapped_keys():
    mapper = inspect(Certificate)
    assert set(mapper.column_attrs.keys()) == set(Certificate.__table__.columns.keys())
    assert all(
        attribute.key == attribute.columns[0].key and len(attribute.columns) == 1
        for attribute in mapper.column_attrs
    )
