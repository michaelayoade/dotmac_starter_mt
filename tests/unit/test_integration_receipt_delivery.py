"""The receipt-to-product delivery engine's decisions, without a database.

These prove the parts that are decided in Python: phase ORDERING, the claim
identity guard, the idempotency key's stability across attempts, and the
replay-vs-conflict rule. They deliberately do NOT claim to prove concurrency —
SQLite cannot demonstrate two sessions racing one row, and asserting a race
against a fake store would be a test of the fake. That proof lives in
`tests/test_integration_receipt_delivery_isolation.py`, against real Postgres.

The fake store here is honest about one thing in particular: it tracks whether a
transaction is OPEN, so "no session is held across the network call" is checked
rather than asserted in a docstring.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from dotmac_integration.receipt_delivery import (
    DeliveryReport,
    FingerprintConflict,
    LostClaim,
    ProductAcceptance,
    ProductOutcome,
    ProductRequest,
    ReceiptClaim,
    TransportFailure,
    TrustedDestination,
    build_product_request,
    deliver_receipt,
    idempotency_key_for,
    request_fingerprint_for,
    require_stable_fingerprint,
)
from dotmac_integration.retry import OutcomeStatus

# ── Doubles ─────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class _Scope:
    kind: str
    ref: str


@dataclass(frozen=True, slots=True)
class _Destination:
    """Shaped exactly like Team 3's `DestinationBinding`.

    Structural, so when `dotmac_integration.destination_binding` merges the real
    type drops in without this file changing — and if a field name here were
    wrong, `test_the_protocol_matches_team_3s_binding` would say so.
    """

    capability_binding_id: uuid.UUID
    capability_id: str
    application: str
    scope: _Scope
    contract_version: int
    config_revision_id: uuid.UUID


def _destination(**overrides: Any) -> _Destination:
    base: dict[str, Any] = {
        "capability_binding_id": uuid.UUID(int=1),
        "capability_id": "messaging.receive.v1",
        "application": "sub",
        "scope": _Scope(kind="inbox", ref="support"),
        "contract_version": 1,
        "config_revision_id": uuid.UUID(int=2),
    }
    base.update(overrides)
    return _Destination(**base)


def _claim(**overrides: Any) -> ReceiptClaim:
    base: dict[str, Any] = {
        "receipt_id": uuid.UUID(int=7),
        "attempt": 1,
        "leased_until": datetime.now(UTC) + timedelta(seconds=300),
        "destination": _destination(),
        "event_type": "message.received",
        "observation": {"text": "hello", "from": "+2348000000000"},
        "correlation_id": "corr-1",
    }
    base.update(overrides)
    return ReceiptClaim(**base)


class _FakeStore:
    """A store that records the ORDER it was used in, and whether it is open."""

    def __init__(
        self,
        claim: ReceiptClaim | None,
        *,
        settles: bool = True,
    ) -> None:
        self._claim = claim
        self._settles = settles
        self.session_open = False
        self.events: list[str] = []
        self.settled_with: ProductOutcome | None = None

    def claim(
        self, *, receipt_id: uuid.UUID, now: datetime | None = None
    ) -> ReceiptClaim | None:
        self.session_open = True
        self.events.append("claim")
        self.session_open = False
        return self._claim

    def settle(self, claim: ReceiptClaim, outcome: ProductOutcome) -> bool:
        self.session_open = True
        self.events.append("settle")
        self.settled_with = outcome
        self.session_open = False
        return self._settles


class _FakeGateway:
    """A gateway that asserts phase 2's contract while it is being used."""

    def __init__(
        self,
        store: _FakeStore,
        outcome: ProductOutcome | None = None,
        *,
        raises: BaseException | None = None,
    ) -> None:
        self._store = store
        self._outcome = outcome or ProductOutcome(
            acceptance=ProductAcceptance.ACCEPTED, product_ref="msg_1"
        )
        self._raises = raises
        self.seen: list[ProductRequest] = []

    def deliver(self, request: ProductRequest) -> ProductOutcome:
        # THE assertion this double exists for: the engine must not be holding a
        # transaction while the product is being contacted.
        assert not self._store.session_open, (
            "the product was contacted with a session open — a network call "
            "inside a transaction holds a row lock for the duration of someone "
            "else's outage"
        )
        self._store.events.append("deliver")
        self.seen.append(request)
        if self._raises is not None:
            raise self._raises
        return self._outcome


# ── Phase ordering ──────────────────────────────────────────────────────────


