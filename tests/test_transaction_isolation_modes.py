"""Slice 3 canary — typed per-transaction isolation modes on `DatabaseRuntime`.

`readonly_session`/`serializable_session` exist for exactly two named real
callers ported from Sub: `party_identity_backfill.py` (SERIALIZABLE,
write-capable) and `migration_source_export.py` (REPEATABLE READ + readonly).
Both apply their mode via `Session.connection(execution_options=...)`, and
Sub's tenant GUC is a **global** `@event.listens_for(Session, "after_begin")`
listener — the exact mechanism `tenant_scope` uses here.

THE load-bearing property under test: execution options must land on the
DBAPI connection before the transaction's BEGIN and before any `after_begin`
listener issues its first statement. `SessionTransaction._connection_for_bind`
(SQLAlchemy 2.0) applies `conn.execution_options(**options)`, THEN
`conn.begin()`, THEN dispatches `after_begin` — in that literal order — which
is why `_isolated_session` calls `db.connection(execution_options=...)` as the
FIRST operation on a fresh session, before the caller (or a nested
`tenant_scope`) can touch it.

This is not merely disciplined ordering. `isolation_level` and
`postgresql_readonly` are both "transactional" SQLAlchemy connection
characteristics (`sqlalchemy.engine.characteristics.IsolationLevelCharacteristic`,
`sqlalchemy.dialects.postgresql.base.PGReadOnlyConnectionCharacteristic`, both
`transactional = True`): the dialect itself raises `InvalidRequestError` if
either is set on a connection that has already begun a transaction. The
near-miss test below plants exactly that mistake and shows it is refused
loudly, not applied "late" and silently.

Requires a real Postgres (isolation levels and write refusal are not
observable under SQLite) — `make test-db-up` +
`TEST_DATABASE_URL`/`TEST_MIGRATION_DATABASE_URL`, same as every other canary
in this directory.
"""

from __future__ import annotations

import uuid

import pytest
from dotmac_kernel.db import runtime
from dotmac_kernel.models import Role
from sqlalchemy import event, select, text
from sqlalchemy.exc import DBAPIError, InvalidRequestError
from sqlalchemy.orm import Session


def _slugs_for(db: Session, tenant_id: object) -> set[str]:
    return set(db.scalars(select(Role.slug).where(Role.tenant_id == tenant_id)).all())


# ── 1. read-only means REPEATABLE READ *plus* a database-enforced refusal ───


def test_readonly_session_is_repeatable_read_and_read_only() -> None:
    with runtime.readonly_session() as db:
        isolation, read_only = db.execute(
            text(
                "SELECT current_setting('transaction_isolation'), "
                "current_setting('transaction_read_only')"
            )
        ).one()
    assert isolation == "repeatable read"
    assert read_only == "on"


def test_readonly_session_refuses_a_write_the_database_enforces(
    admin_session: Session, tenant_a
) -> None:
    """Not a label: an actual write attempt is refused by PostgreSQL.

    Composed with `tenant_scope` inside the block — the mode is already
    established by the time `tenant_scope` issues its GUC statement, so this
    also exercises the composition the module docstring documents.
    """
    slug = f"readonly-write-refusal-{uuid.uuid4().hex[:8]}"
    with pytest.raises(DBAPIError) as exc_info:
        with runtime.readonly_session() as db:
            with runtime.tenant_scope(db, tenant_a.id):
                db.add(Role(tenant_id=tenant_a.id, slug=slug, name=slug))
                db.flush()

    message = str(exc_info.value.orig).lower()
    assert "read-only transaction" in message or "read only transaction" in message

    # No partial write leaked past the refusal.
    assert slug not in _slugs_for(admin_session, tenant_a.id)


# ── 2. serializable means SERIALIZABLE *plus* write capability ─────────────


def test_serializable_session_is_serializable() -> None:
    with runtime.serializable_session() as db:
        isolation = db.execute(
            text("SELECT current_setting('transaction_isolation')")
        ).scalar()
    assert isolation == "serializable"


def test_serializable_session_writes_persist(admin_session: Session, tenant_a) -> None:
    slug = f"serializable-write-{uuid.uuid4().hex[:8]}"
    try:
        with runtime.serializable_session() as db:
            with runtime.tenant_scope(db, tenant_a.id):
                db.add(Role(tenant_id=tenant_a.id, slug=slug, name=slug))
        # `serializable_session` committed on the way out of its own `with` --
        # a fresh, unrelated session must see the row.
        assert slug in _slugs_for(admin_session, tenant_a.id)
    finally:
        admin_session.execute(
            text("DELETE FROM roles WHERE tenant_id = :tid AND slug = :slug"),
            {"tid": str(tenant_a.id), "slug": slug},
        )
        admin_session.commit()


