# ADR-0030: Cloud commerce is composed from complete domain owners

**Status:** Accepted
**Date:** 2026-08-15
**Decision owner:** Michael
**Scope:** Dotmac Cloud and the reusable commerce/service modules it composes;
the single-owner and independent-module rules apply FLEET-WIDE.
**Amends:** [ADR-0017](0017-adoption-is-the-scarce-resource.md) by granting a
named owner-directed implementation exception for the seven unbuilt business
modules and three enabling owners named in this decision. It does not declare
P11 met and does not authorize any other gap-list candidate.
**Extends:** [ADR-0020](0020-billing-owns-operational-receivables.md) and
[ADR-0024](0024-apps-compose-by-synchronizing-data.md).
**Evidence:**
[`cloud-commerce-owner-sources.md`](../inventories/cloud-commerce-owner-sources.md)

## Context

Dotmac Cloud must not become a Blesta-shaped application with Dotmac names. It
must compose business owners that can also be installed by Sub, Vendor CP, or a
future Dotmac application. Blesta, a PSP, a registrar and a hosting panel are
replaceable transports; none is allowed into a product schema, lifecycle enum,
or business decision.

The earlier Cloud solution separated the right concerns but described them as a
Cloud application's internal feature list. That is not enough for build-once
reuse. A feature package that is only complete when imported beside six sibling
packages is a distributed monolith, not a composable unit.

Michael directed a stronger rule on 2026-08-15: each named owner is built and
verified individually. This decision defines what “complete” means, fixes the
ownership matrix, and sequences the work without creating parallel owners.

## Decision

### 1. The business owner matrix

| Fact or decision | Sole owner |
|---|---|
| Stable offer and immutable offer/price versions | `dotmac-subscriptions` |
| Subscription contract, cadence, proration and recurring charge occurrence | `dotmac-subscriptions` |
| Customer order and immutable line snapshots | `dotmac-orders` |
| Rated-obligation acceptance, invoice, operational receivable, settlement acceptance and allocation | `dotmac-billing` |
| PSP credentials, provider webhook verification, wire mapping and delivery transport | a PSP connector plugin run by the Integrator |
| Dunning case, versioned grace/escalation policy and delinquency-driven consequence request | `dotmac-collections` |
| Whether a requested service transition is permitted, and the actual transition | the service lifecycle owner — initially `dotmac-domains` or `dotmac-hosting` |
| Cross-owner fulfillment saga, step attempts, compensation and convergence | `dotmac-fulfillment` |
| Dotmac domain-service lifecycle and interpretation of registrar observations | `dotmac-domains` |
| Dotmac hosting-service lifecycle and interpretation of panel observations | `dotmac-hosting` |
| General ledger, journals, fiscal periods, statutory accounting and tax returns | Dotmac ERP |

The Collections row is deliberately narrow. Customer cancellation, abuse,
security and operator action have their own initiating owners. Collections can
request only a consequence justified by its own delinquency case. A request is
not permission and not a state write: Domains or Hosting locks and revalidates
its own facts, applies or refuses the transition, and returns a receipted
outcome.

Registrar and panel facts remain external observations. `dotmac-domains` and
`dotmac-hosting` own Dotmac's desired state, policy decisions and customer-visible
lifecycle; connector plugins own provider I/O. A collector records a typed,
deduplicated observation, a local resolver derives drift, and the lifecycle
owner decides any consequence. A provider callback never assigns a Dotmac
service status directly.

### 2. Complete means independently releasable for a declared contract

“Fully built” does not mean every future feature. It means the module's declared
version-one contract is complete and can be installed, migrated, tested and
operated without importing another business module.

Every owner must ship, as applicable:

1. a precise positive contract and an equally explicit `NOT` boundary;
2. one lifecycle/decision engine and one canonical writer per owned state;
3. typed commands, facts, observations, outcomes and stable error classes;
4. idempotency identities and conflict rules, with non-transactional effects
   leaving through the durable outbox;
5. its own models, namespace and migration lineage where stateful, with the
   declared persistence plane and live PostgreSQL isolation canaries;
6. a provider-free fake/conformance kit for every port it publishes or consumes;
7. reconciliation that can rebuild derived state and repair missed delivery;
8. source parity tests plus fresh invariant, failure, replay, concurrency and
   drift tests;
9. package/wheel, manifest, migration, import-independence and public-surface
   verification; and
10. an `EXTRACTION.toml` dossier naming source code, preserved tests, consumer,
    first cutover, shadow proof and local-writer retirement gate.

