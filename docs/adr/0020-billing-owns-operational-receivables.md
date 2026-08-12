# ADR 0020 — Billing owns operational receivables; commercial modules stay separate

**Status:** Accepted
**Date:** 2026-08-12
**Decision owner:** Michael
**Extends:** ADR-0003's commercial-module boundary, ADR-0006's extraction and
module-lineage rules, ADR-0008's declaration registries, ADR-0016's derived
coverage rule, and ADR-0017's adoption sequence
**Evidence:** `docs/inventories/billing-sources.md`
**Implementation plan:**
`docs/superpowers/plans/2026-08-11-billing-subscriptions-collections.md`

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

The dependency graph is:

```text
dotmac-subscriptions ──┐
                       ├──> dotmac-billing ──> dotmac-kernel
dotmac-collections ────┘
```

- `dotmac-billing` owns rated-obligation acceptance, invoices and credit notes,
  operational receivables, payment-provider integration, allocations,
  coverage, and applied tax/FX snapshots. It imports neither subscriptions nor
  collections.
- `dotmac-subscriptions` owns offers, immutable contract/price versions,
  cadence, lifecycle, proration, fixed recurring charge generation, and
  entitlement projection. It submits immutable rated obligations to billing.
- `dotmac-collections` owns dunning cases, versioned policy ladders, payment
  arrangements, grace, and consequence requests. It reads billing-owned
  receivables and never writes service or entitlement state directly.

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

- the three package dependency directions match this ADR;
- no billing model or service declares general-ledger concepts;
- one deployment profile cannot bind two commercial authorities;
- manual/ERP mode cannot write the local invoice or receivables subledger;
- collections changes consequences only through owning-service requests;
- receivable, available credit, and prepaid funding remain distinct;
- accounting facts and obligations carry stable identities and versions; and
- every stateful module uses its allocated schema and independent lineage.