# ── 3. execution options are established before the first statement/GUC ────


def test_readonly_mode_is_already_set_when_the_after_begin_hook_fires() -> None:
    """Registered at `Session` class scope, exactly like Sub's real tenant-GUC
    listener — so this proves the property Sub's hook actually depends on,
    not a synthetic ordering that only holds for this test's own listener."""
    observed: list[tuple[str, str]] = []

    def _record(_session: Session, _transaction: object, connection: object) -> None:
        row = connection.execute(
            text(
                "SELECT current_setting('transaction_isolation'), "
                "current_setting('transaction_read_only')"
            )
        ).one()
        observed.append((row[0], row[1]))

    event.listen(Session, "after_begin", _record)
    try:
        with runtime.readonly_session():
            pass
    finally:
        event.remove(Session, "after_begin", _record)

    assert observed == [("repeatable read", "on")]


def test_serializable_mode_is_already_set_when_the_after_begin_hook_fires() -> None:
    observed: list[str] = []

    def _record(_session: Session, _transaction: object, connection: object) -> None:
        observed.append(
            connection.execute(
                text("SELECT current_setting('transaction_isolation')")
            ).scalar()
        )

    event.listen(Session, "after_begin", _record)
    try:
        with runtime.serializable_session():
            pass
    finally:
        event.remove(Session, "after_begin", _record)

    assert observed == ["serializable"]


def test_setting_isolation_level_after_the_first_statement_is_refused() -> None:
    """The near-miss the ordering guarantee rules out MECHANICALLY, not just
    by convention: applying execution options AFTER a connection has already
    begun a transaction is refused outright by SQLAlchemy — there is no
    "late but still correct" outcome, which is exactly why
    `_isolated_session` must call `Session.connection(execution_options=...)`
    as its very first operation and nowhere else."""
    db = runtime.session_factory()
    try:
        db.execute(text("SELECT 1"))  # begins the transaction, unscoped
        with pytest.raises(InvalidRequestError):
            db.connection(execution_options={"isolation_level": "SERIALIZABLE"})
    finally:
        db.rollback()
        db.close()


# ── 4. ordinary sessions remain unchanged ───────────────────────────────────


def test_an_ordinary_session_is_not_left_read_only_by_pool_reuse() -> None:
    """`readonly_session`/`serializable_session` set characteristics on the
    pooled DBAPI connection. If closing the session did not reset them, the
    next caller to check the same connection out of the pool would silently
    inherit READ ONLY/REPEATABLE READ or SERIALIZABLE — exactly the kind of
    leak `tenant_scope`'s own module docstring warns about for `SET` vs `SET
    LOCAL`. SQLAlchemy resets "transactional" connection characteristics on
    pool checkin (`DefaultDialect._reset_characteristics`, scheduled as a
    finalize callback when the characteristic was first applied); this pins
    that the reset actually happens for THESE two modes specifically, not
    merely that the reset mechanism exists in the abstract.
    """
    with runtime.readonly_session():
        pass

    plain = runtime.session_factory()
    try:
        isolation, read_only = plain.execute(
            text(
                "SELECT current_setting('transaction_isolation'), "
                "current_setting('transaction_read_only')"
            )
        ).one()
    finally:
        plain.rollback()
        plain.close()

    assert isolation != "repeatable read"
    assert read_only == "off"


# ── 5. exceptions roll back and close through the existing boundary ────────


def test_readonly_session_rolls_back_and_closes_on_error(
    admin_session: Session, tenant_a
) -> None:
    with pytest.raises(RuntimeError):
        with runtime.readonly_session() as db:
            with runtime.tenant_scope(db, tenant_a.id):
                db.execute(text("SELECT 1"))
            raise RuntimeError("boom")

    # The runtime's ONE disposal path ran: no dangling open transaction left
    # the connection unusable for the next ordinary caller.
    plain = runtime.session_factory()
    try:
        plain.execute(text("SELECT 1"))
    finally:
        plain.rollback()
        plain.close()


def test_serializable_session_rolls_back_on_error(
    admin_session: Session, tenant_a
) -> None:
    slug = f"serializable-rollback-{uuid.uuid4().hex[:8]}"
    with pytest.raises(RuntimeError):
        with runtime.serializable_session() as db:
            with runtime.tenant_scope(db, tenant_a.id):
                db.add(Role(tenant_id=tenant_a.id, slug=slug, name=slug))
                db.flush()
            raise RuntimeError("boom")

    assert slug not in _slugs_for(admin_session, tenant_a.id)
