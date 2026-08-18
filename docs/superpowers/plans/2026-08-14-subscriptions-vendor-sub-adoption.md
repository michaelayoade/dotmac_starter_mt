# `dotmac-subscriptions`: Vendor platform plane, then Sub tenant plane

**Status:** M0 package/namespace implemented on 2026-08-18; release and product
adoption remain pending
**Decision:** ADR-0020 amendment A4, 2026-08-14
**Source audit:** `docs/inventories/subscriptions-sources.md`
**Order selected by Michael:** Vendor CP platform plane first; Sub tenant plane
second

## Exit condition

The work is complete only when Vendor CP and Sub independently pin the same
exact released `dotmac-subscriptions` version, compose their own installation
of its one migration lineage, use the correct persistence plane, and retire
their local writers for every contract slice they adopt.

Vendor must no longer own an `offer_versions` writer. Sub must no longer own
generic offer/price versioning, subscription-contract versioning, cadence,
proration, or fixed-recurring occurrence generation. Vendor approval,
commercial-contract lifecycle, allocation/licensing, and Sub service/access
lifecycle remain product-owned. A module installed beside either local writer
is not a cutover.

## Non-negotiable boundary

```text
Vendor legal contract activated ─┐
                                 ├─assembly─> subscription contract/version
Sub sale/service accepted ───────┘                     |
                                                       v
                                           recurring charge occurrence
                                                       |
                                         typed immutable command/event
                                                       v
                                                dotmac-billing
                                                       |
                                  receivable / settlement / consequence facts
```

`dotmac-subscriptions`, `dotmac-billing`, and `dotmac-collections` are sibling
modules. None imports another. Each consuming assembly wires versioned commands
and events and owns any application coordinator. The subscription module never
writes an invoice, receivable, payment, licence, entitlement, RADIUS state, or
service status.

## Gates before implementation

### G0 — ADR-0017 P11 — PASSED

The original gate required Sub's checked-in adoption ledger to prove the kernel
lineage in production. ADR-0017's 2026-08-17 amendment superseded that wording
after accepting Vendor production as the platform-lineage reference; its
evidence is indexed in `docs/inventories/p11-adoption-status.md`. A prepared
branch, rehearsal, hosted tenant table, or stamped revision would still not
have met the gate.

### G1 — durable timers (P3) — COORDINATED IMPLEMENTATION EXISTS

Automatic recurrence needs the shared durable-timer facility: wake one owner
and entity at one instant, once per generation, with stale generations refused.
Port it product-first from Sub only when the scheduled Vendor adoption is
blocked on it; pair the facility with this real consumer. A cron scan over all
contracts is not a substitute.

### G2 — billing input contract — COORDINATED IMPLEMENTATION EXISTS

The assembly needs a released, provider-neutral
`AcceptRatedObligationV1`/equivalent billing input contract before the first
occurrence can become financially effective. Subscriptions may be tested with a
fake consumer, but an adopter does not enable recurring output into nowhere.
Billing applies tax/FX and owns the financial lifecycle.

### G3 — source audit and ADR

Closed by `docs/inventories/subscriptions-sources.md` and ADR-0020 A4. Vendor's
legal commercial contracts remain distinct; the reusable offer and recurring
subscription contract belong here. Reopen this boundary only through a dated
ADR amendment, not by adding a convenient FK or import.

## Module contract to build

### Public typed surface

The first release carries no product router or screen. It publishes typed
commands, results, queries, values, events, repositories, and fakes:

- `PublishOfferVersionCommand` and immutable offer/price snapshots;
- `RecordSubscriptionContractVersionCommand`, supersession, termination, and
  effective-at queries;
- `BillingCadence`, `Interval`, calendar arithmetic, and declared proration;
- `GenerateRecurringChargeCommand` and a replayable occurrence result;
- `RecurringObligationDueV1`, containing exact pre-tax rating provenance and a
  stable fingerprint;
- explicit `TenantScope` and `PlatformScope` service/repository entry points;
- separate tenant/platform offer-subject and contract-subject link helpers; and
- fakes plus a parametrized contract suite for each assembly-wired output port.

No public method accepts `tenant_id: UUID | None`, a primitive dict payload, a
product/provider mode, or a product model. No shared code branches on Vendor,
Sub, ISP plan names, capability names, providers, currencies, or deployment
profiles.