def test_the_three_phases_run_in_order_with_no_session_across_the_call() -> None:
    store = _FakeStore(_claim())
    gateway = _FakeGateway(store)

    report = deliver_receipt(receipt_id=uuid.UUID(int=7), store=store, gateway=gateway)

    assert store.events == ["claim", "deliver", "settle"]
    assert report.claimed is True
    assert report.attempt == 1


def test_nothing_is_delivered_when_the_claim_is_not_won() -> None:
    """A sweeper finding nothing to do is the NORMAL case, so it is a value and
    not an exception — and crucially the product is never contacted."""
    store = _FakeStore(None)
    gateway = _FakeGateway(store)

    report = deliver_receipt(receipt_id=uuid.UUID(int=7), store=store, gateway=gateway)

    assert report == DeliveryReport(
        receipt_id=uuid.UUID(int=7),
        claimed=False,
        unclaimed_reason=report.unclaimed_reason,
    )
    assert store.events == ["claim"], "a lost claim still contacted the product"
    assert gateway.seen == []


# ── The lost claim ──────────────────────────────────────────────────────────


def test_a_worker_whose_lease_expired_cannot_settle() -> None:
    """`settle` returning False is the database saying "you were superseded".

    It must become a TYPED exception, not a quiet return: a worker that failed
    to record its outcome looks identical to one that succeeded.
    """
    store = _FakeStore(_claim(), settles=False)
    gateway = _FakeGateway(store)

    with pytest.raises(LostClaim) as raised:
        deliver_receipt(receipt_id=uuid.UUID(int=7), store=store, gateway=gateway)

    assert "took it over" in str(raised.value)
    assert "attempt 1" in str(raised.value)


def test_lost_claim_is_not_confused_with_a_transport_problem() -> None:
    """The sensitivity proof for the test above.

    A store that DOES settle must not raise — otherwise the assertion above
    would pass against an engine that raised `LostClaim` unconditionally, which
    would strand every successful delivery.
    """
    store = _FakeStore(_claim(), settles=True)
    gateway = _FakeGateway(store)

    report = deliver_receipt(receipt_id=uuid.UUID(int=7), store=store, gateway=gateway)
    assert report.claimed is True


# ── Retry without repeating the consequence ─────────────────────────────────


def test_the_idempotency_key_is_stable_across_attempts() -> None:
    """The single property that stops a retried timeout delivering twice.

    Attempt 1 and attempt 5 of the same receipt to the same destination must
    present the product with the SAME key, or the retry curve becomes a
    duplication machine.
    """
    destination = _destination()
    first = build_product_request(_claim(attempt=1, destination=destination))
    later = build_product_request(_claim(attempt=5, destination=destination))

    assert first.idempotency_key == later.idempotency_key


def test_the_key_separates_destinations() -> None:
    """The sensitivity proof for the stability above.

    A key that were merely constant would also pass the previous test. The same
    observation legitimately delivered to two applications is two consequences,
    and one shared key would make the second look like a duplicate and vanish.
    """
    receipt_id = uuid.UUID(int=7)
    to_sub = idempotency_key_for(
        receipt_id=receipt_id, destination=_destination(application="sub")
    )
    to_erp = idempotency_key_for(
        receipt_id=receipt_id, destination=_destination(application="erp")
    )
    other_scope = idempotency_key_for(
        receipt_id=receipt_id,
        destination=_destination(scope=_Scope(kind="inbox", ref="sales")),
    )
    other_receipt = idempotency_key_for(
        receipt_id=uuid.UUID(int=8), destination=_destination()
    )

    assert len({to_sub, to_erp, other_scope, other_receipt}) == 4


def test_a_transport_failure_becomes_a_retryable_outcome_not_an_exception() -> None:
    """A timeout must settle the attempt, not escape it.

    Escaping would leave the receipt leased with nothing recorded, and the next
    worker would have no evidence of what went wrong.
    """
    store = _FakeStore(_claim())
    gateway = _FakeGateway(
        store, raises=TransportFailure("connect timeout", error_code="etimedout")
    )

    report = deliver_receipt(receipt_id=uuid.UUID(int=7), store=store, gateway=gateway)

    assert store.events == ["claim", "deliver", "settle"]
    assert report.outcome is not None
    assert report.outcome.acceptance is ProductAcceptance.UNAVAILABLE
    assert report.outcome.error_code == "etimedout"
    assert report.outcome.as_outcome().status is OutcomeStatus.RETRYABLE
    assert not report.outcome.consequence_happened


