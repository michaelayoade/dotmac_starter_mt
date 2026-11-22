"""A DELIVERY attempt is never resent after a crash, and its targets are bound.

Three gaps closed together:

1. an expired `in_flight` DELIVERY lease used to become `retryable` (and
   `claim_delivery` re-claimed it), so a crash after the provider was invoked
   resent the effect. It is now `reconciliation_required`, never auto-retried;
2. every terminal state's payload is redacted after retention;
3. `dispatch.prepare` injects the binding's network targets for DELIVERY.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from dotmac_integration import (
    CapabilityBinding,
    ConnectorConfigRevision,
    ConnectorInstallation,
    DeliveryAttempt,
    DispatchUnavailable,
    NetworkTarget,
    NetworkTargetError,
    NotRepairable,
    OutcomeStatus,
    add_binding,
    claim_delivery,
    create_draft,
    enable,
    enqueue_delivery,
    invoke,
    prepare,
    put_config_revision,
    release_expired_leases,
    replay_delivery,
    require_network_target,
    set_binding_enabled,
)
from dotmac_integration.conformance import FAKE_CAPABILITY, fake_plugin, fake_registry
from dotmac_integration.execution import LEASE_EXPIRED_ERROR_CODE
from dotmac_integration.outbound_repair import (
    ProviderEvidence,
    ProviderVerdict,
    RepairRefused,
    prepare_reconciliation,
    reconcile_with_evidence,
)
from dotmac_integration.policy import ExecutionPolicy
from dotmac_integration.retention import REDACTION_MARKER
from dotmac_integration.spi import NETWORK_TARGETS_CONFIG_KEY, bound_network_targets
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

LEASE = ExecutionPolicy(lease_seconds=60)
TABLES = (
    ConnectorInstallation,
    ConnectorConfigRevision,
    CapabilityBinding,
    DeliveryAttempt,
)


@pytest.fixture(autouse=True)
def audit(monkeypatch: pytest.MonkeyPatch) -> list[dict]:
    """Records every audit event instead of discarding it."""
    import dotmac_integration.operations as operations

    events: list[dict] = []
    monkeypatch.setattr(
        operations, "record_operation", lambda db, **kw: events.append(kw)
    )
    return events


@pytest.fixture()
def db(tmp_path) -> Iterator[Session]:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'q.db'}",
        execution_options={"schema_translate_map": {"mod_intg": None}},
    )
    for model in TABLES:
        model.__table__.create(engine)
    with Session(engine) as session:
        session.info["engine"] = engine
        yield session


def _enabled(db: Session, registry):
    installation = create_draft(
        db, registry=registry, connector_key="conformance_fake", name="primary"
    )
    binding = add_binding(
        db, installation, registry=registry, capability_id=FAKE_CAPABILITY
    )
    put_config_revision(db, installation, registry=registry, config={"a": 1})
    enable(db, installation, registry=registry)
    set_binding_enabled(db, installation, binding, registry=registry, enabled=True)
    db.commit()
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
    db.commit()
    return delivery


# ── Gap 1: no resend after a crash ──────────────────────────────────────────


def test_crash_after_invoke_is_ambiguous_with_no_second_invoke(db: Session) -> None:
    plugin = fake_plugin()
    registry = fake_registry(plugins=[plugin])
    installation, binding = _enabled(db, registry)
    delivery = _queued(db, installation, binding)

    prepared = prepare(db, delivery, registry=registry, policy=LEASE)
    assert prepared is not None
    invoke(prepared, registry=registry, resolve_secrets=lambda r: {})
    assert len(plugin.seen) == 1
    # ...the worker dies here: settle never runs. The lease then expires.
    later = datetime.now(UTC) + timedelta(seconds=600)

    # Neither the sweeper nor another dispatcher may make it retryable.
    assert prepare(db, delivery, registry=registry, policy=LEASE, now=later) is None
    assert release_expired_leases(db, now=later) == 0  # already ambiguous

    db.refresh(delivery)
    assert delivery.state == "reconciliation_required"
    assert delivery.error_code == LEASE_EXPIRED_ERROR_CODE
    assert delivery.next_attempt_at is None
    assert len(plugin.seen) == 1, "the provider was invoked a second time"
    # And it stays that way no matter how often anyone tries.
    assert prepare(db, delivery, registry=registry, policy=LEASE, now=later) is None
    assert len(plugin.seen) == 1


def test_a_failed_success_record_is_ambiguous_on_expiry_with_no_second_invoke(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The handler reported a successful send but recording it raised before
    the DB write (e.g. a missing declared contract). The row stays `in_flight`;
    on expiry it must become ambiguous, never be re-claimed and resent."""
    import dotmac_integration.dispatch as dispatch_module
    from dotmac_integration import settle

    plugin = fake_plugin()
    registry = fake_registry(plugins=[plugin])
    installation, binding = _enabled(db, registry)
    delivery = _queued(db, installation, binding)
    prepared = prepare(db, delivery, registry=registry, policy=LEASE)
    db.commit()  # the claim is committed before provider I/O
    outcome = invoke(prepared, registry=registry, resolve_secrets=lambda r: {})
    assert outcome.status is OutcomeStatus.SUCCEEDED

    def _boom(*args: object, **kwargs: object) -> None:
        raise RuntimeError("declared contract missing")

    monkeypatch.setattr(dispatch_module, "_require_valid_result", _boom)
    with pytest.raises(RuntimeError):
        settle(db, delivery, outcome, prepared=prepared, policy=LEASE)
    db.rollback()
    db.refresh(delivery)
    assert delivery.state == "in_flight"

    later = datetime.now(UTC) + timedelta(seconds=600)
    assert prepare(db, delivery, registry=registry, policy=LEASE, now=later) is None
    db.refresh(delivery)
    assert delivery.state == "reconciliation_required"
    assert len(plugin.seen) == 1, "the mail was sent a second time"


