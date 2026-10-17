"""Public surface for ``dotmac-platform-health``."""

from typing import TYPE_CHECKING

from dotmac_platform_health.admission import (
    AuthenticatedHealthSource,
    EnrollmentAuthority,
    EnrollmentProjection,
    HealthAdmission,
    HealthAdmissionError,
)
from dotmac_platform_health.contracts import (
    DEPLOYMENT_HEALTH_EVIDENCE_SCHEMA,
    ComponentEvidence,
    DeploymentHealthEvidence,
    HealthObservationDocument,
    HealthObservationInput,
    HealthState,
    HealthSummary,
    health_observation_document,
    health_observation_key,
    validate_health_observation,
)
from dotmac_platform_health.evidence import (
    HealthEvidenceError,
    HealthEvidenceSignature,
    HealthEvidenceSigner,
    SignedHealthEvidence,
    canonical_health_evidence_bytes,
)
from dotmac_platform_health.migrations import versions_dir

if TYPE_CHECKING:
    from dotmac_platform_health.manifest import module
    from dotmac_platform_health.service import (
        HealthConflict,
        HealthError,
        ObservationReceipt,
        build_health_evidence,
        produce_signed_health_evidence,
        rebuild_projections,
        record_observation,
        register_component,
        summarize_health,
    )


def __getattr__(name: str) -> object:
    if name == "module":
        from dotmac_platform_health.manifest import module

        globals()[name] = module
        return module
    if name in {
        "HealthConflict",
        "HealthError",
        "ObservationReceipt",
        "build_health_evidence",
        "produce_signed_health_evidence",
        "rebuild_projections",
        "record_observation",
        "register_component",
        "summarize_health",
    }:
        from dotmac_platform_health import service

        value = getattr(service, name)
        globals()[name] = value
        return value
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__version__ = "0.2.0a1"

__all__ = [
    "AuthenticatedHealthSource",
    "EnrollmentAuthority",
    "EnrollmentProjection",
    "HealthAdmission",
    "HealthAdmissionError",
    "__version__",
    "DEPLOYMENT_HEALTH_EVIDENCE_SCHEMA",
    "ComponentEvidence",
    "DeploymentHealthEvidence",
    "HealthConflict",
    "HealthError",
    "HealthEvidenceError",
    "HealthEvidenceSignature",
    "HealthEvidenceSigner",
    "HealthObservationDocument",
    "HealthObservationInput",
    "HealthState",
    "HealthSummary",
    "ObservationReceipt",
    "SignedHealthEvidence",
    "build_health_evidence",
    "canonical_health_evidence_bytes",
    "health_observation_document",
    "health_observation_key",
    "module",
    "produce_signed_health_evidence",
    "rebuild_projections",
    "record_observation",
    "register_component",
    "summarize_health",
    "validate_health_observation",
    "versions_dir",
]
