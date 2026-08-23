# `dotmac-sales` extraction dossier

**Status:** Source audit and contract complete; P11 met; implementation authorized
**As of:** 2026-08-19
**Owner:** Dotmac Sales domain; decision owner Michael
**Source mode:** `product-first`
**Qualifying source:** Sub
`f64946fc451ba94a1d4c8f0a61b7831367d5b598`
**Parity/retirement source:** CRM
`57e112f0757edcee6b9ad625ee3e13ebff5c7d71`
**Requirements-only source:** ERP
`2749ec5396cbbd7a1132b394e85855a1d133a7cd`
**Destination assembly:** Starter
`7828697ef11fb1ae765a5397dfa7dc221ae6207a`
**First consumer/cutover:** Sub
**Decision:** [ADR-0033](../adr/0033-sales-authority-stops-at-an-accepted-quote.md)

This is the pre-package input to
`packages/dotmac-sales/EXTRACTION.toml`. Rule 24 requires this evidence before
implementation. P11 is now met, so the package dossier must carry these exact
pins/paths/tests/consumers and may change them only through a reviewed source
re-audit.

## Owned contract

The module owns tenant-scoped Leads, Pipelines, opportunity Stages, Quote
authoring and lines, Quote discount evidence, Quote lifecycle through
acceptance, accepted-snapshot immutability and
`AcceptedQuoteHandoffV1` publication. The snapshot includes exact currency
minor units, accepted price/terms/specification provenance, component taxes and
finite fulfillment-eligibility requirement membership.

The module owns the decision “this exact commercial snapshot was accepted.” It
does not own the downstream decision “create/advance an Order,” customer-account
conversion, project/field execution, billing, provisioning or service
activation.

Sales owns the finite requirement membership accepted with the Quote. Orders
owns the later fulfillment-eligibility decision over explicitly addressed
owner evidence; the assembly only translates the handoff.

### Public command surface

The initial typed surface is limited to:

- Pipeline create/update/deactivate and Stage create/update/reorder/deactivate;
- Lead create/update/transition/assign Pipeline+Stage;
- append or bind immutable acquisition origin by exact source identity;
- Quote author/update/deactivate plus line replacement under a locked mutable
  Quote;
- apply/change/remove a Quote-level discount with command fingerprint;
- mark sent/rejected/expired through legal lifecycle transitions;
- accept Quote exactly once; and
- report/reconcile module-owned drift.

List/detail/Kanban projections are side-effect-free readers over the same
owner. Routes, jobs, imports, webhooks and delivery adapters are not public
domain commands.

### Public output surface

The only acceptance consequence published by the module is the versioned
product-neutral `AcceptedQuoteHandoffV1` defined in
[`sales-parity-and-canaries.md`](sales-parity-and-canaries.md). It is staged
atomically with acceptance and delivered through the assembly's installed
owner-output mechanism.

No public type names a Subscriber, SalesOrder, Project, WorkOrder, invoice,
ServiceOrder, Subscription, campaign/provider or HTTP response.

## Product seams

Generalisation is confined to typed seams:

| Seam | Input/output | Owner outside module |
| --- | --- | --- |
| `SalesActorPort` | validates tenant-local actor and returns opaque actor ref/label snapshot | assembly authorization/identity |
| `SalesSubjectPort` | validates/binds an opaque prospect/customer subject ref | Party/customer-account owner |
| `PricingSnapshotPort` | resolves optional catalogue/offer/tax inputs to immutable, provider-neutral line snapshots | catalogue/pricing owner |
| `SalesClock` | aware UTC decision instant | assembly/kernel clock |
| owner-output dispatcher registration | delivers committed handoff bytes and receipts result | assembly/event infrastructure |

Ports validate facts or return snapshots. They do not let sales write another
owner's rows, call a provider, or make a downstream consequence authoritative.
Provider identity and wire mapping stay in Integrator connector plugins.

## Persistence contract

The module is tenant-plane only. Its future `ModuleManifest` declares one
namespace/lineage and every table it owns; this dossier deliberately does not
pre-allocate a `short_code` or migration prefix before implementation authority
exists.

Candidate owned resources, whose final names come from the implementation
manifest, are:

