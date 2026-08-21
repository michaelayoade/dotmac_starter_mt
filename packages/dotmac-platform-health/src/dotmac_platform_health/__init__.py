"""Public surface for ``dotmac-platform-health``."""

from dotmac_platform_health.contracts import (
    HealthObservationInput,
    HealthState,
    HealthSummary,
)
from dotmac_platform_health.manifest import module
from dotmac_platform_health.migrations import versions_dir
from dotmac_platform_health.service import (
    HealthConflict,
    HealthError,
    ObservationReceipt,
    rebuild_projections,
    record_observation,
    register_component,
    summarize_health,
)

__all__ = [
    "HealthConflict",
    "HealthError",
    "HealthObservationInput",
    "HealthState",
    "HealthSummary",
    "ObservationReceipt",
    "module",
    "rebuild_projections",
    "record_observation",
    "register_component",
    "summarize_health",
    "versions_dir",
]
