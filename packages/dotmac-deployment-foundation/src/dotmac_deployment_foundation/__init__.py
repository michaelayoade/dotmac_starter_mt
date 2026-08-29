"""``dotmac-deployment-foundation`` — one build-and-deploy facility for the fleet.

A product assembly declares `deploy/product.toml` and nothing else. Everything a
deployment needs is rendered or derived from it: the Compose file, the ingress
site, the collector configuration, the alert rules, and the ordered deployment
plan with its gates.

Read ADR-0070 for the boundary and `EXTRACTION.toml` for what was extracted from
which product and, as importantly, what was deliberately left behind.

## Three properties this package holds on purpose

**Zero runtime dependencies.** Not the kernel, not SQLAlchemy, not FastAPI, not
Jinja, not a YAML library. Standard library only — the same shape as
`dotmac-ui`, for the same reason: a build runner that renders a Compose file has
no database and no web framework, and must not acquire them to validate a
descriptor.

**No state.** No `ModuleManifest`, no models, no migrations, no lineage, no
tenant. A facility that decides how a deployment is built cannot be a table
inside one of the deployments it builds.

**Nothing here runs anything.** The plan is data and the executor talks to an
injected `Effects` provider. That is what makes the failure-injection matrix —
wrong digest, failed backup, candidate never ready, maintenance-required
release attempted online — ordinary unit tests instead of disposable-VM
exercises, and a gate that has never been shown to fire is a gate nobody should
trust.
"""

from __future__ import annotations

from .authenticity import (
    AUTHORIZATION_EVIDENCE_SCHEMA,
    HISTORY_SNAPSHOT_SCHEMA,
    RELEASE_EVIDENCE_SCHEMA,
    SIGNATURE_SCHEMA,
    TRUST_POLICY_SCHEMA,
    ApplicationHistorySnapshotV1,
    ApplicationRepositoryAuthorityV1,
    DeploymentAuthorizationEvidenceV1,
    DeploymentControllerReleaseArtifactV1,
    DeploymentControllerReleaseEvidenceV1,
    DeploymentEvidenceTrustPolicyV1,
    DetachedEvidenceSignatureV1,
    EvidencePurpose,
    GitHubReferencedWorkflowV1,
    GitHubWorkflowRunV1,
    TrustedEvidenceKeyV1,
    WorkflowAuthorityV1,
    canonical_json_bytes,
    signing_payload_bytes,
    verify_detached_evidence,
)
from .backup import Assurance, BackupHealth, BackupRecord, assess, restore_rehearsal
from .conformance import check_all, check_rendered_assets_match
from .controller import (
    STATE_SCHEMA,
    AuthorizedExecutionResult,
    ControllerStateStore,
    ControllerStateV1,
    CurrentReleaseObservation,
    DockerCurrentReleaseObserver,
    deployment_plan_digest,
    deployment_plan_document,
    digest_file,
    execute_authorized,
)
from .document import (
    DESCRIPTOR_DOCUMENT_SCHEMA,
    DeploymentDescriptorDocumentV1,
    build_canonical_document,
)
from .drift import DriftReport, Observation, Verdict, compare
from .engine import (
    DeploymentOutcome,
    DeploymentPlan,
    Effects,
    Executor,
    Step,
    StepKind,
    Strategy,
    build_plan,
    deployment_lock,
    format_plan,
    steps_for_rollback,
)
from .errors import (
    DeploymentError,
    DeploymentFoundationError,
    DriftDetected,
    LockUnavailableError,
    PreconditionFailed,
    RenderDrift,
    SecretValueError,
    SpecError,
    StepFailed,
    UnknownFieldError,
    UnknownSchemaError,
)
from .execution import (
    EXECUTION_SCHEMA,
    LAUNCH_CONTEXT_SCHEMA,
    ApplicationReleaseIdentityV1,
    AuthorizerProvenanceV1,
    ControllerProvenanceV1,
    DeploymentExecutionEnvelopeV1,
    GitRevisionOracle,
    RevisionEvidenceV1,
    RevisionRelation,
    TransitionDecision,
    TransitionOverrideV1,
    decide_transition,
    provenance_from_launch_context,
    strict_json_loads,
)
from .exposure import (
    ExposureEffects,
    ExposureTransaction,
    Finding,
    HostObservation,
    PrivilegedVantageError,
    ProbeOutcome,
    ProbeResult,
    ProbeVantage,
    Severity,
    VerificationReport,
    accept_public_exposure_evidence,
    apply_exposure,
    observation_from_text,
    verify_exposure,
)
from .image import AuditReport, audit_image
from .ingress import (
    ADDRESS_FAMILIES,
    EXPOSURES,
    INGRESS_POLICY_SCHEMA,
    PROVIDERS,
    EdgeEndpoint,
    FirewallRule,
    ProviderCapability,
    admit_bind_address,
    endpoint_token,
)
from .policy import (
    build_edge_plan,
    build_firewall_plan,
    ingress_policy_document,
    public_endpoint_tokens,
)
from .spec import SCHEMA, ProductDeploymentSpec
from .telemetry import (
    RESOURCE_ATTRIBUTES,
    Annotation,
    ResourceAttributes,
    resource_attributes,
)
from .version import VERSION as __version__

