# ADR 0020 — Billing owns operational receivables; commercial modules stay separate

**Status:** Accepted
**Date:** 2026-08-12
**Decision owner:** Michael
**Extends:** ADR-0003's commercial-module boundary, ADR-0006's extraction and
module-lineage rules, ADR-0008's declaration registries, ADR-0016's derived
coverage rule, and ADR-0017's adoption sequence
**Amended by its own 2026-08-14 amendment** under ADR-0023 (dual-plane module
persistence), ADR-0024 (apps compose by synchronizing data; the Integrator owns
provider transport), and ADR-0022 (`dotmac-files` owns stored bytes)
**Evidence:** `docs/inventories/billing-sources.md`
**Implementation plan:**
`docs/superpowers/plans/2026-08-11-billing-subscriptions-collections.md`

## Amendment, 2026-08-14: composition, planes, provider transport, and the app matrix

This ADR was accepted on 2026-08-12. ADR-0023 and ADR-0024 were accepted on
2026-08-13 and are fleet-wide, so they govern these three modules whether or not
this record said so. Two of the corrections below are not clarifications — the
original § 4 dependency graph and the original § 4 allocation of
payment-provider integration are now **wrong as written**, and are corrected in
place with a pointer here. Three further corrections record a stale
prerequisite, an unadjudicated ownership question, and the per-application
composition profile the original decision never stated.

Nothing here starts implementation. § 6 stands unchanged.

### A1. Modules compose at the assembly, not by importing a sibling

ADR-0024 § 2 forbids an installable module from importing another business
module, and makes the consuming assembly the composition root that connects
published contracts or records an event/command. The original arrows —
`dotmac-subscriptions ──> dotmac-billing` and `dotmac-collections ──>
dotmac-billing` — describe exactly the sibling dependency that decision
prohibits, and the import-linter contract *Modules are independent of each
other* would refuse them.

The three modules are therefore **peers over `dotmac-kernel`**, wired by the
consuming assembly:

```text
subscriptions --obligation command--> billing
integrator ----settlement fact------> billing
billing -------receivable fact------> collections
collections ---consequence request--> the owning service (Sub / Vendor CP)
billing -------accounting fact------> ERP
billing -------document facts-------> rendering --> dotmac-files
```

Every arrow is a versioned contract carried by the assembly — an outbox event or
a typed command — never a Python import. The authority statements in § 4 are
unchanged: billing still owns rated-obligation acceptance and the subledger,
subscriptions still submit immutable rated obligations, collections still read
receivables and request consequences. What changes is that "depends on" meant
*imports* and now means *is wired to by the composition root*.

### A2. All three are dual-plane modules (ADR-0023)

Sub needs the tenant plane; the vendor control plane is platform-only and has no
`tenant_id` to give. Each of these modules therefore ships **one
persistence-free behaviour engine** and **two declared persistence planes** —
`tables` (tenant: `tenant_id NOT NULL`, RLS ENABLEd and FORCEd, composite
uniques) and `platform_tables` (control plane: no tenant column, no RLS, `REVOKE
ALL` from the tenant app role, `USAGE` plus row DML for the online platform
role) — with separate repository and link helpers per plane, and **no foreign
key crossing the planes** in either direction.

The plane is declared on the manifest, never inferred. ADR-0023's rejected
workarounds apply unchanged and with force here: a nullable `tenant_id` on an
invoice, a sentinel vendor tenant, and a polymorphic scope column on a
receivable are all refused by the gate.

This is not speculative second-plane work under ADR-0006 § 5: both named
assemblies exist today and each needs its own plane.

### A3. Provider transport belongs to the Integrator, not to billing

ADR-0024 § 6 (amended 2026-08-13, after this ADR) requires every product runtime
to retire direct provider clients, provider credentials, provider webhook
signature verification, connector scheduling, checkpoints, and delivery retries
into the independently deployed Integrator. § 4's "payment-provider integration"
predates that decision and is corrected below.

The split is:

- **`dotmac-billing` owns the money decisions** — payment intent as a domain
  fact, acceptance of a typed settlement observation, allocation,
  deallocation, reversal, refund, coverage, and every financial consequence.
- **A payment connector plugin inside the Integrator owns the transport** —
  the PSP client, credentials, webhook signature verification, raw ingress and
  its receipt evidence, dedupe, retries, and checkpoints. It publishes a
  provider-neutral capability message; billing never learns which PSP produced
  it.

