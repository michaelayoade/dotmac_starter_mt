# `dotmac-subscriptions` — proposed public contracts

**Status:** implemented contract reference. This specification remains
**non-authoritative intent**; the published Python surface and migrations under
`packages/dotmac-subscriptions/` are the as-built truth. The package, namespace,
manifest and lineage now exist, but no release or adopter cutover is claimed.
**As of:** 2026-08-18
**Decision:** ADR-0020 amendment A4, 2026-08-14
**Evidence:** `docs/inventories/subscriptions-sources.md`,
`docs/inventories/a2-commercial-offer-source-audit.md`
**Dossier:** `docs/inventories/subscriptions-extraction-dossier.md`
**Plan:** `docs/superpowers/plans/2026-08-14-subscriptions-vendor-sub-adoption.md`
(this specification is that plan's **G2** gate on the producer side)
**Consumer side:** `AcceptRatedObligationV1` is specified by the billing team.
This document specifies only what subscriptions **produces**.

## Scope

`dotmac-subscriptions` produces exactly **two** contracts for other owners:

1. **`RatedObligationOutputV1`** — a uniquely identified, replayable recurring
   charge occurrence carrying exact **pre-tax** money and its complete rating
   provenance. Consumed by billing's `AcceptRatedObligationV1`, **through the
   assembly**.
2. **`CommercialEntitlementProjectionV1`** — the module's statement of what a
   contract version *commercially intends*, emitted to whichever owner decides
   entitlement. It is an output, never a write.

Everything else in the published surface is *inbound* (typed commands the
assembly calls) or *local* (value objects, queries, ports, fakes). Those are
specified in § 5–§ 7 with less ceremony, because a command an assembly calls
in-process is not a cross-owner contract.

Every contract below defines all eight required properties: **stable identity
and version; idempotency key and request fingerprint; tenant or platform scope;
currency and exact amount representation; source authority and provenance;
correction, supersession and reversal semantics; accepted errors and retry
classification; compatibility rules.**

---

## 1. The design points, and why each is what it is

These bind every contract in this document. Each cites the measured source
defect it exists to prevent.

### 1.1 Cadence is a value object

```
BillingCadence
  rate_basis          # declared: fixed_per_service_period | per_rate_unit
                      #           | per_quantity | usage_metered (declared, refused)
  rate_unit           # closed calendar vocabulary: day | week | month | year
  rate_quantity       # exact decimal, > 0
  service_interval    { unit, count }   # what the customer receives
  invoice_interval    { unit, count }   # how often it is documented
  collection_timing   # advance | arrears  — ONE field
  alignment           # contract_anniversary | calendar_period_start | fixed_anchor_day
  anchor_day          # 1..31, required by fixed_anchor_day, else absent
  timezone_name       # required IANA zone, no default
  end_of_month_rule   # clamp_to_month_end | strict_same_day_or_skip  — ONE declared rule
  proration_policy    # none | full_period | actual_calendar_days | actual_elapsed_time
```

**No `monthly|quarterly|annual` enum. No `days=30`. No `days=365`.** Quarterly
is three calendar months; annual is twelve. Every interval is
`[starts_at, ends_at)` so consecutive periods neither gap nor overlap.

**Rate unit and invoice interval are independent**, and this is the whole point
of the value object: it is what lets one contract carry "daily rate, monthly
invoice, arrears" and another "fourteen-day prepaid, advance" with no new code.
`dotmac_sub:tests/test_billing_cadence.py::test_rate_unit_is_independent_of_invoice_interval`
and `::test_annual_service_period_invoiced_quarterly` already prove it.

*Why the closed calendar vocabulary is not the enum C1 forbids.* `IntervalUnit`
is a fact about calendars, not about products — a product cannot invent a new
unit of time. `BillingCycle(monthly|quarterly|annual)` is a fact about one
product's price list, and a product that sells fourteen-day access needs a code
change. The line is: a closed unit vocabulary **plus a quantity** is expressive
enough that no product ever needs a new member; a preset list is not.

*Evidence the composable form is strictly more expressive.* Sub's own migration
adapter proves it. `app/services/billing/contracts.py:386-392` maps all five
presets onto `(IntervalUnit, count)` — every preset is expressible — while
`service_interval ≠ invoice_interval` has no enum value at all, so the converse
is false.

*Two literals the module must refuse at the same seam.*
`contracts.py:413` hardcodes `timezone_name="Africa/Lagos"` and `:415`
hardcodes `ProrationPolicy.none`. A missing timezone is a **construction
failure**, not a default. Sub's `cadence.py:121-131` already resolves the zone
eagerly in `__post_init__` so a bad zone fails at construction rather than
mid-period; that behaviour ports unchanged.

### 1.2 Advance/arrears is ONE `collection_timing` field

Not two engines, not two scans, not two notice paths, not two error handlers.
One obligation machine and one resolution protocol.

*The measured defect, from ADR-0007's own § Context:* "Postpaid period generation
supports daily, weekly, monthly, quarterly, and annual cycles, while **prepaid
renewal remains materially monthly-specific**", and "Postpaid dunning and prepaid
enforcement have different account scans, timers, notices, commits, and error
handling even though both eventually ask the shared access-lifecycle owner to
act."

*And it is worse in the source than in the ADR's summary.* Sub's prepaid engine
does not merely prefer monthly — its **candidate query hard-filters it**:
`app/services/prepaid_service_renewals.py:2296-2306` selects on
`CatalogOffer.billing_cycle == BillingCycle.monthly`, so a quarterly or annual
prepaid subscription is **never scanned at all**. The period is hard-coded at
`:2351`, price resolution `continue`s past non-monthly at `:427-431`, there is an
explicit `unsupported_cadence` failure at `:694-700`, and the naming is baked in
(`PrepaidMonthlyChargeDetail` at `:258` plus four `resolve_prepaid_monthly_*`
functions). The fork point is a literal `if/else` at
`app/services/advance_renewal_invoicing.py:313-338`. Sub's own SOT registry
already records the pair as `SHADOWING` with `old_owner` "postpaid invoice-period
generation and monthly-specific prepaid renewal decision forks"
(`app/services/sot_registry/domains/financial_access/billing.py:744-765`).

That is not two implementations of one behaviour. It is one behaviour that
**only exists monthly** on one side. Parallel engines do not stay in step; they
diverge in capability, and the customers on the losing side simply cannot be
sold a quarterly plan.

*The contract consequence:* `collection_timing` is a field on the contract
version and a field on the output. It is never a type, never a subclass, never a
module path, and never a symbol name. § 8's guard makes that testable.

### 1.3 Price and contract versions are immutable once effective

A published offer version and its price children never change. An effective
contract version is never edited; a change **supersedes** it and creates the
next, with `[starts_at, ends_at)` contiguous across the boundary and at most one
open-ended effective version per contract.

*Evidence both products need this fixed rather than ported.* Sub's
`OfferVersions.update` `setattr`s every supplied field and commits
(`app/services/catalog/offers.py:587-603`), guarded only by
`assert_offer_version_update_safe`, which fires **only** when the changed field
set intersects `_PRICE_CRITICAL_FIELDS`/`_VERSION_CRITICAL_FIELDS` **and** a live
subscription pinned that version — and `Subscription.offer_version_id` is
**nullable** (`app/models/catalog.py:904`), so a live subscription that pinned
only the offer does not protect the version. Worse, Sub's `offer_versions` has
**no uniqueness constraint at all** on `(offer_id, version_number)`: the class
declares no `__table_args__` and `squashed_schema.sql` carries only the primary
key and four foreign keys. Vendor's `uq_offer_versions_code_ver` exists in both
model and migration — but Vendor's *immutability* is service-only, since
`v002_offer_versions.py:58-59` grants `UPDATE, DELETE` to `platform_api` and
`app_admin`.

*The rule:* immutability is **structural**, and it applies from publication
whether or not a consumer exists yet. A price change publishes a new offer
version; withdrawal blocks new selection and never touches an existing contract
or a historical occurrence. Uniqueness on `(scope, offer, version)` is a database
constraint, on both planes.

### 1.4 Obligation natural identity is a DATABASE uniqueness constraint

```
contract line lineage
+ contract version
+ charge component
+ source fact / source version
+ period start
+ period end
+ currency
```

unique, per plane, in the database — not "checked before insert".

*This is the one place the source already gets it right and must simply be
carried over.* Sub's `uq_billing_obligation_natural_identity`
(`app/models/billing_contract.py:491-501`) is exactly this shape and contains
**no `subscription_id`**, and
`tests/test_billing_obligations.py::test_replaying_the_same_natural_identity_returns_one_obligation`
proves it.

*The cited `subscription_id`-only dedupe defect is the LEGACY path, and it is
real.* `app/services/billing_automation.py:1782-1793` decides "already billed"
from `InvoiceLine.subscription_id` + the line **description string** + the
invoice period — and `:1777` builds that description as
`f"{offer_name} ({period_start.date()} - {period_end.date()})"`, so **renaming an
offer defeats the duplicate-billing guard**. `:2252-2258` (prorated invoices)
keys on subscription + period only, and `:1006-1031` disambiguates recurring
add-ons by looping over JSON metadata in Python. The strongest mitigation there
is a partial unique index on a *concatenated string*,
`uq_invoice_lines_active_billing_line_key`
(`app/models/billing.py:1086-1092`, built at `billing_automation.py:984-994` as
`f"subscription:{id}:{start}:{end}:{component}"`) — and it is `postgresql_where`
only, so the SQLite unit harness does not enforce it at all. A standalone
subscription and an add-on for the same service in the same period collide on
the weaker keys.

*The rule:* duplicate billing is **impossible** under replay and concurrency,
not merely unlikely. Reusing the identity with different coverage, price,
cadence, proration or fingerprint is a **conflict**, not a replay — Sub's
`test_same_natural_identity_with_different_coverage_fails_closed` is the
behaviour. The uniqueness canary runs against real PostgreSQL, because the
module's own tenant constraint is composite with `tenant_id` and the SQLite
harness cannot prove concurrency anyway.

### 1.5 Charge models and obligation sources are declaration registries

Per ADR-0008 and hard rule 12: a product declares `charge_models` and
`obligation_sources` on its manifest; the module validates against the composed
registry and invents nothing. `class ChargeType(enum.Enum)` in the shared module
is forbidden.

Sub's `ChargeComponent` (7 values) and `BillingContractSourceKind` (5 values,
including `sales_order_line`) are *Sub's product vocabulary* and become Sub's
declarations. Vendor CP declares its own. A product selling bandwidth overage, an
installation fee and a per-seat licence declares three sources without a
shared-module change.