def test_the_sweeper_alone_makes_an_expired_lease_ambiguous(db: Session) -> None:
    registry = fake_registry()
    installation, binding = _enabled(db, registry)
    delivery = _queued(db, installation, binding)
    assert claim_delivery(db, delivery, policy=LEASE)
    later = datetime.now(UTC) + timedelta(seconds=600)

    assert release_expired_leases(db, now=later) == 1

    db.refresh(delivery)
    assert delivery.state == "reconciliation_required"
    assert delivery.attempt_count == 1
    assert claim_delivery(db, delivery, now=later) is False


def test_a_live_lease_is_untouched_and_unclaimable(db: Session) -> None:
    registry = fake_registry()
    installation, binding = _enabled(db, registry)
    delivery = _queued(db, installation, binding)
    assert claim_delivery(db, delivery, policy=ExecutionPolicy(lease_seconds=600))
    assert claim_delivery(db, delivery) is False
    assert release_expired_leases(db) == 0
    assert delivery.state == "in_flight"


def test_two_claimers_one_row_exactly_one_wins(db: Session) -> None:
    registry = fake_registry()
    installation, binding = _enabled(db, registry)
    delivery = _queued(db, installation, binding)
    other = Session(db.info["engine"])
    try:
        mirror = other.get(DeliveryAttempt, delivery.id)
        # SQLite has one writer, so the first claim commits before the second
        # session tries; the Postgres test below races them for real.
        first = claim_delivery(db, delivery, policy=LEASE)
        db.commit()
        second = claim_delivery(other, mirror, policy=LEASE)
        other.commit()
        results = [first, second]
    finally:
        other.close()
    assert results.count(True) == 1
    db.refresh(delivery)
    assert delivery.attempt_count == 1


