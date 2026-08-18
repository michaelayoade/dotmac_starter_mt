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

import sqlite3
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import TYPE_CHECKING

from fastapi import FastAPI
from sqlalchemy import Engine, MetaData, create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from dotmac_kernel.models import Base

if TYPE_CHECKING:
    from fastapi.testclient import TestClient


def _schema_tables(metadata: MetaData) -> dict[str | None, set[str]]:
    """Local table names grouped by their declared schema."""
    grouped: dict[str | None, set[str]] = {}
    for table in metadata.tables.values():
        grouped.setdefault(table.schema, set()).add(table.name)
    return grouped


def _sqlite_max_attached() -> int:
    """Return this Python build's real SQLite attachment ceiling."""
    connection = sqlite3.connect(":memory:")
    try:
        return connection.getlimit(sqlite3.SQLITE_LIMIT_ATTACHED)
    finally:
        connection.close()


def _sqlite_schema_layout(
    metadata: MetaData, *, max_attached: int
) -> tuple[tuple[str, ...], dict[str, str | None]]:
    """Allocate SQLite schema slots without allowing table-name collisions.

    SQLite's compile-time attachment limit is commonly ten.  Preserve exact
    schema names for the largest namespaces first; once every attachment slot
    is occupied, translate a smaller namespace into ``main`` or an already
    attached namespace only when their local table names are disjoint.
    """
    grouped = _schema_tables(metadata)
    module_schemas = sorted(
        ((schema, names) for schema, names in grouped.items() if schema is not None),
        key=lambda item: (-len(item[1]), item[0]),
    )
    main_names = set(grouped.get(None, set()))
    attached: list[str] = []
    occupied: dict[str | None, set[str]] = {None: main_names}
    translation: dict[str, str | None] = {}

    for schema, names in module_schemas:
        if len(attached) < max_attached:
            attached.append(schema)
            occupied[schema] = set(names)
            continue

        target = next(
            (
                candidate
                for candidate, existing_names in occupied.items()
                if names.isdisjoint(existing_names)
            ),
            ...,
        )
        if target is ...:
            raise RuntimeError(
                "SQLite cannot represent the imported module schemas without "
                f"a table-name collision: {schema!r} exceeds its "
                f"{max_attached}-schema attachment limit; use PostgreSQL or "
                "split the unit-test composition"
            )
        translation[schema] = target
        occupied[target].update(names)

    return tuple(sorted(attached)), translation


def _module_schemas(metadata: MetaData) -> tuple[str, ...]:
    """Every distinct non-default schema bound to a table in ``metadata``.

    Read off the metadata rather than off `MIGRATION_OWNER_LEDGER` on purpose:
    the harness must attach exactly what the caller actually imported, and it
    must not care whether a schema belongs to an installed module, so it never
    needs to import one.
    """
    return tuple(sorted(schema for schema in _schema_tables(metadata) if schema))


def create_test_engine(*, metadata: MetaData | None = None) -> Engine:
    """A fresh in-memory SQLite engine with the full `Base.metadata` schema
    created. `check_same_thread=False` because a TestClient runs sync route
    dependencies on a worker thread while the test holds one connection —
    sequential use only, never concurrent.

    **Module schemas (ADR-0006 D1).** A stateful module binds its models to
    `mod_<short_code>` via `namespaces.schema_table_args`, so the ORM emits
    fully qualified `mod_x.thing` — which is the entire point of D1, and which
    plain SQLite rejects because it has no schemas. Each such schema is
    therefore ATTACHed as its own in-memory database before `create_all`, on
    every connection.

    ATTACH keeps emitted SQL identical to production's while the imported
    composition fits SQLite's compile-time attachment ceiling.  Above that
    ceiling, the largest schemas keep their exact names and smaller spillover
    schemas are co-located only when their table names cannot collide.  The
    static migration gate and PostgreSQL lane remain the exact qualification,
    RLS and grant authorities; SQLite is only the fast service-logic lane.

    ``metadata`` is injectable for reusable-kernel canaries.  Assemblies omit
    it and receive the shared declarative ``Base.metadata``.
    """
    selected_metadata = Base.metadata if metadata is None else metadata
    schemas, translation = _sqlite_schema_layout(
        selected_metadata, max_attached=_sqlite_max_attached()
    )
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        future=True,
        connect_args={"check_same_thread": False},
        execution_options={"schema_translate_map": translation},
    )
    if schemas:

        @event.listens_for(engine, "connect")
        def _attach_module_schemas(dbapi_connection: object, _record: object) -> None:
            cursor = dbapi_connection.cursor()  # type: ignore[attr-defined]
            try:
                for schema in schemas:
                    # Identifier-quoted, and the names are `mod_<short_code>`
                    # already validated by `namespaces.validate_schema` — this
                    # is defence in depth, not the only check.
                    cursor.execute(f"ATTACH DATABASE ':memory:' AS \"{schema}\"")
            finally:
                cursor.close()

    selected_metadata.create_all(engine)
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