__all__ = [
    "__version__",
    "accept_public_exposure_evidence",
    "ADDRESS_FAMILIES",
    "admit_bind_address",
    "Annotation",
    "ApplicationHistorySnapshotV1",
    "ApplicationReleaseIdentityV1",
    "ApplicationRepositoryAuthorityV1",
    "apply_exposure",
    "assess",
    "Assurance",
    "audit_image",
    "AuditReport",
    "AUTHORIZATION_EVIDENCE_SCHEMA",
    "AuthorizedExecutionResult",
    "AuthorizerProvenanceV1",
    "BackupHealth",
    "BackupRecord",
    "build_canonical_document",
    "build_edge_plan",
    "build_firewall_plan",
    "build_plan",
    "canonical_json_bytes",
    "check_all",
    "check_rendered_assets_match",
    "compare",
    "ControllerProvenanceV1",
    "ControllerStateStore",
    "ControllerStateV1",
    "CurrentReleaseObservation",
    "decide_transition",
    "deployment_lock",
    "deployment_plan_digest",
    "deployment_plan_document",
    "DeploymentAuthorizationEvidenceV1",
    "DeploymentControllerReleaseArtifactV1",
    "DeploymentControllerReleaseEvidenceV1",
    "DeploymentDescriptorDocumentV1",
    "DeploymentError",
    "DeploymentEvidenceTrustPolicyV1",
    "DeploymentExecutionEnvelopeV1",
    "DeploymentFoundationError",
    "DeploymentOutcome",
    "DeploymentPlan",
    "DESCRIPTOR_DOCUMENT_SCHEMA",
    "DetachedEvidenceSignatureV1",
    "digest_file",
    "DockerCurrentReleaseObserver",
    "DriftDetected",
    "DriftReport",
    "EdgeEndpoint",
    "Effects",
    "endpoint_token",
    "EvidencePurpose",
    "execute_authorized",
    "EXECUTION_SCHEMA",
    "Executor",
    "ExposureEffects",
    "EXPOSURES",
    "ExposureTransaction",
    "Finding",
    "FirewallRule",
    "format_plan",
    "GitHubReferencedWorkflowV1",
    "GitHubWorkflowRunV1",
    "GitRevisionOracle",
    "HISTORY_SNAPSHOT_SCHEMA",
    "HostObservation",
    "ingress_policy_document",
    "INGRESS_POLICY_SCHEMA",
    "LAUNCH_CONTEXT_SCHEMA",
    "LockUnavailableError",
    "Observation",
    "observation_from_text",
    "PreconditionFailed",
    "PrivilegedVantageError",
    "ProbeOutcome",
    "ProbeResult",
    "ProbeVantage",
    "ProductDeploymentSpec",
    "provenance_from_launch_context",
    "ProviderCapability",
    "PROVIDERS",
    "public_endpoint_tokens",
    "RELEASE_EVIDENCE_SCHEMA",
    "RenderDrift",
    "RESOURCE_ATTRIBUTES",
    "resource_attributes",
    "ResourceAttributes",
    "restore_rehearsal",
    "RevisionEvidenceV1",
    "RevisionRelation",
    "SCHEMA",
    "SecretValueError",
    "Severity",
    "SIGNATURE_SCHEMA",
    "signing_payload_bytes",
    "SpecError",
    "STATE_SCHEMA",
    "Step",
    "StepFailed",
    "StepKind",
    "steps_for_rollback",
    "Strategy",
    "strict_json_loads",
    "TransitionDecision",
    "TransitionOverrideV1",
    "TRUST_POLICY_SCHEMA",
    "TrustedEvidenceKeyV1",
    "UnknownFieldError",
    "UnknownSchemaError",
    "Verdict",
    "VerificationReport",
    "verify_detached_evidence",
    "verify_exposure",
    "WorkflowAuthorityV1",
]
