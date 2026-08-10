"""Tenant-isolation canary for delivery receipts (`communication_deliveries`).

A receipt names an address and a verdict about it — "this person's mail bounced",
"this person marked us as spam". The same disclosure risk as the consent ledger it
feeds, and the same two failure directions: a cross-tenant read leaks who
complained, a cross-tenant write can fabricate a bounce and suppress another
tenant's address.

Also pins the PARTIAL unique index, which is what makes an at-least-once provider
webhook safe to redeliver while still allowing receipts that never got an id.

Requires real Postgres (`make test-db-up` / `make test-integration`).
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Generator

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import DBAPIError, IntegrityError
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


def _insert(
    session: Session,
    *,
    tenant_id: uuid.UUID,
    address: str = "jane@example.com",
    provider: str = "ses",
    provider_message_id: str | None = "msg-1",
    status: str = "bounced",
) -> uuid.UUID:
    row_id = uuid.uuid4()
    session.execute(
        text(
            "INSERT INTO communication_deliveries "
            "(id, tenant_id, channel, address, provider, provider_message_id, status) "
            "VALUES (:id, :tenant_id, 'email', :address, :provider, :mid, :status)"
        ),
        {
            "id": str(row_id),
            "tenant_id": str(tenant_id),
            "address": address,
            "provider": provider,
            "mid": provider_message_id,
            "status": status,
        },
    )
    return row_id


def test_a_receipt_in_tenant_a_is_invisible_to_tenant_b(
    tenant_a, tenant_b, tenant_sessionmaker: sessionmaker[Session]
) -> None:
    a = _as_tenant(tenant_sessionmaker, tenant_a.id)
    try:
        row_id = _insert(a, tenant_id=tenant_a.id)
        a.commit()
    finally:
        a.close()

    b = _as_tenant(tenant_sessionmaker, tenant_b.id)
    try:
        rows = b.execute(
            text("SELECT id FROM communication_deliveries WHERE id = :id"),
            {"id": str(row_id)},
        ).fetchall()
        assert rows == [], "tenant B can read tenant A's bounce history"
    finally:
        b.close()


def test_a_tenant_cannot_write_a_receipt_against_another_tenant(
    tenant_a, tenant_b, tenant_sessionmaker: sessionmaker[Session]
) -> None:
    """The policy's `WITH CHECK`. Without it tenant A could fabricate a bounce
    for tenant B and, through the feedback loop, suppress B's address."""
    a = _as_tenant(tenant_sessionmaker, tenant_a.id)
    try:
        with pytest.raises((DBAPIError, IntegrityError)):
            _insert(a, tenant_id=tenant_b.id, address="victim@example.com")
            a.flush()
    finally:
        a.rollback()
        a.close()


def test_the_provider_message_id_is_unique_within_a_tenant(
    tenant_a, tenant_sessionmaker: sessionmaker[Session]
) -> None:
    """What makes a redelivered webhook a no-op rather than a second bounce."""
    a = _as_tenant(tenant_sessionmaker, tenant_a.id)
    try:
        _insert(a, tenant_id=tenant_a.id, provider_message_id="dupe-1")
        a.commit()
    finally:
        a.close()

    a = _as_tenant(tenant_sessionmaker, tenant_a.id)
    try:
        with pytest.raises((DBAPIError, IntegrityError)):
            _insert(a, tenant_id=tenant_a.id, provider_message_id="dupe-1")
            a.flush()
    finally:
        a.rollback()
        a.close()


def test_receipts_without_a_provider_id_do_not_collide(
    tenant_a, tenant_sessionmaker: sessionmaker[Session]
) -> None:
    """The index is PARTIAL. A plain unique index would make every id-less
    receipt collide with the previous one, so a second synchronous failure could
    not be recorded at all."""
    a = _as_tenant(tenant_sessionmaker, tenant_a.id)
    try:
        _insert(a, tenant_id=tenant_a.id, provider_message_id=None, status="failed")
        _insert(a, tenant_id=tenant_a.id, provider_message_id=None, status="failed")
        a.commit()
    finally:
        a.close()

    a = _as_tenant(tenant_sessionmaker, tenant_a.id)
    try:
        count = a.execute(
            text(
                "SELECT count(*) FROM communication_deliveries "
                "WHERE provider_message_id IS NULL AND status = 'failed'"
            )
        ).scalar_one()
        assert count >= 2
    finally:
        a.close()


def test_the_same_provider_id_may_exist_in_two_tenants(
    tenant_a, tenant_b, tenant_sessionmaker: sessionmaker[Session]
) -> None:
    """The key carries the tenant — two tenants using the same provider will see
    its id sequences overlap."""
    shared = "shared-provider-id"
    for tenant in (tenant_a, tenant_b):
        session = _as_tenant(tenant_sessionmaker, tenant.id)
        try:
            _insert(session, tenant_id=tenant.id, provider_message_id=shared)
            session.commit()
        finally:
            session.close()

    b = _as_tenant(tenant_sessionmaker, tenant_b.id)
    try:
        count = b.execute(
            text(
                "SELECT count(*) FROM communication_deliveries "
                "WHERE provider_message_id = :mid"
            ),
            {"mid": shared},
        ).scalar_one()
        assert count == 1, "tenant B sees more than its own receipt"
    finally:
        b.close()
