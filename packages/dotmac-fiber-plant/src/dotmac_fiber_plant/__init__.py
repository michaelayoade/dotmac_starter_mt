"""Public Fiber Plant service surface."""

from dotmac_fiber_plant.manifest import module
from dotmac_fiber_plant.migrations import versions_dir
from dotmac_fiber_plant.service import (
    FiberPlantConflict,
    FiberPlantError,
    FiberPlantNotFound,
    accept_change,
    approve_change,
    lookup_cables,
    lookup_changes,
    lookup_structures,
    propose_change,
    record_field_observation,
    record_splice,
    record_termination,
    register_cable,
    register_strand,
    register_structure,
    resolve_continuity,
)

__version__ = "0.1.0a1"
__all__ = [
    "__version__",
    "FiberPlantConflict",
    "FiberPlantError",
    "FiberPlantNotFound",
    "accept_change",
    "approve_change",
    "lookup_cables",
    "lookup_changes",
    "lookup_structures",
    "module",
    "propose_change",
    "record_field_observation",
    "record_splice",
    "record_termination",
    "register_cable",
    "register_strand",
    "register_structure",
    "resolve_continuity",
    "versions_dir",
]
