"""Hosted PostgreSQL prerequisite parity, using actual Kernel/learning heads."""

from uuid import uuid4

import pytest
from dotmac_learning.models import Course
from dotmac_learning.participation_models import CoursePrerequisite
from dotmac_learning.prerequisite_commands import reconcile_prerequisites
from dotmac_learning.prerequisite_contracts import (
    PrerequisiteDeclaration,
    PrerequisiteReconciliationError,
    ReconcilePrerequisitesCommand,
)
from sqlalchemy import create_engine, event, select, text
from sqlalchemy.orm import Session

from tests import test_learning_catalog as learning_catalog
from tests.test_learning_catalog import _course, _scope, _tenant

learning_database = learning_catalog.learning_database


@pytest.fixture
def prerequisite_case(learning_database):
    admin, actor = (create_engine(url) for url in learning_database)
    tenant, other = uuid4(), uuid4()
    try:
        with admin.begin() as conn:
            _tenant(conn, tenant)
            _tenant(conn, other)
        with actor.begin() as conn:
            _scope(conn, tenant)
            ids = tuple(_course(conn, tenant) for _ in range(4))
            conn.execute(
                Course.__table__.update()
                .where(Course.id == ids[2])
                .values(discipline="other")
            )
            _scope(conn, other)
            foreign = _course(conn, other)
        yield actor, tenant, other, ids, foreign
    finally:
        actor.dispose()
        admin.dispose()


def _command(tenant, declarations=(), discipline="engineering"):
    return ReconcilePrerequisitesCommand(
        tenant_id=tenant,
        discipline=discipline,
        declarations=tuple(
            PrerequisiteDeclaration(course=course, requires=requires)
            for course, requires in declarations
        ),
    )


def _pairs(db):
    return set(
        db.execute(
            select(CoursePrerequisite.course_id, CoursePrerequisite.requires_course_id)
        ).all()
    )


def test_cross_tenant_refusal_and_isolation(prerequisite_case):
    engine, tenant, other, (a, b, _, _), foreign = prerequisite_case
    with Session(engine) as db, db.begin():
        _scope(db, tenant)
        assert db.execute(
            text(
                "SELECT rolbypassrls, rolsuper FROM pg_roles WHERE rolname=current_user"
            )
        ).one() == (False, False)
        with pytest.raises(PrerequisiteReconciliationError, match=foreign.hex):
            reconcile_prerequisites(db, _command(tenant, [(a.hex, (foreign.hex,))]))
        assert not db.new and not db.deleted
        result = reconcile_prerequisites(db, _command(tenant, [(a.hex, (b.hex,))]))
        assert (result.added, result.removed, result.declared) == (1, 0, 1)
    with Session(engine) as db, db.begin():
        _scope(db, other)
        assert _pairs(db) == set()
        with pytest.raises(PrerequisiteReconciliationError, match=a.hex):
            reconcile_prerequisites(db, _command(other, [(a.hex, (b.hex,))]))
        # Explicit tenant predicate remains effective with a mismatched input scope.
        with pytest.raises(PrerequisiteReconciliationError, match=a.hex):
            reconcile_prerequisites(db, _command(tenant, [(a.hex, (b.hex,))]))


