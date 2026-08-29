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

from .backup import Assurance, BackupHealth, BackupRecord, assess, restore_rehearsal
from .conformance import check_all, check_rendered_assets_match
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
    ingress_policy_digest,
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
    "ADDRESS_FAMILIES",
    "EXPOSURES",
    "INGRESS_POLICY_SCHEMA",
    "PROVIDERS",
    "RESOURCE_ATTRIBUTES",
    "SCHEMA",
    "Annotation",
    "Assurance",
    "AuditReport",
    "BackupHealth",
    "BackupRecord",
    "DeploymentError",
    "DeploymentFoundationError",
    "DeploymentOutcome",
    "DeploymentPlan",
    "DriftDetected",
    "DriftReport",
    "EdgeEndpoint",
    "Effects",
    "Executor",
    "FirewallRule",
    "LockUnavailableError",
    "Observation",
    "PreconditionFailed",
    "ProductDeploymentSpec",
    "ProviderCapability",
    "RenderDrift",
    "ResourceAttributes",
    "SecretValueError",
    "SpecError",
    "Step",
    "StepFailed",
    "StepKind",
    "Strategy",
    "UnknownFieldError",
    "UnknownSchemaError",
    "Verdict",
    "__version__",
    "admit_bind_address",
    "assess",
    "audit_image",
    "build_edge_plan",
    "build_firewall_plan",
    "build_plan",
    "check_all",
    "check_rendered_assets_match",
    "compare",
    "deployment_lock",
    "endpoint_token",
    "format_plan",
    "ingress_policy_digest",
    "ingress_policy_document",
    "public_endpoint_tokens",
    "resource_attributes",
    "restore_rehearsal",
    "steps_for_rollback",
]