The existing `test_manifest_declarations.py` shape applies in both directions:
declared-with-no-consumer and consumed-with-no-declaration both fail the build.
Vendor's `CapabilityCatalogue.require` (`src/vendor_cp/offers/service.py:90-91`,
proven by `test_undeclared_capability_is_rejected`) is the refusal behaviour to
port; the capability vocabulary itself stays Vendor's.

### 1.6 Publishing an obligation never imports or calls billing

ADR-0024 § 2 and ADR-0020 A1. `dotmac-subscriptions`, `dotmac-billing` and
`dotmac-collections` are **peers over `dotmac-kernel`**. The module emits
`RatedObligationOutputV1` and stops. The **consuming assembly** maps producer
output to consumer input:

```
subscriptions  ──RatedObligationOutputV1──▶  [assembly]  ──AcceptRatedObligationV1──▶  billing
```

The module holds no reference to billing's package, its types, its schema, its
tables or its error taxonomy. It does not know whether a consumer exists. The
existing *Modules are independent of each other* import-linter contract is the
enforcement, and § 8's guard adds the sensitivity proof.

*Consequence for delivery:* the module stages the output **transactionally with
the occurrence**, in one owner transaction, and delivery is the assembly's.
Whether the assembly uses an outbox, a direct call, or a queue is the assembly's
choice; the module's obligation ends at "the fact and its output are committed
together".

