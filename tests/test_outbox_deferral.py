"""K-4 on real Postgres: a deferral does not charge an attempt, and is bounded.

Runs the worker as `outbox_dispatcher` (claim/settle functions only, no table
privilege) with a transport that holds every send. Also proves the extra
`last_error` column the claim now returns works through the real function.
"""

# ruff: noqa: F811  (imported pytest fixtures are re-declared as test arguments)

from __future__ import annotations

import uuid

import pytest
from dotmac_kernel.delivery import DeliveryDeferred
from dotmac_kernel.messaging.relay import ClaimedEvent, RelayPolicy
from dotmac_kernel.messaging.worker import run_once
from sqlalchemy import text
from sqlalchemy.orm import Session

from tests.test_outbox_worker import (  # noqa: F401  (fixtures)
    _cleanup,
    _seed,
    engines,
    factories,
)


class _Hold:
    def deliver(self, event: ClaimedEvent, tenant_db: Session) -> None:
        raise DeliveryDeferred("mail gate held", retry_after=45)


def _row(admin_session: Session, eid: uuid.UUID):
    return admin_session.execute(
        text(
            "SELECT status, attempts, last_error, available_at > now() AS future "
            "FROM outbox_events WHERE id = :id"
        ),
        {"id": str(eid)},
    ).one()


def _make_due(admin_session: Session, eid: uuid.UUID) -> None:
    admin_session.execute(
        text("UPDATE outbox_events SET available_at = now() WHERE id = :id"),
        {"id": str(eid)},
    )
    admin_session.commit()


def _run(factories, policy: RelayPolicy) -> None:
    disp_sm, tenant_sm = factories
    ddb = disp_sm()
    try:
        run_once(
            dispatcher_db=ddb,
            tenant_session_factory=tenant_sm,
            transport=_Hold(),
            worker_id="w1",
            policy=policy,
        )
    finally:
        ddb.close()


def test_deferral_reschedules_without_charging_then_dead_letters_at_the_bound(
    admin_session,
    tenant_a,
    factories,
    _cleanup,
) -> None:
    (eid,) = _seed(admin_session, tenant_a.id)
    policy = RelayPolicy(max_attempts=2, max_deferrals=3)

    for expected in (1, 2, 3):
        _run(factories, policy)
        admin_session.rollback()
        row = _row(admin_session, eid)
        assert row.status == "pending" and row.future
        assert row.attempts == 0  # never charged, though max_attempts is 2
        assert row.last_error == f"deferred:{expected}: mail gate held"
        _make_due(admin_session, eid)

    _run(factories, policy)  # the 4th hold exceeds the bound
    admin_session.rollback()
    row = _row(admin_session, eid)
    assert row.status == "dead" and row.attempts == 0
    assert row.last_error.startswith("deferral bound exceeded (3)")


def test_a_failure_after_deferrals_resets_the_run_and_charges(
    admin_session,
    tenant_a,
    factories,
    _cleanup,
) -> None:
    (eid,) = _seed(admin_session, tenant_a.id)
    _run(factories, RelayPolicy())
    admin_session.rollback()
    assert _row(admin_session, eid).last_error.startswith("deferred:1:")
    _make_due(admin_session, eid)

    from tests.test_outbox_worker import _RecordingTransport

    disp_sm, tenant_sm = factories
    ddb = disp_sm()
    try:
        run_once(
            dispatcher_db=ddb,
            tenant_session_factory=tenant_sm,
            transport=_RecordingTransport(fail=True),
            worker_id="w1",
        )
    finally:
        ddb.close()
    admin_session.rollback()
    row = _row(admin_session, eid)
    assert row.attempts == 1 and not row.last_error.startswith("deferred:")


@pytest.mark.parametrize("n", [1])
def test_claim_returns_last_error_through_the_real_function(
    n,
    admin_session,
    tenant_a,
    factories,
    _cleanup,
) -> None:
    from dotmac_kernel.messaging.relay import claim_batch

    (eid,) = _seed(admin_session, tenant_a.id)
    admin_session.execute(
        text("UPDATE outbox_events SET last_error = 'deferred:9: x' WHERE id = :id"),
        {"id": str(eid)},
    )
    admin_session.commit()
    disp_sm, _ = factories
    ddb = disp_sm()
    try:
        (event,) = claim_batch(ddb, worker_id="w1")
        ddb.commit()
    finally:
        ddb.close()
    assert event.deferrals == 9 and event.last_error == "deferred:9: x"