### Persistence

The package allocates its `subscriptions` short code, `su` prefix,
`subscriptions` branch label, `mod_subscriptions` schema, ledger row, manifest,
migration root and `EXTRACTION.toml` in the M0 implementation. The reference
assembly builds the package but deliberately does not select or install either
plane; Vendor CP and Sub own those explicit selections during cutover.

The first migration creates all seven tenant tables and all seven platform
tables from the source audit. It creates each plane's isolation in that same
migration. Tenant FKs and natural unique keys are composite with `tenant_id`;
platform FKs never reach the tenant plane. Product link tables live in the
assembly's schema and lineage and are generated by the correct plane helper.

The recurring occurrence uniqueness contract is, per plane:

```text
contract line lineage
+ contract version
+ declared source code / source id / source version
+ half-open service period
+ currency
```

Reusing that identity with different coverage, price, cadence, proration, or
fingerprint is a conflict, not a replay.

### Lifecycle

- Offer: draft -> published -> withdrawn. Published versions and prices never
  mutate; withdrawal blocks new selection only.
- Contract: stable identity with immutable draft/effective/superseded/ended
  versions. Effective intervals are half-open, contiguous when superseded, and
  non-overlapping by PostgreSQL constraint.
- Occurrence: scheduled -> due -> emitted, or scheduled -> cancelled before
  earning. Once emitted, a financial correction is a billing reversal/credit
  plus a new occurrence; history is never edited.

Product access suspension is deliberately absent. Collections asks the owning
Vendor/Sub service for a consequence; it does not suspend this contract as a
proxy for service state.

## M0 — canary-first package slice

G0–G2 now permit this slice; the release and adoption gates below remain:

1. Create `EXTRACTION.toml` at `audit-complete`, citing both sources and the
   exact preserved tests. Candidate consumers are Vendor CP then Sub;
   `contract_consumers = []` remains correct.
2. Write cadence/proration and lifecycle tests first. Port Sub's source code
   behind module-owned types only after each test is red for the intended
   reason.
3. Write the dual-plane schema and PostgreSQL canaries first: cross-tenant
   isolation, FORCE RLS, full platform-role revocation/reachability, composite
   tenant FKs, no cross-plane FK, fresh and upgrade migration, and concurrent
   occurrence uniqueness.
4. Add architecture sensitivity proofs: sibling-module imports, product model
   imports, nullable scope, raw DB sessions/commits, provider/currency/product
   identifiers, mutable published rows, undeclared charge/source vocabulary,
   and a fake tenant each fail the build.
5. Implement one persistence-free behaviour engine plus plane-specific models
   and repositories. Plane adapters may differ in scope and isolation only;
   lifecycle decisions and fingerprints must pass one parametrized parity
   suite.
6. Add the module to the release allowlist only when the wheel contains its
   migrations and passes clean-host installation against its exact kernel
   floor. A package that cannot be published cannot be adopted.

M0 acceptance: `make check`, `make test-unit`, composed `make migration-gate`,
fresh/upgrade PostgreSQL migration tests, live-catalog audit for both planes,
and wheel-content/clean-host tests are green. The dossier still says no
consumer.

## Cutover 1 — Vendor CP platform plane

Vendor goes first because it already has the smallest offer source and needs
the platform plane, while no recurring engine exists to retire. This is not a
greenfield offer cutover: the existing `offer_versions` rows and writer must be
migrated and removed.

### V0 — characterize before mapping

Record, from the real target environment before any write:

- offer row count, distinct codes/versions/currencies, duplicate/gapped
  versions, and invalid exact-money values;
- every `contract_lines.offer_version_id` reference and any orphan;
- capability-code declarations and undeclared values;
- contract state/count distribution and which active contracts are intended
  to recur; and
- whether each recurring contract has explicit cadence, timezone, collection
  timing, effective start, and price. Missing terms are a blocking NULL, never
  a monthly/Lagos/default-currency guess.

The classification output is checked-in migration evidence. An unclassified
active contract is a stop condition.

### V1 — pin and compose

Upgrade Vendor CP to the module's exact kernel floor, pin the exact module
release, register its manifest, and compose the module lineage through assembly
migration bindings. Use the platform session/role only. A fake tenant is a
failed cutover.