### 1.7 Entitlement effects are outputs to an owner

The module never writes Vendor allocation, licence state, product access state,
RADIUS state, or a service status. It emits
`CommercialEntitlementProjectionV1` describing what a contract version
*commercially intends*, and the owning service decides.

*Evidence the pattern already exists and works:* Vendor's
`tests/unit/test_allocations.py::test_stage_writes_no_product_ws2_grant` proves
an activated contract projects into an immutable allocation **without touching
product-plane grants**. That is the exact shape, one boundary further out.

*The corollary that matters operationally:* the module's contract aggregate has
**no `suspend` transition**. Collections asks the owning Vendor or Sub service
for a consequence; it does not suspend a subscription contract as a proxy for
service state. C8's existing gate — a feature never reads a plan name and asks
the entitlement evaluator — is the same rule read from the consumer side.

### 1.8 Exact money only

No float, anywhere, ever. No implicit currency and no currency default — a
missing currency is a construction failure, not `"NGN"`. No mutable historical
price: every rated line stamps the immutable price version it used. No unstamped
tax or FX decision — and in this module, **no tax or FX decision at all**: the
output is pre-tax and billing stamps the tax and FX versions it applied.

*Evidence:* `app/models/catalog.py:716`, `:767` and `:797` each declare
`currency: Mapped[str] = mapped_column(String(3), default="NGN")`. Sub is also
internally inconsistent on scale — `Numeric(10,2)` on catalogue prices,
`Numeric(14,4)` on contract versions, lines and obligations — so the module must
declare one representation rather than port both. Vendor stores a quantized
decimal **string** plus an ISO-4217 code and reconstructs kernel `Money`
losslessly (`src/vendor_cp/offers/models.py:37-39`,
`service.py:76`), which is the representation that survives a JSON wire without
a float ever existing.

---

## 2. `RatedObligationOutputV1`

The one inter-module contract. Producer: `dotmac-subscriptions`. Consumer:
billing, via the assembly.

### 2.1 Shape

```text
RatedObligationOutputV1
  # --- identity and version
  contract_type            "subscriptions.rated_obligation"
  contract_version         1
  occurrence_id            UUID          # module-owned, stable for all time
  emitted_at               instant (UTC, aware)
  generation               int >= 1      # timer generation that produced this emission

  # --- scope (exactly one, required)
  scope                    TenantScope{tenant_id} | PlatformScope

  # --- natural identity (the uniqueness contract of § 1.4)
  subscription_contract_id UUID
  contract_version_id      UUID
  contract_line_key        UUID          # stable lineage across supersession
  charge_model_code        declared code # ADR-0008 registry, never an enum
  source_code              declared code # ADR-0008 registry, e.g. "sales_order_line"
  source_id                UUID
  source_version           int >= 1
  period_start             instant (UTC, aware)
  period_end               instant (UTC, aware)   # half-open; period_end > period_start
  currency                 ISO-4217 alpha-3

  # --- exact pre-tax money (§ 1.8)
  pre_tax_amount           ExactAmount   # decimal string + currency + declared scale
  collection_timing        advance | arrears     # ONE field (§ 1.2)

  # --- rating provenance (complete or the emission is refused)
  coverage_start           instant, >= period_start
  coverage_end             instant, <= period_end, > coverage_start
  unit_price               ExactAmount
  quantity                 exact decimal > 0
  rate_basis               declared
  rate_unit                day | week | month | year
  rate_quantity            exact decimal > 0
  rate_units               exact decimal >= 0     # how many rate units the period holds
  proration_policy         none | full_period | actual_calendar_days | actual_elapsed_time
  proration_factor         exact decimal in [0, 1]
  timezone_name            IANA zone (as declared on the contract version)
  rating_policy_version    stable string          # the exact replay implementation
  offer_version_ref        opaque immutable reference + version int
  request_fingerprint      hex digest over the fields above (§ 2.3)

  # --- correction (§ 2.6)
  corrects_occurrence_id   UUID | absent

  # --- idempotency
  idempotency_key          stable string (§ 2.3)
```

**Absent by design**, and each absence is enforced by § 8's guard: no
`tax_amount`, `gross_amount`, `resolved_amount`, `accounting_treatment`,
`resolution_kind`, `opened_at`, `resolved_at`, `due_at`, `reversed_by_id`,
`tax_rate_id`, `tax_rate_percent`, `tax_inclusive`, `invoice_id`, `payment_id`,
`balance`, `outstanding`, `coverage`, `subscriber_id`, `account_id`,
`subscription_id`, `sales_order_id`, `deployment_id`, `capability_code`,
`plan_name`, or any provider or currency name as an identifier.

