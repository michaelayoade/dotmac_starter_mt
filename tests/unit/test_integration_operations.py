"""Health, audit and repair.

The two tests that carry real weight:

* health is DERIVED — a stored summary would be a second writer over facts the
  ledgers already hold, and it drifts the moment a worker dies mid-update;
* a replay RESETS the attempt budget, unlike the source, because leaving the
  count at the cap makes the replay a no-op that looks like an action.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from dotmac_integration import (
    ConnectorInstallation,
    DeliveryAttempt,
    HealthReport,
    InboxReceipt,
    NotRepairable,
    Outcome,
    OutcomeStatus,
    PollingCheckpoint,
    claim_delivery,
    enqueue_delivery,
    health_report,
    receive_verified,
    record_delivery_outcome,
    release_expired_leases,
    replay_delivery,
    replay_receipt,
)
from sqlalchemy import create_engine
from sqlalchemy.orm import Session


class _AuditSpy:
    """Stands in for the kernel's platform audit ledger.

    Patched over `record_operation`'s deferred import so these tests need no
    database for the kernel's own tables — the point being that this module
    WRITES to that ledger rather than keeping one.
    """

    def __init__(self) -> None:
        self.events: list[dict] = []

    def __call__(self, db, **kwargs):
        self.events.append(kwargs)
        return kwargs


@pytest.fixture()
def audit(monkeypatch: pytest.MonkeyPatch) -> _AuditSpy:
    spy = _AuditSpy()
    import dotmac_integration.operations as operations

    monkeypatch.setattr(operations, "record_operation", spy)
    return spy


@pytest.fixture()
def db() -> Session:
    engine = create_engine(
        "sqlite:///:memory:",
        execution_options={"schema_translate_map": {"mod_intg": None}},
    )
    for model in (
        ConnectorInstallation,
        InboxReceipt,
        DeliveryAttempt,
        PollingCheckpoint,
    ):
        model.__table__.create(engine)
    with Session(engine) as session:
        yield session


@pytest.fixture()
def installation(db: Session) -> ConnectorInstallation:
    record = ConnectorInstallation(
        id=uuid.uuid4(),
        connector_key="conformance_fake",
        connector_version="1.0.0",
        spi_range=">=1.0,<2.0",
        manifest_digest="d" * 64,
        name="primary",
        state="enabled",
    )
    db.add(record)
    db.flush()
    return record


# ── Health is derived ───────────────────────────────────────────────────────


def test_a_quiet_platform_needs_no_attention(db: Session) -> None:
    report = health_report(db)
    assert report == HealthReport()
    assert report.needs_attention is False


def test_an_expired_lease_shows_as_stuck(
    db: Session, installation: ConnectorInstallation
) -> None:
    """A worker died holding the lease. Nothing retries it until it is
    reclaimed, and that is exactly what a green dashboard usually misses."""
    delivery, _ = enqueue_delivery(
        db,
        installation_id=installation.id,
        event_type="e",
        idempotency_key="k",
        payload={},
    )
    claim_delivery(delivery, lease_seconds=60)
    db.flush()

    later = datetime.now(UTC) + timedelta(seconds=600)
    report = health_report(db, now=later)
    assert report.in_flight_expired == 1
    assert report.needs_attention


def test_overdue_work_counts_even_though_nothing_failed(
    db: Session, installation: ConnectorInstallation
) -> None:
    """A queue whose due work is in the past is not healthy just because
    nothing has errored yet."""
    enqueue_delivery(
        db,
        installation_id=installation.id,
        event_type="e",
        idempotency_key="k",
        payload={},
    )
    db.flush()
    report = health_report(db, now=datetime.now(UTC) + timedelta(minutes=5))
    assert report.retryable_overdue == 1


def test_dead_letters_and_reconciliation_are_counted_separately(
    db: Session, installation: ConnectorInstallation
) -> None:
    """They need different humans doing different things."""
    for key, status in (
        ("dead", OutcomeStatus.TERMINAL),
        ("recon", OutcomeStatus.RECONCILIATION_REQUIRED),
    ):
        delivery, _ = enqueue_delivery(
            db,
            installation_id=installation.id,
            event_type="e",
            idempotency_key=key,
            payload={},
        )
        claim_delivery(delivery)
        record_delivery_outcome(delivery, Outcome(status=status))
    db.flush()

    report = health_report(db)
    assert report.dead_letter == 1
    assert report.reconciliation_required == 1


def test_an_unprocessed_receipt_is_visible(
    db: Session, installation: ConnectorInstallation
) -> None:
    receive_verified(
        db,
        installation_id=installation.id,
        provider_event_id="evt",
        event_type="e",
        payload={},
    )
    db.flush()
    assert health_report(db).receipts_unprocessed == 1


def test_a_checkpoint_that_never_advanced_is_stale(db: Session) -> None:
    """The window between cursors grows silently, which is the failure mode a
    polling integration dies of."""
    db.add(
        PollingCheckpoint(
            id=uuid.uuid4(),
            capability_binding_id=uuid.uuid4(),
            job_key="live_tail",
            version=1,
        )
    )
    db.flush()
    assert health_report(db).checkpoints_stale == 1


def test_health_is_scoped_when_an_installation_is_named(
    db: Session, installation: ConnectorInstallation
) -> None:
    other = ConnectorInstallation(
        id=uuid.uuid4(),
        connector_key="conformance_fake",
        connector_version="1.0.0",
        spi_range=">=1.0,<2.0",
        manifest_digest="d" * 64,
        name="secondary",
        state="enabled",
    )
    db.add(other)
    db.flush()
    enqueue_delivery(
        db,
        installation_id=other.id,
        event_type="e",
        idempotency_key="k",
        payload={},
    )
    db.flush()

    later = datetime.now(UTC) + timedelta(minutes=5)
    assert (
        health_report(db, installation_id=installation.id, now=later).retryable_overdue
        == 0
    )
    assert health_report(db, installation_id=other.id, now=later).retryable_overdue == 1


def test_no_health_is_persisted_anywhere() -> None:
    """SENSITIVITY PROOF for "derived, never stored".

    A `health` column would be a second writer over the same facts, drifting
    the moment a worker dies between updating a delivery and its summary.
    """
    from dotmac_integration import models
    from dotmac_integration.models import PLATFORM_TABLES

    assert "health" not in " ".join(PLATFORM_TABLES)
    for name in dir(models):
        model = getattr(models, name)
        table = getattr(model, "__table__", None)
        if table is None:
            continue
        assert not [c for c in table.columns if "health" in c.name], table.name


# ── Repair ──────────────────────────────────────────────────────────────────


def test_replaying_a_dead_letter_resets_the_attempt_budget(
    db: Session, installation: ConnectorInstallation, audit: _AuditSpy
) -> None:
    """The source's defect, fixed.

    `dotmac_sub`'s replay leaves attempt_count at the cap, so the very next
    outcome dead-letters the delivery again — a replay that looks like an action
    and does nothing.
    """
    delivery, _ = enqueue_delivery(
        db,
        installation_id=installation.id,
        event_type="e",
        idempotency_key="k",
        payload={},
    )
    delivery.state = "dead_letter"
    delivery.attempt_count = 10

    replay_delivery(db, delivery, reason="provider outage resolved")

    assert delivery.state == "pending"
    assert delivery.attempt_count == 0
    assert delivery.error_code is None


def test_a_replay_preserves_the_evidence_it_resets(
    db: Session, installation: ConnectorInstallation, audit: _AuditSpy
) -> None:
    delivery, _ = enqueue_delivery(
        db,
        installation_id=installation.id,
        event_type="e",
        idempotency_key="k",
        payload={},
    )
    delivery.state = "dead_letter"
    delivery.attempt_count = 7

    replay_delivery(db, delivery, reason="fixed upstream")

    assert len(audit.events) == 1
    details = audit.events[0]["details"]
    assert details["previous_attempt_count"] == 7
    assert details["previous_state"] == "dead_letter"
    assert details["reason"] == "fixed upstream"


def test_a_delivered_effect_is_never_replayable(
    db: Session, installation: ConnectorInstallation, audit: _AuditSpy
) -> None:
    """Replaying a success is how a provider gets charged twice."""
    delivery, _ = enqueue_delivery(
        db,
        installation_id=installation.id,
        event_type="e",
        idempotency_key="k",
        payload={},
    )
    delivery.state = "delivered"
    with pytest.raises(NotRepairable, match="provider sees it twice"):
        replay_delivery(db, delivery, reason="oops")


def test_a_receipt_replay_returns_it_to_verified(
    db: Session, installation: ConnectorInstallation, audit: _AuditSpy
) -> None:
    receipt, _ = receive_verified(
        db,
        installation_id=installation.id,
        provider_event_id="evt",
        event_type="e",
        payload={},
    )
    receipt.state = "dead_letter"
    receipt.attempt_count = 4

    replay_receipt(db, receipt, reason="mapping fixed")

    assert receipt.state == "verified"
    assert receipt.attempt_count == 0
    assert audit.events[0]["details"]["previous_attempt_count"] == 4


def test_a_processed_receipt_is_not_replayable(
    db: Session, installation: ConnectorInstallation, audit: _AuditSpy
) -> None:
    receipt, _ = receive_verified(
        db,
        installation_id=installation.id,
        provider_event_id="evt",
        event_type="e",
        payload={},
    )
    receipt.state = "processed"
    with pytest.raises(NotRepairable):
        replay_receipt(db, receipt, reason="no")


def test_releasing_leases_does_not_reset_the_attempt_budget(
    db: Session, installation: ConnectorInstallation, audit: _AuditSpy
) -> None:
    """Unlike a replay. The attempt genuinely happened, and pretending
    otherwise would let a permanently failing delivery retry forever.
    """
    delivery, _ = enqueue_delivery(
        db,
        installation_id=installation.id,
        event_type="e",
        idempotency_key="k",
        payload={},
    )
    claim_delivery(delivery, lease_seconds=60)
    db.flush()

    later = datetime.now(UTC) + timedelta(seconds=600)
    released = release_expired_leases(db, now=later)

    assert released == 1
    assert delivery.state == "retryable"
    assert delivery.attempt_count == 1
    assert delivery.leased_until is None


def test_releasing_leases_is_idempotent(
    db: Session, installation: ConnectorInstallation, audit: _AuditSpy
) -> None:
    """Safe on a timer: it only touches already-expired leases."""
    delivery, _ = enqueue_delivery(
        db,
        installation_id=installation.id,
        event_type="e",
        idempotency_key="k",
        payload={},
    )
    claim_delivery(delivery, lease_seconds=60)
    db.flush()
    later = datetime.now(UTC) + timedelta(seconds=600)

    assert release_expired_leases(db, now=later) == 1
    assert release_expired_leases(db, now=later) == 0


def test_a_live_lease_is_left_alone(
    db: Session, installation: ConnectorInstallation, audit: _AuditSpy
) -> None:
    delivery, _ = enqueue_delivery(
        db,
        installation_id=installation.id,
        event_type="e",
        idempotency_key="k",
        payload={},
    )
    claim_delivery(delivery, lease_seconds=600)
    db.flush()
    assert release_expired_leases(db) == 0
    assert delivery.state == "in_flight"


# ── Audit is the kernel's ledger ────────────────────────────────────────────


def test_the_module_owns_no_second_audit_ledger() -> None:
    """Same rule as idempotency: one platform audit trail for the fleet.

    A module keeping its own splits the trail exactly when an incident needs it
    whole.
    """
    import inspect

    from dotmac_integration import operations

    source = inspect.getsource(operations)
    assert "write_platform_audit_event" in source
    for forbidden in ("__tablename__", "class AuditEvent"):
        assert forbidden not in source


def test_every_audit_action_reaches_the_kernel_namespaced(
    db: Session,
    installation: ConnectorInstallation,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Patches the KERNEL function, not `record_operation`.

    Spying on `record_operation` would bypass the prefixing that happens inside
    it, and the test would assert the un-prefixed action while claiming to
    prove namespacing — passing for the wrong reason.
    """
    written: list[dict] = []

    import dotmac_kernel.audit as kernel_audit

    monkeypatch.setattr(
        kernel_audit,
        "write_platform_audit_event",
        lambda db, **kwargs: written.append(kwargs) or kwargs,
    )

    delivery, _ = enqueue_delivery(
        db,
        installation_id=installation.id,
        event_type="e",
        idempotency_key="k",
        payload={},
    )
    delivery.state = "dead_letter"
    replay_delivery(db, delivery, reason="r")

    assert written[0]["action"] == "integration.delivery.replayed"
    assert written[0]["entity_type"] == "delivery_attempt"