def test_duplicates_cross_discipline_addition_scoped_delete_and_empty_unknown(
    prerequisite_case,
):
    engine, tenant, _, (a, b, c, d), _ = prerequisite_case
    with Session(engine) as db, db.begin():
        _scope(db, tenant)
        first = reconcile_prerequisites(
            db,
            _command(
                tenant,
                [
                    (a.hex, (b.hex, b.hex)),
                    (c.hex, (d.hex,)),
                    ("unknown", ()),
                ],
            ),
        )
        assert (first.added, first.removed, first.declared) == (2, 0, 2)
    with Session(engine) as db, db.begin():
        _scope(db, tenant)
        repeated = reconcile_prerequisites(
            db,
            _command(
                tenant,
                [
                    (a.hex, (b.hex,)),
                    (c.hex, (d.hex,)),
                ],
            ),
        )
        assert (repeated.added, repeated.removed, repeated.declared) == (0, 0, 2)
        result = reconcile_prerequisites(db, _command(tenant))
        assert (result.added, result.removed, result.declared) == (0, 1, 0)
        assert _pairs(db) == {(c, d)}
        # A missing discipline matches no nonnullable Course.discipline.
        result = reconcile_prerequisites(db, _command(tenant, discipline=None))
        assert result.removed == 0
        # Existing edges are not incorporated into wanted-only cycle validation.
        result = reconcile_prerequisites(db, _command(tenant, [(d.hex, (c.hex,))]))
        assert result.added == 1
        assert _pairs(db) == {(c, d), (d, c)}


@pytest.mark.parametrize("kind", ["missing", "missing_cycle", "self", "cycle"])
def test_failure_precedence_no_owned_dml_but_caller_autoflush(
    prerequisite_case,
    kind,
):
    engine, tenant, _, (a, b, _, _), _ = prerequisite_case
    statements = []

    def record(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement.lower())

    event.listen(engine, "before_cursor_execute", record)
    try:
        with Session(engine) as db:
            _scope(db, tenant)
            caller_course = db.get(Course, a)
            caller_course.title = "pending caller edit"
            declarations = {
                "missing": [("z", ("a", "z")), ("a", ("z",))],
                "missing_cycle": [
                    (a.hex, (b.hex,)),
                    (b.hex, (a.hex,)),
                    ("z", ("a",)),
                ],
                "self": [("missing", (b.hex,)), (a.hex, (a.hex,))],
                "cycle": [(a.hex, (b.hex,)), (b.hex, (a.hex,))],
            }[kind]
            expected = {
                "missing": (
                    "These courses are declared in the file but not imported: a, z"
                ),
                "missing_cycle": (
                    "These courses are declared in the file but not imported: a, z"
                ),
                "self": f"{a.hex} cannot require itself",
                "cycle": "prerequisite cycle detected; refusing to write",
            }[kind]
            statements.clear()
            with pytest.raises(PrerequisiteReconciliationError) as caught:
                reconcile_prerequisites(db, _command(tenant, declarations))
            assert str(caught.value) == expected
            assert any(s.startswith("update mod_learning.courses") for s in statements)
            assert not any("course_prerequisites" in s for s in statements)
            assert not db.new and not db.deleted
            assert db.scalar(select(Course.title).where(Course.id == a)) == (
                "pending caller edit"
            )
            db.rollback()
        with Session(engine) as db, db.begin():
            _scope(db, tenant)
            assert db.get(Course, a).title == "Course"
    finally:
        event.remove(engine, "before_cursor_execute", record)


def test_staged_mutations_no_explicit_flush_and_outer_rollback(prerequisite_case):
    engine, tenant, _, (a, b, c, _), _ = prerequisite_case
    with Session(engine) as db, db.begin():
        _scope(db, tenant)
        reconcile_prerequisites(db, _command(tenant, [(a.hex, (b.hex,))]))
    statements = []

    def record(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement.lower())

    event.listen(engine, "before_cursor_execute", record)
    try:
        with Session(engine) as db:
            _scope(db, tenant)
            result = reconcile_prerequisites(db, _command(tenant, [(a.hex, (c.hex,))]))
            assert (result.added, result.removed, result.declared) == (1, 1, 1)
            assert len(db.new) == len(db.deleted) == 1
            assert not any(
                s.startswith(("insert", "delete", "update")) for s in statements
            )
            db.flush()  # Caller owns flush and rollback.
            assert _pairs(db) == {(a, c)}
            db.rollback()
        with Session(engine) as db, db.begin():
            _scope(db, tenant)
            assert _pairs(db) == {(a, b)}
    finally:
        event.remove(engine, "before_cursor_execute", record)
