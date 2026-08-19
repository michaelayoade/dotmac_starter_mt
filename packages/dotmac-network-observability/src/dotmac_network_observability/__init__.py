"""Public Network Observability service surface."""

from dotmac_network_observability.manifest import module
from dotmac_network_observability.migrations import versions_dir
from dotmac_network_observability.service import (
    NetworkObservationConflict,
    NetworkObservationError,
    NetworkObservationNotFound,
    open_alert_evidence,
    query_alerts,
    query_health,
    query_observations,
    rebuild_health,
    record_availability,
    record_measurement,
    record_observation,
    resolve_alert_evidence,
)

__version__ = "0.1.0a1"
__all__ = [
    "__version__",
    "NetworkObservationConflict",
    "NetworkObservationError",
    "NetworkObservationNotFound",
    "module",
    "open_alert_evidence",
    "query_alerts",
    "query_health",
    "query_observations",
    "rebuild_health",
    "record_availability",
    "record_measurement",
    "record_observation",
    "resolve_alert_evidence",
    "versions_dir",
]
