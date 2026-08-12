# Reusable billing, subscriptions and collections — prerequisites and plan

> **Status:** Proposed implementation sequence. Non-authoritative intent
> (`docs/superpowers/plans/`); ownership decisions are accepted in ADR-0020.
> **Evidence:** `docs/inventories/billing-sources.md` (2026-08-11).
> **Governed by:** ADR-0003 (deployment profiles), ADR-0006 + its product-first
> amendment (extraction rule), ADR-0008 (declaration registries), ADR-0011/0012
> (settings), ADR-0014 (at-most-once), ADR-0016 (coverage is derived),
> **ADR-0017 (adoption is the scarce resource)**, **ADR-0020 (billing owns
> operational receivables; three commercial modules)**, `dotmac_sub` ADR-0007
> (end-to-end billing target architecture).
> **Supersedes nothing.** This is the concrete shape of the deployment plan's
> workstreams 4–7; that plan stays the decision record.

## The finding that shapes everything below

The capability is not blocked on design. Sub's ADR-0007 is an accepted,
detailed target architecture, and ~74,000 lines of production billing code and
174 test files already exist to port from.

Its complete automated shape is blocked on **four shared facilities that
ADR-0017 put under moratorium** — inbound webhooks, scheduling, numbering, and
object storage. ADR-0017's 2026-08-12 amendment makes explicit that `webhooks`
names the inbound/outbound facility family. Money persistence is a separate
partially-adoptable gap: ERP can consume and retire the ADR-0016 coverage owner
without using the rest of billing.

Those dependencies are **profile- and slice-specific**, not one all-or-nothing
gate. A manual/ERP-invoiced deployment does not need a PSP webhook receiver; a
one-off invoice does not need subscription cadence or durable dunning timers;
provider-owned invoicing does not need an internal numbering series.

So the honest sequence is: **dossier and contract now, prerequisites when a
live adoption pulls them, module after the lineage gate.** Anything that starts
by writing invoice tables in this repo is supply-pushed persistence work of exactly
the kind ADR-0017 measured and stopped.

## Part 1 — Capability areas and gates

Ten numbered capability areas plus one external lineage gate. P8 is one
document-production area with two separate owners; combining those owners in
one seam is forbidden. Each row states which slice needs it and its ADR-0017
standing. **Nothing in the "blocked" column may start on a "product will need
this" argument** — ADR-0017's exception is demand-pulled: "a product is blocked
on this today."

### Tier 0 — already owned, adopt rather than rebuild

| Item | Kernel owner | Billing's use |
|---|---|---|
| Exact money values | `dotmac_kernel.money` | Every amount. `Money.allocate()` is the invoice-split primitive. ERP already imports this and nothing else. |
| At-most-once execution | `dotmac_kernel.idempotency` (ADR-0014) | Webhook processing, retry ladders, billing-run replay. |
| Outbox + relay + inbox | `dotmac_kernel.messaging` | ADR-0007's guaranteed owner-output protocol, already built. |
| Entitlement grants | `dotmac_kernel.entitlements` | Where a commercial outcome is *projected*. Feature code reads entitlements, never subscription state. |
| Settings (scope, inherit, crypto) | `settings_resolver` + ADR-0011/0012 | Every tolerance, cadence default, dunning ladder, gateway config. |
| Consent + channel policy + delivery | `consent`, `channel_policy`, `delivery` | Dunning notices are contactability-checked and receipt-tracked. |
| Module namespace + lineage | `namespaces` (ADR-0006 D1) | `mod_<short>` schema so billing tables cannot collide with Sub's 576. |
| Declaration registries | `modules` (ADR-0008) | Charge models, dunning actions, obligation sources — open vocabularies, not enums. |
| Coverage rule | ADR-0016 + ERP `coverage.py` | Ship correct from revision 1; port the parity test. |

### Tier 1 — missing capability areas

