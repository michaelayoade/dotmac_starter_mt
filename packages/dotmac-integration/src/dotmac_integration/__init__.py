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

## Ownership and routing (slice 3)

Two registries that together answer "whose contract is this, and where does it
land?" — and answer it from trusted state only.

`capability_registry` closes the gap `provider-capability-sources.md` § 7.2
records: a capability id was an open string with no declaration, no owner and no
collision check. Now the OWNING business application declares it, this module
validates and binds it, and a connector merely implements it. Three refusals,
three messages: declared twice, named-but-undeclared, declared-but-unimplemented.

`destination_binding` makes the fleet's destination-scope invariant executable:
**provider metadata is corroboration only and can never select a destination.**
A binding names the application, the local scope and the contract version; it is
resolved from an immutable config revision BEFORE any provider I/O; and the
destination application must be the one that DECLARED the capability, so neither
a payload nor a lone edited configuration can redirect a stream.
## And payload retention (slice 3)

A receipt is evidence and it is content, and the first must outlive the second
without becoming permanent.
:mod:`dotmac_integration.retention` ages out `payload_json`, `headers_json` and
the values inside `consequence_json` while touching NOTHING deduplication reads
— so a provider's redelivery months later is still recognised as the event it
is, rather than processed a second time as a new one.

Two refusals define it. There is no default content or replay-evidence period
and no default legal-policy owner: :func:`resolve_retention_policy` refuses
rather than guess,
because a period baked into a library becomes a deployment's data-retention
posture without anyone deciding it. And a receipt under legal hold, claimed by
a worker, unresolved, or awaiting reconciliation is refused BY NAME and counted,
never quietly skipped.

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
from dotmac_integration.capability_registry import (
    EMPTY_REGISTRY,
    CapabilityContract,
    CapabilityOwner,
    CapabilityRegistry,
    CapabilityRegistryError,
    CapabilityRegistryNotInstalled,
    DuplicateCapabilityDeclaration,
    OrphanCapabilityError,
    UnknownCapabilityError,
    capability_registry,
    contract_from_declaration,
    install_capability_registry,
    require_declared_for_binding,
    require_governable,
    require_implements_only_declared,
    require_no_orphans,
)
from dotmac_integration.destination_binding import (
    Corroboration,
    DestinationBinding,
    DestinationBindingError,
    DestinationClient,
    DestinationDisagreement,
    DestinationNotBound,
    DestinationProfile,
    DestinationProfileMissing,
    LocalScope,
    ProductPortDescriptorInvalid,
    ProductPortDescriptorSnapshot,
    UntrustedDestination,
    capability_bindings_for,
    corroborate,
    destination_client,
    establish_destination,
    install_destination_profiles,
    product_port_descriptor_digest,
    reconcile_product_port_descriptor,
    reconcile_product_port_descriptor_for_capability,
    require_corroborated,
    require_profile,
    resolve_destination,
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
    HANDSHAKE_INSTALLATION_STATES,
    ConnectorContract,
    ConnectorRaised,
    ConnectorUnavailable,
    EndpointAddress,
    EndpointLookupFailed,
    EndpointNotServiceable,
    EndpointNotUsable,
    EndpointUnknown,
    EventIdentityCollision,
    HandlerUnavailable,
    IngressCode,
    IngressError,
    IngressOperation,
    IngressOutcome,
    IngressRefused,
    ManifestPinUnhonoured,
    ModeNotAvailable,
    NotAChallenge,
    PayloadTooLarge,
    PreparedIngress,
    ReceiptWriteFailed,
    ReceiptWriteRaced,
    SecretsUnavailable,
    SignatureRejected,
    VerificationObserver,
    answer_challenge,
    challenge_response,
    prepare_ingress,
    receive,
    record_batch,
    refusal_outcome,
    verify_and_normalize,
)
from dotmac_integration.lifecycle import (
    ENDPOINT_AUDIT_ACTIONS,
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
from dotmac_integration.polling import (
    CursorInvalid,
    PollBatch,
    PollConnectorRaised,
    PollContractError,
    PollError,
    PollHandlerUnavailable,
    PollResult,
    PollSecretsUnavailable,
    PollUnavailable,
    PreparedPoll,
    invoke_poll,
    poll_once,
    prepare_poll,
    record_poll_batch,
)
from dotmac_integration.receipt_delivery import (
    DeliveryError,
    DeliveryReport,
    FingerprintConflict,
    # `LostClaim` is deliberately absent: it is ONE class, imported above via
    # `dispatch`, and re-importing it here would be a redefinition of the same
    # name at this module's surface.
    ProductAcceptance,
    ProductOutcome,
    ProductPortClient,
    ProductRequest,
    ReceiptClaim,
    ReceiptClaims,
    ReceiptClaimStore,
    TransportFailure,
    TrustedDestination,
    TrustedScope,
    build_product_request,
    deliver_receipt,
    idempotency_key_for,
    request_fingerprint_for,
    require_stable_fingerprint,
)
from dotmac_integration.retention import (
    REDACTION_MARKER,
    RETENTION_DAYS_VAR,
    RETENTION_LEGAL_POLICY_OWNER_VAR,
    RETENTION_REPLAY_EVIDENCE_DAYS_VAR,
    ReceiptLegalHold,
    ReplayEvidenceSweep,
    RetentionBacklog,
    RetentionNotConfigured,
    RetentionPolicy,
    RetentionRefusal,
    RetentionRefused,
    RetentionSweep,
    active_hold_for,
    classify_receipt,
    is_redacted,
    place_legal_hold,
    purge_expired_payloads,
    purge_expired_replay_evidence,
    redact_receipt,
    release_legal_hold,
    resolve_retention_policy,
    retention_backlog,
)
from dotmac_integration.retry import (
    Outcome,
    OutcomeStatus,
    next_state,
    retry_delay_seconds,
)
from dotmac_integration.runtime_policy import (
    ConnectorRuntimePolicy,
    RuntimeBoundaryMissing,
    RuntimePolicy,
    derive_runtime_policy,
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
from dotmac_integration.shadow import (
    RETRYABLE_SHADOW_VERDICTS,
    SHADOW_PLATFORM_TABLES,
    SafeShadowVerdict,
    ShadowComparisonEvidence,
    ShadowEvidenceCorrupt,
    ShadowObservation,
    ShadowReport,
    due_shadow_receipt_ids,
    normalize_shadow_verdict,
    record_shadow_observation,
    shadow_report,
    unreadable_shadow_verdict,
)
from dotmac_integration.spi import (
    CURRENT_SPI_VERSION,
    MODE_PROTOCOLS,
    Acknowledgement,
    CapabilityDeclaration,
    CapabilityHandler,
    ConnectorManifest,
    ConnectorMode,
    ConnectorPlugin,
    DeliveryPlugin,
    Diagnostic,
    DispatchRequest,
    EgressDeclaration,
    InboundDisposition,
    InboundEvent,
    IngressHandler,
    IngressPlugin,
    IngressRequest,
    InvalidAcknowledgementError,
    InvalidManifestError,
    ModeContract,
    ModeContractError,
    ModeNotDeclaredError,
    PollHandler,
    PollPlugin,
    SecretBindingDeclaration,
    SpiIncompatibleError,
    SpiRange,
    SpiVersion,
    VerificationResult,
    accepts_manifest_digest,
    require_mode,
    verify_plugin_modes,
)

__version__ = "0.1.0a12"

__all__ = [
    # ── Ingress: the endpoint lifecycle and the three-phase engine ──────────
    "ENDPOINT_AUDIT_ACTIONS",
    "HANDSHAKE_INSTALLATION_STATES",
    "Acknowledgement",
    "ConnectorContract",
    "ConnectorRaised",
    "ConnectorUnavailable",
    "EndpointAddress",
    "EndpointLookupFailed",
    "EndpointNotServiceable",
    "EndpointNotUsable",
    "EndpointUnknown",
    "EventIdentityCollision",
    "HandlerUnavailable",
    "InboundDisposition",
    "InboundEvent",
    "IngressCode",
    "IngressError",
    "IngressHandler",
    "IngressOperation",
    "IngressOutcome",
    "IngressPlugin",
    "IngressRefused",
    "IngressRequest",
    "InvalidAcknowledgementError",
    "VerificationObserver",
    "VerificationResult",
    "ManifestPinUnhonoured",
    "ModeNotAvailable",
    "NotAChallenge",
    "PayloadTooLarge",
    "PreparedIngress",
    "ReceiptWriteFailed",
    "ReceiptWriteRaced",
    "SecretsUnavailable",
    "SignatureRejected",
    "answer_challenge",
    "challenge_response",
    "mint_ingress_endpoint",
    "prepare_ingress",
    "receive",
    "record_batch",
    "refusal_outcome",
    "revoke_ingress_endpoint",
    "rotate_ingress_endpoint",
    "verify_and_normalize",
    # ── Capability ownership + the trusted destination binding ─────────────
    "EMPTY_REGISTRY",
    "CapabilityContract",
    "CapabilityOwner",
    "CapabilityRegistry",
    "CapabilityRegistryError",
    "CapabilityRegistryNotInstalled",
    "Corroboration",
    "DestinationBinding",
    "DestinationBindingError",
    "DestinationClient",
    "DestinationDisagreement",
    "DestinationNotBound",
    "DestinationProfile",
    "DestinationProfileMissing",
    "DuplicateCapabilityDeclaration",
    "LocalScope",
    "ProductPortDescriptorInvalid",
    "ProductPortDescriptorSnapshot",
    "OrphanCapabilityError",
    "UnknownCapabilityError",
    "UntrustedDestination",
    "capability_bindings_for",
    "capability_registry",
    "contract_from_declaration",
    "corroborate",
    "destination_client",
    "install_capability_registry",
    "install_destination_profiles",
    "product_port_descriptor_digest",
    "reconcile_product_port_descriptor",
    "reconcile_product_port_descriptor_for_capability",
    "require_corroborated",
    "require_declared_for_binding",
    "require_governable",
    "require_implements_only_declared",
    "require_no_orphans",
    "require_profile",
    "establish_destination",
    "resolve_destination",
    # ── Receipt-to-product delivery ─────────────────────────────────────────
    "DeliveryError",
    "DeliveryReport",
    "FingerprintConflict",
    "ProductAcceptance",
    "ProductPortClient",
    "ProductOutcome",
    "ProductRequest",
    "ReceiptClaim",
    "ReceiptClaimStore",
    "ReceiptClaims",
    "TransportFailure",
    "TrustedDestination",
    "TrustedScope",
    "build_product_request",
    "deliver_receipt",
    "idempotency_key_for",
    "request_fingerprint_for",
    "require_stable_fingerprint",
    # ── Revisioned product-port shadow evidence ────────────────────────────
    "RETRYABLE_SHADOW_VERDICTS",
    "SHADOW_PLATFORM_TABLES",
    "SafeShadowVerdict",
    "ShadowComparisonEvidence",
    "ShadowEvidenceCorrupt",
    "ShadowObservation",
    "ShadowReport",
    "due_shadow_receipt_ids",
    "normalize_shadow_verdict",
    "record_shadow_observation",
    "shadow_report",
    "unreadable_shadow_verdict",
    # ── Payload retention ──────────────────────────────────────────────────
    "REDACTION_MARKER",
    "RETENTION_DAYS_VAR",
    "RETENTION_LEGAL_POLICY_OWNER_VAR",
    "RETENTION_REPLAY_EVIDENCE_DAYS_VAR",
    "ReplayEvidenceSweep",
    "ReceiptLegalHold",
    "RetentionBacklog",
    "RetentionNotConfigured",
    "RetentionPolicy",
    "RetentionRefusal",
    "RetentionRefused",
    "RetentionSweep",
    "active_hold_for",
    "classify_receipt",
    "is_redacted",
    "place_legal_hold",
    "purge_expired_payloads",
    "purge_expired_replay_evidence",
    "redact_receipt",
    "release_legal_hold",
    "resolve_retention_policy",
    "retention_backlog",
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
    # ── SPI 1.x: mode contracts, ingress types and verification evidence ───
    "MODE_PROTOCOLS",
    "Acknowledgement",
    "DeliveryPlugin",
    "InboundEvent",
    "IngressHandler",
    "IngressPlugin",
    "IngressRequest",
    "InvalidAcknowledgementError",
    "ModeContract",
    "ModeContractError",
    "ModeNotDeclaredError",
    "PollHandler",
    "PollPlugin",
    "require_mode",
    "verify_plugin_modes",
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
    "CursorInvalid",
    "PollBatch",
    "PollConnectorRaised",
    "PollContractError",
    "PollError",
    "PollHandlerUnavailable",
    "PollResult",
    "PollSecretsUnavailable",
    "PollUnavailable",
    "PreparedPoll",
    "invoke_poll",
    "poll_once",
    "prepare_poll",
    "record_poll_batch",
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
    "ConnectorRuntimePolicy",
    "DuplicateConnectorError",
    "EgressDeclaration",
    "InvalidManifestError",
    "NoEnabledBindingError",
    "RuntimeBoundaryMissing",
    "RuntimePolicy",
    "SecretBindingDeclaration",
    "SecretValueError",
    "SelectionError",
    "SpiIncompatibleError",
    "SpiRange",
    "SpiVersion",
    "__version__",
    "check_activation",
    "discover",
    "derive_runtime_policy",
    "module",
    "require_activatable",
    "resolve_binding",
    "validate_config_revision",
    "validate_secret_refs",
]
