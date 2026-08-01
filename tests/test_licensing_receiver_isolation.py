"""Tenant-isolation canary for the WS8 receiver's applied-licence record.

`tenant_applied_licences` is a tenant-scoped RLS table (assembly migration
a002). This proves a tenant cannot read another tenant's applied-licence
record, and cannot INSERT one for another tenant (the policy's `WITH CHECK`).
Requires real Postgres.
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


def _insert_applied(session: Session, *, tenant_id: uuid.UUID) -> uuid.UUID:
    row_id = uuid.uuid4()
    session.execute(
        text(
            "INSERT INTO tenant_applied_licences "
            "(id, tenant_id, licence_id, licence_version, digest, validity) "
            "VALUES (:id, :tenant_id, :licence_id, 1, :digest, 'valid')"
        ),
        {
            "id": str(row_id),
            "tenant_id": str(tenant_id),
            "licence_id": f"lic-{row_id}",
            "digest": "sha256:canary",
        },
    )
    return row_id


def test_applied_licence_in_tenant_a_invisible_to_tenant_b(
    admin_session: Session,
    tenant_a,
    tenant_b,
    tenant_sessionmaker: sessionmaker[Session],
) -> None:
    a = _as_tenant(tenant_sessionmaker, tenant_a.id)
    try:
        row_id = _insert_applied(a, tenant_id=tenant_a.id)
        a.commit()
    finally:
        a.close()

    try:
        b = _as_tenant(tenant_sessionmaker, tenant_b.id)
        try:
            rows = b.execute(
                text("SELECT id FROM tenant_applied_licences WHERE id = :id"),
                {"id": str(row_id)},
            ).fetchall()
            assert rows == []  # tenant B: invisible
        finally:
            b.rollback()
            b.close()

        a2 = _as_tenant(tenant_sessionmaker, tenant_a.id)
        try:
            row = a2.execute(
                text("SELECT id FROM tenant_applied_licences WHERE id = :id"),
                {"id": str(row_id)},
            ).fetchone()
            assert row is not None  # tenant A: visible
        finally:
            a2.rollback()
            a2.close()
    finally:
        admin_session.execute(
            text("DELETE FROM tenant_applied_licences WHERE id = :id"),
            {"id": str(row_id)},
        )
        admin_session.commit()


def test_with_check_denies_cross_tenant_applied_licence_insert(
    tenant_a,
    tenant_b,
    tenant_sessionmaker: sessionmaker[Session],
) -> None:
    """Tenant B, with its own context set, cannot INSERT an applied-licence
    record stamped with tenant A's id — the policy's `WITH CHECK` rejects it."""
    b = _as_tenant(tenant_sessionmaker, tenant_b.id)
    try:
        with pytest.raises(DBAPIError, match="row-level security"):
            _insert_applied(b, tenant_id=tenant_a.id)
    finally:
        b.rollback()
        b.close()
