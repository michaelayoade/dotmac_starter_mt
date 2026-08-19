"""DotMac Commercial Agreements — what was agreed, with whom, and on what proof.

The one owner of the **durable commercial agreement between the platform
operator and a counterparty**: its identity, its accepted commercial snapshot,
its lifecycle, and the append-only evidence for every transition.

Extracted product-first from `dotmac_vendor_control_plane:src/vendor_cp/
contracts/` (658-line service, two tables, migration `v004_contracts`) under
ADR-0033 § 1. See `docs/inventories/vendor-cp-gap-sources.md` § 1 for the source
inventory and `EXTRACTION.toml` for the ownership record.

## The one rule to know

**This module is handed evidence; it never infers that something happened
elsewhere.**

`approve()` takes an `ApprovalEvidence` whose `content_digest` must equal the
digest this module froze at `propose()`. There is no `approved: bool` parameter
and no settable status. Change the terms after approval and the digests differ,
which makes the prior approval **stale rather than transferable** — ADR-0026
§ 2's binding, enforced at the boundary where it is checkable.

`activate()` requires that evidence AGAIN, alongside separate activation
evidence naming the satisfied rule. Not because the module distrusts its own
`approved` column, but so an auditor reading one history row can verify the
activation without trusting that an earlier row was written correctly.

## What it owns

Agreement identity and human-readable reference; an opaque counterparty
reference; type and version; the draft → proposed → approved → active →
suspended → terminated → expired lifecycle; effective and expiry dates; the
immutable accepted commercial snapshot and its digest; opaque product/release
references; commercial terms and entitlement promises; approval evidence;
activation, suspension and termination reasons; amendments and supersession;
append-only transition history; optimistic concurrency; idempotent commands;
and the versioned facts an assembly routes.

## What it does NOT own

Product or release definitions (`dotmac-release-catalog`); entitlement balances
or allocations (`dotmac-entitlement-allocation`); licences (`dotmac-licensing`);
deployment state (`dotmac-deployment-control`); invoices, settlements or
collections decisions (`dotmac-billing`, `dotmac-collections`); approval workflow
internals (`dotmac-approvals`); counterparty master data (nobody in this
programme — ADR-0033 § 6); documents or stored bytes (`dotmac-files`, ADR-0022).

## It imports no sibling module

`dotmac-approvals` and `dotmac-entitlement-allocation` are consumed and produced
as VALUES through the assembly, never imported (ADR-0024, and the import-linter
contract `Modules are independent of each other`). The three couplings the
source had — an approvals import, an offer-catalogue FK, and a `vendor_accounts`
id — are each cut at a port described in `ports.py`.

## Transaction authority

Receives a `Session`; only `add` and `flush`. Never commits, never rolls back,
never constructs a session (hard rule 8). The boundary owns the transaction.

## Public surface

Everything importable from this top-level namespace is stable. Submodules are
not: import from here.
"""

from __future__ import annotations

from dotmac_commercial_agreements.facts import (
    AGREEMENT_ACTIVATED_V1,
    AGREEMENT_AMENDED_V1,
    AGREEMENT_APPROVED_V1,
    AGREEMENT_CANCELLED_V1,
    AGREEMENT_EXPIRED_V1,
    AGREEMENT_PROPOSED_V1,
    AGREEMENT_REINSTATED_V1,
    AGREEMENT_REJECTED_V1,
    AGREEMENT_SUSPENDED_V1,
    AGREEMENT_TERMINATED_V1,
    PUBLISHED_EVENT_TYPES,
    AgreementView,
    PromisedLine,
    TransitionRecord,
)
from dotmac_commercial_agreements.manifest import module
from dotmac_commercial_agreements.migrations import versions_dir
from dotmac_commercial_agreements.models import (
    SCHEMA,
    TERMINAL_STATUSES,
    Agreement,
    AgreementEvent,
    AgreementLine,
    AgreementStatus,
)
from dotmac_commercial_agreements.ports import (
    ActivationEvidence,
    AgreementError,
    AgreementPeriod,
    ApprovalEvidence,
    CapabilityCatalogueReader,
    CommercialTerms,
    EmptyAgreementError,
    EvidenceRefusedError,
    ExpectedStateError,
    LineInput,
    TransitionRefusedError,
    UndeclaredCapabilityError,
    UnknownProductError,
)
from dotmac_commercial_agreements.service import (
    AUDIT_ACTION_TRANSITIONED,
    ActivateCommand,
    AmendCommand,
    ApproveCommand,
    DraftCommand,
    ProposeCommand,
    TerminateCommand,
    TransitionCommand,
    accepted_snapshot,
    activate,
    amend,
    approve,
    cancel,
    expire,
    family,
    get,
    history,
    open_draft,
    propose,
    reinstate,
    reject,
    snapshot_digest,
    suspend,
    terminate,
)

__version__ = "0.1.0a1"

__all__ = [
    "AGREEMENT_ACTIVATED_V1",
    "AGREEMENT_AMENDED_V1",
    "AGREEMENT_APPROVED_V1",
    "AGREEMENT_CANCELLED_V1",
    "AGREEMENT_EXPIRED_V1",
    "AGREEMENT_PROPOSED_V1",
    "AGREEMENT_REINSTATED_V1",
    "AGREEMENT_REJECTED_V1",
    "AGREEMENT_SUSPENDED_V1",
    "AGREEMENT_TERMINATED_V1",
    "AUDIT_ACTION_TRANSITIONED",
    "PUBLISHED_EVENT_TYPES",
    "SCHEMA",
    "TERMINAL_STATUSES",
    "ActivateCommand",
    "ActivationEvidence",
    "Agreement",
    "AgreementError",
    "AgreementEvent",
    "AgreementLine",
    "AgreementPeriod",
    "AgreementStatus",
    "AgreementView",
    "AmendCommand",
    "ApprovalEvidence",
    "ApproveCommand",
    "CapabilityCatalogueReader",
    "CommercialTerms",
    "DraftCommand",
    "EmptyAgreementError",
    "EvidenceRefusedError",
    "ExpectedStateError",
    "LineInput",
    "ProposeCommand",
    "PromisedLine",
    "TerminateCommand",
    "TransitionCommand",
    "TransitionRecord",
    "TransitionRefusedError",
    "UndeclaredCapabilityError",
    "UnknownProductError",
    "__version__",
    "accepted_snapshot",
    "activate",
    "amend",
    "approve",
    "cancel",
    "expire",
    "family",
    "get",
    "history",
    "module",
    "open_draft",
    "propose",
    "reinstate",
    "reject",
    "snapshot_digest",
    "suspend",
    "terminate",
    "versions_dir",
]
