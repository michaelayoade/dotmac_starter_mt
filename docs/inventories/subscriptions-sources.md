# Subscription offer, contract, cadence, and recurrence sources

**As of:** 2026-08-14
**Starter:** `1b1d62bebc4651273b2587fb607c49485fed123a`
**Sub:** `27c76aaeebb792f089000af764d80f4dfe45c104`
**Vendor CP:** `89848017d6b87e82dd4d6ffd0b2c9eaed5f9fee8`
**ERP:** `0f4b1698ddbf27a04f4562ecdaf8b93f19c3debf`
**Decision:** ADR-0020 amendment A4, 2026-08-14

This is the A2b offer-catalogue audit required by ADR-0020 and
`docs/superpowers/plans/2026-08-11-billing-subscriptions-collections.md`.
It compares tables, writers, decisions, and tests rather than treating the
shared `offer_versions` table name as proof of one implementation.

## Conclusion

`dotmac-subscriptions` owns the reusable recurring-commercial core on two
explicit persistence planes:

- stable offers and immutable offer/price versions;
- stable subscription contracts and immutable, effective-dated contract
  versions and lines;
- the `BillingCadence` value object and calendar arithmetic;
- declared proration and fixed-recurring rating provenance; and
- the unique recurring charge occurrence that submits a typed, immutable
  obligation to billing.

The **Vendor control plane adopts the platform plane first**, then Sub adopts
the tenant plane through a measured shadow-and-cutover. Sub is still the
qualifying product-first source for cadence, contract versioning, proration,
and recurrence. First adopter and implementation source are intentionally
different claims.

This conclusion does **not** merge Vendor's legal commercial agreement with a
subscription contract. `vendor_cp.contracts` owns proposal, content-bound
approval, countersignature/activation evidence, suspension, and termination of
the vendor-to-operator agreement. On activation, the Vendor assembly submits
the approved recurring terms to `dotmac-subscriptions`; the module never owns
approval, licensing, allocation, deployment, or account lifecycle.

## Source comparison

| Concern | Vendor CP source | Sub source | Disposition |
|---|---|---|---|
| Stable offer identity | `offer_code` is repeated on standalone `OfferVersion` rows | `CatalogOffer` is the mutable parent of versions and prices | Module owns a stable `Offer`; Vendor's repeated code and Sub's parent identity converge here. |
| Immutable offer version | `offers/models.py::OfferVersion`; five business columns; write-once service | `catalog.py::OfferVersion`; effective dating plus ISP-specific relationships; guarded but still updateable CRUD | Module publishes immutable versions. Vendor supplies the strict immutability/idempotency canaries; Sub supplies effective dating. |
| Versioned price | Exact `Money` is embedded on Vendor's offer version | `OfferVersionPrice` is separate; active prices can be changed only while no live subscription depends on them | Price rows are immutable children of an immutable offer version. A price change publishes a new offer version; no live-row mutation guard is needed as a substitute for versioning. |
| Product meaning | `capability_codes` JSON | service/access type, region, usage, SLA, policy, RADIUS, speed, and portal visibility | None belongs in the generic tables. Each assembly owns plane-specific link tables from an offer version to its product semantics. |
| Legal commercial agreement | `Contract`/`ContractLine`, approval policy, activation evidence, lifecycle events | No equivalent; Sub's `billing_contracts` are customer recurring terms | Remains a distinct commercial-contract owner. It may pin an immutable offer reference and price snapshot but does not own recurrence. |
| Subscription contract | No recurring contract aggregate today | `BillingContract`, immutable `BillingContractVersion`, stable line keys, effective interval, supersession | Port from Sub and generalise only at typed product seams. Vendor creates this aggregate after its commercial contract activates. |
| Cadence/calendar | None | `billing/cadence.py::BillingCadence`, 456 LOC plus 287 LOC of focused tests | Port from Sub as pure behaviour. No preset monthly/quarterly/annual enum in the shared contract. |
| Proration | None | declared policy plus coverage interval and deterministic factor | Port the calendar/elapsed/full/none policies and their provenance. Do not port implicit plan-change arithmetic. |
| Fixed recurring rating | Exact offer price only | `billing/rating.py` combines fixed rating, proration, tax, and future usage shapes | Subscription module owns fixed pre-tax recurrence and its replay fingerprint. Tax/FX move to billing; usage rating remains the later metering module. |
| Recurring obligation | None | `BillingObligation` combines scheduling/rating with opening, settlement, credit, write-off, and cancellation | Split the aggregate. Subscriptions owns a unique `RecurringChargeOccurrence`; billing owns acceptance, tax/FX, receivable, allocation, and resolution. |
| Service/access lifecycle | allocation/licensing consequences | `Subscription` plus RADIUS, IP, NAS, bundles, service orders, and access state | Stays product-owned. Products react to subscription-contract facts through assembly-wired commands/events. |