### V2 — expand and backfill the offer catalogue

Backfill each local immutable offer version into a stable platform `Offer`, one
platform `OfferVersion`, and immutable price child while preserving exact
money, source identity, version, and timestamps as provenance. Migrate
`capability_codes` into a Vendor-owned platform link table created by
`link_platform_offer_subject()` (or the final audited helper name).

Shadow reads compare every legacy and module offer snapshot. The reconciler
reports missing, extra, price, currency, version, and capability-link drift and
can idempotently repair only from the declared authoritative side.

### V3 — switch the offer writer

Vendor routes stay thin and call the module's platform service. The commercial
contract coordinator resolves a typed immutable offer snapshot through the
assembly and passes it to the contract owner; the commercial-contract module
never imports subscriptions. Contract lines retain an opaque offer-version
reference and frozen price snapshot, not a cross-module FK.

After parity is zero for the accepted window, switch one writer, disable the
legacy writer, and delete `vendor_cp.offers` models/services/routes plus their
table in an expand/contract release. A two-directional ratchet with sensitivity
proof prevents the local writer or direct offer-table read from returning.

### V4 — create platform subscription contracts

An activated legal commercial contract emits one typed assembly command per
recurring line. The module records a platform subscription contract/version;
the Vendor assembly records the product link to account/deployment/capability.
Approval state and activation evidence remain Vendor facts.

Existing active contracts are backfilled only when V0 proves complete explicit
terms. Others enter a remediation queue and remain non-recurring; no placeholder
contract is created.

### V5 — recurrence and billing handoff

For an effective contract version, schedule the next timer generation in the
same transaction. On wake, create/replay the unique platform occurrence, stage
`RecurringObligationDueV1` transactionally, and schedule the next generation.
The Vendor assembly delivers it to billing's platform input. Billing acceptance
and the occurrence id are reconciled; a swallowed delivery failure without a
named repair path is forbidden.

### V6 — Vendor acceptance

Required evidence:

- exact old/new offer parity and zero unresolved product links;
- immutable publish/idempotency tests on the platform plane;
- commercial approval remains separate from subscription activation;
- no contract, subscription, or billing action writes a product data plane;
- timer replay and concurrent generation create one occurrence and one billing
  acceptance;
- a missing cadence/price/link fails closed;
- platform tables have no tenant/RLS and are unreachable by `app_user` but
  reachable by the online platform role; and
- old offer writer/read counts reach zero and the baseline is lowered in the
  same change.

Only then change the dossier to `adopted` with Vendor as the sole contract
consumer.

## Cutover 2 — Sub tenant plane

Sub is the qualifying source and the risky cutover: hundreds of service,
catalog, billing, and provisioning callers currently read mixed generic and
ISP-specific fields. Move coherent owner slices; do not replace all references
in one release.

### S0 — cross-tenant canary and complete inventory

Write the PostgreSQL cross-tenant isolation canary before the first model or
migration change. Then measure every table, writer, direct mutation, lifecycle
reader, cadence helper, plan-change proration path, contract/line/version row,
and billing-obligation state. Classify each field as generic module state,
Sub-owned service semantics, billing-owned financial state, or historical
provenance. An unclassified field blocks migration.

### S1 — product link tables and offer shadow

Create Sub-owned tenant link tables for ISP semantics—service/access type,
region, usage allowance, SLA/policy, RADIUS/provisioning profile, speed, portal
visibility, and plan family—using the tenant offer helper. They retain product
authority; they are not copied into the module.

Backfill generic `CatalogOffer`/`OfferVersion`/`OfferVersionPrice` data into the
tenant plane. Shadow every sellable and contracted offer at an effective
instant. Ambiguous active prices, mutable live terms, missing currency, and
unversioned contracted prices become explicit remediation cohorts.

### S2 — switch generic offer ownership

Convert Sub's catalog facade into an assembly adapter over the module for
generic offer/version/price commands and over Sub's service-intent owner for ISP
links. Switch writes only after shadow parity; then remove generic writes from
`app/services/catalog/offers.py`. Keep product link writers in Sub.

### S3 — subscription contracts and versions

