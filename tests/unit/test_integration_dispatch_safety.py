"""The four runtime blockers found in review, as canaries.

Each was written to FAIL against the implementation as reviewed, and each
describes damage that only appears in production:

1. a preflight failure left the delivery LEASED — nothing retries it until the
   lease expires, and the queue looks busy rather than broken;
2. settlement read-then-wrote, so a takeover could race between the two;
3. a thrown connector became RETRYABLE, which re-sends an effect the provider
   may already have applied;
4. the claim ignored `next_attempt_at`, so the public dispatch seam bypassed
   backoff entirely — a failing provider gets hammered instead of backed off.

A fifth thing is pinned here too: the two "cannot dispatch" outcomes must stay
DISTINCT. `None` means the database did not grant this worker a claim, and the
caller moves on. `DispatchUnavailable` means the configuration is wrong —
disabled installation, missing binding or plugin, invalid manifest pin — and the
caller should alert and stop. Collapsing them turns a misconfiguration into
silent idling.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from dotmac_integration import (
    CapabilityBinding,
    ConnectorConfigRevision,
    ConnectorInstallation,
    DeliveryAttempt,
    DispatchUnavailable,
    OutcomeStatus,
    add_binding,
    claim_delivery,
    create_draft,
    enable,
    enqueue_delivery,
    invoke,
    prepare,
    put_config_revision,
    set_binding_enabled,
)
from dotmac_integration.conformance import FAKE_CAPABILITY, fake_plugin, fake_registry
from sqlalchemy import create_engine
from sqlalchemy.orm import Session


@pytest.fixture()
def db() -> Session:
    engine = create_engine(
        "sqlite:///:memory:",
        execution_options={"schema_translate_map": {"mod_intg": None}},
    )
    for model in (
        ConnectorInstallation,
        ConnectorConfigRevision,
        CapabilityBinding,
        DeliveryAttempt,
    ):
        model.__table__.create(engine)
    with Session(engine) as session:
        yield session


@pytest.fixture()
def registry():
    return fake_registry()


def _enabled(db: Session, registry) -> tuple:
    installation = create_draft(
        db, registry=registry, connector_key="conformance_fake", name="primary"
    )
    put_config_revision(db, installation, config={"a": 1})
    enable(db, installation, registry=registry)
    binding = add_binding(
        db, installation, registry=registry, capability_id=FAKE_CAPABILITY
    )
    set_binding_enabled(db, installation, binding, registry=registry, enabled=True)
    return installation, binding


def _queued(db: Session, installation, binding, key: str = "k"):
    delivery, _ = enqueue_delivery(
        db,
        installation_id=installation.id,
        capability_binding_id=binding.id,
        event_type="e",
        idempotency_key=key,
        payload={},
    )
    return delivery


# ── Blocker 1: a preflight failure must not leave the delivery leased ───────


def test_a_disabled_installation_does_not_strand_the_delivery(
    db: Session, registry
) -> None:
    """Claiming BEFORE validating leaks the lease.

    The delivery is left `in_flight` with a live lease, so nothing retries it
    until the lease expires — and the queue reports busy rather than broken,
    which is the worst of both.
    """
    installation, binding = _enabled(db, registry)
    delivery = _queued(db, installation, binding)

    installation.state = "disabled"
    db.flush()

    with pytest.raises(DispatchUnavailable):
        prepare(db, delivery, registry=registry)

    db.refresh(delivery)
    assert delivery.state == "pending", "preflight failure left the delivery claimed"
    assert delivery.leased_until is None
    assert delivery.attempt_count == 0, "a rejected dispatch burned an attempt"


def test_an_uninstalled_connector_does_not_strand_the_delivery(
    db: Session, registry
) -> None:
    installation, binding = _enabled(db, registry)
    delivery = _queued(db, installation, binding)

    empty = fake_registry(plugins=[])
    with pytest.raises(DispatchUnavailable):
        prepare(db, delivery, registry=empty)

    db.refresh(delivery)
    assert delivery.state == "pending"
    assert delivery.attempt_count == 0


def test_an_unhonoured_manifest_pin_is_refused_before_claiming(
    db: Session, registry
) -> None:
    """The historical-pin check belongs in preflight.

    An installation pinned to a digest the installed connector no longer honours
    must not reach a provider — the payload shape it was configured for is no
    longer the one the connector implements.
    """
    installation, binding = _enabled(db, registry)
    delivery = _queued(db, installation, binding)

    # A connector that supersedes the pin without keeping it in its window.
    superseding = fake_registry(
        plugins=[fake_plugin(manifest_=_other_manifest(), historical=())]
    )
    with pytest.raises(DispatchUnavailable, match="manifest"):
        prepare(db, delivery, registry=superseding)

    db.refresh(delivery)
    assert delivery.state == "pending"
    assert delivery.attempt_count == 0


def _other_manifest():
    from dotmac_integration.conformance import fake_manifest

    return fake_manifest(version="9.9.9")


# ── The two outcomes stay distinct ──────────────────────────────────────────


def test_a_lost_claim_is_none_and_a_misconfiguration_raises(
    db: Session, registry
) -> None:
    """`None` = the database did not grant a claim; move on.
    `DispatchUnavailable` = the configuration is wrong; alert and stop.

    Collapsing them turns a misconfiguration into silent idling.
    """
    installation, binding = _enabled(db, registry)
    delivery = _queued(db, installation, binding)

    assert prepare(db, delivery, registry=registry) is not None
    # Contended: already claimed by this worker's own first call.
    assert prepare(db, delivery, registry=registry) is None


# ── Blocker 3: a thrown connector is RECONCILIATION_REQUIRED ────────────────


def test_a_thrown_connector_requires_reconciliation_not_retry(db: Session) -> None:
    """A throw tells us nothing about whether the effect LANDED.

    Retrying may apply it twice; dead-lettering hides it. Only an explicit
    connector outcome may request a retry — the connector is the only party that
    knows the effect did not happen.
    """
    broken = fake_registry(
        plugins=[fake_plugin(raises=RuntimeError("socket closed mid-write"))]
    )
    installation, binding = _enabled(db, broken)
    delivery = _queued(db, installation, binding)

    prepared = prepare(db, delivery, registry=broken)
    outcome = invoke(prepared, registry=broken, resolve_secrets=lambda r: {})

    assert outcome.status is OutcomeStatus.RECONCILIATION_REQUIRED
    assert outcome.error_code == "connector_raised"


def test_a_connector_exception_message_is_never_persisted(db: Session) -> None:
    """The sharpest of the three exception-text paths, because it STORES.

    `invoke` hands the handler MATERIALIZED SECRETS, and `error_detail` is
    persisted by `execution` to `inbox_receipts.error_detail` and
    `delivery_attempts.error_detail` — both `Text`. A connector that
    interpolated a resolved credential into its own exception therefore did not
    merely log it, it wrote it to a column an operator reads and a support
    export copies.

    `ingress.ConnectorRaised` already held this line for the request path. This
    is the same discipline on the path that writes to disk.
    """
    sentinel = "SENTINEL-MATERIALIZED-SECRET-7c2e40"
    broken = fake_registry(
        plugins=[fake_plugin(raises=RuntimeError(f"auth failed for {sentinel}"))]
    )
    installation, binding = _enabled(db, broken)
    delivery = _queued(db, installation, binding)

    prepared = prepare(db, delivery, registry=broken)
    outcome = invoke(prepared, registry=broken, resolve_secrets=lambda r: {})

    assert outcome.error_detail is not None
    assert sentinel not in outcome.error_detail
    # Still diagnosable: an operator gets the class, which is what locates the
    # bug. The connector's own logs keep the detail.
    assert outcome.error_detail == "RuntimeError"


def test_a_message_cannot_masquerade_as_a_type_name(db: Session) -> None:
    """Sensitivity proof for the structural half.

    Keeping only `type(exc).__name__` is a convention until something enforces
    the SHAPE. `.isidentifier()` is that enforcement, and this drives it with a
    class whose name is not an identifier — the shape a crafted exception would
    take to smuggle text through a field documented as a type name.
    """
    from dotmac_integration.dispatch import _connector_error_detail

    assert _connector_error_detail(RuntimeError("x")) == "RuntimeError"

    smuggled = type("not an identifier: leaked", (Exception,), {})
    assert _connector_error_detail(smuggled()) == "Exception"
    assert "leaked" not in _connector_error_detail(smuggled())


def test_a_contract_violation_also_requires_reconciliation(db: Session) -> None:
    """Same reasoning: a handler that returned the wrong type may still have
    performed the call before returning."""
    from dotmac_integration.conformance import FakePlugin

    class _WrongType(FakePlugin):
        def handler_for(self, capability_id: str):
            return lambda request: "not an Outcome"

    odd = fake_registry(plugins=[_WrongType()])
    installation, binding = _enabled(db, odd)
    delivery = _queued(db, installation, binding)

    prepared = prepare(db, delivery, registry=odd)
    outcome = invoke(prepared, registry=odd, resolve_secrets=lambda r: {})
    assert outcome.status is OutcomeStatus.RECONCILIATION_REQUIRED


def test_an_explicit_connector_retry_is_still_honoured(db: Session) -> None:
    """Specificity for the two above: the connector CAN ask for a retry, because
    it is the only party that knows the effect did not happen."""
    from dotmac_integration import Outcome

    polite = fake_registry(
        plugins=[
            fake_plugin(
                outcome=Outcome(status=OutcomeStatus.RETRYABLE, error_code="rate_limit")
            )
        ]
    )
    installation, binding = _enabled(db, polite)
    delivery = _queued(db, installation, binding)

    prepared = prepare(db, delivery, registry=polite)
    outcome = invoke(prepared, registry=polite, resolve_secrets=lambda r: {})
    assert outcome.status is OutcomeStatus.RETRYABLE


# ── Blocker 4: the claim must respect backoff ───────────────────────────────


def test_a_delivery_scheduled_for_the_future_cannot_be_claimed(
    db: Session, registry
) -> None:
    """Otherwise the public dispatch seam bypasses backoff entirely and a
    failing provider is hammered instead of backed off."""
    installation, binding = _enabled(db, registry)
    delivery = _queued(db, installation, binding)

    delivery.state = "retryable"
    delivery.next_attempt_at = datetime.now(UTC) + timedelta(hours=1)
    delivery.leased_until = None
    db.flush()

    assert claim_delivery(db, delivery) is False
    assert prepare(db, delivery, registry=registry) is None


def test_a_delivery_whose_backoff_has_elapsed_is_claimable(
    db: Session, registry
) -> None:
    """Specificity: the rule is 'not yet due', not 'never again'."""
    installation, binding = _enabled(db, registry)
    delivery = _queued(db, installation, binding)

    delivery.state = "retryable"
    delivery.next_attempt_at = datetime.now(UTC) - timedelta(minutes=1)
    db.flush()

    assert claim_delivery(db, delivery) is True


def test_a_delivery_with_no_schedule_is_claimable(db: Session, registry) -> None:
    """A freshly enqueued delivery has `next_attempt_at` set to now; one with
    NULL must not be stranded by the new predicate."""
    installation, binding = _enabled(db, registry)
    delivery = _queued(db, installation, binding)
    delivery.next_attempt_at = None
    db.flush()

    assert claim_delivery(db, delivery) is True


# ── Blocker 2: settlement is atomic ─────────────────────────────────────────


def test_settlement_is_one_conditional_update(db: Session, registry) -> None:
    """STRUCTURAL canary; the real proof is the two-session Postgres race in
    `tests/test_integration_isolation.py`.

    Read-then-write leaves a window: another worker can take over between the
    refresh and the flush, and the loser overwrites the winner's outcome.
    """
    import inspect

    from dotmac_integration import dispatch

    source = inspect.getsource(dispatch.settle)

    assert "rowcount" in source, "the UPDATE's rowcount is what proves the claim held"
    assert "update(DeliveryAttempt)" in source

    # ORDERING is the property, not the absence of a read. Reloading the row
    # AFTER a guarded write is harmless — it returns this worker's own result.
    # Reading BEFORE, to decide whether to write, is the race.
    update_at = source.index("update(DeliveryAttempt)")
    refresh_at = source.find("db.refresh(")
    assert (
        refresh_at == -1 or refresh_at > update_at
    ), "settle reads before it writes; a takeover can race between the two"


def test_settle_refuses_after_a_takeover(db: Session, registry) -> None:
    installation, binding = _enabled(db, registry)
    delivery = _queued(db, installation, binding)
    prepared = prepare(db, delivery, registry=registry)

    from dotmac_integration import LostClaim, Outcome, settle

    # Another worker takes over.
    delivery.attempt_count += 1
    delivery.leased_until = datetime.now(UTC) + timedelta(minutes=5)
    db.flush()

    with pytest.raises(LostClaim):
        settle(db, delivery, Outcome(status=OutcomeStatus.SUCCEEDED), prepared=prepared)


def test_the_timezone_workaround_is_gone(db: Session, registry) -> None:
    """Atomic settlement removes the Python comparison that needed it.

    Normalising a naive timestamp was a correct fix for a read-then-compare
    settle. Once the database evaluates the predicate, the workaround is dead
    code that would mislead the next reader.
    """
    import inspect

    from dotmac_integration import dispatch

    source = inspect.getsource(dispatch.settle)
    assert "tzinfo" not in source
    assert uuid  # keep the import meaningful for linting


def test_settling_a_retryable_outcome_schedules_backoff(db: Session, registry) -> None:
    """The branch a linter caught before a test did.

    `settle`'s retryable path referenced an unimported name and every test still
    passed, because nothing exercised it — an explicit connector retry had no
    coverage at all. It is the path a rate-limited provider takes, so it is the
    one most likely to run first in production.
    """
    from dotmac_integration import Outcome, settle

    polite = fake_registry(
        plugins=[
            fake_plugin(
                outcome=Outcome(status=OutcomeStatus.RETRYABLE, error_code="rate_limit")
            )
        ]
    )
    installation, binding = _enabled(db, polite)
    delivery = _queued(db, installation, binding)

    prepared = prepare(db, delivery, registry=polite)
    outcome = invoke(prepared, registry=polite, resolve_secrets=lambda r: {})
    settle(db, delivery, outcome, prepared=prepared)

    assert delivery.state == "retryable"
    assert delivery.leased_until is None
    assert delivery.next_attempt_at is not None
    assert delivery.error_code == "rate_limit"


def test_settling_a_provider_retry_after_is_honoured(db: Session, registry) -> None:
    """Specificity: the schedule comes from the retry policy, and a provider's
    own instruction wins over the curve."""
    from dotmac_integration import Outcome, settle

    now = datetime.now(UTC)
    polite = fake_registry(
        plugins=[
            fake_plugin(
                outcome=Outcome(status=OutcomeStatus.RETRYABLE, retry_after_seconds=7)
            )
        ]
    )
    installation, binding = _enabled(db, polite)
    delivery = _queued(db, installation, binding)

    prepared = prepare(db, delivery, registry=polite)
    outcome = invoke(prepared, registry=polite, resolve_secrets=lambda r: {})
    settle(db, delivery, outcome, prepared=prepared, now=now)

    # SQLite returns a timestamptz column NAIVE after the round-trip, so the
    # comparison is normalised rather than the value being wrong. Postgres
    # returns it aware; the isolation suite covers that side.
    scheduled = delivery.next_attempt_at
    if scheduled.tzinfo is None:
        scheduled = scheduled.replace(tzinfo=UTC)
    assert scheduled == now + timedelta(seconds=7)


def test_settling_a_success_clears_the_schedule(db: Session, registry) -> None:
    from dotmac_integration import Outcome, settle

    installation, binding = _enabled(db, registry)
    delivery = _queued(db, installation, binding)

    prepared = prepare(db, delivery, registry=registry)
    settle(db, delivery, Outcome(status=OutcomeStatus.SUCCEEDED), prepared=prepared)

    assert delivery.state == "delivered"
    assert delivery.next_attempt_at is None
    assert delivery.delivered_at is not None
    assert delivery.error_code is None
