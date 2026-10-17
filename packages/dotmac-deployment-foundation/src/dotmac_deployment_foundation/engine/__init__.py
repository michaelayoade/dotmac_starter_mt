"""The deployment state machine: the plan as data, and an executor for it."""

from __future__ import annotations

from ..live_instance import LiveInstanceObservationEffects
from .lock import (
    DeploymentLockHeld,
    LockUnavailableError,
    deployment_lock,
    lock_path,
)
from .plan import (
    DeploymentPlan,
    Phase,
    Step,
    StepKind,
    Strategy,
    build_plan,
    format_plan,
    steps_for_rollback,
)
from .run import (
    BackupResult,
    CandidateMaterialProbeEffects,
    CommandResult,
    DeploymentOutcome,
    Effects,
    Executor,
    RoleObservation,
    StepRecord,
)

__all__ = [
    "BackupResult",
    "CandidateMaterialProbeEffects",
    "CommandResult",
    "DeploymentLockHeld",
    "DeploymentOutcome",
    "DeploymentPlan",
    "Effects",
    "Executor",
    "LockUnavailableError",
    "LiveInstanceObservationEffects",
    "Phase",
    "RoleObservation",
    "Step",
    "StepKind",
    "StepRecord",
    "Strategy",
    "build_plan",
    "deployment_lock",
    "format_plan",
    "lock_path",
    "steps_for_rollback",
]
