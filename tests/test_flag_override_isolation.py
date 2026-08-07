"""Tenant-isolation canary for `feature_flag_overrides` (kernel 0013).

The table follows `domain_settings`' nullable-tenant shape, so it has the same
asymmetric contract and needs the same proof:

- a tenant reads its OWN rows and the DEPLOYMENT-scope (`tenant_id IS NULL`)
  rows — the second half is the part a naive "tenant_id = current" policy would
  break, silently hiding every platform default;
- a tenant reads NO other tenant's rows;
- a tenant cannot WRITE a deployment-scope row — otherwise any tenant could set
  a flag for the whole fleet, which is a privilege escalation dressed as
  configuration.

Requires real Postgres (`make test-db-up` / `make test-integration`).
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


def _insert_override(
    session: Session, *, tenant_id: uuid.UUID | None, code: str, value: bool
) -> uuid.UUID:
    oid = uuid.uuid4()
    session.execute(
        text(
            "INSERT INTO feature_flag_overrides "
            "(id, tenant_id, flag_code, value, kill_switch) "
            "VALUES (:id, :tenant_id, :code, :value, false)"
        ),
        {
            "id": str(oid),
            "tenant_id": str(tenant_id) if tenant_id else None,
            "code": code,
            "value": "true" if value else "false",
        },
    )
    return oid


def test_a_tenant_override_is_invisible_to_another_tenant(
    admin_session: Session,
    tenant_a,
    tenant_b,
    tenant_sessionmaker: sessionmaker[Session],
) -> None:
    a = _as_tenant(tenant_sessionmaker, tenant_a.id)
    try:
        oid = _insert_override(
            a, tenant_id=tenant_a.id, code="probe.isolation", value=True
        )
        a.commit()
    finally:
        a.close()

    try:
        b = _as_tenant(tenant_sessionmaker, tenant_b.id)
        try:
            rows = b.execute(
                text("SELECT id FROM feature_flag_overrides WHERE id = :id"),
                {"id": str(oid)},
            ).fetchall()
            assert rows == []
        finally:
            b.rollback()
            b.close()
    finally:
        admin_session.execute(
            text("DELETE FROM feature_flag_overrides WHERE id = :id"), {"id": str(oid)}
        )
        admin_session.commit()


def test_every_tenant_reads_the_deployment_scope_row(
    admin_session: Session,
    tenant_a,
    tenant_b,
    tenant_sessionmaker: sessionmaker[Session],
) -> None:
    """The half a naive policy breaks: a platform default must be READABLE by
    every tenant, or no deployment-wide flag would ever take effect."""
    oid = _insert_override(
        admin_session, tenant_id=None, code="probe.platform", value=True
    )
    admin_session.commit()
    try:
        for tenant in (tenant_a, tenant_b):
            session = _as_tenant(tenant_sessionmaker, tenant.id)
            try:
                row = session.execute(
                    text("SELECT id FROM feature_flag_overrides WHERE id = :id"),
                    {"id": str(oid)},
                ).fetchone()
                assert row is not None, f"tenant {tenant.slug} cannot see the default"
            finally:
                session.rollback()
                session.close()
    finally:
        admin_session.execute(
            text("DELETE FROM feature_flag_overrides WHERE id = :id"), {"id": str(oid)}
        )
        admin_session.commit()


def test_a_tenant_cannot_write_a_deployment_scope_override(
    tenant_a, tenant_sessionmaker: sessionmaker[Session]
) -> None:
    """Privilege escalation dressed as configuration: one tenant setting a flag
    for the entire fleet."""
    session = _as_tenant(tenant_sessionmaker, tenant_a.id)
    try:
        with pytest.raises(DBAPIError, match="row-level security"):
            _insert_override(session, tenant_id=None, code="probe.escalate", value=True)
    finally:
        session.rollback()
        session.close()


def test_a_tenant_cannot_write_another_tenants_override(
    tenant_a, tenant_b, tenant_sessionmaker: sessionmaker[Session]
) -> None:
    session = _as_tenant(tenant_sessionmaker, tenant_b.id)
    try:
        with pytest.raises(DBAPIError, match="row-level security"):
            _insert_override(
                session, tenant_id=tenant_a.id, code="probe.cross", value=True
            )
    finally:
        session.rollback()
        session.close()