`PaymentProvider` (P6/C5) survives as a **provider-neutral domain port with a
fake**, which is what makes billing developable with no PSP credentials. It does
not survive as a place to put a Paystack or Stripe client. C5's forbidden-name
check is unchanged and now also bites in the module that would have hosted the
client.

The same reading applies to P2's inbound webhook receiver: for the billing path
it is Integrator work, not a billing facility, and billing is its consumer
rather than its owner.

### A4. A2b is resolved into `dotmac-subscriptions`

`docs/inventories/fleet-decomposition-matrix.md` originally recorded **A2 —
sales-agreements + commercial-offers, Sub ↔ Vendor CP** as unadjudicated in both
directions. The 2026-08-12 review split that bucket, and Michael resolved its
remaining half on 2026-08-14.

Michael ruled on 2026-08-12, during the vendor-CP composable-parts review, that
A2 **splits in two**:

- **(a) vendor↔operator commercial contracts stay a distinct module.** They are
  not subscription contracts, and `dotmac-subscriptions` may not claim them.
- **(b) the reusable recurring-commercial core belongs to
  `dotmac-subscriptions`.** It owns stable offers, immutable offer/price and
  subscription-contract versions, cadence, proration, and recurring charge
  occurrences on explicit tenant and platform planes. Vendor CP adopts the
  platform plane first; Sub adopts the tenant plane second.

The same review disposed of the argument the matrix row rests on. Sub's and the
vendor's `offer_versions` were called "the same shape built twice" on the
strength of a name collision; reading both models showed they are not — 5
business columns against ~18 plus 6 relationships, price embedded against a
separate `offer_version_prices`, standalone `offer_code`+`version` against an FK
to `catalog_offers`, and `capability_codes` against service/access type, price
basis, billing cycle, contract term, region zone, usage allowance, SLA profile,
policy set and effective dating. **A table-name collision is a prompt to compare
columns, not evidence of a shared contract** — ADR-0006 § 5a's failure mode,
found again.

The audit conclusion is checked in at
`docs/inventories/subscriptions-sources.md`. It does not union the two product
schemas. Vendor's `capability_codes` and Sub's ISP service/access, region,
usage, SLA, policy and RADIUS terms stay in product-owned plane-specific link
tables. Sub is the product-first source for contract versioning, cadence,
proration and recurrence; Vendor's exact-money, immutable-publish behavior is a
mandatory port delta.

The source `BillingObligation` is also split at the owner boundary. Subscriptions
owns a unique, replayable recurring charge occurrence and submits an immutable
pre-tax rated-obligation command/event. Billing owns acceptance, applied tax/FX,
receivable, allocation and financial resolution. The subscriptions row is not a
second financial obligation authority.

A2 is therefore no longer an additional implementation gate. ADR-0017 P11,
P3 durable timers, and the assembly-wired billing input remain real gates. The
focused execution plan is
`docs/superpowers/plans/2026-08-14-subscriptions-vendor-sub-adoption.md`.

### A5. P8b (object storage) is met; rendering, numbering, and timers are not

The prerequisite table in the implementation plan lists object storage as
gap-listed and blocked. That is stale: `dotmac-files` (ADR-0022) now owns stored
bytes on both planes and publishes the `StorageProvider` seam.

Billing's obligation is therefore narrower than the plan implied: it emits
**immutable document facts** and does not render, does not store bytes, and does
not import `dotmac-files` (per A1). The assembly connects a rendering owner
(P8a, still a genuine gap) to `dotmac-files`. P3 durable timers, P4 document
numbering, and P8a rendering remain real, gap-listed prerequisites.

**Clarification, 2026-08-14.** An earlier draft of this paragraph said billing
emits document facts "and stops". That was too strong and is corrected here,
because it would have forbidden something billing must in fact own. Emitting the
fact is where billing's involvement in *producing* the document ends. It is not
where billing's involvement in the document ends: **the relation "this stored
artifact is the official document for invoice X at fact version Y" is
invoice-domain meaning and belongs to billing**, which is also what ADR-0022 § 2
requires — the domain owns that relation and `dotmac-files` may not, since it
owns opaque bytes and their physical lifecycle only. Billing therefore holds a
plain identifier column with no foreign key and no import, and records it through
its own typed command. Rendering, template selection and byte storage remain
outside billing entirely.

### A6. The per-application composition profile

