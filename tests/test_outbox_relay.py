"""Outbox relay behavior on real Postgres (WS3 slice 2, PR 2).

Exercises the typed claim/success/failure operations against the PR-1 SECURITY
DEFINER functions, over the least-privilege dispatcher connection. Proves
at-least-once with **one active claim per lease** (concurrent workers never claim
the same row) — NOT exactly-once. Requires real Postgres (migration 0011).
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Generator

import pytest
from dotmac_kernel.messaging import relay
from dotmac_kernel.messaging.relay import RelayPolicy
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker


def _dispatcher_url() -> str:
    base = os.getenv("TEST_DATABASE_URL")
    if not base:
        pytest.skip("TEST_DATABASE_URL not set — relay behavior tests need Postgres")
    scheme_userhost, _, db = base.rpartition("/")
    scheme, _, userhost = scheme_userhost.partition("://")
    host = userhost.rpartition("@")[2]
    return f"{scheme}://outbox_dispatcher@{host}/{db}"


@pytest.fixture(scope="module")
def dispatcher_engine() -> Generator[Engine, None, None]:
    engine = create_engine(_dispatcher_url(), future=True)
    yield engine
    engine.dispose()


@pytest.fixture()
def dispatcher_sessionmaker(dispatcher_engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=dispatcher_engine, autocommit=False, autoflush=False)


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


def _status(admin_session: Session, eid: uuid.UUID) -> str:
    return admin_session.execute(
        text("SELECT status FROM outbox_events WHERE id = :id"), {"id": str(eid)}
    ).scalar()


@pytest.fixture()
def _cleanup(admin_session: Session, tenant_a):
    yield
    admin_session.execute(
        text("DELETE FROM outbox_events WHERE tenant_id = :t"),
        {"t": str(tenant_a.id)},
    )
    admin_session.commit()


def test_claim_then_success(
    admin_session, tenant_a, dispatcher_sessionmaker, _cleanup
) -> None:
    (eid,) = _seed(admin_session, tenant_a.id)
    s = dispatcher_sessionmaker()
    try:
        claimed = relay.claim_batch(
            s, worker_id="w1", policy=RelayPolicy(batch_size=10)
        )
        s.commit()
        mine = [c for c in claimed if c.id == eid]
        assert mine and mine[0].tenant_id == tenant_a.id and mine[0].attempts == 0
        assert relay.record_success(s, event_id=eid, worker_id="w1")
        s.commit()
    finally:
        s.close()
    assert _status(admin_session, eid) == "sent"


def test_failure_backs_off_then_dead_letters(
    admin_session, tenant_a, dispatcher_sessionmaker, _cleanup
) -> None:
    (eid,) = _seed(admin_session, tenant_a.id)
    policy = RelayPolicy(batch_size=10, max_attempts=3, base_backoff_seconds=1.0)
    s = dispatcher_sessionmaker()
    try:
        relay.claim_batch(s, worker_id="w1", policy=policy)
        s.commit()
        # attempts 0 -> 1: retry with a future available_at.
        out = relay.record_failure(
            s, event_id=eid, worker_id="w1", attempts=0, error="boom", policy=policy
        )
        s.commit()
        assert not out.dead_lettered and out.attempts == 1 and out.retry_at is not None
        assert _status(admin_session, eid) == "pending"

        # Re-claim (available_at now in future → won't be picked unless we force it);
        # simulate the final attempt directly by settling from the current lease.
        # Reclaim by making it due, then drive to the dead-letter threshold.
        admin_session.execute(
            text("UPDATE outbox_events SET available_at = now() WHERE id = :id"),
            {"id": str(eid)},
        )
        admin_session.commit()
        relay.claim_batch(s, worker_id="w1", policy=policy)
        s.commit()
        out2 = relay.record_failure(
            s, event_id=eid, worker_id="w1", attempts=2, error="boom", policy=policy
        )
        s.commit()
        assert out2.dead_lettered and out2.attempts == 3 and out2.retry_at is None
        assert _status(admin_session, eid) == "dead"
    finally:
        s.close()


def test_stale_lease_is_reclaimed(
    admin_session, tenant_a, dispatcher_sessionmaker, _cleanup
) -> None:
    (eid,) = _seed(admin_session, tenant_a.id)
    s = dispatcher_sessionmaker()
    try:
        relay.claim_batch(s, worker_id="w1", policy=RelayPolicy(batch_size=10))
        s.commit()
        # Age the lease so it is stale.
        admin_session.execute(
            text(
                "UPDATE outbox_events SET leased_at = now() - interval '1 hour' "
                "WHERE id = :id"
            ),
            {"id": str(eid)},
        )
        admin_session.commit()
        reclaimed = relay.claim_batch(
            s, worker_id="w2", policy=RelayPolicy(batch_size=10, stale_lease_seconds=60)
        )
        s.commit()
        assert any(c.id == eid for c in reclaimed)
    finally:
        s.close()
    leased_by = admin_session.execute(
        text("SELECT leased_by FROM outbox_events WHERE id = :id"), {"id": str(eid)}
    ).scalar()
    assert leased_by == "w2"


def test_concurrent_workers_never_double_claim(
    admin_session, tenant_a, dispatcher_sessionmaker, _cleanup
) -> None:
    ids = set(_seed(admin_session, tenant_a.id, n=6))
    a = dispatcher_sessionmaker()
    b = dispatcher_sessionmaker()
    try:
        # A claims inside an OPEN transaction (holds row locks, uncommitted)...
        a_claim = {
            c.id
            for c in relay.claim_batch(
                a, worker_id="wa", policy=RelayPolicy(batch_size=3)
            )
        }
        # ...B claims concurrently — SKIP LOCKED skips A's locked rows.
        b_claim = {
            c.id
            for c in relay.claim_batch(
                b, worker_id="wb", policy=RelayPolicy(batch_size=10)
            )
        }
        a.commit()
        b.commit()
        assert a_claim.isdisjoint(b_claim)  # one active claim per lease
        assert (a_claim | b_claim) & ids == ids  # every row claimed exactly once
    finally:
        a.close()
        b.close()
