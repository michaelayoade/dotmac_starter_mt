"""Pure lifecycle and drift decisions for the domain owner."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from dotmac_domains.contracts import DomainDrift, DomainLifecycleState


@dataclass(frozen=True, slots=True)
class LifecycleInput:
    current_state: DomainLifecycleState
    state_effective_at: datetime
    observation_kind: str | None
    observation_observed_at: datetime | None
    registrar_expires_at: datetime | None
    reconciled_at: datetime


@dataclass(frozen=True, slots=True)
class LifecycleDecision:
    next_state: DomainLifecycleState
    changed: bool
    reason_code: str
    effective_at: datetime


_CONFIRMATION_TRANSITIONS: dict[
    tuple[DomainLifecycleState, str], DomainLifecycleState
] = {
    (
        DomainLifecycleState.REGISTRATION_REQUESTED,
        "registered",
    ): DomainLifecycleState.ACTIVE,
    (DomainLifecycleState.RENEWAL_REQUESTED, "renewed"): DomainLifecycleState.ACTIVE,
    (
        DomainLifecycleState.TRANSFER_IN_REQUESTED,
        "transfer_completed",
    ): DomainLifecycleState.ACTIVE,
    (
        DomainLifecycleState.TRANSFER_OUT_REQUESTED,
        "transfer_completed",
    ): DomainLifecycleState.RELEASED,
    (
        DomainLifecycleState.RELEASE_REQUESTED,
        "transfer_completed",
    ): DomainLifecycleState.RELEASED,
    (DomainLifecycleState.RELEASE_REQUESTED, "deleted"): DomainLifecycleState.RELEASED,
}


def decide_lifecycle_transition(value: LifecycleInput) -> LifecycleDecision:
    """Interpret one latest provider fact without treating it as authority."""

    if value.observation_kind is None or value.observation_observed_at is None:
        return LifecycleDecision(
            value.current_state, False, "no_observation", value.state_effective_at
        )
    if value.observation_observed_at < value.state_effective_at:
        return LifecycleDecision(
            value.current_state,
            False,
            "stale_observation",
            value.state_effective_at,
        )

    next_state = _CONFIRMATION_TRANSITIONS.get(
        (value.current_state, value.observation_kind)
    )
    if next_state is None and value.observation_kind == "redemption_observed":
        next_state = DomainLifecycleState.REDEMPTION
    if next_state is None and value.observation_kind == "expiry_observed":
        if (
            value.registrar_expires_at is not None
            and value.registrar_expires_at <= value.reconciled_at
        ):
            next_state = DomainLifecycleState.EXPIRED
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


def derive_drift(
    *,
    commercial_renewal_at: datetime | None,
    registrar_expires_at: datetime | None,
    desired_nameservers: tuple[str, ...],
    observed_nameservers: tuple[str, ...],
    desired_contact_digest: str | None,
    observed_contact_digest: str | None,
) -> DomainDrift:
    reasons: list[str] = []
    expiry_disagrees = (
        commercial_renewal_at is not None
        and registrar_expires_at is not None
        and commercial_renewal_at != registrar_expires_at
    )
    if expiry_disagrees:
        reasons.append("commercial_and_registrar_expiry_disagree")
    nameservers_disagree = bool(desired_nameservers) and tuple(
        desired_nameservers
    ) != tuple(observed_nameservers)
    if nameservers_disagree:
        reasons.append("nameservers_disagree")
    contacts_disagree = (
        desired_contact_digest is not None
        and observed_contact_digest is not None
        and desired_contact_digest != observed_contact_digest
    )
    if contacts_disagree:
        reasons.append("contacts_disagree")
    return DomainDrift(
        commercial_renewal_at=commercial_renewal_at,
        registrar_expires_at=registrar_expires_at,
        expiry_disagrees=expiry_disagrees,
        nameservers_disagree=nameservers_disagree,
        contacts_disagree=contacts_disagree,
        reasons=tuple(reasons),
    )


__all__ = [
    "LifecycleDecision",
    "LifecycleInput",
    "decide_lifecycle_transition",
    "derive_drift",
]