def test_provision_lease_semantics_are_unchanged() -> None:
    """PROVISION owns its own lease engine, which is idempotent by contract
    (an unknown outcome is only followed by `observe`). It must not have been
    routed through the DELIVERY ambiguity rule."""
    import inspect

    from dotmac_integration import provisioning

    source = inspect.getsource(provisioning)
    assert "ambiguate_expired_delivery_leases" not in source
    assert "LEASE_EXPIRED_ERROR_CODE" not in source
    # The step claim still takes an expired lease over by the same predicate.
    assert "ProvisionStepAttempt.leased_until < moment" in source


def test_requeue_of_a_lease_ambiguous_delivery_needs_acknowledgement(
    db: Session, audit: list[dict]
) -> None:
    registry = fake_registry()
    installation, binding = _enabled(db, registry)
    delivery = _queued(db, installation, binding)
    assert claim_delivery(db, delivery, policy=LEASE)
    later = datetime.now(UTC) + timedelta(seconds=600)
    release_expired_leases(db, now=later)
    db.refresh(delivery)

    with pytest.raises(NotRepairable, match="acknowledge_possible_duplicate"):
        replay_delivery(db, delivery, reason="operator")
    assert delivery.state == "reconciliation_required"

    replay_delivery(
        db,
        delivery,
        reason="operator accepts duplicate risk",
        acknowledge_possible_duplicate=True,
    )
    assert delivery.state == "pending"
    replayed = [e for e in audit if e["action"] == "delivery.replayed"]
    assert replayed[0]["details"]["acknowledged_possible_duplicate"] is True


# ── Gap 3: DELIVERY network targets ─────────────────────────────────────────

TARGET = {
    "cidr": "192.0.2.0/24",
    "protocol": "tcp",
    "first_port": 25,
    "last_port": 25,
}


def _bind_targets(db: Session, installation, value) -> None:
    revision = db.get(ConnectorConfigRevision, installation.current_config_revision_id)
    revision.network_targets_json = value
    db.commit()


def test_a_delivery_handler_receives_the_bound_targets(db: Session) -> None:
    plugin = fake_plugin()
    registry = fake_registry(plugins=[plugin])
    installation, binding = _enabled(db, registry)
    _bind_targets(db, installation, [TARGET])
    delivery = _queued(db, installation, binding)

    prepared = prepare(db, delivery, registry=registry)
    invoke(prepared, registry=registry, resolve_secrets=lambda r: {})

    config = plugin.seen[0].config
    targets = bound_network_targets(config)
    assert targets == (NetworkTarget("192.0.2.0/24", "tcp", 25, 25),)
    assert config["a"] == 1, "ordinary config is preserved"
    # Engine-owned: a same-named stored config entry cannot widen the targets.
    assert NETWORK_TARGETS_CONFIG_KEY in config
    require_network_target(targets, address="192.0.2.9", port=25, protocol="tcp")
    with pytest.raises(NetworkTargetError, match="outside the bound"):
        require_network_target(targets, address="198.51.100.1", port=25, protocol="tcp")
    with pytest.raises(NetworkTargetError, match="outside the bound"):
        require_network_target(targets, address="192.0.2.9", port=587, protocol="tcp")


def test_stored_config_cannot_forge_network_targets(db: Session) -> None:
    plugin = fake_plugin()
    registry = fake_registry(plugins=[plugin])
    installation, binding = _enabled(db, registry)
    revision = db.get(ConnectorConfigRevision, installation.current_config_revision_id)
    revision.config_json = {NETWORK_TARGETS_CONFIG_KEY: [TARGET]}
    db.commit()
    delivery = _queued(db, installation, binding)

    prepared = prepare(db, delivery, registry=registry)
    invoke(prepared, registry=registry, resolve_secrets=lambda r: {})

    assert bound_network_targets(plugin.seen[0].config) == ()


def test_no_bound_targets_means_deny_all(db: Session) -> None:
    plugin = fake_plugin()
    registry = fake_registry(plugins=[plugin])
    installation, binding = _enabled(db, registry)
    delivery = _queued(db, installation, binding)

    prepared = prepare(db, delivery, registry=registry)
    invoke(prepared, registry=registry, resolve_secrets=lambda r: {})

    targets = bound_network_targets(plugin.seen[0].config)
    assert targets == ()
    with pytest.raises(NetworkTargetError):
        require_network_target(targets, address="192.0.2.9", port=25, protocol="tcp")


