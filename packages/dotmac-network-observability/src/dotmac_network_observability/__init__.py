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
from dotmac_network_observability.models import (
    ALL_MODELS,
    SCHEMA,
)

from dotmac_network_observability.contracts import (
    AlertState,
    AvailabilityState,
    ObservationKind,
    OpenAlertEvidence,
    RebuildHealth,
    RecordAvailability,
    RecordObservation,
    ResolveAlertEvidence,
)

__all__ = [
    "__version__",
    "AlertState",
    "ALL_MODELS",
    "AvailabilityState",
    "module",
    "NetworkObservationConflict",
    "NetworkObservationError",
    "NetworkObservationNotFound",
    "ObservationKind",
    "open_alert_evidence",
    "OpenAlertEvidence",
    "query_alerts",
    "query_health",
    "query_observations",
    "rebuild_health",
    "RebuildHealth",
    "record_availability",
    "record_measurement",
    "record_observation",
    "RecordAvailability",
    "RecordObservation",
    "resolve_alert_evidence",
    "ResolveAlertEvidence",
    "SCHEMA",
    "versions_dir",
]
