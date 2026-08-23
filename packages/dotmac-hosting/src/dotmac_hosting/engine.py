"""Pure lifecycle and drift decisions for the hosting owner."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from dotmac_hosting.contracts import HostingDrift, HostingLifecycleState


@dataclass(frozen=True, slots=True)
class LifecycleInput:
    current_state: HostingLifecycleState
    state_effective_at: datetime
    observation_kind: str | None
    observation_observed_at: datetime | None


@dataclass(frozen=True, slots=True)
class LifecycleDecision:
    next_state: HostingLifecycleState
    changed: bool
    reason_code: str
    effective_at: datetime


_CONFIRMATION_TRANSITIONS = {
    (HostingLifecycleState.PROVISIONING, "active"): HostingLifecycleState.ACTIVE,
    (
        HostingLifecycleState.SUSPENSION_REQUESTED,
        "suspended",
    ): HostingLifecycleState.SUSPENDED,
    (
        HostingLifecycleState.RESTORATION_REQUESTED,
        "active",
    ): HostingLifecycleState.ACTIVE,
    (HostingLifecycleState.TERMINATING, "terminated"): HostingLifecycleState.TERMINATED,
}


def decide_lifecycle_transition(value: LifecycleInput) -> LifecycleDecision:
    if value.current_state is HostingLifecycleState.TERMINATED:
        return LifecycleDecision(
            value.current_state, False, "terminal_state", value.state_effective_at
        )
    if value.observation_kind is None or value.observation_observed_at is None:
        return LifecycleDecision(
            value.current_state, False, "no_observation", value.state_effective_at
        )
    if value.observation_observed_at < value.state_effective_at:
        return LifecycleDecision(
            value.current_state, False, "stale_observation", value.state_effective_at
        )
    next_state = _CONFIRMATION_TRANSITIONS.get(
        (value.current_state, value.observation_kind)
    )
    if next_state is None:
        return LifecycleDecision(
            value.current_state,
            False,
            "observation_does_not_authorize_transition",
            value.state_effective_at,
        )
    return LifecycleDecision(
        next_state,
        next_state is not value.current_state,
        f"confirmed_by:{value.observation_kind}",
        value.observation_observed_at,
    )


def derive_hosting_drift(
    *,
    desired_account_state: str,
    desired_package_ref: str,
    observed_account_state: str | None,
    observed_package_ref: str | None,
) -> HostingDrift:
    reasons: list[str] = []
    account_state_disagrees = (
        observed_account_state is not None
        and desired_account_state != observed_account_state
    )
    if account_state_disagrees:
        reasons.append("account_state_disagrees")
    package_disagrees = (
        observed_package_ref is not None
        and desired_package_ref != observed_package_ref
    )
    if package_disagrees:
        reasons.append("package_disagrees")
    return HostingDrift(
        desired_account_state=desired_account_state,
        observed_account_state=observed_account_state,
        desired_package_ref=desired_package_ref,
        observed_package_ref=observed_package_ref,
        account_state_disagrees=account_state_disagrees,
        package_disagrees=package_disagrees,
        reasons=tuple(reasons),
    )


__all__ = [
    "LifecycleDecision",
    "LifecycleInput",
    "decide_lifecycle_transition",
    "derive_hosting_drift",
]