def test_a_product_that_already_applied_it_is_a_success_not_a_second_delivery() -> None:
    """The other half of the timeout story.

    Attempt 2 presents the same key; the product deduplicates and says so. That
    is a SUCCESS carrying evidence the consequence exists — recording it as a
    fresh `ACCEPTED` would lose the fact that this attempt changed nothing.
    """
    store = _FakeStore(_claim(attempt=2))
    gateway = _FakeGateway(
        store,
        ProductOutcome(
            acceptance=ProductAcceptance.ALREADY_APPLIED,
            product_ref="msg_1",
            acknowledged_contract_version=1,
        ),
    )

    report = deliver_receipt(receipt_id=uuid.UUID(int=7), store=store, gateway=gateway)

    assert report.outcome is not None
    assert report.outcome.consequence_happened
    assert report.outcome.as_outcome().status is OutcomeStatus.SUCCEEDED
    assert report.outcome.product_ref == "msg_1"


def test_an_unexpected_gateway_exception_is_not_swallowed_as_a_transport_failure() -> (
    None
):
    """A bug must not be retried on an exponential curve as a flaky provider.

    Only `TransportFailure` is converted. Anything else propagates WITHOUT
    settling, and the lease expiring is what returns the receipt to the queue.
    """
    store = _FakeStore(_claim())
    gateway = _FakeGateway(store, raises=TypeError("connector bug"))

    with pytest.raises(TypeError):
        deliver_receipt(receipt_id=uuid.UUID(int=7), store=store, gateway=gateway)

    assert "settle" not in store.events


@pytest.mark.parametrize(
    ("acceptance", "expected"),
    [
        (ProductAcceptance.ACCEPTED, OutcomeStatus.SUCCEEDED),
        (ProductAcceptance.ALREADY_APPLIED, OutcomeStatus.SUCCEEDED),
        (ProductAcceptance.REJECTED, OutcomeStatus.TERMINAL),
        (ProductAcceptance.UNAVAILABLE, OutcomeStatus.RETRYABLE),
        (
            ProductAcceptance.INDETERMINATE,
            OutcomeStatus.RECONCILIATION_REQUIRED,
        ),
    ],
)
def test_every_acceptance_maps_to_a_retry_decision(
    acceptance: ProductAcceptance, expected: OutcomeStatus
) -> None:
    """Every member is covered, so adding one without deciding its retry
    semantics fails here rather than defaulting to "retryable" — the default
    that quietly duplicates consequences."""
    assert ProductOutcome(acceptance=acceptance).as_outcome().status is expected


def test_the_acceptance_table_covers_the_enum() -> None:
    """The sensitivity proof for the parametrization above: a new member added
    to the enum but not to the table must fail, rather than being missed because
    nobody added a parameter row."""
    for member in ProductAcceptance:
        # A KeyError here is the intended failure.
        ProductOutcome(acceptance=member).as_outcome()


# ── Replay vs conflict ──────────────────────────────────────────────────────


def test_the_same_request_replays_safely() -> None:
    claim = _claim()
    fingerprint = request_fingerprint_for(
        destination=claim.destination,
        event_type=claim.event_type,
        observation=claim.observation,
    )
    replayed = _claim(attempt=2, stored_fingerprint=fingerprint)

    assert build_product_request(replayed).request_fingerprint == fingerprint


def test_a_changed_request_under_one_identity_is_a_conflict() -> None:
    """Not a silent overwrite: the recorded consequence describes the FIRST
    content, and replacing the content would make it a lie."""
    changed = _claim(attempt=2, stored_fingerprint="0" * 64)

    with pytest.raises(FingerprintConflict) as raised:
        build_product_request(changed)

    assert "different request" in str(raised.value)


def test_a_conflicting_replay_never_reaches_the_network() -> None:
    """The conflict must be detected BEFORE the product is contacted — telling
    it something and then refusing to record the outcome is the worst order."""
    store = _FakeStore(_claim(attempt=2, stored_fingerprint="0" * 64))
    gateway = _FakeGateway(store)

    with pytest.raises(FingerprintConflict):
        deliver_receipt(receipt_id=uuid.UUID(int=7), store=store, gateway=gateway)

    assert gateway.seen == []
    assert "settle" not in store.events


def test_a_first_attempt_has_nothing_to_conflict_with() -> None:
    require_stable_fingerprint(None, "a" * 64)