The original decision named a first adopter but never said which applications
install these modules at all. They are optional distributions and most of the
fleet installs none of them.

| Application | Composition |
|---|---|
| **Starter** | Owns the packages, contracts, conformance tests, and the reference assembly. It holds no commercial rows of its own. |
| **Sub** | Tenant-plane subscriptions + billing + collections; metering later. The qualifying product-first source, and an adopter only through measured shadow-and-cutover. |
| **Vendor CP** | Platform-plane subscriptions + billing + collections. Retains its commercial accounts, approval, allocation/licensing, and consequence execution. **No fake tenant** (ADR-0023). |
| **ERP** | Installs none of the three. Remains the GL and statutory-accounting authority and consumes billing's immutable accounting facts (§ 2). |
| **CRM** | Installs none. Acquires no new commercial authority; its local sales/payment/subscription writers retire into Sub, keeping only temporary projections and adapters during consolidation. |
| **Academy** | Installs none today — there is no demonstrated paid-commerce owner. Compose later only when a real paid-course slice exists. |
| **Workspace** | No billing state. Consumes entitlement and application-access projections only (ADR-0021). |
| **Integrator** | No billing decisions and no billing state. Hosts the PSP/payment connector plugins and their transport evidence (A3). |

Two rows are load-bearing and easy to get wrong later: ERP installing billing
would recreate the shadow-GL this ADR rejects, and Academy installing it would
be supply-pushed persistence of exactly the kind ADR-0017 measured.

### A7. Rulings, 2026-08-14

Decided by Michael after the six-workstream evidence batch. These are decisions,
not proposals; the specs they govern are updated to match.

| Decision | Ruling |
|---|---|
| **Money persistence** | `NUMERIC(20,6)`, uppercase ISO currency, persisted minor-unit precision. Decimal strings remain a **wire format only** — Vendor's `String(40)` storage does not survive extraction. |
| **ERP accounting transport** | Billing durable outbox → idempotent ERP inbox. Migrated explicitly from ERP pull. **The two never run as accounting writers at the same time.** |
| **Artifact mismatch** | **Strict.** A different semantic projection cannot replace an issued invoice's official artifact. There is no "cosmetic-only" exemption, because nothing can verify a diff is cosmetic — an exemption whose premise is unenforceable is refused under ADR-0018. |
| **Finance vocabulary** | `external_finance`. `manual_erp` retires. |

**The accounting migration is a sequence, and its order is the decision.** Pull
remains authoritative → push shadow comparison → single watermark cutover →
disable pull posting → enable inbox posting → retire the pull decision path. A
pull reconciler may afterwards detect gaps and request replay, but **it may never
post independently** — that would restore the second writer the sequence exists
to remove. This resolves the contradiction the adoption workstream found between
billing's push spec and ERP's checked-in pull-only contract: ownership was never
in dispute, transport was, and the transport migrates rather than coexisting.

**Provider metadata is never a routing input.** The Integrator is the sole
transport owner per credential set, and tenant, product, invoice and account are
chosen from a trusted binding established *before* provider I/O. Anything a payer
can influence at checkout cannot select what a settlement lands against.

Two facility owners were named at the same time and are recorded in ADR-0017's
2026-08-14 amendment rather than here, because they are adoption-sequence
decisions: **P3 durable timers** become `dotmac_kernel.durable_timers`, extracted
product-first from Sub; **P4 document numbering** becomes a new stateful,
dual-plane `dotmac-numbering` module, extracted product-first from ERP.
(P3's owner was corrected on 2026-08-15 to the `dotmac-durable-timers` MODULE,
reusing the kernel outbox/relay for claiming — ADR-0017's 2026-08-15 amendment
and ADR-0030 § 4a. P4 shipped as described.)

### What this amendment does not do

It does not lift ADR-0017's moratorium, does not claim P11, does not create a
package, namespace, lineage, or dossier, and does not grant these modules the
owner-directed exception `dotmac-approvals` received under ADR-0026. § 6
remains the gate.

## Context

The fleet already contains a mature billing implementation, but not a shared
one. Sub has 66 money-domain tables, roughly 74,000 lines of billing service
code, and 174 money-domain test files. ERP has the stronger payment-coverage
owner and tax/FX structure, while the vendor control plane has contracts but no
invoice writer. The starter kernel has exact money values and workflow
substrate, but no money-domain tables.

That evidence resolves the source question but leaves two ownership questions:

