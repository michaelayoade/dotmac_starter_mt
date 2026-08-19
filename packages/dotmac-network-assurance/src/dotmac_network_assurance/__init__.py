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
__all__ = [
    "__version__",
    "AssuranceConflict",
    "AssuranceError",
    "AssuranceNotFound",
    "classify_impact",
    "lookup_incidents",
    "module",
    "open_incident",
    "query_impacts",
    "query_maintenance",
    "query_sla_evidence",
    "recommend_escalation",
    "record_notification_evidence",
    "record_sla_evidence",
    "resolve_incident",
    "schedule_maintenance",
    "update_incident",
    "versions_dir",
]
