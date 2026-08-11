"""Tenant-isolation canary for the consent ledger (`communication_suppressions`).

A consent ledger is the worst table in the system to get wrong, in both
directions:

- a cross-tenant READ lets one tenant enumerate another's unsubscribes, bounces
  and erasure requests — a list of people who complained, which is exactly the
  data a regulator asks about;
- a cross-tenant WRITE lets one tenant silence another's invoices, because an
  `all`-scoped row blocks transactional delivery by design.

Sub's source table has neither `tenant_id` nor RLS (it is single-tenant), so
this behaviour is NEW in the port and gets its own proof rather than being
assumed from the migration's presence.

Requires real Postgres (`make test-db-up` / `make test-integration`). SQLite has
no RLS, so `tests/unit/test_consent.py` proves the RULES and this file proves the
database ENFORCES the tenant boundary underneath them.
"""

from __future__ import annotations

import os
import threading
import uuid
from collections.abc import Generator
from concurrent.futures import ThreadPoolExecutor

import pytest
from dotmac_kernel import consent
from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session, sessionmaker

TABLE = "communication_suppressions"


@pytest.fixture(scope="module")
def tenant_engine() -> Generator[Engine, None, None]:
    url = os.getenv("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL not set — these tests require a real Postgres")
    engine = create_engine(url, future=True)
    yield engine
    engine.dispose()


@pytest.fixture()
def tenant_sessionmaker(tenant_engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=tenant_engine, autocommit=False, autoflush=False)


def _as_tenant(factory: sessionmaker[Session], tenant_id: uuid.UUID) -> Session:
    session = factory()
    session.execute(
        text("SELECT set_config('app.current_tenant', :tenant_id, true)"),
        {"tenant_id": str(tenant_id)},
    )
    return session


def _insert(
    session: Session,
    *,
    tenant_id: uuid.UUID,
    address: str,
    scope: str = "marketing",
    reason: str = "unsubscribe",
) -> uuid.UUID:
    row_id = uuid.uuid4()
    session.execute(
        text(
            "INSERT INTO communication_suppressions "
            "(id, tenant_id, channel, address, scope, reason) "
            "VALUES (:id, :tenant_id, 'email', :address, :scope, :reason)"
        ),
        {
            "id": str(row_id),
            "tenant_id": str(tenant_id),
            "address": address,
            "scope": scope,
            "reason": reason,
        },
    )
    return row_id


def test_a_suppression_in_tenant_a_is_invisible_to_tenant_b(
    tenant_a, tenant_b, tenant_sessionmaker: sessionmaker[Session]
) -> None:
    a = _as_tenant(tenant_sessionmaker, tenant_a.id)
    try:
        row_id = _insert(a, tenant_id=tenant_a.id, address="jane@example.com")
        a.commit()
    finally:
        a.close()

    b = _as_tenant(tenant_sessionmaker, tenant_b.id)
    try:
        rows = b.execute(
            text("SELECT id FROM communication_suppressions WHERE id = :id"),
            {"id": str(row_id)},
        ).fetchall()
        assert rows == [], "tenant B can read tenant A's suppression list"
    finally:
        b.close()


def test_a_tenant_cannot_stamp_a_row_with_another_tenants_id(
    tenant_a, tenant_b, tenant_sessionmaker: sessionmaker[Session]
) -> None:
    """The policy's `WITH CHECK`. Without it, tenant A could insert an
    `all`-scoped row against tenant B's id and stop B's invoices."""
    a = _as_tenant(tenant_sessionmaker, tenant_a.id)
    try:
        with pytest.raises((DBAPIError, IntegrityError)):
            _insert(
                a,
                tenant_id=tenant_b.id,
                address="victim@example.com",
                scope="all",
                reason="bounce",
            )
            a.flush()
    finally:
        a.rollback()
        a.close()


