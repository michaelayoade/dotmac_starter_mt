# ADR-0033: The vendor control plane composes existing owners, and gains three new ones

**Status:** Accepted
**Date:** 2026-08-19
**Decision owner:** Michael
**Scope:** the programme that recomposes `dotmac_vendor_control_plane` as a thin
assembly over Starter-owned modules
**Owns:** which of the six candidate Vendor capabilities become new Starter
modules, which resolve to owners that already exist, the plane each new module
declares, and the counterparty-identity rule all of them follow
**Does not own:** any module's internal contract, the Vendor assembly's own
routes or UI, the schedule on which Vendor adopts a module, or the release
sequencing of unrelated modules

**Applies:** [ADR-0006](0006-white-label-product-foundation.md) § 5 and its
2026-08-08 product-first amendment (hard rule 24),
[ADR-0023](0023-dual-plane-modules-declare-both-persistence-planes.md),
[ADR-0024](0024-apps-compose-by-synchronizing-data.md)
**Relates to:** [ADR-0019](0019-party-identity-follows-the-archetype.md) § 1/§ 5b/§ 6,
[ADR-0026](0026-approvals-decide-approval-never-the-transition.md),
[ADR-0029](0029-access-is-requested-approved-and-issued-by-three-owners.md),
[ADR-0021](0021-the-tenant-workspace-is-a-third-plane.md) § 5
**Evidence:** [`docs/inventories/vendor-cp-gap-sources.md`](../inventories/vendor-cp-gap-sources.md)

## Context

The target Vendor journey is:

```
product release → commercial agreement → approval → agreement activation
  → entitlement allocation → licence issuance → branded deployment intent
  → Integrator delivery → verified deployment receipt → billing, collections, support
```

Most of that journey already has owners. `dotmac-release-catalog`,
`dotmac-entitlement-allocation` and `dotmac-approvals` are composed and
adopted in Vendor production today; `dotmac-integration` owns delivery
transport; `dotmac-billing` and `dotmac-collections` own the tail.

Six links were proposed as missing. Taking one dossier across all six — rather
than three dossiers for the three that looked mandatory — was the decision that
mattered, because **two of the six dissolved on contact with the checked-in
record.** Had they been built first and audited second, the result would have
been two second owners of decisions that already have one, which is the exact
failure ADR-0006 § 5 and hard rule 24 exist to prevent.

## Decision

### 1. Three new owners, and only three

| Module | Owns | Source mode |
|---|---|---|
| `dotmac-commercial-agreements` | the durable commercial agreement between the platform operator and a counterparty, and its lifecycle | product-first, from Vendor `contracts/` |
| `dotmac-licensing` | the licences that translate an active agreement and an allocated entitlement into enforceable software-use authority | product-first, from Vendor `licensing/` |
| `dotmac-deployment-control` | desired deployment intent, rollout planning, acknowledgement and reconciliation for licensed deployments | **split** — see § 3 |

### 2. Brand profiles is a fourth owner, and it is dual-plane

Sub's `BrandProfile` (897 LOC, production) is a qualifying implementation, and no
existing owner covers it: `dotmac_kernel.branding` resolves six keys from a
deployment-static source overlaid by one tenant setting, `dotmac-ui` is
dependency-free and holds no data, `dotmac_kernel.profiles` declares composition
rather than presentation, and `dotmac-files` owns bytes rather than meaning.

The gap is narrow and real: **a named, addressable brand profile that is not a
tenant setting.** It is what lets one released artifact appear as Dotmac Academy
and as NDIC Academy. A per-tenant settings overlay cannot express it, because
the profile must be selectable by host *before* a tenant is resolved.

It declares **both planes** under ADR-0023, because both consumers exist today:
Sub on the tenant plane and the Vendor control plane on the platform plane.

Three constraints bind the extraction:

- **ADR-0006 D8 holds.** No arbitrary CSS and no executable template in profile
  data. Sub's `primary_color`/`secondary_color` are *value-named* and must
  generalise to published `dotmac-ui` **role** tokens (hard rule 16). This is
  the one place the extraction corrects its source rather than porting it.