“Complete package” and “adopted owner” are separate claims. A package becomes
complete when the contract above passes. It becomes adopted only when a real
application runs the exact version, switches authority through a measured
shadow/cutover, and retires the displaced local writer. A green test suite may
not be reported as a cutover.

### 3. Modules are peers; the assembly composes them

No module imports a sibling or reads its tables. The consuming application
translates one owner's published output into another owner's input and records
the receipt. The minimum Cloud flow is:

```text
subscriptions --immutable offer/price fact-----> Cloud assembly
orders --------order + line snapshots----------> Cloud assembly
billing -------coverage/receivable facts--------> Cloud assembly
domains -------domain command outcomes----------> Cloud assembly
hosting -------hosting command outcomes---------> Cloud assembly
collections ---delinquency consequence request--> Cloud assembly
fulfillment <--order/coverage/outcomes----------> Cloud assembly
                         |
                         v
                 Integrator capabilities
              PSP / registrar / panel plugins
```

The arrows are versioned commands/events through assembly adapters, never Python
dependencies between business modules. Provider names, endpoints, credentials,
webhook signatures, retry checkpoints and wire payloads stay in Integrator
connector distributions. In particular, no `blesta_client_id`, Blesta enum, or
Blesta status belongs in any module named above. A Blesta connector may exist,
but choosing it is an installation binding, not an architecture branch.

### 4. The matrix needs three enabling owners

The business matrix is correct but is not an executable dependency graph by
itself. Three already-audited cross-cutting capabilities must be delivered
without being absorbed into a business owner:

| Enabling capability | Owner | Why it is separate |
|---|---|---|
| Generation-safe due work and wake-up | `dotmac_kernel.durable_timers` | subscriptions and collections must not each invent a scheduler ledger |
| Concurrency-safe document series | `dotmac-numbering` | billing owns what an invoice number means, not the reusable allocation engine |
| Deterministic issued-document bytes | `dotmac-document-rendering` | billing emits immutable facts; rendering produces bytes; `dotmac-files` stores bytes |

ADR-0017 already names the first two owners, and the rendering dossier names the
third. This ADR records them as prerequisites of the directed Cloud commerce
programme. It does not move invoice meaning out of Billing or timers into
Collections.

### 5. Build order

Work completes one owner before opening the next package, except that adopter
integration may run after a package reaches its independent completion gate.

1. **Close the shared prerequisites:** `dotmac-numbering`, then
   `dotmac_kernel.durable_timers`. Build `dotmac-document-rendering` after
   Billing freezes `InvoiceDocumentFactV1`; its source audit is already present.
2. **Build `dotmac-billing`.** It is the commercial spine and already has the
   deepest source audit, extraction dossier, parity ledger and first-adopter
   plan. Freeze its obligation, settlement, allocation, receivable, coverage
   and document-fact contracts before downstream assembly wiring.
3. **Build `dotmac-subscriptions`.** Port Sub's cadence/contract/recurrence
   behavior and Vendor CP's immutable publication deltas. It emits a recurring
   charge occurrence; the Cloud assembly translates that output into Billing's
   accepted-obligation input.
4. **Build `dotmac-orders`.** Port the product-neutral order aggregate and
   immutable line-snapshot behavior from Sub. ERP supplies physical-order
   requirements, not the shared lifecycle; CRM's parallel owner is retiring.
5. **Build `dotmac-domains`, then `dotmac-hosting`.** Both are
   greenfield-after-inventory and therefore start from lifecycle contracts and
   failure/reconciliation canaries, not a copied provider API. Their command
   surfaces must stabilize before the saga can depend on them.
6. **Build `dotmac-collections`.** Port Sub's live and target dunning evidence,
   but emit only typed delinquency consequence requests. No service status or
   provider call exists in this package.
7. **Build `dotmac-fulfillment`.** Port Sub's idempotent run/step/readiness and
   sole-writer patterns, while excluding installations, appointments, OLT,
   RADIUS and ISP activation. It is deliberately last among domain modules
   because a saga built before its participant contracts stabilise merely
   hardcodes guesses about them.
8. **Build and certify connector plugins** against the stable owner ports: PSP,
   registrar and hosting-panel plugins. Blesta, if retained temporarily, is one
   optional connector profile and no more.
9. **Compose Dotmac Cloud and prove the journey:** offer → order → obligation →
   independently confirmed settlement → coverage → per-line fulfillment →
   active service → renewal → delinquency request → permitted/refused service
   consequence → restoration. Test partial fulfillment and provider-success /
   callback-loss before external customers.

This order does not mean one giant release. Each numbered owner ends at its own
completion gate and can be reviewed, versioned and adopted independently.