def test_the_same_address_may_be_suppressed_independently_per_tenant(
    tenant_a, tenant_b, tenant_sessionmaker: sessionmaker[Session]
) -> None:
    """The unique key is `(tenant_id, channel, address)`, not `(channel,
    address)` as in Sub. One person unsubscribing from tenant A must not
    unsubscribe them from tenant B — and must not collide on insert."""
    shared = "shared@example.com"

    a = _as_tenant(tenant_sessionmaker, tenant_a.id)
    try:
        _insert(a, tenant_id=tenant_a.id, address=shared)
        a.commit()
    finally:
        a.close()

    b = _as_tenant(tenant_sessionmaker, tenant_b.id)
    try:
        # No unique-constraint violation: the key carries the tenant.
        _insert(b, tenant_id=tenant_b.id, address=shared)
        b.commit()
    finally:
        b.close()

    # A FRESH session for the read. `set_config(..., true)` is transaction-local,
    # so the commit above dropped the GUC and a read on that same session would
    # see nothing — which would pass this assertion for entirely the wrong reason.
    b = _as_tenant(tenant_sessionmaker, tenant_b.id)
    try:
        visible = b.execute(
            text(
                "SELECT count(*) FROM communication_suppressions "
                "WHERE address = :address"
            ),
            {"address": shared},
        ).scalar_one()
        assert visible == 1, "tenant B sees more than its own row for a shared address"
    finally:
        b.close()


def test_a_tenant_cannot_delete_another_tenants_suppression(
    tenant_a, tenant_b, tenant_sessionmaker: sessionmaker[Session]
) -> None:
    """Deletion is how a suppression is lifted, so an unprotected DELETE would
    let one tenant re-enable sending to an address another tenant blocked."""
    a = _as_tenant(tenant_sessionmaker, tenant_a.id)
    try:
        row_id = _insert(
            a,
            tenant_id=tenant_a.id,
            address="protected@example.com",
            scope="all",
            reason="erasure",
        )
        a.commit()
    finally:
        a.close()

    b = _as_tenant(tenant_sessionmaker, tenant_b.id)
    try:
        b.execute(
            text("DELETE FROM communication_suppressions WHERE id = :id"),
            {"id": str(row_id)},
        )
        b.commit()
    finally:
        b.close()

    a = _as_tenant(tenant_sessionmaker, tenant_a.id)
    try:
        still_there = a.execute(
            text("SELECT count(*) FROM communication_suppressions WHERE id = :id"),
            {"id": str(row_id)},
        ).scalar_one()
        assert still_there == 1, "tenant B deleted tenant A's erasure suppression"
    finally:
        a.close()


def test_concurrent_duplicate_suppressions_converge_on_one_row(
    tenant_a,
    tenant_engine: Engine,
    tenant_sessionmaker: sessionmaker[Session],
) -> None:
    """Provider callbacks are concurrent as well as repeated.

    Both calls deliberately finish their initial lookup before either inserts;
    the unique constraint then chooses a winner and the loser must replay it
    inside a savepoint rather than poisoning its outer transaction.
    """
    barrier = threading.Barrier(2)
    marker = f"consent-race-{uuid.uuid4()}"

    def synchronise_initial_lookup(conn, cursor, statement, params, context, many):
        if (
            statement.lstrip().startswith("SELECT")
            and TABLE in statement
            and not conn.info.get(marker)
        ):
            conn.info[marker] = True
            barrier.wait(timeout=10)

    event.listen(tenant_engine, "after_cursor_execute", synchronise_initial_lookup)

    def worker() -> uuid.UUID:
        db = _as_tenant(tenant_sessionmaker, tenant_a.id)
        try:
            row = consent.suppress(
                db,
                tenant_a.id,
                channel="email",
                address="concurrent@example.com",
            )
            row_id = row.id
            db.commit()
            return row_id
        finally:
            db.close()

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            row_ids = list(pool.map(lambda _: worker(), range(2)))
    finally:
        event.remove(tenant_engine, "after_cursor_execute", synchronise_initial_lookup)

    assert row_ids[0] == row_ids[1]