| # | Capability | Slice that needs it | ADR-0017 |
|---|---|---|---|
| **P1** | **Money persistence primitives**: a column/composite, the ADR-0016 coverage mixin (`total_amount`, `amount_paid`, generated `balance_due`), declared rounding, configurable currencies, and immutable FX snapshots | Every money table otherwise hand-rolls its representation. The coverage owner is independently useful and ERP can retire its local `coverage.py`; configurable-currency and persisted-FX work need separate named adopters. | Not gap-listed. Only the coverage slice is demand-backed now; do not bundle the rest into its cutover. |
| **P2** | **Inbound webhook receiver**: signature verification, raw-payload store, provider-event dedupe, replay, and ordering policy | Required when a PSP/provider supplies settlement facts by webhook. Manual/ERP invoicing does not need it. | **Gap-listed. Blocked** absent a live adopter; ADR-0017 amendment 2026-08-12. |
| **P3** | **Durable timers**: wake an owner/entity at a time, exactly once, with a generation | Required for recurring renewals, grace expiry, dunning offsets, retry ladders, and arrangement due dates—not for billing core itself. | **Gap-listed as scheduling. Blocked.** |
| **P4** | **Document numbering**: one owner per series, declared gapless-or-not policy, period reset, concurrency-safe | Required when internal billing issues invoices, credit notes, or receipts. Provider/manual authorities keep their own series. | **Gap-listed. Blocked.** |
| **P5** | **Cadence value object + calendar arithmetic**, owned by `dotmac-subscriptions` | The recurring-contract composability core. Without it "monthly" becomes a code path, as measured in Sub. One-off billing does not need it. | Not gap-listed, but it is module domain code—not a reason to grow the kernel before the module/adopter exists. |
| **P6** | **`PaymentProvider` seam**, owned by `dotmac-billing`: protocol, typed results, stable errors, fake, parametrized contract suite | Required only by profiles that take provider payments or ingest provider-owned invoice/settlement state. | Contract-only does not mean adoption-free. Land with the billing module and named adopter, not in the kernel now. |
| **P7** | **Tax seam**, owned by `dotmac-billing`: rate resolution, inclusive/exclusive/exempt, reverse charge, immutable applied-policy snapshot | Required only where the selected invoicing authority applies jurisdiction policy. ERP owns the source structure to port. | Land with billing and an adopter. No shared Nigerian VAT default. |
| **P8a** | **Document rendering**, owned by a document-generation capability separate from billing and Template Studio | Required when the product renders invoices, statements, or attachments locally. Billing supplies immutable document facts; rendering produces bytes. | Not the object-storage facility. Needs its own dossier and adopter. |
| **P8b** | **Object storage**, a byte-storage provider seam | Required to retain locally produced documents and attachments. It does not decide content or render PDFs. | **Gap-listed. Blocked.** |
| **P9** | **Locale, message IDs, formatting** (deployment plan WS4) | Required for a second locale and for localized invoices/notices. `display.py` gives timezone/date formatting only. | Not gap-listed; defer until a named locale/document adopter needs it. |
| **P10** | **Operational receivables subledger**, owned by `dotmac-billing`: immutable posting groups and typed receivable/funding effects | Required for internal billing. ADR-0020 limits it to operational receivables; ERP keeps the chart of accounts, journals, periods, statutory posting, and GL reconciliation. | Ownership decided. Stateful implementation waits on P11. |

### Tier 2 — the external gate, not another capability

**P11 is ADR-0017's lineage gate:** the kernel migration lineage must run in a
product database in production (Sub, S7). Every commercial module above is
stateful. A module whose tables each product hand-creates is a library, not a
shared lineage—the ADR-0014 failure repeated.

### The honest read of that table

Four capability areas contain gap-listed work and P11 is gate-shaped.
**Automated end-to-end billing is downstream of the
single blockage ADR-0017 identified**, and adding it to the queue does not move
that queue. What it does do — and this is the value of doing the dossier now —
is make billing a *named consumer* of P2/P3/P4/P8b, so that when Sub or the
vendor control plane is genuinely blocked on one of them, the demand-pulled
exception has a designed contract to build rather than an improvised one.

## Part 2 — The composability contract

The requirement is that a product configures its billing rather than forking
it. That is a testable property, not a stance, and each line below names the
hardcoding it forbids and the check that catches it.

### C1 — Cadence is a value object, never a cycle enum