### 6. Implementation authorization and gates

Michael's direction to start the composable Cloud modules is the named
owner-directed exception required by ADR-0017 for the three prerequisites:

- `dotmac-numbering`;
- `dotmac_kernel.durable_timers`; and
- `dotmac-document-rendering`;

and the seven business owners:

- `dotmac-billing`;
- `dotmac-subscriptions`;
- `dotmac-orders`;
- `dotmac-domains`;
- `dotmac-hosting`;
- `dotmac-collections`; and
- `dotmac-fulfillment`.

The exception removes the moratorium for those ten names only. It does not pretend
P11 is production-proven, relax live PostgreSQL migration/plane gates, create a
consumer by assertion, or allow a package before its product inventory and
`EXTRACTION.toml` are complete. Namespace allocation still occurs in the same
change that creates the stateful package.

The enabling owners' existing source rulings remain authoritative;
implementation must follow their own dossiers and named source code rather than
this ADR inventing a second contract. Where a dossier is incomplete, the
exception permits completing the audit; it does not turn missing evidence into
permission to greenfield.

### 7. Application adoption matrix

“Shared module” means one versioned distribution installed locally by each
adopter. It does not mean one shared service or database. Every application runs
its own pinned copy, migrations, authorization, transactions and rows; cross-app
views synchronize typed data under ADR-0024.

| Application | Modules it adopts | Disposition |
|---|---|---|
| **Dotmac Sub** | `dotmac-billing`, `dotmac-subscriptions`, `dotmac-collections`, `dotmac-orders`, and the generic engine of `dotmac-fulfillment` | Sub is the product-first source for these behaviors and must cut over by shadowing the module and retiring each displaced local writer. ISP catalog, RADIUS, installation, field work and network activation stay in Sub as product participants/links. |
| **Dotmac Cloud** | all seven business modules in §6 | Cloud owns its local order, receivable, subscription, service and saga rows. It is not a façade over Sub and never reads Sub's database. |
| **Vendor CP** | platform planes of Billing, Subscriptions and Collections only | Existing ADR-0020 composition; it retains vendor agreements, approvals, allocation/licensing and consequence execution. |
| **Dotmac ERP** | none of the seven | Receives immutable accounting facts and retains GL/statutory authority. ERP's physical sales-order implementation remains ERP-owned. |
| **Dotmac CRM** | none | Its parallel sales-order and commercial writers retire; customer-experience projections arrive through versioned synchronization. |
| **Integrator** | none of the business modules | Runs `dotmac-integration` and independently released PSP/registrar/panel connector plugins; holds transport evidence only. |

`dotmac-domains` and `dotmac-hosting` are Cloud-only at first because no other
application has that lifecycle. Sub may display a customer's Cloud portfolio
through a rebuildable Cloud projection or link to the Cloud portal; it does not
install those modules merely to render navigation. If Sub later becomes a real
domain/hosting lifecycle owner, that is a deliberate new local adoption, not a
shortcut through Cloud's tables.

## Consequences

- Dotmac Cloud is a composition profile, not the owner of reusable commercial
  or service lifecycles.
- Blesta can accelerate an early deployment only as a replaceable connector; it
  is never the billing or lifecycle authority in the internal-authority profile.
- Orders and fulfillment are separate. An order records what the customer
  bought; fulfillment records attempts to make it true.
- Partial fulfillment is natural because each order line has its own service
  command and outcome while the saga derives aggregate progress.
- ERP receives immutable accounting facts and remains the only GL/statutory
  accounting owner; operational invoices are not recreated in ERP.
- The first coding change after this decision is the prerequisite/source dossier
  for the first buildable owner, not seven empty package directories.

## Alternatives rejected

**One `dotmac-cloud` service owning every row.** Faster for the first demo, but
it makes the promised modules implementation folders rather than reusable
owners and makes every later extraction an authority migration.

**Blesta as the hidden lifecycle owner.** This makes provider status the real
truth and Dotmac's state a projection. Replacing Blesta would then be a data and
policy migration rather than a connector swap.

**Build all package skeletons first.** Empty manifests and interfaces create
parallel WIP, speculative contracts and no independently verified owner. The
programme completes one coherent owner at a time.

**Put fulfillment inside Orders.** The order becomes coupled to every service
type and retry policy. It also cannot express an immutable purchase snapshot
alongside a long-running, repairable saga without acquiring two authorities.

**Let Collections suspend directly.** A financial policy engine would become a
second writer of domain/hosting state and could bypass non-financial holds,
retention policy, transfer locks and destructive-action approval.
