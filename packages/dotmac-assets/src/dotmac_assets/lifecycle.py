"""Pure expected-state transition guards for durable assets."""

from __future__ import annotations

from collections.abc import Mapping
from typing import TypeVar
from uuid import UUID

from dotmac_assets.contracts import (
    AssetState,
    AssignmentStatus,
    DisposalStatus,
    MaintenanceStatus,
)


class AssetLifecycleError(ValueError):
    """Base for deterministic asset lifecycle refusals."""


class StaleState(AssetLifecycleError):
    """The aggregate no longer has the state the caller reviewed."""


class InvalidTransition(AssetLifecycleError):
    """The requested lifecycle edge is not permitted."""


class SeparationOfDutiesViolation(AssetLifecycleError):
    """The disposal requester attempted to approve their own request."""


_ASSET_TRANSITIONS: Mapping[AssetState, frozenset[AssetState]] = {
    AssetState.REGISTERED: frozenset(
        {AssetState.IN_SERVICE, AssetState.OUT_OF_SERVICE, AssetState.RETIRED}
    ),
    AssetState.IN_SERVICE: frozenset({AssetState.OUT_OF_SERVICE, AssetState.RETIRED}),
    AssetState.OUT_OF_SERVICE: frozenset({AssetState.IN_SERVICE, AssetState.RETIRED}),
    AssetState.RETIRED: frozenset({AssetState.DISPOSED}),
    AssetState.DISPOSED: frozenset(),
}

_ASSIGNMENT_TRANSITIONS: Mapping[AssignmentStatus, frozenset[AssignmentStatus]] = {
    AssignmentStatus.ACTIVE: frozenset(
        {
            AssignmentStatus.RETURNED,
            AssignmentStatus.TRANSFERRED,
            AssignmentStatus.LOST,
        }
    ),
    AssignmentStatus.RETURNED: frozenset(),
    AssignmentStatus.TRANSFERRED: frozenset(),
    AssignmentStatus.LOST: frozenset(),
}

_MAINTENANCE_TRANSITIONS: Mapping[MaintenanceStatus, frozenset[MaintenanceStatus]] = {
    MaintenanceStatus.SCHEDULED: frozenset(
        {MaintenanceStatus.IN_PROGRESS, MaintenanceStatus.CANCELLED}
    ),
    MaintenanceStatus.IN_PROGRESS: frozenset(
        {MaintenanceStatus.COMPLETED, MaintenanceStatus.CANCELLED}
    ),
    MaintenanceStatus.COMPLETED: frozenset(),
    MaintenanceStatus.CANCELLED: frozenset(),
}

_DISPOSAL_TRANSITIONS: Mapping[DisposalStatus, frozenset[DisposalStatus]] = {
    DisposalStatus.REQUESTED: frozenset(
        {DisposalStatus.APPROVED, DisposalStatus.CANCELLED}
    ),
    DisposalStatus.APPROVED: frozenset(
        {DisposalStatus.COMPLETED, DisposalStatus.CANCELLED}
    ),
    DisposalStatus.COMPLETED: frozenset(),
    DisposalStatus.CANCELLED: frozenset(),
}


StateT = TypeVar("StateT")


def _transition(
    current: StateT,
    requested: StateT,
    *,
    expected: StateT,
    allowed: Mapping[StateT, frozenset[StateT]],
    label: str,
) -> StateT:
    if current != expected:
        raise StaleState(
            f"{label} expected {getattr(expected, 'value', expected)}, "
            f"found {getattr(current, 'value', current)}"
        )
    targets = allowed[current]
    if requested not in targets:
        suffix = "terminal" if not targets else "invalid"
        raise InvalidTransition(
            f"{label} transition {getattr(current, 'value', current)} -> "
            f"{getattr(requested, 'value', requested)} is {suffix}"
        )
    return requested


def transition_asset(
    current: AssetState,
    requested: AssetState,
    *,
    expected: AssetState,
) -> AssetState:
    return _transition(
        current,
        requested,
        expected=expected,
        allowed=_ASSET_TRANSITIONS,
        label="asset state",
    )


def transition_assignment(
    current: AssignmentStatus,
    requested: AssignmentStatus,
    *,
    expected: AssignmentStatus,
) -> AssignmentStatus:
    return _transition(
        current,
        requested,
        expected=expected,
        allowed=_ASSIGNMENT_TRANSITIONS,
        label="assignment state",
    )


def transition_maintenance(
    current: MaintenanceStatus,
    requested: MaintenanceStatus,
    *,
    expected: MaintenanceStatus,
) -> MaintenanceStatus:
    return _transition(
        current,
        requested,
        expected=expected,
        allowed=_MAINTENANCE_TRANSITIONS,
        label="maintenance state",
    )


def transition_disposal(
    current: DisposalStatus,
    requested: DisposalStatus,
    *,
    expected: DisposalStatus,
    requested_by_id: UUID,
    actor_id: UUID,
) -> DisposalStatus:
    if requested is DisposalStatus.APPROVED and requested_by_id == actor_id:
        raise SeparationOfDutiesViolation(
            "a disposal requester cannot approve the same request"
        )
    return _transition(
        current,
        requested,
        expected=expected,
        allowed=_DISPOSAL_TRANSITIONS,
        label="disposal state",
    )


__all__ = [
    "AssetLifecycleError",
    "InvalidTransition",
    "SeparationOfDutiesViolation",
    "StaleState",
    "transition_asset",
    "transition_assignment",
    "transition_disposal",
    "transition_maintenance",
]
