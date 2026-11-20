"""S10 entry criteria on real Postgres, before the transport (K-8).

(a) Exhausting relay attempts (or the deferral bound) dead-letters the outbox
    row AND moves the dispatch to `dead`, and S8's audited requeue brings it
    back to `pending`.
(b) A terminal verdict from a transport (an unknown provider outcome) dead-letters
    at once: no retry, attempts not charged, dispatch `dead`.

These use a deliberately small test transport; the real transport and the full
outcome matrix are in `tests/test_delivery_relay_transport_pg.py`.
"""

# ruff: noqa: F811  (imported pytest fixtures are re-declared as test arguments)

from __future__ import annotations

import uuid

from dotmac_kernel.delivery import DeliveryDeferred, DeliveryTerminal
from dotmac_kernel.delivery_dispatch import settle_dispatch
from dotmac_kernel.delivery_requeue import requeue_dead_dispatch
from dotmac_kernel.messaging.relay import ClaimedEvent, RelayPolicy
from sqlalchemy import text
from sqlalchemy.orm import Session

from tests.delivery_relay_support import (  # noqa: F401  (fixtures + helpers)
    audit_registry,
    cleanup,
    dispatch_row,
    engines,
    factories,
    make_dispatch,
    make_due,
    outbox_rows,
    run,
)


class _Transport:
    def __init__(self, mode: str, *, dead_hook_fails: bool = False) -> None:
        self.mode = mode
        self.calls = 0
        self.dead_hook_fails = dead_hook_fails

    @staticmethod
    def _id(event: ClaimedEvent) -> uuid.UUID:
        return uuid.UUID(event.payload["dispatch_id"])

    def deliver(self, event: ClaimedEvent, tenant_db: Session) -> None:
        self.calls += 1
        if self.mode == "fail":
            raise RuntimeError("provider down")
        if self.mode == "defer":
            raise DeliveryDeferred("gate held", retry_after=30)
        if self.mode == "unknown":
            settle_dispatch(tenant_db, event.tenant_id, self._id(event), "dead")
            tenant_db.commit()
            raise DeliveryTerminal("provider outcome unknown")
        if self.mode == "ok":
            settle_dispatch(tenant_db, event.tenant_id, self._id(event), "sent")

    def dead_letter(self, event: ClaimedEvent, tenant_db: Session) -> None:
        if self.dead_hook_fails:
            raise RuntimeError("db down")
        settle_dispatch(tenant_db, event.tenant_id, self._id(event), "dead")


def test_exhausting_attempts_marks_outbox_and_dispatch_dead_and_requeue_revives(
    admin_session, tenant_a, factories, audit_registry, cleanup
) -> None:
    did = make_dispatch(factories, tenant_a)
    policy = RelayPolicy(max_attempts=2)
    transport = _Transport("fail")

    assert run(factories, transport, policy) == 1
    (row,) = outbox_rows(admin_session, tenant_a)
    assert (row.status, row.attempts) == ("pending", 1)
    assert dispatch_row(admin_session, did).status == "pending"  # retry, not dead
    make_due(admin_session)

    assert run(factories, transport, policy) == 1  # second failure exhausts
    (row,) = outbox_rows(admin_session, tenant_a)
    assert (row.status, row.attempts) == ("dead", 2)
    dead = dispatch_row(admin_session, did)
    assert dead.status == "dead" and dead.terminal_at is not None

    # Nothing re-enters by itself.
    make_due(admin_session)
    assert run(factories, transport, policy) == 0

    # S8's requeue-dead returns it to pending with a fresh outbox event.
    _, tenant_sm = factories
    db = tenant_sm()
    try:
        db.execute(
            text("SELECT set_config('app.current_tenant', :t, true)"),
            {"t": str(tenant_a.id)},
        )
        res = requeue_dead_dispatch(
            db,
            tenant_a.id,
            dispatch_id=did,
            actor_id="operator-1",
            reason="provider restored",
            source_application="dotmac_starter_mt",
        )
        db.commit()
    finally:
        db.close()
    assert res.requeued and dispatch_row(admin_session, did).status == "pending"
    rows = outbox_rows(admin_session, tenant_a)
    assert [r.status for r in rows] == ["dead", "pending"]
    assert rows[1].payload == {"dispatch_id": str(did)}

    assert run(factories, _Transport("ok"), policy) == 1
    assert dispatch_row(admin_session, did).status == "sent"
    assert outbox_rows(admin_session, tenant_a)[1].status == "sent"


def test_exceeding_the_deferral_bound_marks_the_dispatch_dead_too(
    admin_session, tenant_a, factories, cleanup
) -> None:
    did = make_dispatch(factories, tenant_a)
    policy = RelayPolicy(max_deferrals=1)
    transport = _Transport("defer")

    run(factories, transport, policy)
    (row,) = outbox_rows(admin_session, tenant_a)
    assert row.status == "pending" and row.attempts == 0
    assert dispatch_row(admin_session, did).status == "pending"
    make_due(admin_session)

    run(factories, transport, policy)  # the second hold exceeds the bound
    (row,) = outbox_rows(admin_session, tenant_a)
    assert row.status == "dead" and row.last_error.startswith("deferral bound")
    assert dispatch_row(admin_session, did).status == "dead"


def test_a_failed_dead_marking_leaves_the_outbox_row_unsettled(
    admin_session, tenant_a, factories, cleanup
) -> None:
    """The outbox row never goes dead while the dispatch is still live: if the
    dispatch cannot be marked, the lease is left to expire and the event is
    reclaimed."""
    did = make_dispatch(factories, tenant_a)
    transport = _Transport("fail", dead_hook_fails=True)
    run(factories, transport, RelayPolicy(max_attempts=1))
    (row,) = outbox_rows(admin_session, tenant_a)
    assert row.status == "claimed" and row.attempts == 0
    assert dispatch_row(admin_session, did).status == "pending"


def test_a_terminal_verdict_dead_letters_at_once_and_is_never_retried(
    admin_session, tenant_a, factories, cleanup
) -> None:
    did = make_dispatch(factories, tenant_a)
    transport = _Transport("unknown")

    assert run(factories, transport, RelayPolicy(max_attempts=8)) == 1
    (row,) = outbox_rows(admin_session, tenant_a)
    assert row.status == "dead"
    assert row.attempts == 0  # not a charged failure
    assert row.last_error == "terminal: provider outcome unknown"
    assert dispatch_row(admin_session, did).status == "dead"

    make_due(admin_session)
    assert run(factories, transport) == 0  # claimed nothing: no retry loop
    assert transport.calls == 1
