"""DotMac Licensing — signed, versioned, revocable software-use authority.

The **issuer** half of WS8: it turns an active agreement's allocated entitlement
into a signed document a deployment can verify entirely offline, and it owns that
document's lifecycle, its acknowledgements, and its revocation.

Extracted product-first from `dotmac_vendor_control_plane:src/vendor_cp/
licensing/` (3,689 LOC, migrations `v006` to `v011`) under ADR-0057 § 2. Source
inventory: `docs/inventories/vendor-cp-gap-sources.md` § 2. Ownership record:
`EXTRACTION.toml`.

## Two rules that shape everything else

**1. The module names signing material; it never holds it.** `LicenceSigner` is
a protocol and this distribution ships no implementation of it — not an ephemeral
one for tests, not a file reader, not a null object. `signing_keys` has no
private-key column. A wheel, a source checkout, a database dump, a replica and a
stack trace are all structurally incapable of leaking a key, rather than
prevented from it by policy. Custody is the product's, through
`dotmac_kernel.secret_sources` (ADR-0009, hard rule 20).

**2. Every envelope is round-tripped through the kernel's own verifier before it
is recorded, and a failure is fatal.** The kernel is the receiver. If the
receiver would reject the document, the issuer must not record it. That check —
ported from the source, where it was the single most valuable line — is what
keeps two halves of one protocol from drifting.

## What it owns

Licence identity and lineage; an opaque subject reference; product and release
references; agreement and allocation provenance; licensed capabilities, limits
and validity; the issue → activate → suspend → revoke → expire → replace
lifecycle; licence generation and version; cryptographic payload metadata and a
key identifier; immutable issued facts; installation acknowledgements checked
against what was actually issued; revocation and supersession evidence;
idempotency and expected-state transitions; an offline-verifiable representation;
and a typed inspection API.

## What it does NOT own

The product catalogue (`dotmac-release-catalog`); commercial negotiation
(`dotmac-commercial-agreements`); entitlement arithmetic
(`dotmac-entitlement-allocation`); deployment orchestration
(`dotmac-deployment-control`); **signing secret material** (the product);
application authentication or authorization (the receiving product); billing or
collections; and **provider delivery** — `transport.py`, attempt counters,
retry outcomes and connection refs all stayed behind with the Integrator
(ADR-0024, hard rule 28). This module ends at a signed envelope and resumes at
an acknowledgement.

## It imports no sibling module

Agreement and allocation facts arrive as a `LicensableGrant` value from the
assembly (ADR-0024, and the import-linter contract `Modules are independent of
each other`).

## Transaction authority

Receives a `Session`; only `add` and `flush` (hard rule 8).

## Reading

`list_licences` (over a closed, page-bounded `LicenceFilter`), `licence_view`,
`get_issuance`, `current_issuance`, `acknowledgements`, `issuance_handoff`,
`signing_keys`, `revocations`, `revocation_lists`, `latest_revocation_list` and
`inspect_issued_envelope` are the whole read surface, and they answer in the
typed values of `facts` — never in ORM rows.

`signing_keys` returns key IDs and statuses and NO material of any kind. The
public half is distributed by `build_keyring`, a protocol artefact a deployment
verifies with; the private half has no column here at all.

`issuance_handoff` is the Integrator boundary as a type: the envelope and digest
on the way out, acknowledgements on the way back, and no attempt count, retry
outcome or connection reference anywhere — those are the transport's, and a
field for one here would make this module a second delivery authority with no
honest way to fill it.

## Public surface

Everything importable from this top-level namespace is stable. Submodules are
not: import from here.
"""

from __future__ import annotations