```text
BillingCadence
  rate_basis        # fixed_per_period | per_unit | per_active_day | usage
  rate_unit, rate_quantity
  service_interval  { unit, count }     # what the customer receives
  invoice_interval  { unit, count }     # how often it is documented
  collection_timing { advance | arrears }
  anchor, timezone, alignment           # incl. one declared end-of-month rule
  proration_policy  # actual_days | actual_elapsed | full_period | none
```

Forbidden: a `BillingCycle` enum of `monthly|quarterly|annual`; `days=30`;
`days=365`. Quarterly is three calendar months, annual is twelve; every
interval is `[starts_at, ends_at)`.

Rate unit and invoice interval are **independent**, which is what lets one
contract carry "daily rate, monthly invoice, arrears" and another carry
"fourteen-day prepaid, advance" with no new code.

*Check:* an architecture test rejecting cycle/preset enums such as
`BillingCycle(monthly|quarterly|annual)`, while allowing a closed interval-unit
vocabulary plus a quantity; a property test that every supported cadence
round-trips a period generator across DST, leap years and the 29th/30th/31st.

*Evidence it is needed:* Sub ADR-0007 records that prepaid renewal there
"remains materially monthly-specific" while postpaid supports five cycles.

### C2 — Prepaid and postpaid are one contract field, not two subsystems

`collection_timing` on the contract version. One obligation machine, one
resolution protocol, one collections entry point.

Forbidden: parallel `prepaid_*` / `postpaid_*` scans, runners, notice paths and
error handling.

*Check:* an architecture test that no module symbol is named for exactly one
timing mode; a behavioural test that the same scenario under `advance` and
`arrears` traverses the same owner functions.

*Evidence:* Sub ADR-0007 § Context — the two "have different account scans,
timers, notices, commits and error handling even though both eventually ask the
shared access-lifecycle owner to act."

### C3 — Charge models and obligation sources are declaration registries

Per ADR-0008: a module declares `charge_models` and `obligation_sources` on its
manifest; the billing module validates against the composed registry and
invents nothing. A product that sells bandwidth overage, an installation fee
and a per-seat licence declares three sources without a shared-module change.

Forbidden: `class ChargeType(enum.Enum)` in the shared module.

*Check:* the existing `test_manifest_declarations.py` shape — declared-with-no-
consumer and consumed-with-no-declaration both fail the build.

### C4 — Dunning policy is versioned data, not control flow

A dunning ladder is rows, resolved through the settings resolver:

```text
DunningPolicyVersion
  applies_to      # a declared receivable/coverage predicate, not a plan name
  steps[] { offset_from, offset_days, action_code, channel_preference,
            template_id, condition, requires_approval }
  grace, retry ladder, floor/minimum, suppression windows
  version, effective_from, actor, reason
```

Forbidden: `if days_overdue > 30:`; a hardcoded three-notice sequence; a
literal `Decimal("0.01")` anywhere (ADR-0016 § 4 — the tolerance is a
`SettingSpec`).

Every `action_code` is a declared code. Every consequence is a **request to the
owning service** (access, notification, arrangement), never a direct write —
that is what stops a collections module from becoming a second writer of
service state.

*Check:* an AST test rejecting numeric-literal day thresholds and money
literals in the collections module; a policy-replay test asserting that
changing only the policy version changes only the outcome.

### C5 — Every external system is a seam with a fake

`PaymentProvider`, `TaxProvider`, `FxProvider`, `NumberingProvider`,
`DocumentRenderer`, and `DocumentStorageProvider` — protocol + typed results +
stable error taxonomy + in-memory fake + one parametrized contract suite every
implementation must pass. Rendering and storage remain separate contracts: one
produces bytes from immutable facts; the other transports bytes. The deployment
plan already requires this (Lane A step 4): a product team develops with no PSP,
tax, rendering, or storage credentials.

Forbidden: `paystack`, `flutterwave`, `stripe`, `remita` or `NGN` as an
identifier or default anywhere in the shared module.

*Check:* a grep-based architecture test over the module for provider and
currency names; profile tests that boot with only fakes bound.