`pre_tax_amount` is **present on purpose** and is the one money field. It is
Sub's `net_amount` (`app/models/billing_contract.py:616`) under a name that
cannot be read as net-of-payments: Sub computes it from the contracted line,
rate units and proration alone (`rating.py::_net_for_period`), and adds tax
separately into `gross_amount`. The full per-field classification — 25 fields
stay, 16 move to billing, 4 become named rebuildable projections, 4 are flagged
as possibly reader-less — is `docs/inventories/a2-commercial-offer-source-audit.md`
§ 5, which is a decision gate in its own right.

Two fields need a rule rather than a flat ban. `tax_treatment_code` and
`payment_terms_days` ride on the contract version or line and are forwarded **as
opaque declared values the module never reads**: a code the module branches on is
a tax decision the module made, and a due date the module computes is `overdue`
the module owns. `discount_code`/`discount_amount` are different again — a
discount changes the **pre-tax** figure, so they are rating inputs and sit inside
the fingerprint (§ 2.3).

`period_start` and `period_end` are carried to the consumer even though the
period is subscriptions' fact, because Sub's prepaid collections policy
(`app/services/collections/prepaid_policy.py:57`) gates on the service period
having started. After the split that consumer reads billing's receivable, so
billing must stamp the period on acceptance or the guard is lost.

`collection_timing` is carried for the same reason and is deliberately **not**
persisted on the occurrence row. In Sub it is copied onto the obligation
(`obligations.py:536`) and then **never read by anyone** — every real consumer
reads `BillingContractVersion.collection_timing`. The field belongs to the
contract version; the output carries it because the consumer needs it.

### 2.2 Stable identity and version

`occurrence_id` is the module-owned stable identity of the recurring charge
occurrence. It is generated once, at scheduling, and never changes — including
across replay, redelivery, timer regeneration and contract supersession.

The contract's own version is carried **twice on purpose**: in the type name
(`RatedObligationOutputV1`) so a consumer can dispatch statically, and in the
`contract_version` field so a consumer reading a persisted envelope can dispatch
without parsing a type name. They must agree; disagreement is a terminal
rejection.

`generation` is the durable-timer generation that produced this emission. It is
provenance, not identity: two emissions of the same `occurrence_id` with
different generations are the **same** obligation redelivered, and the consumer
must accept once.

### 2.3 Idempotency key and request fingerprint

Two distinct values with two distinct jobs. ADR-0014 requires the fingerprint be
its own field, never folded into the key.

**`idempotency_key`** is derived deterministically from the natural identity of
§ 1.4 — the business key. Two emissions with the same key are the same
obligation, whatever else differs.

**`request_fingerprint`** is a hex digest over the exact rating inputs:
`unit_price`, `quantity`, `rate_basis`, `rate_unit`, `rate_quantity`,
`rate_units`, `proration_policy`, `proration_factor`, `coverage_start`,
`coverage_end`, `currency`, `timezone_name`, `rating_policy_version`,
`offer_version_ref`. Decimals are serialised in one canonical textual form and
instants as UTC ISO-8601, so the digest is stable across processes and
languages.

The three-way rule, which is the whole point of having both:

| Key | Fingerprint | Outcome |
|---|---|---|
| same | same | **replay** — accept once, report `was_duplicate`, no second effect |
| same | **different** | **conflict** — terminal rejection, never a silent overwrite |
| different | any | a new obligation |

*This is ported behaviour, not new design.* Sub's
`rating_input_fingerprint` (`app/services/billing/rating.py:177`) and its
`test_corrupt_recorded_rating_fingerprint_fails_replay` and
`test_same_natural_identity_with_different_coverage_fails_closed` already
implement and prove it.

**Nothing is reserved before the effect** (ADR-0014): the occurrence row and its
staged output commit together, and the key is not claimed in advance of the work.
Retention of the idempotency record is the **product's** policy, declared in the
adopter's settings, not a module constant.

### 2.4 Tenant or platform scope

`scope` is required and is exactly one of `TenantScope{tenant_id}` or
`PlatformScope`. There is no default, no `tenant_id: UUID | None`, no sentinel
tenant, and no polymorphic `scope_kind` + nullable `scope_id` (ADR-0023's
rejected workarounds, all three).

Scope travels on the output because the consumer's persistence plane must match
the producer's: a `TenantScope` obligation is accepted onto billing's tenant
plane and a `PlatformScope` obligation onto billing's platform plane. A consumer
that receives a scope it does not implement issues a **terminal rejection**, not
a best-effort write.

The module's public methods take the scope as a required first-class value.
`tenant_id: UUID | None` anywhere in the public surface is a build failure
(§ 8).

### 2.5 Currency and exact amount representation

`ExactAmount` is `{ amount: string, currency: ISO-4217 alpha-3, scale: int }`
where `amount` is a decimal already quantized to `scale`.

- **No float**, at any layer — construction, storage, serialisation, or
  comparison. A float in the module is a build failure.
- **`currency` is required.** There is no default and no inference from a
  setting, a locale, or a country. A missing currency is a construction failure.
- **`scale` is declared, not assumed.** It removes the ambiguity Sub currently
  carries between `Numeric(10,2)` and `Numeric(14,4)`, and it makes a
  three-decimal or zero-decimal currency representable without a code change.
- Every amount on one output carries the **same** currency. Mixed currency
  within a contract, a version, or a line set is refused at recording time —
  Sub's `test_mixed_currency_between_contract_and_line_is_refused` is the
  behaviour.
- `pre_tax_amount` is **pre-tax by definition**. The module applies no tax and no
  FX and stamps no tax or FX version. Billing does both and stamps both.