1. whether the operational customer subledger is shared or product-owned; and
2. whether billing, subscriptions, and collections are one installable module
   or independently installable modules.

The first question is dangerous if phrased only as "shared or local". A
customer receivables ledger and a general ledger are not interchangeable.
Sharing the former must not create a second chart of accounts, journal, fiscal
period, or statutory accounting authority beside ERP.

The second question is an authority question as much as packaging. A product
may invoice one-off work without selling subscriptions, and may collect money
without running an automated dunning policy. Installing one capability must not
silently install the other two or let their migrations share a lineage.

## Decision

### 1. `dotmac-billing` owns the operational receivables subledger

The shared owner is the optional `dotmac-billing` distribution, not the kernel
and not a product-local service. Its authoritative scope is:

- invoices and credit notes;
- payment, credit, and refund facts received through the selected commercial
  authority;
- allocations, deallocations, reversals, and refunds;
- immutable posting groups carrying typed receivable and funding-position
  effects; and
- separately derived per-currency collectible receivable, available customer
  credit, and prepaid funding positions.

The word **subledger** in this ADR means that operational scope only. It does
not include a chart of accounts, journal-entry approval, fiscal periods,
statutory books, financial statements, account mapping, tax returns, treasury,
or general-ledger reconciliation.

`dotmac-billing` is an optional stateful module with its own `mod_<short>`
schema and migration lineage. Its tables never enter `dotmac-kernel`.

### 2. ERP remains the current sole general-ledger and statutory-accounting owner

Billing emits immutable, versioned accounting facts after its own transaction
commits. ERP consumes those facts idempotently, maps them to ERP-owned accounts
and fiscal dimensions, and posts them under ERP policy. There is no synchronous
cross-database transaction and no billing-side fallback journal.

The integration contract carries a stable source identity and source version.
ERP can reconcile and replay from those authoritative inputs; it does not ask
billing to maintain an ERP-shaped shadow journal. Conversely, ERP never writes
billing allocations or operational customer positions.

ERP is the current back-office finance authority, not a mandatory product
dependency. If a deployment replaces ERP with another finance system, that
system receives the same explicit authority boundary; the general ledger does
not fall back into billing.

### 3. One deployment has one invoice and receivables authority

The deployment profile selects exactly one commercial authority:

- **internal invoicing** — `dotmac-billing` owns invoices and the operational
  receivables subledger;
- **provider-owned invoicing** — the provider owns invoice and settlement
  facts; `dotmac-billing` may ingest an idempotent local projection needed for
  product operation, but the projection is labelled with its external source
  and is never an independent decision authority; or
- **manual/ERP invoicing** — the external finance system owns invoicing and
  receivables, and the local billing invoice/subledger writer is disabled.

The assembly refuses to boot with two authorities. A cache, webhook copy, or
reporting projection does not acquire decision authority merely because it is
local.

### 4. Billing, subscriptions, and collections are three distributions

*Corrected by amendment A1/A3 above — the original text drew subscriptions and
collections as importers of billing, and gave billing the payment-provider
client.* The three are peers over the kernel, wired by the consuming assembly:

```text
dotmac-subscriptions ──┐
dotmac-billing ────────┼──> dotmac-kernel
dotmac-collections ────┘

subscriptions --obligation command--> billing --receivable fact--> collections
```

- `dotmac-billing` owns rated-obligation acceptance, invoices and credit notes,
  operational receivables, payment intents and accepted settlement facts,
  allocations, coverage, and applied tax/FX snapshots. It imports neither
  subscriptions nor collections — and holds no PSP client, credential, or
  webhook verifier, which are Integrator connector-plugin concerns (A3).
- `dotmac-subscriptions` owns stable offers, immutable offer/price and
  subscription-contract versions, cadence, proration, and fixed recurring
  charge occurrences. It submits immutable pre-tax rated obligations to
  billing through the assembly, never by importing it. Product owners project
  commercial outcomes into their own service, allocation, licensing, and
  entitlement state.
- `dotmac-collections` owns dunning cases, versioned policy ladders, payment
  arrangements, grace, and consequence requests. It reads billing-owned
  receivables through a published contract and never writes service or
  entitlement state directly.

Each declares both persistence planes per ADR-0023 (A2).

Usage metering and usage rating are a later fourth module. They submit rated
usage obligations through the same billing contract; billing does not absorb a
meter merely because it can invoice the result.

