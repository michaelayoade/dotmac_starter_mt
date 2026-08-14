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

## And the execution machinery (slice 2)

Inbox receipts with binding-scoped deduplication, an outbox with atomic
leasing, retry classification and backoff, versioned polling checkpoints, and
health/audit/repair. Each mechanism has exactly ONE owner, and a plugin may
never maintain a parallel ledger.

Two of those owners are the KERNEL, not this module: at-most-once execution is
`dotmac_kernel.idempotency` (ADR-0014, hard rule 21) and the platform audit
trail is `dotmac_kernel.audit`. Both are adapted here, never reimplemented.

Operational numbers — retry cap, lease duration, staleness threshold — arrive
through :class:`dotmac_integration.policy.ExecutionPolicy` rather than as
hardcoded defaults: a webhook fan-out and a nightly bulk poll do not want the
same backoff.

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
from dotmac_integration.dispatch import (
    DispatchError,
    DispatchUnavailable,
    LostClaim,
    PreparedDispatch,
    invoke,
    prepare,
    settle,
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
from dotmac_integration.ingress import (
    ConnectorContract,
    ConnectorRaised,
    ConnectorUnavailable,
    EndpointNotServiceable,
    EndpointNotUsable,
    EndpointUnknown,
    EventIdentityCollision,
    IngressCode,
    IngressError,
    IngressOutcome,
    IngressRefused,
    ManifestPinUnhonoured,
    ModeNotAvailable,
    NotAChallenge,
    PayloadTooLarge,
    PreparedIngress,
    ReceiptWriteFailed,
    ReceiptWriteRaced,
    SignatureRejected,
    UnitOfWork,
    answer_challenge,
    challenge_response,
    prepare_ingress,
    receive,
    record_batch,
    refusal_outcome,
    verify_and_normalize,
)
from dotmac_integration.lifecycle import (
    AdoptionPreview,
    LifecycleError,
    add_binding,
    adopt_manifest,
    create_draft,
    disable,
    enable,
    mint_ingress_endpoint,
    preview_adoption,
    put_config_revision,
    quarantine,
    retire,
    revoke_ingress_endpoint,
    rotate_ingress_endpoint,
    set_binding_enabled,
)
from dotmac_integration.manifest import module
from dotmac_integration.models import (
    PLATFORM_TABLES,
    SCHEMA,
    TENANT_TABLES,
    CapabilityBinding,
    ConnectorConfigRevision,
    ConnectorInstallation,
    DeliveryAttempt,
    EventSubscription,
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
from dotmac_integration.policy import DEFAULT_POLICY, ExecutionPolicy
from dotmac_integration.retry import (
    Outcome,
    OutcomeStatus,
    next_state,
    retry_delay_seconds,
)
from dotmac_integration.secret_refs import (
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
    MODE_PROTOCOLS,
    CapabilityDeclaration,
    CapabilityHandler,
    ConnectorManifest,
    ConnectorMode,
    ConnectorPlugin,
    DeliveryPlugin,
    Diagnostic,
    DispatchRequest,
    InboundEvent,
    IngressHandler,
    IngressPlugin,
    InvalidManifestError,
    ModeContract,
    ModeNotDeclaredError,
    PollHandler,
    PollPlugin,
    SpiIncompatibleError,
    SpiRange,
    SpiVersion,
    accepts_manifest_digest,
    require_mode,
)

__version__ = "0.1.0a4"

__all__ = [
    # ── The ingress engine (SPI 1.1) ────────────────────────────────────────
    "IngressCode",
    "IngressOutcome",
    "IngressError",
    "IngressRefused",
    "EndpointUnknown",
    "EndpointNotUsable",
    "EndpointNotServiceable",
    "ConnectorUnavailable",
    "ManifestPinUnhonoured",
    "ModeNotAvailable",
    "SignatureRejected",
    "NotAChallenge",
    "ConnectorRaised",
    "ConnectorContract",
    "EventIdentityCollision",
    "ReceiptWriteFailed",
    "ReceiptWriteRaced",
    "PayloadTooLarge",
    "PreparedIngress",
    "UnitOfWork",
    "refusal_outcome",
    "prepare_ingress",
    "verify_and_normalize",
    "record_batch",
    "challenge_response",
    "receive",
    "answer_challenge",
    "mint_ingress_endpoint",
    "rotate_ingress_endpoint",
    "revoke_ingress_endpoint",
    "DispatchUnavailable",
    "settle",
    "set_binding_enabled",
    "retire",
    "quarantine",
    "put_config_revision",
    "preview_adoption",
    "prepare",
    "invoke",
    "enable",
    "disable",
    "create_draft",
    "adopt_manifest",
    "add_binding",
    "accepts_manifest_digest",
    "PreparedDispatch",
    "LostClaim",
    "LifecycleError",
    "DispatchRequest",
    "DispatchError",
    "Diagnostic",
    "ConnectorPlugin",
    "ConnectorMode",
    "CapabilityHandler",
    "AdoptionPreview",
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
    "EventSubscription",
    "DEFAULT_POLICY",
    "ExecutionPolicy",
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
    "DeliveryPlugin",
    "InboundEvent",
    "IngressHandler",
    "IngressPlugin",
    "MODE_PROTOCOLS",
    "ModeContract",
    "ModeNotDeclaredError",
    "PollHandler",
    "PollPlugin",
    "require_mode",
    "__version__",
    "check_activation",
    "discover",
    "module",
    "require_activatable",
    "resolve_binding",
    "validate_config_revision",
    "validate_secret_refs",
]
