"""Platform outbox relay worker on real Postgres (WS3 platform relay).

Proves the platform worker drains via the `platform_outbox_dispatcher` connection
while delivery runs on a SEPARATE `platform_api` connection (connection separation),
that NO tenant context is set (platform events are tenant-free), retry/dead-letter,
clean shutdown, and duplicate-delivery dedupe via `process_once_platform`.
Requires Postgres (0012).
"""

from __future__ import annotations

import os
import threading
import time
import uuid
from collections.abc import Generator

import pytest
from dotmac_kernel.messaging import process_once_platform
from dotmac_kernel.messaging.platform_relay import ClaimedPlatformEvent
from dotmac_kernel.messaging.platform_worker import run_forever, run_once
from dotmac_kernel.messaging.relay import RelayPolicy
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
    disp = create_engine(_url_as("platform_outbox_dispatcher"), future=True)
    platform = create_engine(_url_as("platform_api"), future=True)
    yield disp, platform
    disp.dispose()
    platform.dispose()


@pytest.fixture()
def factories(engines):
    disp, platform = engines
    return sessionmaker(bind=disp), sessionmaker(bind=platform)


class _RecordingTransport:
    """Delivers on the platform_api connection, recording the effective DB user
    and the tenant context — proving delivery is NOT tenant-scoped and runs on
    the platform (not dispatcher) identity."""

    def __init__(self, fail: bool = False) -> None:
        self.seen_user: dict[uuid.UUID, str] = {}
        self.seen_tenant_ctx: dict[uuid.UUID, str | None] = {}
        self.fail = fail

    def deliver(self, event: ClaimedPlatformEvent, platform_db: Session) -> None:
        self.seen_user[event.id] = platform_db.execute(
            text("SELECT current_user")
        ).scalar()
        self.seen_tenant_ctx[event.id] = platform_db.execute(
            text("SELECT current_setting('app.current_tenant', true)")
        ).scalar()
        if self.fail:
            raise RuntimeError("transport boom")


def _seed(admin_session: Session, n: int = 1) -> list[uuid.UUID]:
    ids = [uuid.uuid4() for _ in range(n)]
    for eid in ids:
        admin_session.execute(
            text(
                "INSERT INTO platform_outbox_events (id, event_type, status) "
                "VALUES (:id, 'contract.activated', 'pending')"
            ),
            {"id": str(eid)},
        )
    admin_session.commit()
    return ids


@pytest.fixture()
def _cleanup(admin_session):
    yield
    admin_session.execute(text("DELETE FROM platform_outbox_events"))
    admin_session.execute(
        text(
            "DELETE FROM platform_idempotency_records "
            "WHERE operation = 'test.consume'"
        )
    )
    admin_session.commit()


def test_run_once_delivers_on_platform_connection_without_tenant_context(
    admin_session, factories, _cleanup
) -> None:
    disp_sm, platform_sm = factories
    (eid,) = _seed(admin_session)
    transport = _RecordingTransport()
    ddb = disp_sm()
    try:
        n = run_once(
            dispatcher_db=ddb,
            platform_session_factory=platform_sm,
            transport=transport,
            worker_id="w1",
        )
    finally:
        ddb.close()
    assert n == 1
    # Delivery ran as platform_api, and NO tenant context was set (tenant-free).
    assert transport.seen_user[eid] == "platform_api"
    assert transport.seen_tenant_ctx[eid] in ("", None)
    status = admin_session.execute(
        text("SELECT status FROM platform_outbox_events WHERE id = :id"),
        {"id": str(eid)},
    ).scalar()
    assert status == "sent"


def test_run_once_failure_backs_off(admin_session, factories, _cleanup) -> None:
    disp_sm, platform_sm = factories
    (eid,) = _seed(admin_session)
    ddb = disp_sm()
    try:
        run_once(
            dispatcher_db=ddb,
            platform_session_factory=platform_sm,
            transport=_RecordingTransport(fail=True),
            worker_id="w1",
            policy=RelayPolicy(max_attempts=5),
        )
    finally:
        ddb.close()
    row = admin_session.execute(
        text("SELECT status, attempts FROM platform_outbox_events WHERE id = :id"),
        {"id": str(eid)},
    ).one()
    assert row.status == "pending" and row.attempts == 1  # retry, not dead yet


def test_run_forever_drains_then_stops_cleanly(
    admin_session, factories, _cleanup
) -> None:
    disp_sm, platform_sm = factories
    ids = _seed(admin_session, n=3)
    transport = _RecordingTransport()
    stop = threading.Event()
    t = threading.Thread(
        target=run_forever,
        kwargs={
            "dispatcher_session_factory": disp_sm,
            "platform_session_factory": platform_sm,
            "transport": transport,
            "worker_id": "w1",
            "stop": stop,
            "poll_interval": 0.05,
        },
    )
    t.start()
    deadline = time.time() + 10
    while time.time() < deadline and len(transport.seen_user) < 3:
        time.sleep(0.05)
    stop.set()
    t.join(timeout=5)
    assert not t.is_alive()  # clean shutdown
    assert set(transport.seen_user) == set(ids)


def test_duplicate_delivery_is_deduped_by_process_once_platform(
    admin_session, factories, _cleanup
) -> None:
    """At-least-once + consumer dedupe: re-delivering the SAME event twice runs the
    consumer's effect once, because it wraps the effect in `process_once_platform`
    keyed on the event id."""
    _disp_sm, platform_sm = factories
    event = ClaimedPlatformEvent(
        id=uuid.uuid4(),
        event_type="contract.activated",
        payload={"contract_id": "c1"},
        attempts=0,
        correlation_id=None,
    )
    runs: list[str] = []

    class _ConsumingTransport:
        def deliver(self, ev: ClaimedPlatformEvent, platform_db: Session) -> None:
            def handler(_db: Session) -> dict[str, object]:
                runs.append(str(ev.id))
                return {"applied": True}

            process_once_platform(
                platform_db,
                command_id=str(ev.id),
                command_type="test.consume",
                handler=handler,
            )

    transport = _ConsumingTransport()
    # Two independent deliveries of the same event (a crash-and-redeliver).
    for _ in range(2):
        db = platform_sm()
        try:
            transport.deliver(event, db)
            db.commit()
        finally:
            db.close()

    assert runs == [str(event.id)]  # effect ran exactly once despite 2 deliveries