- No currency name appears as an identifier or a default anywhere in the module
  (C5, § 8's guard).

### 2.6 Source authority and provenance

**Authority.** `dotmac-subscriptions` is the sole authority for: the occurrence's
existence and identity; its service period; its coverage; the exact pre-tax
amount and every rating input that produced it. It is the authority for **none**
of: whether the money is owed, when it is due, whether it was paid, what tax
applies, or what the customer's balance is.

**Provenance.** Every output carries the full chain: the contract and version it
came from, the stable line lineage, the declared source fact and its version, the
immutable offer version reference, the rating policy version, and the
fingerprint. A consumer can therefore reconstruct the rating deterministically
and, given the same inputs, reproduce the same amount.

Sub already proves the mechanism end to end —
`test_replay_uses_recorded_tax_provenance_after_tax_configuration_changes` shows
replay reading recorded provenance rather than live configuration, and
`ck_billing_obligation_rating_provenance_complete`
(`app/models/billing_contract.py:526-544`) makes completeness a database
invariant. The module re-derives that constraint over the **pre-tax inputs
only**; copying it verbatim would force `tax_rate_percent` and `tax_inclusive`
onto a pre-tax row.

**Correction, supersession and reversal.**

- **Before emission**, a scheduled occurrence may be **cancelled**. Nothing was
  emitted, so nothing is corrected and no output exists.
- **After emission**, history is never edited. The module emits a **new**
  occurrence with a **new** `occurrence_id` and `corrects_occurrence_id` set to
  the prior one. `corrects_occurrence_id` names a prior fact so the consumer can
  reverse it; it carries **no instruction** about what the reversal should be,
  because the financial reversal, credit note or write-off is billing's decision
  and billing's alone.
- **Contract supersession** is the ordinary cause. A superseded version closes
  contiguously at `ends_at` and the next version begins there; occurrences after
  the boundary are generated from the new version. A **retroactive** supersession
  that changes an already-emitted period produces a correcting occurrence as
  above.
- The module never emits a negative `pre_tax_amount`. A correction is a new,
  non-negative rated fact plus a pointer; the sign of the financial consequence
  is billing's.
- An occurrence is never deleted, and `occurrence_id` is never reused.

### 2.7 Accepted errors and retry classification

The producer's own failures, all **fail-closed** and all raised before anything
is staged:

| Class | Examples | Behaviour |
|---|---|---|
| **Construction** | unknown IANA timezone; naive datetime; `anchor_day` outside 1–31; `fixed_anchor_day` without an anchor; non-positive interval count; non-positive rate quantity | typed domain error at value construction (Sub's `CadenceError` codes port directly) |
| **Fail-closed data** | missing price; missing cadence; missing currency; missing timezone; missing product link; undeclared charge model or source code; covered interval outside the period; a period walk exceeding its bound | typed domain error. **No zero price, no monthly default, no `Africa/Lagos`, no default currency is invented at a call site** |
| **Conflict** | same natural identity, different fingerprint; publishing an offer version that already exists; a version starting before the current effective one | typed conflict error, **never** a silent overwrite |

The consumer-facing classification the assembly must honour. The module does not
deliver, so this is a contract about how a delivery failure is interpreted:

| Classification | Cases | Assembly behaviour |
|---|---|---|
| **`retryable`** | transport failure, timeout, consumer unavailable, consumer's own transient store failure | redeliver the identical output, unchanged, with the same key and fingerprint. Redelivery is safe by § 2.3 |
| **`accepted-duplicate`** | same key, same fingerprint | terminal success. Not an error, not a retry, and not a second effect |
| **`terminal-rejected`** | fingerprint conflict; undeclared code; scope the consumer does not implement; contract version the consumer cannot read; malformed exact amount; `period_end <= period_start`; `proration_factor` outside `[0, 1]`; coverage outside period | **must not be retried.** It surfaces as durable, visible, reviewed work. A terminal rejection is never converted into a successful log entry or silently abandoned |
| **`terminal-refused-by-policy`** | the consumer's deployment profile does not bind an internal invoicing authority (ADR-0020 § 3) | terminal, and correct. Subscriptions keeps producing; the assembly declines to deliver. The occurrence remains a valid module fact |

A swallowed delivery failure with no named repair path is forbidden. The module
exposes a query for occurrences whose output has not been acknowledged so a
named reconciler can repair from authoritative inputs.

### 2.8 Compatibility rules

- **Additive-only within `V1`.** A new **optional** field with a safe absent
  meaning is a minor version of the package and does not change the contract
  version. Consumers **must ignore unknown fields**.
- **`V2` is required** for: a new required field; removing or renaming a field;
  changing a field's meaning, type, or nullability; changing the fingerprint
  input set or its canonical serialisation; changing the natural identity;
  changing the idempotency-key derivation.
- **The fingerprint input set is part of the contract.** Changing it silently
  would make a genuine conflict look like a replay. That is why it is enumerated
  in § 2.3 rather than described.
- **Both versions may be emitted during a migration**, selected by the assembly,
  never by a runtime flag inside the module. `rating_policy_version` is the
  separate, finer-grained knob for changing *how* an amount is computed without
  changing the contract's shape — and Sub already requires that every recorded
  policy version have an explicit replay implementation
  (`test_recorded_policy_version_must_have_an_explicit_replay_implementation`).
- **Declared vocabularies are open and are not a version axis.** A product adding
  a charge model or obligation source is a manifest declaration, not a `V2`. This
  is exactly why § 1.5 insists on registries.
- **The consumer's contract is not the producer's.** `AcceptRatedObligationV1`
  may version independently; the assembly owns the mapping. Neither side pins the
  other's package.
- The module's public surface stability policy follows `dotmac-ui`'s
  `COMPATIBILITY.md` shape: what is public, what may change, and what requires a
  major.

---

## 3. `CommercialEntitlementProjectionV1`

The module's statement of what a contract version commercially **intends**. It
is an output to whichever owner decides entitlement — Vendor CP's allocation and
licensing owner, or Sub's service and access owner. It is never a write.

### 3.1 Shape

```text
CommercialEntitlementProjectionV1
  contract_type            "subscriptions.commercial_entitlement_projection"
  contract_version         1
  projection_id            UUID      # stable per (contract_version_id, line, revision)
  emitted_at               instant (UTC)
  scope                    TenantScope{tenant_id} | PlatformScope

  subscription_contract_id UUID
  contract_version_id      UUID
  contract_line_key        UUID
  entitlement_codes        [declared code, ...]   # PRODUCT vocabulary, opaque here
  quantity                 exact decimal > 0
  intent                   intended_effective | intended_ended
  effective_from           instant (UTC)
  effective_until          instant (UTC) | absent   # half-open
  supersedes_projection_id UUID | absent
  source_code / source_id / source_version
  idempotency_key          stable string
  request_fingerprint      hex digest
```

**Absent by design:** no grant id, allocation id, licence id, deployment id,
`access_state`, RADIUS profile, service status, `is_active`, or any field that
would let a consumer treat this as an instruction rather than a statement. The
word in `intent` is deliberate.

### 3.2–3.8 The eight properties

- **Identity and version.** `projection_id` is stable per contract version, line
  lineage and revision. Version carried in both the type name and
  `contract_version`.
- **Idempotency and fingerprint.** Key derived from
  `(scope, contract_version_id, contract_line_key, intent, effective_from)`;
  fingerprint over `entitlement_codes`, `quantity`, `effective_from`,
  `effective_until` and the source triple. Same key + same fingerprint is a
  replay; same key + different fingerprint is a conflict.
- **Scope.** Required, exactly one of `TenantScope`/`PlatformScope`, same rules
  as § 2.4.
- **Currency and amount.** **None.** This contract carries no money at all — a
  deliberate absence. An entitlement projection that carried a price would
  invite a consumer to make a commercial decision the contract version already
  made.
- **Source authority and provenance.** Subscriptions is the authority for the
  *commercial intent* only: which declared codes the contract version bought, in
  what quantity, over what interval. It is the authority for **none** of:
  whether the entitlement is granted, whether a licence is issued, whether the
  service is activated, or what the customer may currently do. Provenance is the
  contract version plus the source triple.
- **Correction, supersession, reversal.** Intent is superseded, never edited:
  a new projection with `supersedes_projection_id` set. A contract version that
  ends emits `intended_ended`. The module **never** revokes a grant, never
  writes an allocation, never issues or revokes a licence, and never mutates
  product access state — the owner reacts. Vendor's
  `test_stage_writes_no_product_ws2_grant` is the existing proof that this
  boundary is holdable.
- **Errors and retry.** Same four classes as § 2.7. One addition:
  `terminal-rejected` includes an entitlement code not declared by the consuming
  product, because the codes are the product's vocabulary and the module never
  invents one.
- **Compatibility.** Same rules as § 2.8. Adding an entitlement code is a
  product manifest declaration and is **not** a contract version change.

---

## 4. What the module never produces

Stated as a contract because an absent contract is easier to enforce than a
prose boundary. `dotmac-subscriptions` publishes **no** contract carrying:
an invoice or credit note; a receivable, balance, available credit or prepaid
funding position; a payment, settlement, allocation, refund or reversal; a tax or
FX determination; a dunning case, ladder, arrangement or grace period; a vendor
account, commercial-agreement approval or countersignature; an entitlement
*grant*, licence or deployment activation; a product access, service or RADIUS
state change; a PSP or provider message of any kind; a usage observation or a
usage rating; a rendered document, document number or stored byte; a general
ledger journal.

The dossier's § 2 table gives the owner and the check for each.

---

## 5. Inbound command surface (assembly-called, not a cross-owner contract)

Typed commands, typed results, typed domain errors. No bare dict payload, no
`Any`, no product model, no `tenant_id: UUID | None`, no product/provider mode.

```text
PublishOfferVersionCommand(scope, offer_ref, version, prices[], effective_from,
                           effective_until?, command_id)
    -> PublishOfferVersionResult(offer_version_ref, was_duplicate)

WithdrawOfferVersionCommand(scope, offer_version_ref, reason, command_id)
    -> blocks new selection; touches no existing contract or occurrence

RecordSubscriptionContractVersionCommand(scope, contract_ref?, source_code,
    source_id, source_version, starts_at, ends_at?, currency, cadence,
    lines[], actor, reason, command_id, correlation_id, idempotency_key)
    -> ContractVersionResult(contract_id, version_id, version, line_keys, replayed)

SupersedeContractVersionCommand / EndContractVersionCommand / CancelContractVersionCommand

GenerateRecurringChargeCommand(scope, contract_version_id, period_index,
                               generation, command_id)
    -> OccurrenceResult(occurrence_id, replayed, staged_output)
```

Queries: `effective_version_at`, `cadence_of`, `offer_version_snapshot`,
`occurrences_for_contract`, `unacknowledged_outputs`.

Two rules that make these safe to call from an adapter:

- **Transaction authority.** A service receives a session and only
  adds/flushes; the **assembly adapter owns the commit**. This is Vendor's
  existing contract (`offers/service.py` docstring: "receives a `Session`… only
  add/flush; the route owns commit") and the opposite of Sub's catalogue CRUD,
  which calls `db.commit()` eleven times inside the service
  (`app/services/catalog/offers.py:181, 292, 393, 452, 479, 536, 603, 630, 680,
  751, 779`).
- **Typed domain errors, never `HTTPException`.** Sub's catalogue service raises
  it nine times (`:203, 307, 333, 498, 579, 586, 650, 726, 733`); Sub's own
  newer `web_catalog_offers.py:1177-1181` documents why that is wrong. The
  module follows the newer file: transport mapping is the adapter's.

---

## 6. Value objects and link helpers

- **`BillingCadence`** (§ 1.1) and **`Interval`** — persistence-free, frozen,
  validating in `__post_init__`, with `service_period`, `invoice_period`,
  `period_containing`, `proration_factor` and `rate_units_in`. Ported from
  `dotmac_sub:app/services/billing/cadence.py` with its 18-test suite, and with
  the vocabulary owned by the module rather than imported from a product model.
- **`ExactAmount`** (§ 2.5) and **`TenantScope`/`PlatformScope`** (§ 2.4).
- **Link helpers, one per plane, never one with a flag.**
  `link_tenant_offer_subject()` / `link_platform_offer_subject()` and the
  matching contract-subject pair. Each creates a product-owned link table **in
  the adopter's schema and lineage** — tenant-scoped with a composite FK and an
  isolation policy, or platform-scoped with a single-column FK and the revoke.
  Vendor's `capability_codes` and Sub's ISP service/access, region, usage, SLA,
  policy, RADIUS and portal-visibility semantics live there and nowhere else.

---

## 7. Ports, fakes, and contract suites

Every assembly-wired output is a **port**: a protocol, typed results, a stable
error taxonomy, an in-memory fake, and **one parametrized contract suite every
implementation must pass**. The point is that a product team develops the module
with no billing installed, no timer daemon, and no database.

| Port | Contract | Fake proves |
|---|---|---|
| `RatedObligationPublisher` | stage `RatedObligationOutputV1` atomically with the occurrence | replay yields one staged output; a conflicting fingerprint raises; a redelivery is byte-identical; nothing is staged when the occurrence write fails |
| `EntitlementProjectionPublisher` | stage `CommercialEntitlementProjectionV1` | supersession chains; `intended_ended` on contract end; no grant is ever written |
| `DurableTimer` | schedule one owner + entity at one instant, once per generation | a stale generation is refused; a replayed wake creates one occurrence; concurrent wakes create one occurrence and one output |
| `OfferVersionRepository` (×2 planes) | publish, get, list | parametrized over both planes: identical lifecycle decisions, different scope and isolation only |
| `SubscriptionContractRepository` (×2 planes) | record, supersede, effective-at | same parametrization |
| `OccurrenceRepository` (×2 planes) | schedule, replay, cancel | uniqueness under concurrency (PostgreSQL only) |

**Plane parity is a parametrized suite, not two suites.** Plane adapters may
differ in scope and isolation only; every lifecycle decision, every refusal and
every fingerprint must pass one shared parametrization. If they can diverge, the
"one behaviour" claim of ADR-0023 § 1 is false.

---

## 8. Canaries and architecture guards, each with a sensitivity proof

ADR-0018: a guard that cannot fail is not a guard, and an exemption must state an
enforceable premise. Each row's sensitivity proof is a deliberately introduced
violation the check **must** catch, kept as a test.

| # | Guard | Sensitivity proof |
|---|---|---|
| G1 | **No sibling-module import.** The package imports neither `dotmac_billing`, `dotmac_collections`, nor any consuming assembly. | Add `import dotmac_billing` behind a fixture; import-linter must fail. |
| G2 | **No cycle/preset enum.** No enum whose members are cadence presets (`monthly`, `quarterly`, `annual`, `biannual`, …); a closed interval-unit vocabulary plus a quantity is allowed. | Add `class BillingCycle(Enum): monthly = "monthly"`; the AST check must fail. Add `class IntervalUnit(Enum): day/week/month/year`; it must **pass** — proving the check discriminates rather than banning every enum. |
| G3 | **No day-count calendar.** No `timedelta(days=30 \| 365 \| 90 \| 28)` and no `days=` literal in period arithmetic. | Replace one `_shift` month branch with `timedelta(days=30)`; the check must fail **and** the cadence property test must fail independently, so two guards bite. |
| G4 | **Timing is a field, not a shape.** No module symbol — module, class, function, file — is named for exactly one of `advance`/`arrears`/`prepaid`/`postpaid`. | Add `def _run_prepaid_cycle()`; the name check must fail. Add a local variable `prepaid = timing is advance`; it must **pass**, proving the check targets the public shape and not every occurrence of the word. |
| G5 | **Same path, both timings.** The same scenario under `advance` and `arrears` traverses the same owner functions. | Instrument the call path; branch the engine on `collection_timing` at one site; the trace comparison must fail. |
| G6 | **No category-2 financial field on a module model.** The names enumerated in the dossier § 4.4 — themselves derived from the per-field classification in `a2-commercial-offer-source-audit.md` § 5 — may not appear as columns. | **Both directions, because this is a classification and not a ban on the word "amount".** Add `tax_amount`, `resolved_amount` or `due_at`; each must fail. Add `pre_tax_amount`, `period_start` or `rating_proration_factor`; each must **pass**. A check that rejects both halves is over-broad and is itself a defect. |
| G7 | **No `suspend` on a module aggregate**, and the occurrence state vocabulary is exactly `scheduled`, `due`, `emitted`, `cancelled`. | Add a `suspended` member; the vocabulary test must fail. Add `resolved`; same. |
| G8 | **No provider, currency, product or plan name** as an identifier or default (`paystack`, `flutterwave`, `stripe`, `remita`, `NGN`, `USD`, `splynx`, vendor/Sub names, plan names). | Add `default="NGN"` to a currency column; the grep check must fail. Add `currency: str` with no default; it must pass. |
| G9 | **No float**, anywhere, including in a test fixture that feeds a public value. | Construct `ExactAmount(amount=1.5)`; construction and the AST check must both fail. |
| G10 | **No nullable or polymorphic scope.** No `tenant_id: UUID \| None` in the public surface; no `scope_kind` + nullable `scope_id`; no sentinel tenant constant. | Add `tenant_id: UUID \| None = None` to a public method; the signature check must fail. |
| G11 | **Planes are declared and disjoint.** Every table is in exactly one of `tables`/`platform_tables`; a table in both is refused at manifest construction and again in the registry. | Declare one table in both; both refusals must fire, proving the second is not dead code. |
| G12 | **Tenant plane: `tenant_id NOT NULL`, RLS ENABLEd *and* FORCEd, composite uniques and FKs.** PostgreSQL canary. | Create the table without `FORCE`; the live-catalog canary must fail. Insert cross-tenant with the GUC set; the isolation canary must fail. |
| G13 | **Platform plane: `REVOKE ALL` from the tenant app role across every table *and column* privilege, plus schema `USAGE` and real row DML for the online platform role.** Covers **every** declared platform table. | Grant `SELECT` back to `app_user` on one platform table; the canary must fail. Revoke schema `USAGE` from the platform role; the reachability half must fail — proving declared-and-unreachable is caught too. (Vendor CP's existing test covers only `vendor_accounts` and ten licence tables; this gap must not be inherited.) |
| G14 | **No FK crosses the planes**, in either direction. | Add a platform→tenant FK; the catalog gate must fail. Then add tenant→platform; it must fail too, proving the check is not one-directional. |
| G15 | **Occurrence uniqueness holds under concurrency.** Two concurrent generators for one natural identity produce one row and one output. Real PostgreSQL. | Drop the unique constraint and run the concurrent test; it must fail. (Sub's equivalent legacy index is `postgresql_where`-only, so SQLite proves nothing here.) |
| G16 | **Same identity, different fingerprint is a conflict, not a replay.** | Re-emit with a changed `proration_factor`; the conflict must raise. Re-emit byte-identical; it must be accepted as a duplicate. |
| G17 | **Published rows are immutable.** An `UPDATE` on a published offer version, price or effective contract version fails — structurally, not by service convention. | `UPDATE` a published price row directly in SQL; the constraint or trigger must reject it. |
| G18 | **Undeclared vocabulary fails.** A charge model or obligation source not declared by an installed manifest is refused at use; a declared code with no consumer fails the build. | Emit with an undeclared `source_code`; the refusal must fire. Declare a code nothing reads; the build must fail. |
| G19 | **Fail-closed defaults.** A missing price, cadence, timezone, currency, source declaration or product link raises. | Remove `timezone_name` and assert the raise; then add a `"Africa/Lagos"` default and assert the guard catches the default itself. |
| G20 | **No `db.commit()` and no `HTTPException` in a module service.** | Add each to a service; both AST checks must fail. |
| G21 | **Retirement ratchets, two-directional**, over the nine parallel cadence owners and their five secondary consumers named in the dossier § 5.2. | Raise the count by one: fail. Lower it by one without lowering the baseline: fail. Lower both together: pass. All three cases are kept as tests, because a one-directional ratchet is the failure mode ADR-0018 names. |
| G22 | **A category-3 projection has exactly ONE writer, carries provenance, and has a named drift detector and repair.** This is an *adopter*-side guard, not a module one — it belongs in Sub's suite, and the module's job is to expose the query the detector compares against. | Add a second writer to `Subscription.next_billing_at`; the writer-count check must fail (today there are three: `catalog/subscriptions.py:983`, `:1498`, `account_lifecycle.py:1394`). Remove the drift detector while leaving the projection; the "projection without a detector" check must fail — otherwise a projection silently becomes a second authority, which is the exact thing category 3 exists to prevent. |

Guards G12–G15 and G17 require real PostgreSQL and run in the database lane. The
rest are static and run in `make check` / `make test-unit`.

---

## 9. Remaining adoption dependencies

The producer implementation now exists; these are release/cutover gates, not
permission to create another owner.

- **ADR-0017 P11** — closed before M0; the package, namespace and lineage are
  allocated together in the implemented extraction.
- **P3 durable timers** — a product-first timer implementation exists on the
  coordinated worktree. An adopter must bind this package's `DurableTimerPort`
  to its released contract and prove generation/replay. A cron scan over all
  contracts is not a substitute.
- **G2 / billing input** — ownership is **settled**: subscriptions produces
  `RatedObligationOutputV1`, billing consumes `AcceptRatedObligationV1`, the
  assembly maps between them. This is no longer an ownership gap. What remains is
  a **release** dependency: the consumer half is the billing team's
  implementation on the coordinated billing worktree. The assembly mapping and
  release proof remain required: **an adopter does not enable recurring output
  into nowhere**.
- **`vendor_cp.contracts` packaging** — recommended to stay in Vendor CP for now;
  see `docs/inventories/a2-commercial-offer-source-audit.md` § 7 for the
  evidence, the trigger, and the three preconditions.