def test_malformed_bound_targets_refuse_before_the_claim(db: Session) -> None:
    registry = fake_registry()
    installation, binding = _enabled(db, registry)
    _bind_targets(db, installation, [{"cidr": "not-a-cidr"}])
    delivery = _queued(db, installation, binding)

    with pytest.raises(DispatchUnavailable, match="network targets"):
        prepare(db, delivery, registry=registry)
    db.refresh(delivery)
    assert delivery.state == "pending"
    assert delivery.attempt_count == 0
    assert OutcomeStatus.SUCCEEDED  # keep import honest


# ── Review fixes ────────────────────────────────────────────────────────────


def test_the_claim_path_ambiguation_is_audited_like_the_sweep(
    db: Session, audit: list[dict]
) -> None:
    registry = fake_registry()
    installation, binding = _enabled(db, registry)
    delivery = _queued(db, installation, binding)
    assert claim_delivery(db, delivery, policy=LEASE)
    later = datetime.now(UTC) + timedelta(seconds=600)

    assert claim_delivery(db, delivery, now=later) is False

    (event,) = (e for e in audit if e["action"] == "leases.released")
    assert event["details"]["via"] == "claim"
    assert event["details"]["delivery_ids"] == [str(delivery.id)]
    assert event["details"]["state"] == "reconciliation_required"


def test_the_sweep_audit_names_its_path(db: Session, audit: list[dict]) -> None:
    registry = fake_registry()
    installation, binding = _enabled(db, registry)
    delivery = _queued(db, installation, binding)
    assert claim_delivery(db, delivery, policy=LEASE)
    release_expired_leases(db, now=datetime.now(UTC) + timedelta(seconds=600))
    (event,) = (e for e in audit if e["action"] == "leases.released")
    assert event["details"]["via"] == "sweep"


@pytest.mark.parametrize(
    "code", [LEASE_EXPIRED_ERROR_CODE, "reconcile_inconclusive", "connector_raised"]
)
def test_every_ambiguous_replay_needs_acknowledgement_whatever_the_code(
    db: Session, code: str
) -> None:
    """An UNKNOWN reconcile overwrites `error_code`; the gate must not key on it."""
    registry = fake_registry()
    installation, binding = _enabled(db, registry)
    delivery = _queued(db, installation, binding)
    delivery.state = "reconciliation_required"
    delivery.error_code = code
    db.commit()

    with pytest.raises(NotRepairable, match="acknowledge_possible_duplicate"):
        replay_delivery(db, delivery, reason="operator")
    assert delivery.state == "reconciliation_required"
    replay_delivery(
        db, delivery, reason="operator", acknowledge_possible_duplicate=True
    )
    assert delivery.state == "pending"


def test_a_dead_letter_replay_needs_no_acknowledgement(db: Session) -> None:
    registry = fake_registry()
    installation, binding = _enabled(db, registry)
    delivery = _queued(db, installation, binding)
    delivery.state = "dead_letter"
    db.commit()
    replay_delivery(db, delivery, reason="provider outage over")
    assert delivery.state == "pending"


def _tombstone(db: Session, delivery: DeliveryAttempt) -> None:
    delivery.payload_json = {REDACTION_MARKER: {"payload_digest": "x"}}
    db.commit()


def test_replay_refuses_a_redacted_row(db: Session) -> None:
    registry = fake_registry()
    installation, binding = _enabled(db, registry)
    delivery = _queued(db, installation, binding)
    delivery.state = "dead_letter"
    _tombstone(db, delivery)
    with pytest.raises(NotRepairable, match="redacted"):
        replay_delivery(db, delivery, reason="operator")


