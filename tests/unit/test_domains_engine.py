from __future__ import annotations

from datetime import UTC, datetime, timedelta

from dotmac_domains.contracts import DomainLifecycleState
from dotmac_domains.engine import (
    LifecycleInput,
    decide_lifecycle_transition,
    derive_drift,
)

NOW = datetime(2026, 8, 19, 10, 0, tzinfo=UTC)


def test_callback_is_evidence_until_the_owner_reconciles_it() -> None:
    decision = decide_lifecycle_transition(
        LifecycleInput(
            current_state=DomainLifecycleState.REGISTRATION_REQUESTED,
            state_effective_at=NOW,
            observation_kind="registered",
            observation_observed_at=NOW + timedelta(seconds=1),
            registrar_expires_at=NOW + timedelta(days=365),
            reconciled_at=NOW + timedelta(seconds=2),
        )
    )
    assert decision.next_state is DomainLifecycleState.ACTIVE
    assert decision.changed


def test_out_of_order_expiry_cannot_regress_a_newer_confirmation() -> None:
    decision = decide_lifecycle_transition(
        LifecycleInput(
            current_state=DomainLifecycleState.ACTIVE,
            state_effective_at=NOW,
            observation_kind="expiry_observed",
            observation_observed_at=NOW - timedelta(seconds=1),
            registrar_expires_at=NOW - timedelta(days=1),
            reconciled_at=NOW + timedelta(seconds=2),
        )
    )
    assert decision.next_state is DomainLifecycleState.ACTIVE
    assert not decision.changed
    assert decision.reason_code == "stale_observation"


def test_paid_renewal_is_only_requested_until_registry_confirmation() -> None:
    no_observation = decide_lifecycle_transition(
        LifecycleInput(
            current_state=DomainLifecycleState.RENEWAL_REQUESTED,
            state_effective_at=NOW,
            observation_kind=None,
            observation_observed_at=None,
            registrar_expires_at=None,
            reconciled_at=NOW,
        )
    )
    assert no_observation.next_state is DomainLifecycleState.RENEWAL_REQUESTED

    confirmed = decide_lifecycle_transition(
        LifecycleInput(
            current_state=DomainLifecycleState.RENEWAL_REQUESTED,
            state_effective_at=NOW,
            observation_kind="renewed",
            observation_observed_at=NOW + timedelta(seconds=1),
            registrar_expires_at=NOW + timedelta(days=365),
            reconciled_at=NOW + timedelta(seconds=2),
        )
    )
    assert confirmed.next_state is DomainLifecycleState.ACTIVE


def test_commercial_and_registry_dates_are_compared_not_repaired() -> None:
    commercial = NOW + timedelta(days=350)
    registrar = NOW + timedelta(days=365)
    drift = derive_drift(
        commercial_renewal_at=commercial,
        registrar_expires_at=registrar,
        desired_nameservers=("ns1.dotmac.ng", "ns2.dotmac.ng"),
        observed_nameservers=("ns1.dotmac.ng", "ns3.dotmac.ng"),
        desired_contact_digest="a" * 64,
        observed_contact_digest="b" * 64,
    )
    assert drift.commercial_renewal_at == commercial
    assert drift.registrar_expires_at == registrar
    assert drift.expiry_disagrees
    assert drift.nameservers_disagree
    assert drift.contacts_disagree
