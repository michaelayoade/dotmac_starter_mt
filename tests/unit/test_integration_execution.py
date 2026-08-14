"""Inbox, outbox, retry classification and checkpoints.

Slice 2, ported from `dotmac_sub`'s integration platform. The tests worth
reading are the ones that encode a distinction the source got right and a naive
port would flatten:

* a redelivery is a duplicate; the SAME event id with DIFFERENT content is a
  provider identity collision and must NOT be deduped;
* `reconciliation_required` is neither a retry nor a failure;
* a delivery is claimed with a LEASE, a checkpoint with an optimistic VERSION,
  because the risks differ — a double provider call versus a silently skipped
  polling window.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from dotmac_integration import (
    DEFAULT_POLICY,
    CapabilityBinding,
    CheckpointConflict,
    ConnectorInstallation,
    DeliveryAttempt,
    ExecutionError,
    ExecutionPolicy,
    InboxReceipt,
    Outcome,
    OutcomeStatus,
    PollingCheckpoint,
    ProviderEventIdentityCollision,
    advance_checkpoint,
    claim_delivery,
    claim_receipt,
    enqueue_delivery,
    next_state,
    payload_digest,
    receive_verified,
    record_delivery_outcome,
    record_receipt_outcome,
    retry_delay_seconds,
    scope_for,
)
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
        CapabilityBinding,
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


@pytest.fixture()
def binding(db: Session, installation: ConnectorInstallation) -> CapabilityBinding:
    record = CapabilityBinding(
        id=uuid.uuid4(),
        installation_id=installation.id,
        capability_id="conformance.echo.v1",
        state="enabled",
    )
    db.add(record)
    db.flush()
    return record


# ── Inbox: dedup vs identity collision ──────────────────────────────────────


def test_a_redelivery_of_the_same_payload_is_a_duplicate(
    db: Session, installation: ConnectorInstallation, binding: CapabilityBinding
) -> None:
    payload = {"id": "evt_1", "text": "hello"}
    first, new_first = receive_verified(
        db,
        installation_id=installation.id,
        capability_binding_id=binding.id,
        provider_event_id="evt_1",
        event_type="message.received",
        payload=payload,
    )
    second, new_second = receive_verified(
        db,
        installation_id=installation.id,
        capability_binding_id=binding.id,
        provider_event_id="evt_1",
        event_type="message.received",
        payload=payload,
    )
    assert new_first is True
    assert new_second is False
    assert first.id == second.id


def test_the_same_event_id_with_different_content_is_refused(
    db: Session, installation: ConnectorInstallation, binding: CapabilityBinding
) -> None:
    """The distinction a naive dedup flattens.

    A provider reusing an event id for different content is a provider defect.
    Deduping it discards the second payload silently, on the assumption the
    provider is well behaved.
    """
    receive_verified(
        db,
        installation_id=installation.id,
        capability_binding_id=binding.id,
        provider_event_id="evt_1",
        event_type="message.received",
        payload={"text": "hello"},
    )
    with pytest.raises(ProviderEventIdentityCollision, match="identity collision"):
        receive_verified(
            db,
            installation_id=installation.id,
            capability_binding_id=binding.id,
            provider_event_id="evt_1",
            event_type="message.received",
            payload={"text": "GOODBYE"},
        )


def test_key_order_does_not_change_a_digest() -> None:
    """Without `sort_keys`, a provider that reorders JSON turns every
    redelivery into an identity collision."""
    assert payload_digest({"a": 1, "b": 2}) == payload_digest({"b": 2, "a": 1})


def test_two_bindings_may_see_the_same_provider_event_id(
    db: Session, installation: ConnectorInstallation, binding: CapabilityBinding
) -> None:
    """Dedup is scoped to the BINDING, as in the source: the binding determines
    which capability handles the event, so two bindings observing one upstream
    event are two receipts with two consequences."""
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
    other_binding = CapabilityBinding(
        id=uuid.uuid4(),
        installation_id=other.id,
        capability_id="conformance.echo.v1",
        state="enabled",
    )
    db.add(other_binding)
    db.flush()

    _, first_new = receive_verified(
        db,
        installation_id=installation.id,
        capability_binding_id=binding.id,
        provider_event_id="shared",
        event_type="e",
        payload={"x": 1},
    )
    _, second_new = receive_verified(
        db,
        installation_id=other.id,
        capability_binding_id=other_binding.id,
        provider_event_id="shared",
        event_type="e",
        payload={"x": 1},
    )
    assert first_new and second_new


def test_a_dead_letter_receipt_cannot_be_claimed(
    db: Session, installation: ConnectorInstallation, binding: CapabilityBinding
) -> None:
    receipt, _ = receive_verified(
        db,
        installation_id=installation.id,
        capability_binding_id=binding.id,
        provider_event_id="evt",
        event_type="e",
        payload={},
    )
    receipt.state = "dead_letter"
    with pytest.raises(ExecutionError, match="authorized replay"):
        claim_receipt(receipt)


def test_no_product_error_code_leaks_into_the_generic_claim(
    db: Session, installation: ConnectorInstallation, binding: CapabilityBinding
) -> None:
    """SENSITIVITY PROOF for what did NOT port.

    Sub's generic claim branched on `crm_customer_name_rejected` — one product's
    business rule inside the shared engine. Any retryable receipt must be
    claimable here regardless of its error code; a connector that needs an error
    to be terminal says so with OutcomeStatus.TERMINAL.
    """
    receipt, _ = receive_verified(
        db,
        installation_id=installation.id,
        capability_binding_id=binding.id,
        provider_event_id="evt",
        event_type="e",
        payload={},
    )
    receipt.state = "retryable"
    receipt.error_code = "crm_customer_name_rejected"
    assert claim_receipt(receipt) is True

    # And the string must not appear as a CODE literal. Scanned via the AST
    # rather than the raw source, because this module's own docstring names it
    # to explain why it was dropped — a text search would match the
    # explanation and pass for the wrong reason.
    import ast
    import inspect

    from dotmac_integration import execution

    tree = ast.parse(inspect.getsource(execution))
    docstrings = {
        node.body[0].value
        for node in ast.walk(tree)
        if isinstance(
            node, ast.Module | ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef
        )
        and node.body
        and isinstance(node.body[0], ast.Expr)
        and isinstance(node.body[0].value, ast.Constant)
    }
    literals = [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and node not in docstrings
    ]
    assert not [
        text for text in literals if "crm_" in text
    ], "a product's error vocabulary is a code literal in the shared engine"


# ── Retry classification ────────────────────────────────────────────────────


def test_reconciliation_required_is_neither_retry_nor_failure() -> None:
    """The outcome people leave out. The effect may have half-landed, so
    retrying risks duplicating it and dead-lettering it hides it."""
    outcome = Outcome(status=OutcomeStatus.RECONCILIATION_REQUIRED)
    assert next_state(outcome, attempt_count=1) == "reconciliation_required"
    assert outcome.is_final


def test_attempt_exhaustion_turns_retryable_into_dead_letter() -> None:
    outcome = Outcome(status=OutcomeStatus.RETRYABLE)
    assert (
        next_state(outcome, attempt_count=3, policy=ExecutionPolicy(max_attempts=10))
        == "retryable"
    )
    assert (
        next_state(outcome, attempt_count=10, policy=ExecutionPolicy(max_attempts=10))
        == "dead_letter"
    )


def test_a_nonsense_attempt_cap_is_refused_where_it_is_configured() -> None:
    """Moved from a clamp to a construction-time refusal.

    `max_attempts=0` caught when the policy is BUILT beats it discovered when a
    delivery dead-letters on its first attempt.
    """
    with pytest.raises(ValueError, match="dead-letter every delivery"):
        ExecutionPolicy(max_attempts=0)
    with pytest.raises(ValueError, match="immortal"):
        ExecutionPolicy(max_attempts=999)


def test_backoff_is_exponential_and_capped() -> None:
    assert retry_delay_seconds(1) == 60
    assert retry_delay_seconds(2) == 120
    assert retry_delay_seconds(50) == DEFAULT_POLICY.max_backoff_seconds


def test_a_provider_retry_after_wins_but_is_bounded() -> None:
    """A provider that says when to come back knows better than a curve —
    ignoring it is how rate limits become outages. But `retry_after: 10y` would
    park a delivery past any operator's attention."""
    outcome = Outcome(status=OutcomeStatus.RETRYABLE, retry_after_seconds=5)
    assert retry_delay_seconds(9, outcome) == 5
    absurd = Outcome(status=OutcomeStatus.RETRYABLE, retry_after_seconds=10**9)
    assert retry_delay_seconds(1, absurd) == DEFAULT_POLICY.max_backoff_seconds


