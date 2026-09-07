"""Public surface for ``dotmac-platform-health``."""

from dotmac_platform_health.contracts import (
    DEPLOYMENT_HEALTH_EVIDENCE_SCHEMA,
    ComponentEvidence,
    DeploymentHealthEvidence,
    HealthObservationInput,
    HealthState,
    HealthSummary,
)
from dotmac_platform_health.evidence import (
    HealthEvidenceError,
    HealthEvidenceSignature,
    HealthEvidenceSigner,
    SignedHealthEvidence,
    canonical_health_evidence_bytes,
    produce_signed_evidence,
)
from dotmac_platform_health.manifest import module
from dotmac_platform_health.migrations import versions_dir
from dotmac_platform_health.service import (
    HealthConflict,
    HealthError,
    ObservationReceipt,
    build_health_evidence,
    rebuild_projections,
    record_observation,
    register_component,
    summarize_health,
)

__version__ = "0.1.0a1"

__all__ = [
    "__version__",
    "DEPLOYMENT_HEALTH_EVIDENCE_SCHEMA",
    "ComponentEvidence",
    "DeploymentHealthEvidence",
    "HealthConflict",
    "HealthError",
    "HealthEvidenceError",
    "HealthEvidenceSignature",
    "HealthEvidenceSigner",
    "HealthObservationInput",
    "HealthState",
    "HealthSummary",
    "ObservationReceipt",
    "SignedHealthEvidence",
    "build_health_evidence",
    "canonical_health_evidence_bytes",
    "module",
    "produce_signed_evidence",
    "rebuild_projections",
    "record_observation",
    "register_component",
    "summarize_health",
    "versions_dir",
]
