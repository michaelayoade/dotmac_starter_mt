"""Assembly test harness (kernel-boundary Task 5).

The in-memory-SQLite engine + savepoint-isolated session + TestClient wiring the
reference repo's `tests/unit/conftest.py` used to hand-build, packaged so any
consumer assembly gets the same fast unit-test setup without copying it.

SQLite has no RLS — this harness is for service-logic/unit tests; tenancy
enforcement is proven separately against real Postgres. `create_test_engine`
does `Base.metadata.create_all`, so the ASSEMBLY must import its own feature
models before calling it (that populates the shared `Base.metadata`).

`dotmac_kernel.deps` is imported INSIDE `assembly_test_client`, not at module
scope. Importing it pulls `dotmac_kernel.db`, which constructs the SQLAlchemy
engine from `DATABASE_URL` at import — which made `import
dotmac_kernel.testing` fail outright without a database, so a consumer could
not reach the fakes or the in-memory engine (neither of which needs one). Only
the TestClient helper, which is building a real app anyway, pays that cost.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager
from typing import TYPE_CHECKING

from fastapi import FastAPI
from sqlalchemy import Engine, Table, create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from dotmac_kernel.models import Base

if TYPE_CHECKING:
    from fastapi.testclient import TestClient


def _module_schemas(tables: Iterable[Table]) -> tuple[str, ...]:
    """Every distinct non-default schema bound to the selected tables.

    Read off the tables rather than off `MIGRATION_OWNER_LEDGER` on purpose:
    the harness must attach exactly what the caller actually imported, and it
    must not care whether a schema belongs to an installed module, so it never
    needs to import one.
    """
    return tuple(sorted({table.schema for table in tables if table.schema}))


_SQLITE_MAX_ATTACHED_SCHEMAS = 10


def _sqlite_schema_plan(
    schemas: tuple[str, ...], tables: tuple[Table, ...]
) -> tuple[dict[str, str], tuple[str, ...]]:
    """Fit module namespaces inside SQLite's ten-attachment hard limit.

    PostgreSQL remains the namespace and isolation proof. The fast SQLite lane
    keeps as many real ``mod_*`` aliases as SQLite permits. Any overflow is
    translated to SQLite's built-in, explicitly qualified ``main`` namespace,
    but only when its table names cannot collide with public or another
    translated table. A collision fails loudly instead of silently merging two
    owners.
    """
    overflow = max(0, len(schemas) - _SQLITE_MAX_ATTACHED_SCHEMAS)
    if overflow == 0:
        return {}, schemas

    occupied = {table.name for table in tables if table.schema is None}
    names_by_schema = {
        schema: {table.name for table in tables if table.schema == schema}
        for schema in schemas
    }
    translated: list[str] = []
    # Prefer the smallest safe namespace: this preserves exact ``mod_*`` SQL
    # for the greatest number of module tables in the fast lane.
    for schema in sorted(schemas, key=lambda item: (len(names_by_schema[item]), item)):
        names = names_by_schema[schema]
        if names.isdisjoint(occupied):
            translated.append(schema)
            occupied.update(names)
        if len(translated) == overflow:
            break
    if len(translated) != overflow:
        raise RuntimeError(
            "SQLite cannot represent every imported module namespace within "
            "its ten attached-database limit without a table-name collision; "
            "run this case on PostgreSQL or reduce the imported metadata"
        )
    translated_set = set(translated)
    attached = tuple(schema for schema in schemas if schema not in translated_set)
    return {schema: "main" for schema in translated}, attached


def create_test_engine(*, tables: Iterable[Table] | None = None) -> Engine:
    """A fresh in-memory SQLite engine with the selected `Base.metadata` tables.

    `check_same_thread=False` because a TestClient runs sync route dependencies
    on a worker thread while the test holds one connection — sequential use
    only, never concurrent.

    `tables` defaults to every table currently registered on `Base.metadata`.
    A large shared test process SHOULD pass the exact assembly/package table
    snapshot it is exercising: test collection may import many uncomposed
    packages into the shared metadata, while SQLite supports at most ten
    attached databases. Selection is explicit rather than pretending an
    uncomposed package belongs to the assembly. If the selected surface itself
    crosses SQLite's attachment limit, collision-free overflow namespaces are
    translated deterministically to SQLite's qualified ``main`` namespace.

    **Module schemas (ADR-0006 D1).** A stateful module binds its models to
    `mod_<short_code>` via `namespaces.schema_table_args`, so the ORM emits
    fully qualified `mod_x.thing` — which is the entire point of D1, and which
    plain SQLite rejects because it has no schemas. Each such schema is
    therefore ATTACHed as its own in-memory database before `create_all`, on
    every connection.

    ATTACH keeps emitted SQL identical to production's until SQLite's hard
    limit of ten attached databases is reached. Beyond that limit, the smallest
    collision-free overflow namespace is translated to the explicitly
    qualified built-in ``main`` namespace. Static architecture gates and the
    required PostgreSQL lane remain the proof of the original namespace and
    isolation; SQLite remains a service-logic lane.
    """
    selected_tables = tuple(Base.metadata.tables.values() if tables is None else tables)
    schemas = _module_schemas(selected_tables)
    translated, attached = _sqlite_schema_plan(schemas, selected_tables)
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        future=True,
        connect_args={"check_same_thread": False},
        execution_options={"schema_translate_map": translated},
    )
    if attached:

        @event.listens_for(engine, "connect")
        def _attach_module_schemas(dbapi_connection: object, _record: object) -> None:
            cursor = dbapi_connection.cursor()  # type: ignore[attr-defined]
            try:
                for schema in attached:
                    # Identifier-quoted, and the names are `mod_<short_code>`
                    # already validated by `namespaces.validate_schema` — this
                    # is defence in depth, not the only check.
                    cursor.execute(f"ATTACH DATABASE ':memory:' AS \"{schema}\"")
            finally:
                cursor.close()

    Base.metadata.create_all(engine, tables=selected_tables)
    return engine


@contextmanager
def isolated_session(engine: Engine) -> Iterator[Session]:
    """A session wrapped in an outer transaction + a restarting SAVEPOINT, so a
    test is fully rolled back even if service code commits. Same pattern the
    reference `db` fixture used."""
    connection = engine.connect()
    outer = connection.begin()
    factory = sessionmaker(bind=connection, autocommit=False, autoflush=False)
    session = factory()
    connection.begin_nested()

    @event.listens_for(session, "after_transaction_end")
    def _restart_savepoint(sess: Session, trans) -> None:
        if trans.nested and not trans._parent.nested:
            connection.begin_nested()

    try:
        yield session
    finally:
        session.close()
        outer.rollback()
        connection.close()


@contextmanager
def assembly_test_client(app: FastAPI, *, session: Session) -> Iterator[TestClient]:
    """A `TestClient` for a `create_app`-built app with the request/platform DB
    dependencies overridden to `session` (the isolated test session). Overrides
    are removed on exit so the app is left clean.

    `TestClient` (and its `httpx` dependency) and `dotmac_kernel.deps` are both
    imported lazily, so `import dotmac_kernel.testing` — and the engine,
    session and fakes it exposes — stays usable without the test HTTP stack AND
    without `DATABASE_URL`; install `dotmac-kernel[testing]` to use this
    helper."""
    from fastapi.testclient import TestClient

    from dotmac_kernel.deps import get_db, get_platform_db

    deps: list[Callable[..., object]] = [get_db, get_platform_db]
    for dep in deps:
        app.dependency_overrides[dep] = lambda: session
    try:
        with TestClient(app) as client:
            yield client
    finally:
        for dep in deps:
            app.dependency_overrides.pop(dep, None)


__all__ = ["assembly_test_client", "create_test_engine", "isolated_session"]
