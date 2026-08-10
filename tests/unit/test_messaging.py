"""Unit tests for `dotmac_kernel.messaging` (kernel WS3) — SQLite, no RLS.

Covers the write-side behavior: idempotent command processing (inbox) and the
transactional outbox enqueue. Tenant isolation is proven separately on Postgres
(`tests/test_messaging_isolation.py`).
"""

from __future__ import annotations

import dataclasses

import pytest
from dotmac_kernel.idempotency_models import IdempotencyRecord
from dotmac_kernel.messaging import (
    CommandEnvelope,
    OutboxEvent,
    OutboxStatus,
    PlatformOutboxEvent,
    enqueue_event,
    enqueue_platform_event,
    process_once,
)
from dotmac_kernel.models import Tenant
from sqlalchemy import select
from sqlalchemy.orm import Session


# ── command envelope ─────────────────────────────────────────────────────────
def test_command_envelope_is_frozen(tenant_row: Tenant) -> None:
    env = CommandEnvelope(
        command_id="c1", command_type="do.thing", tenant_id=tenant_row.id
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        env.command_id = "c2"  # type: ignore[misc]


# ── outbox ───────────────────────────────────────────────────────────────────
def test_enqueue_event_writes_pending_row(db: Session, tenant_row: Tenant) -> None:
    ev = enqueue_event(
        db,
        tenant_id=tenant_row.id,
        event_type="thing.happened",
        payload={"x": 1},
        correlation_id="corr-1",
    )
    assert ev.id is not None
    assert ev.status == OutboxStatus.PENDING.value
    assert ev.attempts == 0
    assert ev.event_type == "thing.happened"
    assert ev.payload == {"x": 1}
    assert ev.correlation_id == "corr-1"
    # It is queryable in the session after flush.
    fetched = db.execute(
        select(OutboxEvent).where(OutboxEvent.id == ev.id)
    ).scalar_one()
    assert fetched.event_type == "thing.happened"


def test_enqueue_event_is_transactional(db: Session, tenant_row: Tenant) -> None:
    """The event participates in the caller's transaction — rolled back with a
    SAVEPOINT, it leaves nothing behind (the transactional-outbox guarantee)."""
    sp = db.begin_nested()
    enqueue_event(db, tenant_id=tenant_row.id, event_type="rolled.back")
    sp.rollback()
    remaining = db.execute(
        select(OutboxEvent).where(OutboxEvent.event_type == "rolled.back")
    ).all()
    assert remaining == []


# ── inbox / idempotent processing ────────────────────────────────────────────
def test_process_once_runs_handler_and_records_result(
    db: Session, tenant_row: Tenant
) -> None:
    calls: list[str] = []

    def handler(session: Session, env: CommandEnvelope) -> dict[str, object]:
        calls.append(env.command_id)
        return {"handled": env.command_id}

    env = CommandEnvelope(
        command_id="cmd-1",
        command_type="do.thing",
        tenant_id=tenant_row.id,
        payload={"a": 1},
        correlation_id="corr-9",
    )
    outcome = process_once(db, env, handler)
    assert outcome.status == "processed"
    assert not outcome.was_duplicate
    assert outcome.result == {"handled": "cmd-1"}
    assert calls == ["cmd-1"]

    record = db.execute(
        select(IdempotencyRecord).where(IdempotencyRecord.key == "cmd-1")
    ).scalar_one()
    assert record.tenant_id == tenant_row.id
    assert record.status == "executed"
    assert record.result == {"handled": "cmd-1"}
    assert record.correlation_id == "corr-9"


def test_process_once_is_idempotent_and_replays_result(
    db: Session, tenant_row: Tenant
) -> None:
    calls: list[str] = []

    def handler(session: Session, env: CommandEnvelope) -> dict[str, object]:
        calls.append(env.command_id)
        return {"n": len(calls)}

    env = CommandEnvelope(
        command_id="cmd-dup", command_type="do.thing", tenant_id=tenant_row.id
    )
    first = process_once(db, env, handler)
    assert first.status == "processed"
    assert first.result == {"n": 1}

    # Same command_id again: handler NOT re-run, prior result replayed.
    second = process_once(db, env, handler)
    assert second.was_duplicate
    assert second.result == {"n": 1}
    assert calls == ["cmd-dup"]  # exactly once

    # Only one ledger row exists.
    rows = db.execute(
        select(IdempotencyRecord).where(IdempotencyRecord.key == "cmd-dup")
    ).all()
    assert len(rows) == 1


def test_process_once_distinct_command_ids_each_run(
    db: Session, tenant_row: Tenant
) -> None:
    calls: list[str] = []

    def handler(session: Session, env: CommandEnvelope) -> None:
        calls.append(env.command_id)
        return None

    for cid in ("a", "b", "c"):
        process_once(
            db,
            CommandEnvelope(command_id=cid, command_type="t", tenant_id=tenant_row.id),
            handler,
        )
    assert calls == ["a", "b", "c"]


# ── platform outbox ──────────────────────────────────────────────────────────
def test_enqueue_platform_event_writes_pending_row_without_tenant(db: Session) -> None:
    ev = enqueue_platform_event(
        db,
        event_type="contract.activated",
        payload={"contract_id": "c1"},
        correlation_id="corr-1",
    )
    assert ev.id is not None
    assert ev.status == OutboxStatus.PENDING.value
    assert ev.attempts == 0
    assert ev.event_type == "contract.activated"
    assert ev.payload == {"contract_id": "c1"}
    assert ev.correlation_id == "corr-1"
    assert not hasattr(ev, "tenant_id")  # platform events are tenant-free
    fetched = db.execute(
        select(PlatformOutboxEvent).where(PlatformOutboxEvent.id == ev.id)
    ).scalar_one()
    assert fetched.event_type == "contract.activated"


def test_enqueue_platform_event_rolls_back_atomically(db: Session) -> None:
    """The event is durable iff the caller's transaction commits — a rollback
    drops it (the atomic guarantee ContractService relies on)."""
    ev = enqueue_platform_event(db, event_type="contract.submitted")
    ev_id = ev.id
    db.rollback()
    assert (
        db.execute(
            select(PlatformOutboxEvent).where(PlatformOutboxEvent.id == ev_id)
        ).first()
        is None
    )
