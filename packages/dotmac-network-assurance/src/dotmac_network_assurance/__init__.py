"""Public Network Assurance service surface."""

from dotmac_network_assurance.manifest import module
from dotmac_network_assurance.migrations import versions_dir
from dotmac_network_assurance.service import (
    AssuranceConflict,
    AssuranceError,
    AssuranceNotFound,
    classify_impact,
    lookup_incidents,
    open_incident,
    query_impacts,
    query_maintenance,
    query_sla_evidence,
    recommend_escalation,
    record_notification_evidence,
    record_sla_evidence,
    resolve_incident,
    schedule_maintenance,
    update_incident,
)

__version__ = "0.1.0a1"
from dotmac_network_assurance.contracts import (
    ImpactSeverity,
    IncidentState,
    OpenIncident,
    ResolveIncident,
    UpdateIncident,
)
from dotmac_network_assurance.models import (
    ALL_MODELS,
    SCHEMA,
)

__all__ = [
    "__version__",
    "ALL_MODELS",
    "AssuranceConflict",
    "AssuranceError",
    "AssuranceNotFound",
    "classify_impact",
    "ImpactSeverity",
    "IncidentState",
    "lookup_incidents",
    "module",
    "open_incident",
    "OpenIncident",
    "query_impacts",
    "query_maintenance",
    "query_sla_evidence",
    "recommend_escalation",
    "record_notification_evidence",
    "record_sla_evidence",
    "resolve_incident",
    "ResolveIncident",
    "schedule_maintenance",
    "SCHEMA",
    "update_incident",
    "UpdateIncident",
    "versions_dir",
]