- Pipelines and Pipeline Stages;
- Leads and immutable Lead origins;
- Quotes and Quote lines;
- append-only Quote discount revisions; and
- accepted-Quote outputs/idempotency records through kernel facilities, not a
  second local outbox/idempotency engine.

Every row is tenant-scoped. All same-module references are tenant-composite.
The creating migration enables and forces RLS, creates policies/grants, and
qualifies its schema. There is no platform plane and no supported dual-plane
selection.

## Exact source material

The mandatory Sub paths and test suites are enumerated in
[`sales-sources.md`](sales-sources.md). The exact caller/writer set is
[`sales-caller-inventory.md`](sales-caller-inventory.md). The behavior mapping
and red-first canaries are
[`sales-parity-and-canaries.md`](sales-parity-and-canaries.md).

The extraction starts by moving Sub code and parity cases, then changing only:

1. imports/types at declared product seams;
2. tenancy, schema qualification, RLS and grants;
3. transaction completion to `dotmac_kernel.db` authority;
4. accepted-snapshot catalog immutability;
5. the accepted-Quote-only output boundary; and
6. error/event vocabulary needed for the stable public contract.

Any other behavior change needs an explicit parity-row disposition and test.

## Drift proof

Before Sub's write flip, the module and Sub source projection are compared by:

- per-resource count and key-set equality;
- full-column, typed, domain-separated digests at declared encoding version;
- exact Decimal/currency totals and ordered immutable line snapshots;
- Lead status/Pipeline/Stage and origin-fingerprint equality;
- Quote lifecycle/expiry/acceptance instant and discount-revision equality;
- accepted handoff fingerprint equality; and
- report-only repeatability with zero writes.

The sealing cutover follows ADR-0031: lock the legacy sales tables against
writers, observe and verify inside the same transaction that switches
authority, verify effective privileges, and roll everything back on mismatch.
A prior report is rehearsal evidence, not cutover authorization.

## Consumer and retirement contract

### Sub first cutover

1. install/pin the released module and compose its lineage;
2. backfill tenant-aware module rows without changing current authority;
3. shadow reads and compare owner-command results without dual writes;
4. seal the authority switch;
5. route all Sub sales commands/readers through the module;
6. reconcile and remove/gate Sub local tables/services/writers; and
7. retain a documented rollback window that does not re-enable two writers.

### CRM retirement

After Sub/module authority is proven, migrate each CRM API/web/import/portal
caller, freeze CRM writers, reconcile data, verify traffic and delete the
corresponding routes/services. CRM does not install the module as an independent
sales authority; applications synchronize data through versioned APIs/outputs.

### Local-copy retirement gate

Extraction is incomplete until:

- Sub no longer constructs or directly mutates local sales rows outside the
  module migration/adoption adapter;
- CRM has no Lead/Pipeline/Stage/Quote writer, writer job or accepted-Quote
  webhook decision path;
- all corresponding CRM routes have passed the checked-in retirement ledger;
- shadow/reconciliation evidence is clean;
- no fallback can silently resume the old writer; and
- the writer detector baseline is zero and sensitive.

## Gates

| Gate | State at this dossier | Required evidence |
| --- | --- | --- |
| Source audit and exact pins | **MET** | this dossier and linked inventories |
| Ownership boundary | **MET** | ADR-0033 and approved Sub SOT amendment |
| Campaign owner | **NOT APPLICABLE / UNVERIFIED** | separate audit; cannot block or be absorbed into sales |
| Retention owner | **UNRESOLVED / OUT OF SCOPE** | explicit future owner decision |
| P11 product production lineage | **MET** | accepted checked-in record in `p11-adoption-status.md`; Starter merge `ae508e1173b8643a4031936cc32cc411a6395f26` |
| Package/lineage/canaries | **AUTHORIZED / NOT STARTED** | begins red-first from the frozen C-SALES contract |
| Sub adoption | **NOT STARTED** | released package plus backfill/shadow/sealed cutover |
| CRM writer retirement | **PLANNED** | adoption plus ledger gates and source deletion |
| Production cutover/deletion | **NOT AUTHORIZED** | separate explicit authorization |

The pre-P11 prohibition correctly prevented an empty placeholder package. The
accepted P11 record now authorizes canary-first implementation; it does not
advance any module-specific release, adoption, cutover or retirement gate.