# ── Outbox: enqueue dedup, lease, outcome ───────────────────────────────────


def test_enqueueing_one_effect_twice_is_one_row(
    db: Session, installation: ConnectorInstallation, binding: CapabilityBinding
) -> None:
    first, new_first = enqueue_delivery(
        db,
        installation_id=installation.id,
        event_type="message.send",
        idempotency_key="k1",
        payload={"a": 1},
    )
    second, new_second = enqueue_delivery(
        db,
        installation_id=installation.id,
        event_type="message.send",
        idempotency_key="k1",
        payload={"a": 1},
    )
    assert new_first and not new_second
    assert first.id == second.id


def test_a_leased_delivery_cannot_be_claimed_twice(
    db: Session, installation: ConnectorInstallation, binding: CapabilityBinding
) -> None:
    """Two dispatchers without this both call the provider, and no downstream
    idempotency repairs a provider that has already seen two requests."""
    delivery, _ = enqueue_delivery(
        db,
        installation_id=installation.id,
        event_type="e",
        idempotency_key="k",
        payload={},
    )
    assert claim_delivery(db, delivery) is True
    assert claim_delivery(db, delivery) is False


def test_an_expired_lease_may_be_reclaimed(
    db: Session, installation: ConnectorInstallation, binding: CapabilityBinding
) -> None:
    """A worker that died holding a lease must not strand the delivery."""
    delivery, _ = enqueue_delivery(
        db,
        installation_id=installation.id,
        event_type="e",
        idempotency_key="k",
        payload={},
    )
    claim_delivery(db, delivery, policy=ExecutionPolicy(lease_seconds=60))
    later = datetime.now(UTC) + timedelta(seconds=120)
    assert claim_delivery(db, delivery, now=later) is True


