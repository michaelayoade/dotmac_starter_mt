"""Outbox/inbox tenant-isolation canaries (kernel WS3).

`outbox_events` and `inbox_records` are tenant-scoped tables with RLS (migration
0008). This is the load-bearing proof that a tenant cannot read another tenant's
queued events / command ledger, and cannot INSERT a row for another tenant
(the policy's `WITH CHECK`). Same convention as `tests/test_party_isolation.py`.

Requires a real Postgres (RLS doesn't exist on SQLite).
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
    """Engine bound as `app_user` — the RLS-enforced, tenant-facing role."""
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
    """A fresh `app_user` session with `app.current_tenant` set for this transaction."""
    session = factory()
    session.execute(
        text("SELECT set_config('app.current_tenant', :tenant_id, true)"),
        {"tenant_id": str(tenant_id)},
    )
    return session


def _insert_outbox_event(
    session: Session, *, tenant_id: uuid.UUID, event_type: str
) -> uuid.UUID:
    event_id = uuid.uuid4()
    session.execute(
        text(
            "INSERT INTO outbox_events (id, tenant_id, event_type, status) "
            "VALUES (:id, :tenant_id, :event_type, 'pending')"
        ),
        {"id": str(event_id), "tenant_id": str(tenant_id), "event_type": event_type},
    )
    return event_id


def _insert_inbox_record(
    session: Session, *, tenant_id: uuid.UUID, command_id: str
) -> uuid.UUID:
    record_id = uuid.uuid4()
    session.execute(
        text(
            "INSERT INTO inbox_records "
            "(id, tenant_id, command_id, command_type, status) "
            "VALUES (:id, :tenant_id, :command_id, 'test.command', 'processed')"
        ),
        {"id": str(record_id), "tenant_id": str(tenant_id), "command_id": command_id},
    )
    return record_id


def test_outbox_event_in_tenant_a_invisible_to_tenant_b(
    admin_session: Session,
    tenant_a,
    tenant_b,
    tenant_sessionmaker: sessionmaker[Session],
) -> None:
    a = _as_tenant(tenant_sessionmaker, tenant_a.id)
    try:
        event_id = _insert_outbox_event(
            a, tenant_id=tenant_a.id, event_type="thing.happened"
        )
        a.commit()
    finally:
        a.close()

    try:
        b = _as_tenant(tenant_sessionmaker, tenant_b.id)
        try:
            rows = b.execute(
                text("SELECT id FROM outbox_events WHERE id = :id"),
                {"id": str(event_id)},
            ).fetchall()
            assert rows == []  # tenant B: invisible
        finally:
            b.rollback()
            b.close()

        a2 = _as_tenant(tenant_sessionmaker, tenant_a.id)
        try:
            row = a2.execute(
                text("SELECT id FROM outbox_events WHERE id = :id"),
                {"id": str(event_id)},
            ).fetchone()
            assert row is not None  # tenant A: visible
        finally:
            a2.rollback()
            a2.close()
    finally:
        admin_session.execute(
            text("DELETE FROM outbox_events WHERE id = :id"), {"id": str(event_id)}
        )
        admin_session.commit()


def test_inbox_record_in_tenant_a_invisible_to_tenant_b(
    admin_session: Session,
    tenant_a,
    tenant_b,
    tenant_sessionmaker: sessionmaker[Session],
) -> None:
    command_id = f"cmd-{uuid.uuid4().hex[:12]}"
    a = _as_tenant(tenant_sessionmaker, tenant_a.id)
    try:
        record_id = _insert_inbox_record(
            a, tenant_id=tenant_a.id, command_id=command_id
        )
        a.commit()
    finally:
        a.close()

    try:
        b = _as_tenant(tenant_sessionmaker, tenant_b.id)
        try:
            rows = b.execute(
                text("SELECT id FROM inbox_records WHERE id = :id"),
                {"id": str(record_id)},
            ).fetchall()
            assert rows == []
        finally:
            b.rollback()
            b.close()
    finally:
        admin_session.execute(
            text("DELETE FROM inbox_records WHERE id = :id"), {"id": str(record_id)}
        )
        admin_session.commit()


def test_outbox_with_check_denies_cross_tenant_insert(
    tenant_a,
    tenant_b,
    tenant_sessionmaker: sessionmaker[Session],
) -> None:
    """Tenant B, with its own tenant context set, cannot INSERT an outbox row
    stamped with tenant A's id — the policy's `WITH CHECK` rejects it."""
    b = _as_tenant(tenant_sessionmaker, tenant_b.id)
    try:
        with pytest.raises(DBAPIError, match="row-level security"):
            _insert_outbox_event(
                b, tenant_id=tenant_a.id, event_type="cross.tenant.forgery"
            )
    finally:
        b.rollback()
        b.close()
