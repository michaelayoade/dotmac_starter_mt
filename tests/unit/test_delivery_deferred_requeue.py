"""K-4 (deferral) and K-5 (audited requeue) behaviour, SQLite lane.

Deferral: `DeliveryDeferred` reschedules without charging an attempt, is bounded,
and never writes a receipt. Requeue: tenant-scoped, idempotent, audited, and it
does not bypass consent or the K-2 replay rules. RLS and grants are proven on
Postgres in `tests/test_delivery_receipts_append_only.py`.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from dotmac_kernel import consent
from dotmac_kernel.audit import AuditEvent
from dotmac_kernel.delivery import DeliveryDeferred, record_receipt
from dotmac_kernel.delivery_dispatch import DispatchNotFound, create_dispatch
from dotmac_kernel.delivery_models import (
    DELIVERY_ACCEPTED,
    DELIVERY_FAILED,
    DISPATCH_DEAD,
    DISPATCH_PENDING,
    DISPATCH_SENT,
    CommunicationDispatch,
)
from dotmac_kernel.delivery_providers import OutboundMessage, ProviderResult, send
from dotmac_kernel.delivery_requeue import (
    AUDIT_ACTION_REQUEUED,
    RequeueRefused,
    requeue_dead_dispatch,
)
from dotmac_kernel.messaging import relay, worker
from dotmac_kernel.messaging.models import OutboxEvent
from dotmac_kernel.models import Tenant
from sqlalchemy import select

ACTOR = {"actor_type": "user", "actor_id": "operator-1"}
SOURCE = "dotmac_starter_mt"


class _Db:
    """Records the settle calls `record_*` make; the dispatcher role has no table
    privilege, so everything must go through settle_outbox_event."""

    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []
        self.commits = 0

    def execute(self, stmt, params=None):
        self.calls.append({"sql": str(stmt), **(params or {})})

    def commit(self) -> None:
        self.commits += 1


EID = uuid4()


def test_deferred_validates_retry_after() -> None:
    assert DeliveryDeferred("held").retry_after is None
    assert DeliveryDeferred("held", retry_after=5).retry_after == 5
    for bad in (-1, float("nan"), float("inf"), True, "5"):
        with pytest.raises(ValueError):
            DeliveryDeferred("held", retry_after=bad)  # type: ignore[arg-type]


def test_deferral_does_not_charge_an_attempt_and_counts_the_run() -> None:
    db = _Db()
    out = relay.record_deferral(
        db,  # type: ignore[arg-type]
        event_id=EID,
        worker_id="w",
        attempts=3,
        deferrals=0,
        reason="gate held",
    )
    assert not out.dead_lettered and out.deferrals == 1
    (call,) = db.calls
    assert "'pending'" in call["sql"]
    assert call["a"] == 3  # attempts passed back unchanged
    assert call["e"] == "deferred:1: gate held"
    assert (
        relay.ClaimedEvent(
            EID,
            uuid4(),
            "t",
            {},
            3,
            None,
            last_error=call["e"],  # type: ignore[arg-type]
        ).deferrals
        == 1
    )
    delay = out.retry_at - datetime.now(UTC)  # type: ignore[operator]
    assert timedelta(seconds=290) < delay <= timedelta(seconds=300)


@pytest.mark.parametrize(
    ("retry_after", "low", "high"),
    [(0, 29, 30), (1e9, 3599, 3600), (120, 119, 120)],
)
def test_retry_after_is_clamped(retry_after, low, high) -> None:
    out = relay.record_deferral(
        _Db(),  # type: ignore[arg-type]
        event_id=EID,
        worker_id="w",
        attempts=0,
        deferrals=0,
        reason="x",
        retry_after=retry_after,
    )
    delay = (out.retry_at - datetime.now(UTC)).total_seconds()  # type: ignore[operator]
    assert low < delay <= high


def test_deferral_is_bounded_then_dead_letters_without_charging() -> None:
    policy = relay.RelayPolicy(max_deferrals=3)
    db = _Db()
    ok = relay.record_deferral(
        db,  # type: ignore[arg-type]
        event_id=EID,
        worker_id="w",
        attempts=2,
        deferrals=2,
        reason="gate",
        policy=policy,
    )
    assert not ok.dead_lettered and ok.deferrals == 3
    over = relay.record_deferral(
        db,  # type: ignore[arg-type]
        event_id=EID,
        worker_id="w",
        attempts=2,
        deferrals=3,
        reason="gate",
        policy=policy,
    )
    assert over.dead_lettered and over.retry_at is None
    assert "'dead'" in db.calls[-1]["sql"]
    assert db.calls[-1]["a"] == 2  # still not charged
    assert str(db.calls[-1]["e"]).startswith("deferral bound exceeded (3)")


def test_default_bound_is_a_day_at_the_default_delay() -> None:
    p = relay.RelayPolicy()
    assert p.max_deferrals * p.default_defer_seconds == 24 * 3600


def test_failure_resets_the_run_and_garbage_counts_as_zero() -> None:
    for text in (None, "", "boom", "deferred:", "deferred:x: y", "deferred:-1: y"):
        assert relay.ClaimedEvent(EID, uuid4(), "t", {}, 0, None, text).deferrals == 0


def test_worker_routes_a_hold_to_deferral_not_failure(monkeypatch) -> None:
    seen: dict[str, object] = {}
    monkeypatch.setattr(
        worker, "record_deferral", lambda *a, **k: seen.setdefault("defer", k)
    )
    monkeypatch.setattr(
        worker, "record_failure", lambda *a, **k: seen.setdefault("fail", k)
    )

    class _Tenant:
        def execute(self, *a, **k): ...
        def rollback(self): ...
        def commit(self): ...
        def close(self): ...

    class _Transport:
        def deliver(self, event, tenant_db):
            raise DeliveryDeferred("gate held", retry_after=60)

    event = relay.ClaimedEvent(
        EID, uuid4(), "t", {}, 4, None, last_error="deferred:7: old"
    )
    worker._deliver_one(
        dispatcher_db=_Db(),  # type: ignore[arg-type]
        tenant_session_factory=_Tenant,  # type: ignore[arg-type]
        transport=_Transport(),
        event=event,
        worker_id="w",
        policy=relay.RelayPolicy(),
    )
    assert "fail" not in seen
    assert seen["defer"]["attempts"] == 4  # type: ignore[index]
    assert seen["defer"]["deferrals"] == 7  # type: ignore[index]
    assert seen["defer"]["retry_after"] == 60  # type: ignore[index]


def test_other_exceptions_still_charge_an_attempt(monkeypatch) -> None:
    seen: dict[str, object] = {}
    monkeypatch.setattr(
        worker, "record_failure", lambda *a, **k: seen.setdefault("fail", k)
    )

    class _Tenant:
        def execute(self, *a, **k): ...
        def rollback(self): ...
        def commit(self): ...
        def close(self): ...

    class _Transport:
        def deliver(self, event, tenant_db):
            raise RuntimeError("boom")

    worker._deliver_one(
        dispatcher_db=_Db(),  # type: ignore[arg-type]
        tenant_session_factory=_Tenant,  # type: ignore[arg-type]
        transport=_Transport(),
        event=relay.ClaimedEvent(EID, uuid4(), "t", {}, 4, None),
        worker_id="w",
        policy=relay.RelayPolicy(),
    )
    assert seen["fail"]["attempts"] == 4  # type: ignore[index]


class _HeldProvider:
    name = "held"

    def send(self, message: OutboundMessage) -> ProviderResult:
        raise DeliveryDeferred("gate held")


# ---------------------------------------------------------------- DB fixtures
@pytest.fixture(autouse=True)
def _registries():
    previous = consent._reset_registries_for_tests(marketing=("marketing",))
    yield
    consent._reset_registries_for_tests(marketing=previous[0], numeric=previous[1])


@pytest.fixture
def tenant(db) -> Tenant:
    row = Tenant(name="Acme", slug=f"acme-{uuid4().hex[:8]}")
    db.add(row)
    db.flush()
    return row


def _dead(db, tenant, key="k-1", address="jane@example.com"):
    res = create_dispatch(
        db,
        tenant.id,
        idempotency_key=key,
        on_conflict="raise_if_different",
        channel="email",
        category="transactional",
        address=address,
        template_provenance="template:welcome@r1",
        subject="Hi",
        text="body",
    )
    row = res.dispatch
    row.status = DISPATCH_DEAD
    row.terminal_at = datetime.now(UTC)
    db.flush()
    return res.dispatch_id


def _events(db, tenant):
    return list(
        db.execute(
            select(OutboxEvent).where(OutboxEvent.tenant_id == tenant.id)
        ).scalars()
    )


def _audits(db, tenant):
    return list(
        db.execute(
            select(AuditEvent).where(
                AuditEvent.tenant_id == tenant.id,
                AuditEvent.action == AUDIT_ACTION_REQUEUED,
            )
        ).scalars()
    )


def test_a_hold_writes_no_receipt_and_propagates(db, tenant) -> None:
    did = _dead(db, tenant)
    message = OutboundMessage(
        dispatch_id=did,
        channel="email",
        address="jane@example.com",
        body="b",
        category="transactional",
        subject="s",
    )
    with pytest.raises(DeliveryDeferred):
        send(db, tenant.id, provider=_HeldProvider(), message=message)
    from dotmac_kernel.delivery import latest_receipt_for_dispatch

    assert latest_receipt_for_dispatch(db, tenant.id, dispatch_id=did) is None


def test_requeue_moves_dead_to_pending_audits_and_enqueues_once(db, tenant) -> None:
    did = _dead(db, tenant)
    before = len(_events(db, tenant))
    res = requeue_dead_dispatch(
        db,
        tenant.id,
        dispatch_id=did,
        reason="gate lifted",
        source_application=SOURCE,
        **ACTOR,
    )
    assert res.requeued and res.status == DISPATCH_PENDING and res.outbox_event_id
    row = db.get(CommunicationDispatch, did)
    assert row.status == DISPATCH_PENDING and row.terminal_at is None
    events = _events(db, tenant)
    assert len(events) == before + 1
    new = next(e for e in events if e.id == res.outbox_event_id)
    assert new.payload == {"dispatch_id": str(did)}  # id only, never content
    (audit,) = _audits(db, tenant)
    assert audit.actor_id == "operator-1" and audit.actor_type == "user"
    assert audit.entity_id == str(did)
    assert audit.details["reason"] == "gate lifted"
    assert "jane@example.com" not in str(audit.details)


def test_requeue_is_idempotent(db, tenant) -> None:
    did = _dead(db, tenant)
    kw = {"reason": "r", "source_application": SOURCE, **ACTOR}
    first = requeue_dead_dispatch(db, tenant.id, dispatch_id=did, **kw)
    second = requeue_dead_dispatch(db, tenant.id, dispatch_id=did, **kw)
    assert first.requeued and not second.requeued
    assert second.outbox_event_id is None
    assert len(_audits(db, tenant)) == 1


def test_requeue_is_tenant_scoped(db, tenant) -> None:
    other = Tenant(name="Other", slug=f"o-{uuid4().hex[:8]}")
    db.add(other)
    db.flush()
    did = _dead(db, tenant)
    with pytest.raises(DispatchNotFound):
        requeue_dead_dispatch(
            db,
            other.id,
            dispatch_id=did,
            reason="r",
            source_application=SOURCE,
            **ACTOR,
        )
    assert db.get(CommunicationDispatch, did).status == DISPATCH_DEAD
    assert _audits(db, other) == []


def test_requeue_refuses_a_suppressed_address(db, tenant) -> None:
    did = _dead(db, tenant)
    consent.suppress(
        db,
        tenant.id,
        channel="email",
        address="jane@example.com",
        scope="all",
        reason="bounce",
    )
    with pytest.raises(RequeueRefused, match="suppressed"):
        requeue_dead_dispatch(
            db,
            tenant.id,
            dispatch_id=did,
            reason="r",
            source_application=SOURCE,
            **ACTOR,
        )
    assert db.get(CommunicationDispatch, did).status == DISPATCH_DEAD
    assert _audits(db, tenant) == []


def test_requeue_refuses_a_dispatch_with_a_final_receipt(db, tenant) -> None:
    did = _dead(db, tenant)
    record_receipt(
        db,
        tenant.id,
        channel="email",
        address="jane@example.com",
        provider="smtp",
        status=DELIVERY_ACCEPTED,
        dispatch_id=did,
        provider_message_id="m-1",
    )
    with pytest.raises(RequeueRefused, match="final receipt"):
        requeue_dead_dispatch(
            db,
            tenant.id,
            dispatch_id=did,
            reason="r",
            source_application=SOURCE,
            **ACTOR,
        )


def test_a_failed_only_receipt_does_not_block_requeue(db, tenant) -> None:
    did = _dead(db, tenant)
    record_receipt(
        db,
        tenant.id,
        channel="email",
        address="jane@example.com",
        provider="smtp",
        status=DELIVERY_FAILED,
        dispatch_id=did,
    )
    res = requeue_dead_dispatch(
        db, tenant.id, dispatch_id=did, reason="r", source_application=SOURCE, **ACTOR
    )
    assert res.requeued


def test_requeue_refuses_non_dead_redacted_and_bad_input(db, tenant) -> None:
    kw = {"source_application": SOURCE, **ACTOR}
    sent = _dead(db, tenant, key="k-sent")
    db.get(CommunicationDispatch, sent).status = DISPATCH_SENT
    db.flush()
    with pytest.raises(RequeueRefused, match="only a dead"):
        requeue_dead_dispatch(db, tenant.id, dispatch_id=sent, reason="r", **kw)

    redacted = _dead(db, tenant, key="k-red")
    row = db.get(CommunicationDispatch, redacted)
    row.redacted_at = datetime.now(UTC)
    row.address = row.subject = row.text_body = row.html_body = None
    row.headers = row.attachments = None
    db.flush()
    with pytest.raises(RequeueRefused, match="redacted"):
        requeue_dead_dispatch(db, tenant.id, dispatch_id=redacted, reason="r", **kw)

    did = _dead(db, tenant, key="k-bad")
    with pytest.raises(RequeueRefused, match="reason"):
        requeue_dead_dispatch(db, tenant.id, dispatch_id=did, reason="  ", **kw)
    with pytest.raises(RequeueRefused, match="exceeds"):
        requeue_dead_dispatch(db, tenant.id, dispatch_id=did, reason="x" * 501, **kw)
    # An actor is mandatory and nothing changed before it was refused.
    with pytest.raises(Exception, match="actor_id"):
        requeue_dead_dispatch(
            db,
            tenant.id,
            dispatch_id=did,
            reason="r",
            actor_type="user",
            actor_id=None,
            source_application=SOURCE,
        )
    assert db.get(CommunicationDispatch, did).status == DISPATCH_DEAD
    assert _audits(db, tenant) == []
