"""Tenant-isolation canary for WS2 entitlements.

`tenant_entitlement_grants` is a tenant-scoped RLS table (migration 0010). This
proves a tenant cannot read another tenant's grants, and cannot INSERT a grant
for another tenant (the policy's `WITH CHECK`). Requires real Postgres.
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


def _insert_grant(session: Session, *, tenant_id: uuid.UUID, code: str) -> uuid.UUID:
    gid = uuid.uuid4()
    session.execute(
        text(
            "INSERT INTO tenant_entitlement_grants "
            "(id, tenant_id, capability_code, granted) "
            "VALUES (:id, :tenant_id, :code, true)"
        ),
        {"id": str(gid), "tenant_id": str(tenant_id), "code": code},
    )
    return gid


def test_grant_in_tenant_a_invisible_to_tenant_b(
    admin_session: Session,
    tenant_a,
    tenant_b,
    tenant_sessionmaker: sessionmaker[Session],
) -> None:
    a = _as_tenant(tenant_sessionmaker, tenant_a.id)
    try:
        gid = _insert_grant(a, tenant_id=tenant_a.id, code="billing.use")
        a.commit()
    finally:
        a.close()

    try:
        b = _as_tenant(tenant_sessionmaker, tenant_b.id)
        try:
            rows = b.execute(
                text("SELECT id FROM tenant_entitlement_grants WHERE id = :id"),
                {"id": str(gid)},
            ).fetchall()
            assert rows == []  # tenant B: invisible
        finally:
            b.rollback()
            b.close()

        a2 = _as_tenant(tenant_sessionmaker, tenant_a.id)
        try:
            row = a2.execute(
                text("SELECT id FROM tenant_entitlement_grants WHERE id = :id"),
                {"id": str(gid)},
            ).fetchone()
            assert row is not None  # tenant A: visible
        finally:
            a2.rollback()
            a2.close()
    finally:
        admin_session.execute(
            text("DELETE FROM tenant_entitlement_grants WHERE id = :id"),
            {"id": str(gid)},
        )
        admin_session.commit()


def test_with_check_denies_cross_tenant_grant_insert(
    tenant_a,
    tenant_b,
    tenant_sessionmaker: sessionmaker[Session],
) -> None:
    """Tenant B, with its own context set, cannot INSERT a grant stamped with
    tenant A's id — the policy's `WITH CHECK` rejects it."""
    b = _as_tenant(tenant_sessionmaker, tenant_b.id)
    try:
        with pytest.raises(DBAPIError, match="row-level security"):
            _insert_grant(b, tenant_id=tenant_a.id, code="billing.use")
    finally:
        b.rollback()
        b.close()