- **`dotmac-files` stores the bytes.** The profile holds references.
- **Native mobile brands stay separate signed builds from shared source.** The
  profile holds build-profile metadata *references* only, never build inputs.

### 3. Deployment control is split, and the split is recorded

Hard rule 24 requires a qualifying production implementation, or checked-in
evidence that none exists. Deployment control has **one of each**, so the module
declares two source modes rather than claiming one it cannot support:

- **The receipt / acknowledgement half has a tested reference, not a source.** Vendor's V6 slices
  (`admission.py`, `admission_models.py`, `credentials.py`,
  `credential_models.py`, ~1,300 LOC with ~1,425 LOC of tests and a 723-line
  design document) are ported with their parity tests. They were **never merged
  and never deployed**, and their migration slots were subsequently reused by
  different work on Vendor `main` — so they are a *tested reference*, not a
  production-used implementation. `EXTRACTION.toml` records
  `source_mode = "greenfield-after-inventory"`, not `product-first`. Rule 24's
  test is a qualifying PRODUCTION-USED implementation, and a never-deployed
  branch is not one however good it is — so the reference is recorded in
  `source_paths` as material consulted, and the mode states the truth about
  provenance rather than borrowing the stronger word. Only deployment-target
  identity and possession proof are taken from `credentials.py`; provider
  credentials stay with the Integrator.
- **The plan / rollout half is greenfield, and the absence is evidenced.** The
  `v015`–`v017` fleet desired-state source described in Knowledge is not
  recoverable: every local and remote Vendor branch, `git stash list`,
  `git fsck --lost-found`, `git reflog --all`, and the named worktree were
  searched, and no ERP, Sub, CRM, Academy, Workspace, Integrator or Backoffice
  equivalent exists. Ruled by Michael, 2026-08-19: *"if we don't have it on any
  of our repos, let's build."*

**Sub's UISP device control is a pattern reference, not a source.** Its
`desired_state` / `observed_config` / `desired_revision` / snapshot-by-source
decomposition is adopted; none of its code is copied and Sub is not a consumer.
ADR-0006 § 5 warns that a name collision is a prompt to compare columns rather
than evidence of a shared contract; this applies the same caution to a **shape**
collision, which is the less-often-stated half. A subscriber's third-party-managed
network device and a licensed Dotmac application deployment are not one subject.

### 4. `dotmac-support-access` is not built

[ADR-0029](0029-access-is-requested-approved-and-issued-by-three-owners.md)
already names three owners for this decision: `dotmac-approvals` for the approval
and its decision evidence, `dotmac-application-access` for desired access —
*"grant-set issuance, delivery, acknowledgement, drift against applied state, and
revocation"* — and the assembly for routing. `dotmac_kernel.audit` owns the
immutable evidence. Break-glass is an **attribute of a request**, not a fourth
owner.

The audit also found the claimed source does not exist. Sub's "audited admin
impersonation" is `issue_impersonation_access_token` plus an `is_impersonation`
boolean on the portal session; its 4,036-line `support.py` is support *tickets*,
owned by `dotmac-ticketing`. There is no access-request table, no approval
linkage, no time-bounded grant and no revocation ledger anywhere in the fleet.

**Building a fourth owner would recreate exactly the conflict ADR-0029 resolved.**
When `dotmac-application-access` is built — still deferred by ADR-0021 § 5 and
ADR-0017 — it gains time-bounded grants and a break-glass classification by
amendment to ADR-0029. That is not part of this programme.

### 5. `dotmac-notifications` is not built

ADR-0006 § 5c decomposed this capability into owners, and three of its required
dossiers have since been taken — two of which shrank their own owner. Every
concept a notifications module would own already has one:

| Concept | Owner |
|---|---|
| Notification intent | the product domain, emitted as a typed kernel outbox payload |
| Recipient / channel decision inputs | `dotmac_kernel.channel_policy` |
| Consent and suppression references | `dotmac_kernel.consent` |
| Deduplication | `dotmac_kernel.idempotency` (ADR-0014) |
| Template / version reference | `dotmac-template-studio` |
| Delivery state and receipts | `dotmac_kernel.delivery` |
| Retry eligibility | the kernel outbox — attempts, `available_at`, lease reclaim, dead-letter |
| Provider transport, credentials, wire retries | `dotmac-integration` (ADR-0024) |

`delivery-outbox-sources.md` found Sub's notification queue and the kernel
outbox to be *"the same machine, built twice"*, and concluded that porting the
queue *"would install the duplicate permanently rather than retire it."* A
notifications module would be that duplicate a third time.

### 6. The counterparty is an opaque reference, and no module defines one

ADR-0019 § 1 (Party is not an account), § 5b (Account attaches to PartyRole),
§ 6 (no kernel identity extension yet), ruling A3 (`vendor_accounts` must not
retire into kernel `Party`) and ADR-0024's correlation-only rule all agree. There
is no conflict between the sources, and the answer each gives is the same.

Every module in this programme therefore takes an **opaque counterparty
reference** and defines no counterparty master. The Vendor assembly binds that
reference to its own `vendor_accounts.id`. A future commercial-account module may
rebind it without touching any module here.

### 7. Planes are declared from consumers that exist today

| Module | Tenant | Platform | Named consumer |
|---|---|---|---|
| `dotmac-commercial-agreements` | — | ✅ | Vendor CP |
| `dotmac-licensing` | — | ✅ | Vendor CP |
| `dotmac-deployment-control` | — | ✅ | Vendor CP |
| brand profiles | ✅ | ✅ | Sub **and** Vendor CP |

Three platform-only modules, following `dotmac-release-catalog`,
`dotmac-entitlement-allocation` and `dotmac-integration`. One genuinely
dual-plane module. **No tenant plane is declared because it might be useful
later** — ADR-0023 requires a real named assembly on each declared side today.

### 8. Modules compose through the assembly, never through each other

`dotmac-licensing` consumes agreement and allocation facts as typed values passed
in by the Vendor assembly; it imports neither `dotmac-commercial-agreements` nor
`dotmac-entitlement-allocation`. `dotmac-deployment-control` emits
provider-neutral deployment intent and records deduplicated observations; it
imports no Integrator model and shares no database with one. This is ADR-0024
and the `Modules are independent of each other` import-linter contract, and it is
the same shape ADR-0029 § 3 uses for approvals and access.

## Consequences

**Positive.** Two capabilities are not built, so two decisions keep one owner
each. The three that are built each start from a measured source rather than a
design. The Vendor assembly ends with one owner per decision across the whole
journey.

**Negative.** Deployment control carries a genuinely weaker provenance than the
other two, and this ADR records that rather than smoothing it over. Its
plan/rollout half is the only greenfield behaviour in the programme, and it is
the half most likely to need revision once Vendor composes it.

**Cost deferred, not avoided.** ERP's `app/licensing/` remains a second
incompatible licence format in the fleet. This programme does not retire it —
that is a receiver-side obligation against `dotmac_kernel.licensing`, recorded
here so it is not mistaken for solved by the arrival of an issuer.

## Alternatives rejected

- **Build all six.** Rejected: two would be second owners of already-owned
  decisions.
- **Extend `dotmac_kernel.branding` instead of a brand-profile module.** Rejected:
  the kernel resolver is a six-key per-tenant settings overlay, and profile
  selection must happen before tenant resolution. Widening it would put stateful,
  dual-plane, file-referencing data into a pure resolver.
- **Fold licence issuance into `dotmac-entitlement-allocation`.** Rejected: an
  allocation is arithmetic over a promise; a licence is signed, versioned,
  revocable authority with its own lifecycle and cryptographic provenance.
- **Wait for the `v015`–`v017` source to be recovered.** Rejected after the search
  in § 3 returned nothing on any ref, stash, dangling object or reflog.
- **Declare a tenant plane on the three Vendor modules "for later".** Rejected by
  ADR-0023: a declared plane with no assembly is a plane whose isolation nobody
  tests.
