"""Tenant-isolation canary for the WS8 receiver's imported revocation state.

`tenant_revocation_lists` is a tenant-scoped RLS table (assembly migration
a003). Isolation matters more here than for most tables: the stored revoked set
is what every licence application is checked against, so a leak across tenants
would let one tenant's revocations gate another's entitlements — or, worse, let
a tenant read a set it could then omit. Requires real Postgres.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Generator

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, sessionmaker


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


def _insert_revocation(session: Session, *, tenant_id: uuid.UUID) -> uuid.UUID:
    row_id = uuid.uuid4()
    session.execute(
        text(
            "INSERT INTO tenant_revocation_lists "
            "(id, tenant_id, list_version, revoked_licence_ids) "
            "VALUES (:id, :tenant_id, 1, :ids)"
        ),
        {
            "id": str(row_id),
            "tenant_id": str(tenant_id),
            "ids": '["lic-canary"]',
        },
    )
    return row_id


def test_revocation_list_in_tenant_a_invisible_to_tenant_b(
    admin_session: Session,
    tenant_a,
    tenant_b,
    tenant_sessionmaker: sessionmaker[Session],
) -> None:
    a = _as_tenant(tenant_sessionmaker, tenant_a.id)
    try:
        row_id = _insert_revocation(a, tenant_id=tenant_a.id)
        a.commit()
    finally:
        a.close()

    try:
        b = _as_tenant(tenant_sessionmaker, tenant_b.id)
        try:
            rows = b.execute(
                text("SELECT id FROM tenant_revocation_lists WHERE id = :id"),
                {"id": str(row_id)},
            ).fetchall()
            assert rows == []  # tenant B: invisible
        finally:
            b.rollback()
            b.close()

        a2 = _as_tenant(tenant_sessionmaker, tenant_a.id)
        try:
            row = a2.execute(
                text("SELECT id FROM tenant_revocation_lists WHERE id = :id"),
                {"id": str(row_id)},
            ).fetchone()
            assert row is not None  # tenant A: visible
        finally:
            a2.rollback()
            a2.close()
    finally:
        admin_session.execute(
            text("DELETE FROM tenant_revocation_lists WHERE id = :id"),
            {"id": str(row_id)},
        )
        admin_session.commit()


def test_with_check_denies_cross_tenant_revocation_list_insert(
    tenant_a,
    tenant_b,
    tenant_sessionmaker: sessionmaker[Session],
) -> None:
    """Tenant B, with its own context set, cannot INSERT a revocation-list row
    stamped with tenant A's id — the policy's `WITH CHECK` rejects it."""
    b = _as_tenant(tenant_sessionmaker, tenant_b.id)
    try:
        with pytest.raises(DBAPIError, match="row-level security"):
            _insert_revocation(b, tenant_id=tenant_a.id)
    finally:
        b.rollback()
        b.close()
