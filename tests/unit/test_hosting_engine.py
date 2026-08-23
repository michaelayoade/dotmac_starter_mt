from __future__ import annotations

from datetime import UTC, datetime, timedelta

from dotmac_hosting.contracts import HostingLifecycleState
from dotmac_hosting.engine import (
    LifecycleInput,
    decide_lifecycle_transition,
    derive_hosting_drift,
)

NOW = datetime(2026, 8, 19, 10, 0, tzinfo=UTC)


def test_provider_fact_changes_nothing_until_owner_reconciliation() -> None:
    decision = decide_lifecycle_transition(
        LifecycleInput(
            current_state=HostingLifecycleState.PROVISIONING,
            state_effective_at=NOW,
            observation_kind="active",
            observation_observed_at=NOW + timedelta(seconds=1),
        )
    )
    assert decision.next_state is HostingLifecycleState.ACTIVE
    assert decision.changed


def test_stale_observation_cannot_regress_a_newer_decision() -> None:
    decision = decide_lifecycle_transition(
        LifecycleInput(
            current_state=HostingLifecycleState.SUSPENDED,
            state_effective_at=NOW,
            observation_kind="active",
            observation_observed_at=NOW - timedelta(seconds=1),
        )
    )
    assert decision.next_state is HostingLifecycleState.SUSPENDED
    assert decision.reason_code == "stale_observation"


def test_suspension_and_restoration_wait_for_independent_observation() -> None:
    suspended = decide_lifecycle_transition(
        LifecycleInput(
            current_state=HostingLifecycleState.SUSPENSION_REQUESTED,
            state_effective_at=NOW,
            observation_kind="suspended",
            observation_observed_at=NOW + timedelta(seconds=1),
        )
    )
    assert suspended.next_state is HostingLifecycleState.SUSPENDED
    assert suspended.changed

    late_suspension = decide_lifecycle_transition(
        LifecycleInput(
            current_state=HostingLifecycleState.RESTORATION_REQUESTED,
            state_effective_at=NOW + timedelta(seconds=2),
            observation_kind="suspended",
            observation_observed_at=NOW + timedelta(seconds=3),
        )
    )
    assert late_suspension.next_state is HostingLifecycleState.RESTORATION_REQUESTED
    assert not late_suspension.changed

    active = decide_lifecycle_transition(
        LifecycleInput(
            current_state=HostingLifecycleState.RESTORATION_REQUESTED,
            state_effective_at=NOW + timedelta(seconds=2),
            observation_kind="active",
            observation_observed_at=NOW + timedelta(seconds=4),
        )
    )
    assert active.next_state is HostingLifecycleState.ACTIVE
    assert active.changed


def test_termination_is_only_confirmed_by_an_independent_observation() -> None:
    decision = decide_lifecycle_transition(
        LifecycleInput(
            current_state=HostingLifecycleState.TERMINATING,
            state_effective_at=NOW,
            observation_kind="terminated",
            observation_observed_at=NOW + timedelta(seconds=1),
        )
    )
    assert decision.next_state is HostingLifecycleState.TERMINATED
    assert decision.changed
    late = decide_lifecycle_transition(
        LifecycleInput(
            current_state=HostingLifecycleState.TERMINATED,
            state_effective_at=NOW,
            observation_kind="active",
            observation_observed_at=NOW + timedelta(days=1),
        )
    )
    assert late.next_state is HostingLifecycleState.TERMINATED
    assert late.reason_code == "terminal_state"


def test_drift_compares_desired_account_and_package_without_repairing() -> None:
    drift = derive_hosting_drift(
        desired_account_state="suspended",
        desired_package_ref="package:v2",
        observed_account_state="active",
        observed_package_ref="package:v1",
    )
    assert drift.account_state_disagrees
    assert drift.package_disagrees
    assert drift.reasons == ("account_state_disagrees", "package_disagrees")