ERP has no offer, subscription-contract, cadence, or recurring-obligation
implementation. Its payroll and cash-basis proration are different domains.
ERP therefore supplies no code to this module and does not install it; its tax
and FX structure remains input to `dotmac-billing` under ADR-0020.

## The product-neutral contract

### Offers

An `Offer` is a stable commercial identity. An `OfferVersion` is an immutable
published snapshot with a version number and half-open effective interval. Its
immutable price children carry exact money, a declared charge model, and the
cadence offered for that charge. Withdrawing an offer prevents a new contract;
it never changes an existing contract or historical occurrence.

The first implementation supports **fixed recurring** charges only. Charge
models and occurrence source codes use ADR-0008 declaration registries, so the
module does not close a future vocabulary in a database enum. Metered usage is
not smuggled into this package through a speculative second implementation.

### Subscription contracts

A `SubscriptionContract` is the stable recurring-commercial identity. Terms
live only on immutable, effective-dated `SubscriptionContractVersion` rows.
Each line has a stable lineage key across supersession and freezes the selected
offer version, unit price, quantity, currency, cadence, collection timing, and
proration policy.

The module does not store a Subscriber, VendorAccount, deployment, capability,
RADIUS profile, licence, or entitlement FK. The tenant and platform link
helpers create product-owned link tables in the consuming assembly's lineage.
The two helpers are separate; a nullable tenant or polymorphic scope is not an
allowed shortcut.

### Recurrence versus billing

The source `BillingObligation` is too broad for the post-ADR-0024 boundary. The
split is structural:

| Subscriptions owns | Billing owns |
|---|---|
| contract/line/version identity | accepted rated-obligation identity |
| service period and coverage interval | applied tax and FX snapshots |
| exact unit price, quantity, rate units, proration factor, pre-tax amount | invoice/credit, operational receivable, funding, allocation |
| scheduled/due/cancelled recurring charge occurrence | open/partial/resolved/written-off/reversed financial lifecycle |
| immutable rating input fingerprint | settlement and correction evidence |

The subscriptions row is named `RecurringChargeOccurrence`, not a second
`BillingObligation`. When due it emits `subscriptions.recurring_obligation_due.v1`
through the assembly. The event carries the stable occurrence id, contract and
line/version identity, source fact/version, period, coverage, currency, exact
pre-tax rating inputs/result, collection timing, and fingerprint. Billing
accepts it idempotently and becomes the sole owner of the monetary obligation.

## Two declared persistence planes

One persistence-free behaviour engine is shared. Storage is deliberately
duplicated and isolated:

| Tenant plane | Platform plane |
|---|---|
| `offers` | `platform_offers` |
| `offer_versions` | `platform_offer_versions` |
| `offer_version_prices` | `platform_offer_version_prices` |
| `subscription_contracts` | `platform_subscription_contracts` |
| `subscription_contract_versions` | `platform_subscription_contract_versions` |
| `subscription_contract_lines` | `platform_subscription_contract_lines` |
| `recurring_charge_occurrences` | `platform_recurring_charge_occurrences` |