Each stateful distribution owns one namespace, one migration lineage, and one
set of canonical writers. Sharing a release repository does not merge those
owners.

### 5. The composability contract is binding

The implementation plan's C1–C10 contract is the required shape for these
modules: cadence is a value object, collection timing is one field rather than
parallel prepaid/postpaid engines, extensible vocabularies are declaration
registries, dunning policy is versioned data, externals are provider seams with
fakes, money is exact and version-stamped, entitlements are projections,
receivables and funding stay separate, and obligation identity is enforced by
a database uniqueness constraint.

Source implementations are ported with their parity tests, but known
non-conformances in the inventory are corrected at the shared boundary rather
than preserved as compatibility behavior.

### 6. Adoption sequencing remains governed by ADR-0017

This decision authorizes a boundary, not an implementation start. No package
directory or persistence lineage lands until ADR-0017 permits it. Permitted
preparatory work must have a named adopter and retire a local owner; a pure
contract with no consumer still counts as work in progress.

The three modules do not share every prerequisite. All wait on P11;
subscriptions additionally needs P3 durable timers and a released, assembly-
wired billing input before recurring output becomes effective. The A2 source
audit is complete (amendment A4) and is no longer a gate.

After the kernel-lineage gate, the vendor control plane is the recommended
first adopter of the billing module because it has a live invoicing need and no
invoice rows or writer to migrate. This does not replace ADR-0017's decision
that Sub is the first adopter of **kernel persistence**. Sub remains the
qualifying source for most billing behavior and follows through a measured
shadow-and-cutover migration.

## Consequences

- Products can install one-off billing without a subscription engine and can
  omit automated collections without forking billing.
- The shared subledger becomes the single operational writer wherever internal
  billing is selected; product-local allocation and balance writers retire at
  cutover.
- ERP receives accounting facts but keeps every accounting decision it already
  owns. A shared billing module cannot become a shadow ERP.
- Manual/ERP invoicing remains a supported replacement, not a second mode that
  runs beside internal invoicing.
- Provider-owned invoicing requires explicit projection provenance and drift
  reconciliation because the local rows are observations, not authority.
- Package and schema boundaries carry some coordination cost, but they make the
  optionality and canonical-writer rules mechanically testable.

## Rejected alternatives

**Put the subledger in the kernel.** Receivables are optional domain state, not
a universal facility. This would install money-domain tables in products that
do not bill and would violate ADR-0006's module boundary.

**Keep a subledger in every product.** Sub already supplies the qualifying
implementation and the fleet already has divergent balance and allocation
paths. Local copies would retain several writers and defeat product-first
extraction.

**Let ERP own customer receivables in every profile.** That would make products
dependent on one replaceable back-office system and prevent provider-owned or
internal billing profiles from operating independently.

**Put a general ledger in billing.** That creates a shadow ERP and a second
owner for journals, periods, account mappings, and statutory accounting.

**Ship one commercial mega-module.** One-off invoicing would acquire catalog,
renewal, entitlement, timer, and dunning state it does not need; independent
deployment-profile choices would become flags inside one schema and lineage.

**Make subscriptions the dependency root.** One-off invoices and externally
rated obligations do not require a subscription. Billing is the smaller common
contract.

## Enforcement required with implementation

The first code slice must include sensitivity-proven checks that:

- the three packages import neither each other nor a consuming assembly, and
  every cross-module outcome travels a published contract the assembly wires
  (ADR-0024 § 2; the existing *Modules are independent of each other* contract);
- each module declares both persistence planes, with RLS FORCEd on the tenant
  plane, `REVOKE ALL` from the tenant app role plus a reachable online platform
  role on the platform plane, and no FK crossing them (ADR-0023);
- no module contains a PSP client, provider credential, provider webhook
  signature verifier, or connector retry/checkpoint engine, and no provider or
  currency name appears as an identifier or default (C5, ADR-0024 § 6/§ 7);
- no billing model or service declares general-ledger concepts;
- one deployment profile cannot bind two commercial authorities;
- manual/ERP mode cannot write the local invoice or receivables subledger;
- collections changes consequences only through owning-service requests;
- receivable, available credit, and prepaid funding remain distinct;
- accounting facts and obligations carry stable identities and versions; and
- a recurring charge occurrence cannot acquire receivable/settlement state,
  while a billing obligation cannot decide cadence, coverage or proration; and
- every stateful module uses its allocated schema and independent lineage.
