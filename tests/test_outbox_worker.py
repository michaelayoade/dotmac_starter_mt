"""Outbox relay worker on real Postgres (WS3 slice 2, PR 3).

Proves the worker drains via the dispatcher connection while delivery-time reads
run on a SEPARATE tenant-scoped connection whose context is restored to the
event's own tenant, plus retry/dead-letter and clean shutdown. Requires Postgres.
"""

from __future__ import annotations

import os
import threading
import time
import uuid
from collections.abc import Generator

import pytest
from dotmac_kernel.messaging.relay import ClaimedEvent, RelayPolicy
from dotmac_kernel.messaging.worker import run_forever, run_once
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker


def _url_as(user: str) -> str:
    base = os.getenv("TEST_DATABASE_URL")
    if not base:
        pytest.skip("TEST_DATABASE_URL not set — relay worker tests need Postgres")
    scheme_userhost, _, db = base.rpartition("/")
    scheme, _, userhost = scheme_userhost.partition("://")
    host = userhost.rpartition("@")[2]
    return f"{scheme}://{user}@{host}/{db}"


@pytest.fixture(scope="module")
def engines() -> Generator[tuple[Engine, Engine], None, None]:
    disp = create_engine(_url_as("outbox_dispatcher"), future=True)
    tenant = create_engine(_url_as("app_user"), future=True)
    yield disp, tenant
    disp.dispose()
    tenant.dispose()


@pytest.fixture()
def factories(engines):
    disp, tenant = engines
    return sessionmaker(bind=disp), sessionmaker(bind=tenant)


class _RecordingTransport:
    """Reads the restored tenant context via the tenant-scoped connection and
    records it per event — proving delivery runs in the event's tenant."""

    def __init__(self, fail: bool = False) -> None:
        self.seen_context: dict[uuid.UUID, str] = {}
        self.fail = fail

    def deliver(self, event: ClaimedEvent, tenant_db: Session) -> None:
        ctx = tenant_db.execute(
            text("SELECT current_setting('app.current_tenant', true)")
        ).scalar()
        self.seen_context[event.id] = ctx
        if self.fail:
            raise RuntimeError("transport boom")


def _seed(admin_session: Session, tenant_id: uuid.UUID, n: int = 1) -> list[uuid.UUID]:
    ids = [uuid.uuid4() for _ in range(n)]
    for eid in ids:
        admin_session.execute(
            text(
                "INSERT INTO outbox_events (id, tenant_id, event_type, status) "
                "VALUES (:id, :t, 'thing.happened', 'pending')"
            ),
            {"id": str(eid), "t": str(tenant_id)},
        )
    admin_session.commit()
    return ids


@pytest.fixture()
def _cleanup(admin_session, tenant_a):
    yield
    admin_session.execute(
        text("DELETE FROM outbox_events WHERE tenant_id = :t"), {"t": str(tenant_a.id)}
    )
    admin_session.commit()


def test_run_once_delivers_and_restores_tenant_context(
    admin_session, tenant_a, factories, _cleanup
) -> None:
    disp_sm, tenant_sm = factories
    (eid,) = _seed(admin_session, tenant_a.id)
    transport = _RecordingTransport()
    ddb = disp_sm()
    try:
        n = run_once(
            dispatcher_db=ddb,
            tenant_session_factory=tenant_sm,
            transport=transport,
            worker_id="w1",
        )
    finally:
        ddb.close()
    assert n == 1
    # Delivery ran with the tenant context restored to THIS event's tenant.
    assert transport.seen_context[eid] == str(tenant_a.id)
    status = admin_session.execute(
        text("SELECT status FROM outbox_events WHERE id = :id"), {"id": str(eid)}
    ).scalar()
    assert status == "sent"


def test_run_once_failure_backs_off(
    admin_session, tenant_a, factories, _cleanup
) -> None:
    disp_sm, tenant_sm = factories
    (eid,) = _seed(admin_session, tenant_a.id)
    ddb = disp_sm()
    try:
        run_once(
            dispatcher_db=ddb,
            tenant_session_factory=tenant_sm,
            transport=_RecordingTransport(fail=True),
            worker_id="w1",
            policy=RelayPolicy(max_attempts=5),
        )
    finally:
        ddb.close()
    row = admin_session.execute(
        text("SELECT status, attempts FROM outbox_events WHERE id = :id"),
        {"id": str(eid)},
    ).one()
    assert row.status == "pending" and row.attempts == 1  # retry, not dead yet


def test_run_forever_drains_then_stops_cleanly(
    admin_session, tenant_a, factories, _cleanup
) -> None:
    disp_sm, tenant_sm = factories
    ids = _seed(admin_session, tenant_a.id, n=3)
    transport = _RecordingTransport()
    stop = threading.Event()
    t = threading.Thread(
        target=run_forever,
        kwargs={
            "dispatcher_session_factory": disp_sm,
            "tenant_session_factory": tenant_sm,
            "transport": transport,
            "worker_id": "w1",
            "stop": stop,
            "poll_interval": 0.05,
        },
    )
    t.start()
    # Wait until all seeded events are delivered, then signal shutdown.
    deadline = time.time() + 10
    while time.time() < deadline and len(transport.seen_context) < 3:
        time.sleep(0.05)
    stop.set()
    t.join(timeout=5)
    assert not t.is_alive()  # clean shutdown
    assert set(transport.seen_context) == set(ids)