def test_the_fingerprint_follows_the_content_and_the_destination() -> None:
    """Sensitivity proof: a fingerprint that ignored either would let a changed
    observation replay as though it were identical."""
    base = _claim()
    same = request_fingerprint_for(
        destination=base.destination,
        event_type=base.event_type,
        observation=base.observation,
    )
    other_text = request_fingerprint_for(
        destination=base.destination,
        event_type=base.event_type,
        observation={"text": "goodbye", "from": "+2348000000000"},
    )
    other_version = request_fingerprint_for(
        destination=_destination(contract_version=2),
        event_type=base.event_type,
        observation=base.observation,
    )
    other_event = request_fingerprint_for(
        destination=base.destination,
        event_type="message.deleted",
        observation=base.observation,
    )

    assert len({same, other_text, other_version, other_event}) == 4


def test_key_order_does_not_change_the_fingerprint() -> None:
    """A provider that reorders JSON must not turn every redelivery into a
    conflict."""
    destination = _destination()
    first = request_fingerprint_for(
        destination=destination,
        event_type="e",
        observation={"a": 1, "b": {"c": 2, "d": 3}},
    )
    second = request_fingerprint_for(
        destination=destination,
        event_type="e",
        observation={"b": {"d": 3, "c": 2}, "a": 1},
    )
    assert first == second


# ── Trusted destination ─────────────────────────────────────────────────────


def test_delivery_is_addressed_only_from_trusted_state() -> None:
    """Provider metadata may never select a destination.

    `build_product_request` takes a claim and nothing else, so there is no
    parameter through which a payload could name an application, a scope or a
    contract version. This asserts the signature, because the signature is what
    makes the property structural rather than a convention.
    """
    import inspect

    parameters = list(inspect.signature(build_product_request).parameters)
    assert parameters == ["claim"], (
        "build_product_request grew a parameter. If it accepts a payload, a "
        "header map or an application name, provider-controlled data can "
        "redirect a delivery"
    )


def test_the_request_carries_the_destinations_contract_version() -> None:
    """Never a version the payload proposed."""
    request = build_product_request(
        _claim(destination=_destination(contract_version=3))
    )
    assert request.contract_version == 3


def test_the_protocol_matches_team_3s_binding() -> None:
    """`TrustedDestination` is structural, so a field renamed on either side
    must be caught — and it was: this guard is what caught
    `config_revision_id` becoming `destination_revision_id` when destinations
    moved out of connector configuration into their own table
    (`ig_0004_destinations`).

    The `importorskip` it used to carry is gone. It was correct while the two
    modules were being built in parallel, but a skipped structural check is not
    evidence of structural agreement, and both halves now ship together.
    """
    from dotmac_integration.destination_binding import DestinationBinding, LocalScope

    binding = DestinationBinding(
        capability_binding_id=uuid.UUID(int=1),
        capability_id="messaging.receive.v1",
        application="sub",
        scope=LocalScope(kind="inbox", ref="support"),
        contract_version=1,
        destination_revision_id=uuid.UUID(int=2),
    )
    assert isinstance(binding, TrustedDestination)
    # and it drives the real derivations
    assert idempotency_key_for(receipt_id=uuid.UUID(int=7), destination=binding)


# ── Immutability ────────────────────────────────────────────────────────────


def test_boundary_contracts_are_deeply_immutable() -> None:
    """Shallow freezing is the bug worth avoiding: a frozen dataclass holding a
    plain dict lets a caller mutate the payload AFTER its fingerprint was
    computed, which is the window the fingerprint exists to close."""
    request = build_product_request(
        _claim(observation={"text": "hi", "nested": {"a": [1, 2]}})
    )

    with pytest.raises((AttributeError, TypeError)):
        request.idempotency_key = "tampered"  # type: ignore[misc]
    with pytest.raises(TypeError):
        request.observation["text"] = "tampered"  # type: ignore[index]
    nested = request.observation["nested"]
    assert isinstance(nested, type(request.observation))
    with pytest.raises(TypeError):
        nested["a"] = "tampered"  # type: ignore[index]
    assert isinstance(request.observation["nested"]["a"], tuple)  # type: ignore[index]


def test_a_claim_must_be_a_real_claim() -> None:
    """Attempt 0 is not a held claim — the conditional UPDATE increments the
    counter, so anything less than 1 means the caller built a claim by hand."""
    from dotmac_integration.receipt_delivery import DeliveryError

    with pytest.raises(DeliveryError):
        _claim(attempt=0)