def test_a_terminal_delivery_is_never_reclaimed(
    db: Session, installation: ConnectorInstallation, binding: CapabilityBinding
) -> None:
    delivery, _ = enqueue_delivery(
        db,
        installation_id=installation.id,
        event_type="e",
        idempotency_key="k",
        payload={},
    )
    delivery.state = "dead_letter"
    assert claim_delivery(db, delivery) is False


def test_a_successful_outcome_clears_the_lease_and_the_schedule(
    db: Session, installation: ConnectorInstallation, binding: CapabilityBinding
) -> None:
    delivery, _ = enqueue_delivery(
        db,
        installation_id=installation.id,
        event_type="e",
        idempotency_key="k",
        payload={},
    )
    claim_delivery(db, delivery)
    record_delivery_outcome(delivery, Outcome(status=OutcomeStatus.SUCCEEDED))

    assert delivery.state == "delivered"
    assert delivery.leased_until is None
    assert delivery.next_attempt_at is None
    assert delivery.delivered_at is not None


def test_a_terminal_outcome_leaves_nothing_due(
    db: Session, installation: ConnectorInstallation, binding: CapabilityBinding
) -> None:
    """Leaving `next_attempt_at` set would make a dispatcher pick it up
    forever."""
    delivery, _ = enqueue_delivery(
        db,
        installation_id=installation.id,
        event_type="e",
        idempotency_key="k",
        payload={},
    )
    claim_delivery(db, delivery)
    record_delivery_outcome(
        delivery, Outcome(status=OutcomeStatus.TERMINAL, error_code="bad_request")
    )
    assert delivery.state == "dead_letter"
    assert delivery.next_attempt_at is None
    assert delivery.error_code == "bad_request"


def test_a_retryable_outcome_schedules_the_next_attempt(
    db: Session, installation: ConnectorInstallation, binding: CapabilityBinding
) -> None:
    delivery, _ = enqueue_delivery(
        db,
        installation_id=installation.id,
        event_type="e",
        idempotency_key="k",
        payload={},
    )
    claim_delivery(db, delivery)
    now = datetime.now(UTC)
    record_delivery_outcome(delivery, Outcome(status=OutcomeStatus.RETRYABLE), now=now)
    assert delivery.state == "retryable"
    assert delivery.leased_until is None
    assert delivery.next_attempt_at == now + timedelta(seconds=60)


def test_a_receipt_outcome_records_what_it_caused(
    db: Session, installation: ConnectorInstallation, binding: CapabilityBinding
) -> None:
    receipt, _ = receive_verified(
        db,
        installation_id=installation.id,
        capability_binding_id=binding.id,
        provider_event_id="evt",
        event_type="e",
        payload={},
    )
    claim_receipt(receipt)
    record_receipt_outcome(
        receipt,
        Outcome(status=OutcomeStatus.SUCCEEDED),
        consequence={"ticket_id": "abc"},
    )
    assert receipt.state == "processed"
    assert receipt.consequence_json == {"ticket_id": "abc"}
    assert receipt.processed_at is not None


# ── Checkpoints: optimistic version ─────────────────────────────────────────


def _checkpoint(db: Session, binding: CapabilityBinding) -> PollingCheckpoint:
    checkpoint = PollingCheckpoint(
        id=uuid.uuid4(),
        capability_binding_id=binding.id,
        job_key="live_tail",
        version=1,
        cursor_json={"since": "2026-01-01"},
    )
    db.add(checkpoint)
    db.flush()
    return checkpoint


def test_advancing_a_checkpoint_bumps_its_version(
    db: Session, installation: ConnectorInstallation, binding: CapabilityBinding
) -> None:
    checkpoint = _checkpoint(db, binding)
    advance_checkpoint(
        db, checkpoint=checkpoint, cursor={"since": "2026-02-01"}, expected_version=1
    )
    assert checkpoint.version == 2
    assert checkpoint.cursor_json == {"since": "2026-02-01"}
    assert checkpoint.advanced_at is not None


def test_a_stale_checkpoint_write_is_refused(
    db: Session, installation: ConnectorInstallation, binding: CapabilityBinding
) -> None:
    """A CONDITIONAL UPDATE, not a Python comparison. Comparing in memory leaves
    a window in which both workers pass the check and the slower write wins,
    losing the range between the two cursors permanently."""
    checkpoint = _checkpoint(db, binding)
    advance_checkpoint(db, checkpoint=checkpoint, cursor={}, expected_version=1)
    with pytest.raises(CheckpointConflict, match="another worker"):
        advance_checkpoint(db, checkpoint=checkpoint, cursor={}, expected_version=1)


def test_integration_scopes_are_namespaced() -> None:
    assert scope_for("delivery") == "integration.delivery"
    for bad in ("", "delivery.inbound", "Delivery Inbound"):
        with pytest.raises(ValueError):
            scope_for(bad)