### C6 — The deployment profile names the billing authority

Three authorities are legitimate and mutually exclusive per deployment:
**provider-owned invoicing** (the PSP invoices), **internal invoicing** (this
module invoices), **manual/ERP invoicing** (an external finance system
invoices). The profile declares which; the module refuses to boot with two.

Internal invoicing activates billing's operational receivables writer.
Provider-owned invoicing may maintain a source-labelled, reconcilable local
projection of external invoice/settlement facts, never a second writer.
Manual/ERP invoicing disables the local invoice and subledger writer entirely.

Forbidden: `if deployment_mode == ...` anywhere in feature code — already a CI
gate in the deployment plan.

### C7 — Money and versions

No float, ever. Every rated line stamps the **immutable** price version, tax
policy version and FX snapshot it used, so a policy change never rewrites
history. Coverage is derived (`balance_due` generated, tolerance a setting);
no `PAID` in any lifecycle enum.

*Check:* ADR-0016's enforcement tests, ported into the module from revision 1;
a test that no rating line references a mutable price row.

### C8 — Nothing reads a plan name

Feature code asks the entitlement evaluator. Subscription state projects
**into** grants; it is never read directly for access. Already a CI gate
("a feature checks a plan name or billing-provider state").

### C9 — Receivable and funding never collapse

Per currency, separately: collectible receivable, available customer credit,
prepaid funding. A single "balance" field is forbidden in the shared read model.

*Evidence:* Sub's `web_subscriber_details.py:385` computes
`current_balance = balance_due + available_credit` — credit added to debt.

### C10 — Obligation identity is a database constraint

`contract line + contract version + charge component + source fact/version +
period start + period end + currency`, unique. Duplicate billing is then
impossible under replay and concurrency rather than merely unlikely.

*Evidence:* Sub's recurring run dedupes on a single `subscription_id`, so a
standalone subscription and an add-on for the same service never collide.

## Part 3 — Module shape

**Three distributions, not one.** ADR-0020 settles this boundary. The deployment
plan makes subscriptions (WS5), metering/rating (WS6), and billing (WS7)
independently optional, and a product that invoices one-off orders should not
carry a subscription engine.

```text
dotmac-subscriptions ──┐
                       ├──> dotmac-billing ──> dotmac-kernel
dotmac-collections ────┘
```

`dotmac-billing` owns rated-obligation acceptance, invoices, credit notes,
payments, operational receivables, allocations, coverage, and tax/FX/provider
seam consumption. `dotmac-subscriptions` owns catalog offers, immutable
contract/price versions, cadence, lifecycle, proration, fixed recurring charge
generation, and entitlement projection. `dotmac-collections` owns dunning
cases, versioned ladders, arrangements, grace, and consequence requests.

Dependency direction is deliberate: **subscriptions and collections are
sibling dependents of billing; billing knows about neither.** Subscriptions
submit immutable rated obligations; collections read receivables and request
consequences from whichever owner the product declares. Cross-module outcomes
travel through versioned contracts/outbox events, not reverse imports. A product
can install billing alone (one-off invoicing), billing + subscriptions (SaaS
without collections), or all three.

Each stateful module takes one `mod_<short>` allocation in
`MIGRATION_OWNER_LEDGER` (`namespaces.py`) and owns one migration lineage.
Metering/usage rating (WS6) stays out of scope here; it is a fourth module and
submits rated usage obligations through the same billing input contract.

### What these modules must not absorb

Sub's ISP domain (RADIUS, service entitlements as network access, subscriber
lifecycle), ERP's general ledger and statutory accounting, any product's
category taxonomy or approval workflow. The ticketing dossier's boundary
language applies unchanged.

## Part 4 — Sequence

### Stage A — Evidence and accepted boundary *(done; no kernel code)*

1. `docs/inventories/billing-sources.md` — **done** (this change).
2. ADR-0020 — **accepted**: the operational receivables subledger belongs to
   `dotmac-billing`; ERP retains the general ledger; billing, subscriptions, and
   collections are three distributions.
3. C1–C10 — accepted as ADR-0020's binding implementation contract.
4. This plan registers the module slices as named future consumers of
   P2/P3/P4/P8b, without claiming the demand-pulled exception.