The names are design input, not a namespace allocation. The package, short
code, prefix, branch label, manifest declarations, and lineage are allocated
together only when ADR-0017's gate permits implementation.

Every tenant table carries `tenant_id UUID NOT NULL`, tenant-composite unique
keys and FKs, and ENABLEd+FORCEd RLS. Every platform table carries no tenant
column and no RLS, is REVOKEd from `app_user` across all table and column
privileges, and is reachable by the online platform role. No FK crosses the
planes.

## Product-specific state that stays outside

### Vendor control plane

- commercial account and PartyRole relationship;
- content-bound approval and legal contract lifecycle;
- capability membership, entitlement allocation, licence issuance, and
  deployment activation/suspension consequences; and
- the assembly coordinator that turns an activated commercial contract into a
  platform subscription contract.

### Sub

- subscriber/account identity and customer service lifecycle;
- ISP service/access type, region, usage allowance, SLA/policy, RADIUS, NAS,
  IP, bundle, and provisioning state;
- sales-order acceptance and service activation decisions; and
- the consequence owner that projects an effective subscription contract into
  local service/access state.

## Source defects that must not be ported

1. Sub's preset `BillingCycle` paths and `_add_months`/`_compute_next_billing_at`
   helpers are parallel cadence owners. Port `BillingCadence`, not those paths.
2. Sub's `OfferVersion` and `OfferVersionPrice` remain mutable until a live-row
   guard happens to notice a dependent subscription. Shared published versions
   are immutable from publication, whether or not a consumer exists yet.
3. Vendor embeds `capability_codes`; Sub embeds ISP semantics. Neither product
   vocabulary enters the generic module.
4. Sub's `BillingObligation` combines recurrence with receivable settlement.
   Port the split above, not the aggregate.
5. Sub's rating resolver reads mutable tax configuration. Subscriptions records
   pre-tax rating provenance; billing stamps tax/FX versions.
6. Sub's service-layer catalog CRUD commits transactions and raises HTTP
   exceptions. Module services mutate/flush and raise typed domain errors;
   assembly adapters own transactions and transport mapping.
7. A missing price, cadence, timezone, source declaration, or product link
   fails closed. No zero price, monthly default, `Africa/Lagos`, or provider/
   currency literal is invented at a call site.

## Preserved parity tests

Port with the code they prove:

- Vendor exact-money, declared-capability, immutability, and idempotent-publish
  tests from `tests/unit/test_offers.py`; capability membership becomes a
  Vendor link-table test rather than a generic offer column test.
- Sub `tests/test_billing_cadence.py` in full, including leap year, month end,
  strict-same-day, half-open, timezone, and alignment cases.
- Sub `tests/test_billing_contracts.py` for immutable versions, contiguous
  supersession, stable line lineage, effective-at resolution, mixed-currency
  refusal, locking, and idempotency.
- The fixed-period, deterministic replay, daily-rate aggregation, and declared
  proration cases from `tests/test_billing_rating.py`. Tax and usage cases move
  to their owning modules.
- The natural-identity, replay, coverage-conflict, and consecutive-period cases
  from `tests/test_billing_obligations.py`. Opening, settlement, credit,
  write-off, and account-receivable queries move to billing.
- Sub's Phase-2 durable shadow evidence for exact period/amount parity,
  base-plus-add-on identity, missing occurrence detection, and replay.

## Adoption order

1. **Vendor CP, platform plane.** Migrate its offer versions and capability
   links, then create recurring subscription contracts from newly activated
   commercial contracts. Its local offer writer is retired at cutover.
2. **Sub, tenant plane.** Shadow the mature cadence/contract/recurrence source,
   migrate generic offer data while retaining ISP semantics in Sub-owned link
   tables, switch one owner slice at a time, and delete each local writer.

The executable plan is
`docs/superpowers/plans/2026-08-14-subscriptions-vendor-sub-adoption.md`.