from dotmac_licensing.facts import (
    LICENCE_ACKNOWLEDGED_V1,
    LICENCE_ACTIVATED_V1,
    LICENCE_EXPIRED_V1,
    LICENCE_ISSUED_V1,
    LICENCE_REINSTATED_V1,
    LICENCE_REVOKED_V1,
    LICENCE_SUSPENDED_V1,
    PUBLISHED_EVENT_TYPES,
    REVOCATION_LIST_PUBLISHED_V1,
    AcknowledgementState,
    AcknowledgementView,
    InspectionResult,
    IssuanceHandoff,
    IssuanceView,
    LicenceFilter,
    LicencePage,
    LicenceSummary,
    LicenceView,
    RevocationListView,
    RevocationView,
    SigningKeyView,
)
from dotmac_licensing.manifest import module
from dotmac_licensing.migrations import versions_dir
from dotmac_licensing.models import (
    SCHEMA,
    TERMINAL_ISSUANCE_STATUSES,
    AcknowledgementOutcome,
    IssuanceStatus,
    Licence,
    LicenceAcknowledgement,
    LicenceIssuance,
    Revocation,
    RevocationList,
    SigningKey,
    SigningKeyStatus,
)
from dotmac_licensing.ports import (
    AcknowledgementRefusedError,
    EmptyGrantError,
    ExpectedStateError,
    InstallationReport,
    LicenceSigner,
    LicensableGrant,
    LicensedCapability,
    LicensingError,
    RevocationSupersessionError,
    SignerRefusedError,
    TransitionRefusedError,
    UnverifiableIssuanceError,
    require_usable_signers,
)
from dotmac_licensing.service import (
    AUDIT_ACTION_ACKNOWLEDGED,
    AUDIT_ACTION_ISSUED,
    AUDIT_ACTION_TRANSITIONED,
    DEFAULT_ISSUER,
    AcknowledgeCommand,
    IssuanceTransitionCommand,
    IssueCommand,
    RevokeCommand,
    acknowledge,
    acknowledgements,
    activate,
    build_keyring,
    current_issuance,
    expire,
    get_issuance,
    inspect_issued_envelope,
    issuance_handoff,
    issuances_by_key,
    issue_licence,
    latest_revocation_list,
    licence_view,
    list_licences,
    publish_revocation_list,
    register_signing_key,
    reinstate,
    revocation_lists,
    revocations,
    revoke_licence,
    set_key_status,
    signing_keys,
    suspend,
)

__version__ = "0.1.0a1+dev"

__all__ = [
    "AUDIT_ACTION_ACKNOWLEDGED",
    "AUDIT_ACTION_ISSUED",
    "AUDIT_ACTION_TRANSITIONED",
    "DEFAULT_ISSUER",
    "LICENCE_ACKNOWLEDGED_V1",
    "LICENCE_ACTIVATED_V1",
    "LICENCE_EXPIRED_V1",
    "LICENCE_ISSUED_V1",
    "LICENCE_REINSTATED_V1",
    "LICENCE_REVOKED_V1",
    "LICENCE_SUSPENDED_V1",
    "PUBLISHED_EVENT_TYPES",
    "REVOCATION_LIST_PUBLISHED_V1",
    "SCHEMA",
    "TERMINAL_ISSUANCE_STATUSES",
    "AcknowledgeCommand",
    "AcknowledgementOutcome",
    "AcknowledgementRefusedError",
    "AcknowledgementState",
    "AcknowledgementView",
    "EmptyGrantError",
    "ExpectedStateError",
    "InspectionResult",
    "InstallationReport",
    "IssuanceHandoff",
    "IssuanceStatus",
    "IssuanceTransitionCommand",
    "IssuanceView",
    "IssueCommand",
    "Licence",
    "LicenceAcknowledgement",
    "LicenceFilter",
    "LicenceIssuance",
    "LicencePage",
    "LicenceSigner",
    "LicenceSummary",
    "LicenceView",
    "LicensableGrant",
    "LicensedCapability",
    "LicensingError",
    "Revocation",
    "RevocationList",
    "RevocationListView",
    "RevocationSupersessionError",
    "RevocationView",
    "RevokeCommand",
    "SignerRefusedError",
    "SigningKey",
    "SigningKeyStatus",
    "SigningKeyView",
    "TransitionRefusedError",
    "UnverifiableIssuanceError",
    "__version__",
    "acknowledge",
    "acknowledgements",
    "activate",
    "build_keyring",
    "current_issuance",
    "expire",
    "get_issuance",
    "inspect_issued_envelope",
    "issuance_handoff",
    "issuances_by_key",
    "issue_licence",
    "latest_revocation_list",
    "licence_view",
    "list_licences",
    "module",
    "publish_revocation_list",
    "register_signing_key",
    "reinstate",
    "require_usable_signers",
    "revocation_lists",
    "revocations",
    "revoke_licence",
    "set_key_status",
    "signing_keys",
    "suspend",
    "versions_dir",
]
