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
from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from dotmac_kernel.models import Base

if TYPE_CHECKING:
    from fastapi.testclient import TestClient


def _module_schema_tables() -> dict[str, frozenset[str]]:
    """Tables grouped by non-default schema in `Base.metadata`.

    Read off the metadata rather than off `MIGRATION_OWNER_LEDGER` on purpose:
    the harness must attach exactly what the caller actually imported, and it
    must not care whether a schema belongs to an installed module, so it never
    needs to import one.
    """
    grouped: dict[str, set[str]] = {}
    for table in Base.metadata.tables.values():
        if table.schema:
            grouped.setdefault(table.schema, set()).add(table.name)
    return {schema: frozenset(names) for schema, names in sorted(grouped.items())}


def _sqlite_attached_limit() -> int:
    """Return this interpreter's real SQLite attachment limit."""

    connection = sqlite3.connect(":memory:")
    try:
        return connection.getlimit(sqlite3.SQLITE_LIMIT_ATTACHED)
    finally:
        connection.close()


def _sqlite_schema_layout(
    schema_tables: dict[str, frozenset[str]],
    *,
    max_attached: int,
) -> tuple[tuple[str, ...], dict[str, str]]:
    """Fit qualified module schemas within SQLite's finite attachment slots.

    Every schema keeps its own attached alias while slots remain. Overflow
    schemas may share an existing alias only when their table names are
    disjoint, so no table can shadow another.
    """

    if schema_tables and max_attached < 1:
        raise RuntimeError("SQLite provides no attached-database slots")

    aliases: list[str] = []
    group_table_names: list[set[str]] = []
    translations: dict[str, str] = {}
    for schema, table_names in sorted(schema_tables.items()):
        if len(aliases) < max_attached:
            aliases.append(schema)
            group_table_names.append(set(table_names))
            continue

        compatible = [
            index
            for index, existing_names in enumerate(group_table_names)
            if not table_names.intersection(existing_names)
        ]
        if not compatible:
            raise RuntimeError(
                "SQLite's attached-database limit cannot represent the imported "
                f"module schemas without a table-name collision: {schema}"
            )
        target_index = min(
            compatible,
            key=lambda index: (len(group_table_names[index]), aliases[index]),
        )
        alias = aliases[target_index]
        translations[schema] = alias
        group_table_names[target_index].update(table_names)

    return tuple(aliases), translations


def create_test_engine() -> Engine:
    """A fresh in-memory SQLite engine with the full `Base.metadata` schema
    created. `check_same_thread=False` because a TestClient runs sync route
    dependencies on a worker thread while the test holds one connection —
    sequential use only, never concurrent.

    **Module schemas (ADR-0006 D1).** A stateful module binds its models to
    `mod_<short_code>` via `namespaces.schema_table_args`, so the ORM emits
    fully qualified `mod_x.thing` — which is the entire point of D1, and which
    plain SQLite rejects because it has no schemas. Each such schema is
    therefore ATTACHed as an in-memory database before `create_all`, on every
    connection.

    SQLite normally permits only ten attachments. Above that limit, schemas
    whose table names are disjoint share an attached database through a narrow
    `schema_translate_map`. SQL remains qualified; only the test database alias
    changes. The namespace gate and PostgreSQL integration lane remain the
    proof of each exact production schema name.
    """
    attached_schemas, schema_translations = _sqlite_schema_layout(
        _module_schema_tables(),
        max_attached=_sqlite_attached_limit(),
    )
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        future=True,
        connect_args={"check_same_thread": False},
        execution_options={"schema_translate_map": schema_translations},
    )
    if attached_schemas:

        @event.listens_for(engine, "connect")
        def _attach_module_schemas(dbapi_connection: object, _record: object) -> None:
            cursor = dbapi_connection.cursor()  # type: ignore[attr-defined]
            try:
                for schema in attached_schemas:
                    # Identifier-quoted, and the names are `mod_<short_code>`
                    # already validated by `namespaces.validate_schema` — this
                    # is defence in depth, not the only check.
                    cursor.execute(f"ATTACH DATABASE ':memory:' AS \"{schema}\"")
            finally:
                cursor.close()

    Base.metadata.create_all(engine)
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