def test_replay_sees_a_purge_committed_by_another_session(db: Session) -> None:
    """The replay's lock re-reads the payload even though this session's copy
    of the row is stale (the purge ran in another transaction)."""
    registry = fake_registry()
    installation, binding = _enabled(db, registry)
    delivery = _queued(db, installation, binding)
    delivery.state = "dead_letter"
    db.commit()
    stale = db.get(DeliveryAttempt, delivery.id)  # loaded, unredacted
    assert stale is not None and REDACTION_MARKER not in (stale.payload_json or {})
    other = Session(db.info["engine"])
    try:
        row = other.get(DeliveryAttempt, delivery.id)
        row.payload_json = {REDACTION_MARKER: {"payload_digest": "x"}}
        other.commit()
    finally:
        other.close()
    with pytest.raises(NotRepairable, match="redacted"):
        replay_delivery(db, stale, reason="operator")


def test_the_claim_never_dispatches_a_tombstone(db: Session) -> None:
    """Backstop: even if a redacted row reaches `pending`, the database refuses
    to claim it."""
    plugin = fake_plugin()
    registry = fake_registry(plugins=[plugin])
    installation, binding = _enabled(db, registry)
    delivery = _queued(db, installation, binding)
    _tombstone(db, delivery)  # pending + tombstone

    assert prepare(db, delivery, registry=registry) is None
    db.refresh(delivery)
    assert delivery.state == "pending"
    assert delivery.attempt_count == 0
    assert plugin.seen == []


