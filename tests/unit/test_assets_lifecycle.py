"""Behavior canaries for the product-neutral durable-asset lifecycle."""

from __future__ import annotations

from uuid import uuid4

import pytest
from dotmac_assets import (
    AssetState,
    DisposalStatus,
    InvalidTransition,
    MaintenanceStatus,
    SeparationOfDutiesViolation,
    StaleState,
    transition_asset,
    transition_disposal,
    transition_maintenance,
)


def test_disposed_assets_are_terminal() -> None:
    with pytest.raises(InvalidTransition, match="terminal"):
        transition_asset(
            AssetState.DISPOSED,
            AssetState.IN_SERVICE,
            expected=AssetState.DISPOSED,
        )


def test_an_asset_transition_refuses_stale_state() -> None:
    with pytest.raises(StaleState, match="expected registered"):
        transition_asset(
            AssetState.IN_SERVICE,
            AssetState.OUT_OF_SERVICE,
            expected=AssetState.REGISTERED,
        )


def test_maintenance_must_start_before_it_can_complete() -> None:
    with pytest.raises(InvalidTransition):
        transition_maintenance(
            MaintenanceStatus.SCHEDULED,
            MaintenanceStatus.COMPLETED,
            expected=MaintenanceStatus.SCHEDULED,
        )


def test_completed_maintenance_is_terminal() -> None:
    with pytest.raises(InvalidTransition, match="terminal"):
        transition_maintenance(
            MaintenanceStatus.COMPLETED,
            MaintenanceStatus.IN_PROGRESS,
            expected=MaintenanceStatus.COMPLETED,
        )


def test_a_disposal_creator_cannot_approve_their_own_request() -> None:
    actor_id = uuid4()
    with pytest.raises(SeparationOfDutiesViolation):
        transition_disposal(
            DisposalStatus.REQUESTED,
            DisposalStatus.APPROVED,
            expected=DisposalStatus.REQUESTED,
            requested_by_id=actor_id,
            actor_id=actor_id,
        )


def test_disposal_must_be_approved_before_completion() -> None:
    with pytest.raises(InvalidTransition):
        transition_disposal(
            DisposalStatus.REQUESTED,
            DisposalStatus.COMPLETED,
            expected=DisposalStatus.REQUESTED,
            requested_by_id=uuid4(),
            actor_id=uuid4(),
        )