The three `EXTRACTION.toml` dossiers are **not** created in Stage A. Repository
convention locates a dossier in its package root, and this stage deliberately
creates no package directories. Stage E creates each dossier beside its package
before code, with status `audit-complete` (never `approved` before adoption),
using ticketing's fields: `source_repositories`, `source_paths`,
`preserved_tests`, `first_cutover`, `shadow_and_drift`,
`local_copy_retirement`, and `next_action`.

**Gate:** complete when this documentation change is accepted. No package
directory exists and no facility implementation has started.

### Stage B — The adoption-backed coverage slice of P1

**Implementation status (2026-08-12):** the product-first kernel owner and its
DB-free/PostgreSQL canaries are present as an **unreleased WIP**. The Stage B
gate remains open: the already-allocated kernel `0.1.0a41` change must land,
this surface must ship in the next alpha, and ERP must exact-pin it and delete
its local `coverage.py`. Presence in the kernel is not counted as adoption.

`money` is ERP's one kernel import and ADR-0016 § 5 requires a shared coverage
owner. Port only the independently adoptable slice: the monetary coverage
mixin/generated `balance_due`, the one owning coverage evaluator/query surface,
and ERP's Python/SQL parity test. ERP consumes the released owner on an exact
pin and deletes its local `coverage.py` in the same cutover.

Do **not** bundle the money composite, configurable currency set, persisted FX
snapshot, P5 cadence, or P6/P7 seams into this slice. Each needs its own named
adopter and owner. DB-free code with no consumer is still WIP under ADR-0017's
metric.

**Gate:** ERP consumes the coverage owner from the kernel on an exact pin, and
its local `coverage.py` is deleted. This is a second adopted kernel contract in
the product that currently imports one, not a pretext for the rest of P1.

### Stage C — Wait on the lineage gate (P11)

Sub's S7. **Billing does not jump this queue and does not lobby to.** ADR-0017's
stop rule has a start rule: the freed capacity goes to the constraint.

### Stage D — Blocked prerequisites, demand-pulled only

P2, P3, P4, P8b — each when a product is blocked on it *today*, built to the
contract Stage A designed. Likely order by real demand: numbering (ERP has
five), webhooks (Sub's PSP path), timers (any dunning cutover), storage.

### Stage E — The modules

Create each package root and its `EXTRACTION.toml` **before implementation**.
Product-first: port Sub's cadence, fixed recurring charge generation,
obligation, allocation, and dunning owners with their tests; port ERP's
coverage and tax structure. P5 lives in subscriptions; P6/P7/P10 live in
billing. Usage rating remains outside these three modules. Ship the C1–C10
gates in the same revisions as the code they govern, never after.

### Stage F — First cutover

**Decision: the vendor control plane, after P11—not Sub—is the first adopter of
the billing module.**

It already owns `offer_versions`, `contracts` and `contract_lines`, consumes
`dotmac_kernel.money`, and has **no invoicing at all** — greenfield, so no rows
to migrate and no local writer to retire. It also has genuine demand: it sells
deployments and cannot currently bill for them. That is the same reasoning that
put ticketing's first cutover there, and the same reason the first adopter is
deliberately not the source product.

This does not change ADR-0017: Sub is still the first adopter of **kernel
persistence**. The vendor control plane is first only for the later optional
billing module.

Sub follows, with shadow verification on every knowing divergence — and there
will be several, because § 4 of the inventory lists behaviours that are wrong
today and whose correction will change customer-visible numbers. Those get
measured before cutover, not discovered in a billing run.

## Part 5 — What this plan deliberately does not do

- It does not start a `packages/dotmac-billing/` directory. Stage A is
  documents; Stage B is one kernel coverage slice with an ERP cutover.
- It does not propose lifting ADR-0017's moratorium.
- It does not implement ADR-0020's shared subledger before the lineage gate.
- It does not add metering/rating (WS6). Usage pricing is a fourth module and
  only where a product prices usage.
- It does not touch ERP's general ledger. Sub must not become a shadow ERP, and
  neither must a shared module.