def test_not_landed_reconcile_refuses_if_the_purge_won_the_race(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The UPDATE itself carries the not-redacted predicate: simulate the purge
    landing after the Python evidence check by lying to that check."""
    import dotmac_integration.outbound_repair as repair

    registry = fake_registry()
    installation, binding = _enabled(db, registry)
    delivery = _queued(db, installation, binding)
    delivery.state = "reconciliation_required"
    delivery.attempt_count = 1
    db.commit()
    subject = prepare_reconciliation(db, delivery_id=delivery.id)
    _tombstone(db, delivery)
    monkeypatch.setattr(
        repair, "classify_request_evidence", lambda d: repair.EvidenceStatus.INTACT
    )

    with pytest.raises(RepairRefused) as excinfo:
        reconcile_with_evidence(
            db,
            subject,
            ProviderEvidence(verdict=ProviderVerdict.NOT_LANDED),
            reason="probe said absent",
        )
    assert "raced" in str(excinfo.value) or getattr(excinfo.value, "reason", "") == (
        "raced"
    )
    db.refresh(delivery)
    assert delivery.state == "reconciliation_required"


# ── Every terminal path stamps terminal_at ─────────────────────────────────


def _settle_with(db: Session, plugin_outcome, *, attempts_cap: int = 1):
    from dotmac_integration import Outcome, settle  # noqa: F401

    plugin = fake_plugin(outcome=plugin_outcome)
    registry = fake_registry(plugins=[plugin])
    installation, binding = _enabled(db, registry)
    delivery = _queued(db, installation, binding)
    policy = ExecutionPolicy(lease_seconds=600, max_attempts=attempts_cap)
    prepared = prepare(db, delivery, registry=registry, policy=policy)
    outcome = invoke(prepared, registry=registry, resolve_secrets=lambda r: {})
    settle(db, delivery, outcome, prepared=prepared, policy=policy)
    db.refresh(delivery)
    return delivery


@pytest.mark.parametrize(
    ("status", "state"),
    [
        (OutcomeStatus.SUCCEEDED, "delivered"),
        (OutcomeStatus.TERMINAL, "dead_letter"),
        (OutcomeStatus.RECONCILIATION_REQUIRED, "reconciliation_required"),
        (OutcomeStatus.RETRYABLE, "dead_letter"),  # exhausted at max_attempts=1
    ],
)
def test_settle_stamps_terminal_at_on_every_terminal_state(
    db: Session, status: OutcomeStatus, state: str
) -> None:
    from dotmac_integration import Outcome

    delivery = _settle_with(db, Outcome(status=status))
    assert delivery.state == state
    assert delivery.terminal_at is not None


def test_settle_leaves_a_retryable_row_unstamped(db: Session) -> None:
    from dotmac_integration import Outcome

    delivery = _settle_with(db, Outcome(status=OutcomeStatus.RETRYABLE), attempts_cap=5)
    assert delivery.state == "retryable"
    assert delivery.terminal_at is None


@pytest.mark.parametrize(
    ("status", "state"),
    [
        (OutcomeStatus.SUCCEEDED, "delivered"),
        (OutcomeStatus.TERMINAL, "dead_letter"),
        (OutcomeStatus.RECONCILIATION_REQUIRED, "reconciliation_required"),
    ],
)
def test_record_delivery_outcome_stamps_terminal_at(
    db: Session, status: OutcomeStatus, state: str
) -> None:
    from dotmac_integration import Outcome
    from dotmac_integration.execution import record_delivery_outcome

    registry = fake_registry()
    installation, binding = _enabled(db, registry)
    delivery = _queued(db, installation, binding)
    moment = datetime(2026, 11, 22, 12, 0, tzinfo=UTC)
    record_delivery_outcome(delivery, Outcome(status=status), now=moment)
    assert delivery.state == state
    assert delivery.terminal_at == moment


def test_lease_ambiguation_stamps_terminal_at_on_both_paths(db: Session) -> None:
    registry = fake_registry()
    installation, binding = _enabled(db, registry)
    swept = _queued(db, installation, binding, key="a")
    claimed = _queued(db, installation, binding, key="b")
    assert claim_delivery(db, swept, policy=LEASE)
    assert claim_delivery(db, claimed, policy=LEASE)
    later = datetime.now(UTC) + timedelta(seconds=600)

    assert claim_delivery(db, claimed, now=later) is False  # claim path
    release_expired_leases(db, now=later)  # sweep path

    for row in (swept, claimed):
        db.refresh(row)
        assert row.state == "reconciliation_required"
        assert row.terminal_at is not None


def test_a_landed_reconcile_stamps_and_a_requeue_clears_terminal_at(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    import dotmac_integration.outbound_repair as repair

    monkeypatch.setattr(repair, "record_operation", lambda *a, **k: None)
    registry = fake_registry()
    installation, binding = _enabled(db, registry)
    landed = _queued(db, installation, binding, key="landed")
    landed.state = "reconciliation_required"
    landed.attempt_count = 1
    db.commit()
    subject = prepare_reconciliation(db, delivery_id=landed.id)
    reconcile_with_evidence(
        db,
        subject,
        ProviderEvidence(verdict=ProviderVerdict.LANDED),
        reason="provider has it",
    )
    db.refresh(landed)
    assert landed.state == "delivered"
    assert landed.terminal_at is not None

    rearmed = _queued(db, installation, binding, key="rearm")
    rearmed.state = "dead_letter"
    rearmed.terminal_at = datetime.now(UTC)
    db.commit()
    replay_delivery(db, rearmed, reason="operator")
    assert rearmed.state == "pending"
    assert rearmed.terminal_at is None


def test_replay_refuses_a_live_in_flight_row_even_from_a_stale_copy(
    db: Session,
) -> None:
    """The replay's lock re-reads the STATE: this session still believes the row
    is retryable, but another worker has claimed it."""
    registry = fake_registry()
    installation, binding = _enabled(db, registry)
    delivery = _queued(db, installation, binding)
    delivery.state = "retryable"
    db.commit()
    stale = db.get(DeliveryAttempt, delivery.id)
    assert stale is not None and stale.state == "retryable"
    other = Session(db.info["engine"])
    try:
        row = other.get(DeliveryAttempt, delivery.id)
        assert claim_delivery(other, row, policy=ExecutionPolicy(lease_seconds=600))
        other.commit()
    finally:
        other.close()

    with pytest.raises(NotRepairable, match="in_flight"):
        replay_delivery(db, stale, reason="double click")
    db.refresh(delivery)
    assert delivery.state == "in_flight"
    assert delivery.attempt_count == 1