Map Sub's `BillingContract`/Version/Line source into the tenant module without
changing stable line lineage, source fact/version, half-open effective ranges,
price, currency, or cadence. Compare effective-at queries and supersession on
the complete active cohort. Promote the module writer only through Sub's SOT
registry/map update, then delete the local contract writer.

The Sub `Subscription` row remains service state. A tenant product link binds it
to the module contract; no module table gains subscriber, NAS, IP, RADIUS,
bundle, or service-status columns.

### S4 — cadence and proration

Route every billing boundary through the module `BillingCadence`. Retire
`BillingCycle` decision branches, `_add_months`, `_compute_next_billing_at`, and
the plan-change `_calculate_proration` owner after the following shadow matrix
is accepted:

- daily, weekly, monthly, N-month, and yearly intervals;
- month-end 29/30/31, leap years, and strict-same-day refusal;
- contract-anniversary, calendar-start, and fixed-anchor alignment;
- timezone/DST boundaries; and
- none/full/calendar-day/elapsed-time proration, including no-coverage and
  out-of-period failures.

Known customer-visible differences are measured and approved before cutover,
not normalized away to make parity green.

### S5 — split legacy obligations by owner

For every legacy `BillingObligation`, classify and migrate:

- schedule, period, coverage, price, quantity, cadence, proration, source, and
  rating fingerprint -> subscription occurrence/provenance;
- tax/FX, open/partial/resolved/cancelled/written-off, applications, and
  receivable queries -> billing; and
- any product service consequence -> Sub's owning service.

The assembly stores opaque correlation between the two new owner identities.
Neither module FKs to its sibling. Backfill and shadow reconciliation prove the
pair reconstructs the legacy fact with no double charge and no lost resolution.

### S6 — switch recurrence

Enable module timers and occurrence output for one explicitly named cohort.
Compare exact period, pre-tax amount, proration factor, source identity, and
billing acceptance to Sub's Phase-2 shadow evidence. Expand cohort only when
duplicate, missing, overlapping, and fingerprint-conflict counts are zero.

Switch the owner once per cohort, disable the matching legacy recurring scan,
and delete it. Never leave both enabled behind a runtime flag.

### S7 — consequences and reconciliation

Sub consumes effective/superseded/ended contract facts and billing outcomes
through its assembly coordinator. The local service/access owner decides
activation, suspension, expiry, RADIUS/provisioning, and customer timeline
consequences. A named reconciler detects and repairs missed product links,
contract projections, timer generations, occurrence deliveries, and billing
acceptances from authoritative inputs.

### S8 — retirement and reuse proof

Delete or contract the migrated generic columns/tables only after upgrade
rehearsal and the fallback/read counts reach zero. Add two-directional ratchets
with sensitivity proofs for:

- local generic offer/version/price writers;
- local billing-contract/version/line writers;
- cadence/proration helper calls outside the module adapter;
- recurring-obligation scheduling/rating writes; and
- direct sibling-module or cross-application persistence access.

Update Sub's SOT registry, relationship map, ADR-0007 migration state, operator
runbook, and module dossier in the same cutover. When the local owners are
actually gone and both products exercise the same released contract, set
`contract_consumers = ["dotmac_vendor_control_plane", "dotmac_sub"]` and mark
the contract `reuse-proven`.

## Validation matrix

Every implementation slice runs the repository-prescribed checks for the files
it changes. Before any release or product cutover:

| Surface | Required proof |
|---|---|
| Pure behaviour | full cadence/proration/lifecycle/fingerprint suite plus property tests for contiguous periods |
| Module unit/architecture | `make check`, `make test-unit`, import-linter, declaration consumers, no-product/provider-name sensitivity canaries |
| Database | composed migration gate; fresh and predecessor-to-head PostgreSQL migrations; RLS/live-catalog canaries; concurrent uniqueness/locking |
| Vendor | `make check`, `make test`, platform-role isolation, offer parity, timer/output/billing contract tests |
| Sub | full prescribed lint/type/import/security suite, `make test-architecture`, `make test`, disposable migrated-PostgreSQL integration, shadow reports and retirement ratchets |
| Release | exact pins, wheel migrations, clean-host install, immutable artifact/digest, required Governance job at the pinned revision |

Failures and skipped checks remain visible in the handoff. No commit, push, PR,
merge, release, deployment, or production/SSH action is part of this plan
without Michael asking for it explicitly.
