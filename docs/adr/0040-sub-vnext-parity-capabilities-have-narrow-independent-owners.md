# ADR-0040: Sub vNext parity capabilities have narrow independent owners

**Status:** Accepted
**Date:** 2026-08-20
**Decision owner:** Michael
**Scope:** Dotmac applications and independently released Starter modules
**Amends:** ADR-0029 and ADR-0057 as described below
**Relates to:** ADR-0006 (product-first extraction), ADR-0010 (thin adapters),
ADR-0014 (one idempotency owner), ADR-0024 (applications synchronize data),
ADR-0026 (approvals decide approval only), and ADR-0031 (sealed cutover)

## Context

The thin ISP replacement needs several capabilities that legacy Sub exposes
directly or that the intended product needs for parity. Similar names already
occur in CRM, ERP, the Integrator and the Vendor control plane. A package-per-
name exercise would preserve duplicate writers, provider logic and product
state machines. The exact-source inventory in
[`sub-vnext-parity-sources.md`](../inventories/sub-vnext-parity-sources.md)
therefore adjudicates the owner before implementation.

## Decision

| Capability | Decision | Owner boundary |
|---|---|---|
| Referrals | **retain as `dotmac-referrals`** | Programmes, invitations/codes, attribution and conversion evidence. Party, Customers, Sales/Leads, Billing and reward fulfilment remain typed collaborators. |
| Reseller Management | **retain as `dotmac-reseller-management`** | Reseller account identity, hierarchy, delegated commercial authority and lifecycle. Party, Customers, Commercial Agreements and Entitlement Allocation remain separate owners. |
| AI Operations | **retain as `dotmac-ai-operations`** | Provider-neutral intake, transcription/model-execution evidence, resumable operator workflow and advisory insights. Integrator connector plugins own provider identity, credentials, APIs, wire mappings and I/O. |
| Remote Access | **retain as `dotmac-remote-access`** | Device/network access requests, grants, enforced expiry and revocation. Approvals owns only the decision evidence; Network Control executes device/network commands and reports observations. |
| Compliance Reporting | **retain as `dotmac-compliance-reporting`** | Regulatory classification, evidence-pack assembly, submissions, acknowledgements and filing status. Ticketing retains complaint/case lifecycle; source domains retain their facts. |
| Workflow Runtime | **retain as `dotmac-workflow-runtime`** | User-authored resumable execution instances, checkpoints, repair evidence and runtime state. Definition authoring is an opaque versioned input. Fulfillment, Durable Timers, provisioning and every domain state machine remain separate. |
| Support Access | **retain as `dotmac-support-access`** | Temporary support-access request, grant, expiry, revocation and audit workflow. Approvals owns the approval record; kernel owns enforcement primitives. No standing credential or renew-in-place path is legal. |
| Platform Health | **retain as `dotmac-platform-health`** | General application/runtime-health observations, freshness projections and incident-ready summaries. Raw telemetry, alert transport and network monitoring stay with observability owners; health never mutates deployment intent. |
| Fleet Control | **adopt existing `dotmac-deployment-control`** | Desired deployment intent, plans, rollouts, acknowledgements and drift already have one owner. Kernel's closed update-authority vocabulary remains canonical. No `dotmac-fleet-control` package is created. |
| Reusable Forms | **retain separately as `dotmac-forms`** | ERP's independently used seven-table definition/version/section/field/option/submission/answer capability qualifies. Forms owns data capture and validation only; Workflow Runtime may carry an opaque form-version reference but imports no Forms code. |

All retained modules are independently installable. They import the kernel, not
one another or a product; use opaque external references; create no cross-
lineage foreign key; emit typed outbox facts rather than calling another
application; and contain no provider or product mode branch.

## Source and retirement decisions

- Sub is the product-first source for Referrals, Reseller Management and AI
  Operations. Its remote-access implementation is the closest production
  reference but not a complete request/grant owner.
- Sub's NCC pack is the product-first source for Compliance Reporting's
  evidence-pack behavior. Filing lifecycle is a typed port delta, not a reason
  to discard the source.
- ERP is the product-first source for Workflow Runtime and Forms. Workflow
  extracts execution mechanics only; ERP's product enum, webhook/email effects,
  schedules and domain mutation actions do not port.
- Sub's read-only impersonation token is a production reference for the
  Support Access enforcement seam, not a support-workflow implementation.
  Inventory proves no complete request/consent/grant/revocation ledger exists,
  so the workflow is greenfield after inventory.
- Sub/CRM task heartbeats and Vendor/Integrator operational rollups are pattern
  references for Platform Health. None is the general authenticated health
  projection, so that owner is greenfield after inventory.
- CRM's referral and reseller writers, duplicated AI state and copied NCC pack
  are retirement targets. They remain authoritative until each adopter has
  backfilled, shadowed, reconciled and sealed its writer; this ADR does not
  pretend deletion has already happened.

## Dated amendment to ADR-0029 and ADR-0057

ADR-0029 remains correct for **standing cross-application access**:
`dotmac-application-access` owns desired grant-set issuance and drift. The
temporary support workflow decided here is different: it is purpose-bound,
case/incident-bound, least-privilege and finite. `dotmac-support-access` owns
that temporary request/grant lifecycle and hands an admitted grant to kernel
enforcement. It owns no Workspace application-role catalogue and issues no
standing cross-application grant set.

ADR-0057 section 4 recorded that Support Access was not built because the then-
proposed unit overlapped Application Access and no complete source existed. The
second fact still informs provenance; the first is superseded by Michael's
2026-08-20 explicit boundary above. This is an amendment, not rewritten
history.

## Planes and first consumers

- Tenant plane: Referrals, Reseller Management, AI Operations, Remote Access,
  Compliance Reporting, Workflow Runtime and Forms.
- Platform plane: Support Access and Platform Health. The Vendor control plane
  is their named first candidate consumer; a data plane receives only typed
  enforcement or observation contracts.
- Fleet Control remains the existing platform-only Deployment Control module.

No second plane is declared "for later". Adding one requires a named assembly,
an ADR amendment and the full ADR-0023/0028 live-catalog proof.

## Consequences

Each retained capability must land dossier first, canary first, then source-
based implementation. A source product remains the owner until its exact
module pin, backfill, complete-state shadow comparison, immutable cutover
watermark and old-writer seal are proven. Release, composition and authority
cutover are separate acts; an `audit-complete` package is not production.
