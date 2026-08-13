"""DotMac Integration — the external connector control plane (ADR-0024 §§ 6-7).

The reusable engine behind one independently deployed Integrator. It owns the
generic machinery of RUNNING a connector; it never owns what a payload means.

## Two artifacts, one of which is this

`dotmac-integration` is this module: independently versioned, composed by an
assembly, importing no product. `dotmac_integrator` is a thin assembly
repository that pins the kernel, this module and connector distributions and
runs them.

"Independently deployed" is a RUNTIME boundary, not a code-location exception to
Starter's layering — an earlier reading put this capability outside the module
system entirely and had to be corrected.

Products do NOT each compose this module. Doing so would duplicate credential
ownership, provider-account rate limiting, backoff and idempotency, which is the
whole reason the runtime is separate at all.

## What this slice ships

Installations, immutable configuration revisions, capability bindings, the
connector SPI and package-metadata discovery — with three refusals:

* a duplicate `connector_key` across installed distributions;
* a connector whose declared SPI range excludes this module;
* a binding naming a capability its connector never declared.

Plus secret REFERENCES only, and a dispatch seam that resolves exactly one
enabled binding or fails closed.

No inbox, no outbox, no retries, no checkpoints yet — that is slice 2, extracted
from `dotmac_sub` with the tests that prove it. Each of those mechanisms gets
exactly one owner here; a plugin may never maintain a parallel ledger.

## enabled is not selected

Many installations may be ENABLED for one capability; exactly one is SELECTED
per dispatch. The schema constrains `(installation_id, capability_id)` and
nothing else, and ambiguity is resolved per dispatch where the caller's intent
exists. See :mod:`dotmac_integration.selection`.

## Public surface

Everything importable from this top-level namespace is stable per this
package's compatibility policy. Submodules are not: import from here.
"""

from __future__ import annotations

from dotmac_integration.activation import (
    ActivationRefused,
    check_activation,
    require_activatable,
)
from dotmac_integration.discovery import (
    ENTRY_POINT_GROUP,
    ConnectorRegistry,
    DuplicateConnectorError,
    discover,
)
from dotmac_integration.execution import (
    CheckpointConflict,
    ExecutionError,
    ProviderEventIdentityCollision,
    advance_checkpoint,
    claim_delivery,
    claim_receipt,
    enqueue_delivery,
    payload_digest,
    receive_verified,
    record_delivery_outcome,
    record_receipt_outcome,
)
from dotmac_integration.idempotency import run_effect_once, scope_for
from dotmac_integration.manifest import module
from dotmac_integration.models import (
    PLATFORM_TABLES,
    SCHEMA,
    TENANT_TABLES,
    CapabilityBinding,
    ConnectorConfigRevision,
    ConnectorInstallation,
    DeliveryAttempt,
    InboxReceipt,
    PollingCheckpoint,
)
from dotmac_integration.operations import (
    AUDIT_ACTION_PREFIX,
    HealthReport,
    NotRepairable,
    health_report,
    record_operation,
    release_expired_leases,
    replay_delivery,
    replay_receipt,
)
from dotmac_integration.retry import (
    DEFAULT_MAX_ATTEMPTS,
    Outcome,
    OutcomeStatus,
    next_state,
    retry_delay_seconds,
)
from dotmac_integration.secrets import (
    SECRET_REFERENCE_SCHEMES,
    SecretValueError,
    validate_config_revision,
    validate_secret_refs,
)
from dotmac_integration.selection import (
    AmbiguousBindingError,
    NoEnabledBindingError,
    SelectionError,
    resolve_binding,
)
from dotmac_integration.spi import (
    CURRENT_SPI_VERSION,
    CapabilityDeclaration,
    ConnectorManifest,
    InvalidManifestError,
    SpiIncompatibleError,
    SpiRange,
    SpiVersion,
)

__version__ = "0.1.0a1"

__all__ = [
    "replay_receipt",
    "replay_delivery",
    "release_expired_leases",
    "record_operation",
    "health_report",
    "NotRepairable",
    "HealthReport",
    "AUDIT_ACTION_PREFIX",
    "scope_for",
    "run_effect_once",
    "retry_delay_seconds",
    "record_receipt_outcome",
    "record_delivery_outcome",
    "receive_verified",
    "payload_digest",
    "next_state",
    "enqueue_delivery",
    "claim_receipt",
    "claim_delivery",
    "advance_checkpoint",
    "ProviderEventIdentityCollision",
    "PollingCheckpoint",
    "OutcomeStatus",
    "Outcome",
    "InboxReceipt",
    "ExecutionError",
    "DeliveryAttempt",
    "DEFAULT_MAX_ATTEMPTS",
    "CheckpointConflict",
    "CURRENT_SPI_VERSION",
    "ENTRY_POINT_GROUP",
    "PLATFORM_TABLES",
    "SCHEMA",
    "SECRET_REFERENCE_SCHEMES",
    "TENANT_TABLES",
    "ActivationRefused",
    "AmbiguousBindingError",
    "CapabilityBinding",
    "CapabilityDeclaration",
    "ConnectorConfigRevision",
    "ConnectorInstallation",
    "ConnectorManifest",
    "ConnectorRegistry",
    "DuplicateConnectorError",
    "InvalidManifestError",
    "NoEnabledBindingError",
    "SecretValueError",
    "SelectionError",
    "SpiIncompatibleError",
    "SpiRange",
    "SpiVersion",
    "__version__",
    "check_activation",
    "discover",
    "module",
    "require_activatable",
    "resolve_binding",
    "validate_config_revision",
    "validate_secret_refs",
]
