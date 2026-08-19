"""Product-first canaries for publication lifecycle and typed handoff values.

These tests precede ``dotmac-publishing``. The first Observer execution must be
RED because the package does not exist; Gate 2 makes the same contract green.
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import UTC, datetime
from importlib import import_module
from types import ModuleType

import pytest


def _module(name: str) -> ModuleType:
    try:
        return import_module(f"dotmac_publishing.{name}")
    except ModuleNotFoundError as exc:
        if not (exc.name or "").startswith("dotmac_publishing"):
            raise
        pytest.fail(
            "dotmac-publishing is intentionally absent: this is the Gate 1 "
            "RED canary; implement it only after the RED run is recorded"
        )


def test_publication_and_delivery_vocabularies_are_exact() -> None:
    lifecycle = _module("lifecycle")
    assert [state.value for state in lifecycle.PublicationState] == [
        "scheduled",
        "dispatching",
        "partial",
        "published",
        "failed",
        "cancelled",
    ]
    assert [state.value for state in lifecycle.DeliveryState] == [
        "pending",
        "intent_published",
        "accepted",
        "published",
        "failed",
        "cancelled",
    ]


def test_pending_targets_derive_scheduled_and_inflight_targets_dispatching() -> None:
    lifecycle = _module("lifecycle")
    assert lifecycle.derive_publication_state(
        (lifecycle.DeliveryState.PENDING, lifecycle.DeliveryState.PENDING)
    ) is lifecycle.PublicationState.SCHEDULED
    assert lifecycle.derive_publication_state(
        (lifecycle.DeliveryState.PUBLISHED, lifecycle.DeliveryState.ACCEPTED)
    ) is lifecycle.PublicationState.DISPATCHING


def test_all_success_partial_success_and_all_failure_are_distinct() -> None:
    lifecycle = _module("lifecycle")
    assert lifecycle.derive_publication_state(
        (lifecycle.DeliveryState.PUBLISHED, lifecycle.DeliveryState.PUBLISHED)
    ) is lifecycle.PublicationState.PUBLISHED
    assert lifecycle.derive_publication_state(
        (lifecycle.DeliveryState.PUBLISHED, lifecycle.DeliveryState.FAILED)
    ) is lifecycle.PublicationState.PARTIAL
    assert lifecycle.derive_publication_state(
        (lifecycle.DeliveryState.FAILED, lifecycle.DeliveryState.FAILED)
    ) is lifecycle.PublicationState.FAILED


def test_all_cancelled_is_not_misreported_as_failed() -> None:
    lifecycle = _module("lifecycle")
    assert lifecycle.derive_publication_state(
        (lifecycle.DeliveryState.CANCELLED, lifecycle.DeliveryState.CANCELLED)
    ) is lifecycle.PublicationState.CANCELLED


def test_a_publication_requires_at_least_one_target_state() -> None:
    lifecycle = _module("lifecycle")
    with pytest.raises(ValueError, match="delivery"):
        lifecycle.derive_publication_state(())


def test_failed_delivery_can_retry_but_published_and_cancelled_are_terminal() -> None:
    lifecycle = _module("lifecycle")
    lifecycle.check_delivery_transition(
        lifecycle.DeliveryState.FAILED,
        lifecycle.DeliveryState.INTENT_PUBLISHED,
    )
    for terminal in (
        lifecycle.DeliveryState.PUBLISHED,
        lifecycle.DeliveryState.CANCELLED,
    ):
        lifecycle.check_delivery_transition(terminal, terminal)
        with pytest.raises(lifecycle.TransitionError, match="terminal"):
            lifecycle.check_delivery_transition(
                terminal, lifecycle.DeliveryState.INTENT_PUBLISHED
            )


def test_delivery_cannot_claim_success_before_an_intent_exists() -> None:
    lifecycle = _module("lifecycle")
    with pytest.raises(lifecycle.TransitionError, match="intent_published"):
        lifecycle.check_delivery_transition(
            lifecycle.DeliveryState.PENDING,
            lifecycle.DeliveryState.PUBLISHED,
        )


def _snapshot(contracts: ModuleType):
    return contracts.PublicationSnapshotV1(
        source_ref="content:item:01",
        title="August launch",
        body="One immutable body",
        variant_key=None,
        creative_refs=("file:hero", "file:terms"),
    )


def test_snapshot_is_immutable_and_digest_is_canonical() -> None:
    contracts = _module("contracts")
    first = _snapshot(contracts)
    second = _snapshot(contracts)
    assert first.digest == second.digest
    assert len(first.digest) == 64
    with pytest.raises(FrozenInstanceError):
        first.body = "mutated"


def test_request_requires_aware_time_and_unique_nonempty_targets() -> None:
    contracts = _module("contracts")
    target = contracts.PublicationTarget(target_ref="binding:primary")
    with pytest.raises(contracts.ContractError, match="timezone-aware"):
        contracts.RequestPublication(
            request_key="launch-01",
            requested_for=datetime(2026, 8, 19, 9, 0),
            snapshot=_snapshot(contracts),
            targets=(target,),
        )
    with pytest.raises(contracts.ContractError, match="target"):
        contracts.RequestPublication(
            request_key="launch-01",
            requested_for=datetime(2026, 8, 19, 9, 0, tzinfo=UTC),
            snapshot=_snapshot(contracts),
            targets=(),
        )
    with pytest.raises(contracts.ContractError, match="duplicate"):
        contracts.RequestPublication(
            request_key="launch-01",
            requested_for=datetime(2026, 8, 19, 9, 0, tzinfo=UTC),
            snapshot=_snapshot(contracts),
            targets=(target, target),
        )


def test_request_fingerprint_changes_with_snapshot_or_target() -> None:
    contracts = _module("contracts")
    requested_for = datetime(2026, 8, 19, 9, 0, tzinfo=UTC)
    first = contracts.RequestPublication(
        request_key="launch-01",
        requested_for=requested_for,
        snapshot=_snapshot(contracts),
        targets=(contracts.PublicationTarget(target_ref="binding:primary"),),
    )
    replay = contracts.RequestPublication(
        request_key="launch-01",
        requested_for=requested_for,
        snapshot=_snapshot(contracts),
        targets=(contracts.PublicationTarget(target_ref="binding:primary"),),
    )
    changed = contracts.RequestPublication(
        request_key="launch-01",
        requested_for=requested_for,
        snapshot=_snapshot(contracts),
        targets=(contracts.PublicationTarget(target_ref="binding:secondary"),),
    )
    assert first.fingerprint == replay.fingerprint
    assert first.fingerprint != changed.fingerprint


def test_published_observation_requires_an_opaque_remote_reference() -> None:
    contracts = _module("contracts")
    with pytest.raises(contracts.ContractError, match="remote_ref"):
        contracts.DeliveryObservationV1(
            receipt_ref="receipt:01",
            attempt_ref="attempt:01",
            outcome=contracts.DeliveryOutcome.PUBLISHED,
            observed_at=datetime(2026, 8, 19, 9, 1, tzinfo=UTC),
            remote_ref=None,
            error_detail=None,
        )


def test_contract_surface_contains_no_provider_vocabulary() -> None:
    contracts = _module("contracts")
    forbidden = ("Provider", "Channel", "Credential", "Adapter")
    assert not [
        name
        for name in contracts.__all__
        if any(token in name for token in forbidden)
    ]
