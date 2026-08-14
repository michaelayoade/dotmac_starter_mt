# A2 — independent verification of the commercial offer/contract source audit

**Audit id:** A2-V (verification of A2b)
**As of:** 2026-08-14
**Subject under review:** `docs/inventories/subscriptions-sources.md` (the
published A2b offer-catalogue audit) and
`docs/superpowers/plans/2026-08-14-subscriptions-vendor-sub-adoption.md`
**Starter revision:** working tree on `docs/whatsapp-connector-extraction-dossier`
(`5417e51`), **including the uncommitted ADR-0020 2026-08-14 amendment A1–A6**
**Sub revision read:** `27c76aaeebb792f089000af764d80f4dfe45c104`
**Vendor CP revision read:** `89848017d6b87e82dd4d6ffd0b2c9eaed5f9fee8`
**Governing decisions:** ADR-0006 § 5 / § 5a, ADR-0008, ADR-0017, ADR-0020
(incl. A4), ADR-0023, ADR-0024, ADR-0026
**Verdict:** the published conclusion is **upheld**. Four source
characterisations are overstated or imprecise and are corrected here; two
material facts are missing and are added; one boundary claim is too blunt to act
on and is replaced by a per-field classification (**§ 5, a decision gate**); one
point is genuinely open (§ 7).

## What this document is

An independent re-derivation of the A2 comparison from the two products' actual
source, written to test the published A2b conclusion rather than restate it. It
creates no package, namespace, lineage, or `EXTRACTION.toml`; ADR-0017's P11
gate is closed and this document does not touch it. No test was executed — CI is
this fleet's acceptance owner and a source audit is a reading exercise.

Where this verification agrees, it says so briefly and cites the line. Where it
disagrees, it says so plainly and shows the source.

### Verdict summary

| # | Published claim | Verdict |
|---|---|---|
| 1 | Vendor `OfferVersion` is 5 business columns, write-once service | **Verified**, with one addition: its immutability is service-only, not structural |
| 2 | Sub `OfferVersion` is effective-dated, ISP-coupled, "guarded but still updateable" | **Verified but understated** — it has *no uniqueness constraint at all* |
| 3 | Sub's active prices "can be changed only while no live subscription depends on them" | **Overstated** — the guard is narrower in two independent ways (§ 1.3) |
| 4 | Sub's version carries RADIUS, speed, portal visibility | **Imprecise** — those are on the offer *parent*, not the version (§ 1.4) |
| 5 | Vendor has no recurring contract, cadence, proration, obligation | **Verified** by exhaustive grep — zero hits across all of `src/vendor_cp/` |
| 6 | `BillingCadence` is 456 LOC + 287 LOC of focused tests | **Verified exactly** |
| 7 | Sub's preset cycle paths and `_add_months` are "parallel cadence owners" | **Verified and badly understated** — 5 month-add implementations, 9 owners (§ 2.1) |
| 8 | Sub's rating "reads mutable tax configuration" | **Verified at rating time, but the audit misses that Sub already fixed it at replay time** (§ 2.5) |
| 9 | Catalog CRUD commits and raises HTTP | **Verified exactly** — 11 commits, 9 raises |
| 10 | "Recurrence carries no financial status" | **Too blunt to act on** — replaced by a per-field classification (**§ 5, a decision gate**): most of the occurrence stays, fifteen columns move to billing, the `state` enum splits, four things become named rebuildable projections, four fields are flagged as possibly reader-less |
| 11 | The split honours Michael's 2026-08-12 ruling | **Verified** (§ 6) |
| 12 | Every preserved-parity-test path named | **All verified to exist**; two important files are missing from the list (§ 3) |

---

## 1. Claim-by-claim verification of the "Source comparison" table

### 1.1 Stable offer identity — **verified**

Vendor has **no offer parent table**. `offer_code` is a bare `String(120)`
repeated on every version row (`src/vendor_cp/offers/models.py:35`), with no FK
and nothing constraining the set of codes. The full Vendor table list —
`offer_versions`, `contracts`, `contract_lines`, `vendor_accounts`,
`allocations`, `allocation_entries` and twelve licence tables — confirms it.

Sub's `CatalogOffer` (`app/models/catalog.py:519-641`) is a mutable parent of
`versions` (`:618`) and `prices` (`:614`), written by
`app/services/catalog/offers.py::Offers.update` (`:257`). Its `code` column is a
**nullable** `String(60)` (`:541`) and is not the identity; `id` is.

**Addition the published audit does not record:** of `CatalogOffer`'s 34
business columns, only **five** are generic — `name`, `code`, `description`,
`status`, `is_active`. The other 29 are ISP product meaning. That is the entire
generic offer parent Sub has, and it is worth stating because it sizes the
migration: the module's `Offer` table is small, and almost all of `catalog_offers`
stays in Sub.

### 1.2 Immutable offer version — **verified, but understated**

| | Vendor `offer_versions` | Sub `offer_versions` |
|---|---|---|
| Source | `src/vendor_cp/offers/models.py:26-46` | `app/models/catalog.py:644-700` |
| Business columns | **5** (`offer_code`, `version`, `amount`, `currency_code`, `capability_codes`) | **18** |
| Relationships | **0** | **7** — `offer`, `region_zone`, `usage_allowance`, `sla_profile`, `policy_set`, `prices`, `subscriptions` (`:694-700`) |
| Uniqueness | `uq_offer_versions_code_ver` on `(offer_code, version)`, in the model (`:31`) **and** in the migration (`alembic/versions/v002_offer_versions.py:55`) | **none** |
| Mutation | `publish_offer_version` raises `ConflictError` on a duplicate (`offers/service.py:100-104`) | `OfferVersions.update` `setattr`s every supplied field and commits (`offers.py:587-603`) |
| Effective dating | none | `effective_start`, `effective_end`, both nullable (`:681-682`) |

Two corrections to the record:

**(a) The relationship count is 7, not 6.** ADR-0020 A4 and the Knowledge entry
`vendor-cp-capability-gaps-and-composable-plan` both say "~18 plus **6**
relationships". Read from `app/models/catalog.py:694-700` there are **seven**.
The conclusion is unaffected; the number should be corrected wherever repeated.

**(b) The strongest single fact in this audit, and it is missing from the
published one.** Sub's `OfferVersion` class declares **no `__table_args__` at
all**, and `alembic/versions/squashed_schema.sql` carries only
`offer_versions_pkey` on `id` plus four foreign keys (`:8066-8070`,
`:11796-11832`). **There is no database constraint preventing two rows from both
being version 3 of the same offer.** Vendor's five-column table is the only one
of the two where offer versioning is a database invariant.

This inverts the usual product-first intuition and it matters for sourcing:
Sub is the qualifying source for cadence, contracts, proration and recurrence,
and is **disqualified** as the source of the versioning primitive itself. The
published audit's disposition ("Vendor supplies the strict immutability/
idempotency canaries; Sub supplies effective dating") reaches the right answer —
but on the strength of a *behavioural* observation, when a *structural* one was
available and is much harder to argue with.

**(c) An addition on the Vendor side too.** Vendor's immutability is enforced by
the service, not the database. `v002_offer_versions.py:58-59` grants
`UPDATE, DELETE` on `offer_versions` to both `platform_api` and `app_admin`, and
the migration docstring says so: "never updated (**enforced by the service**)".
The module must make immutability structural rather than inherit a convention as
if it were a constraint.

### 1.3 Versioned price — **the published claim is overstated**

Published: "*active prices can be changed only while no live subscription
depends on them*."

That is not what the source does. `assert_offer_version_price_update_safe`
(`app/services/catalog_billing_governance.py:354-394`) blocks a change only when
**both** conditions hold, and each has a hole:

1. **The changed field set must intersect `_PRICE_CRITICAL_FIELDS`**
   (`:55-66` — `amount`, `billing_cycle`, `currency`, `offer_id`,
   `offer_version_id`, `price_type`, `unit`, `is_active`). A change to
   `description` on a live price passes unguarded.
2. **`_live_version_subscription_count` must be non-zero** (`:124-131`), and it
   counts `Subscription.offer_version_id == version_id`. But
   `Subscription.offer_version_id` is **nullable** (`app/models/catalog.py:904`)
   — a subscription may pin the offer through `offer_id` (`:901`, `NOT NULL`)
   without pinning a version. **A live subscription that did not pin a version
   does not protect that version's price from being edited.**

`_live_version_subscription_count` also counts only
`_LIVE_SUBSCRIPTION_STATUSES` (`:34-40`: `pending`, `active`, `blocked`,
`suspended`, `stopped`) — a defensible product policy, but it means a version
whose subscriptions are all in another state is freely mutable.

The published audit's disposition is nonetheless right, and this evidence
*strengthens* it: "*A price change publishes a new offer version; no live-row
mutation guard is needed as a substitute for versioning*" is exactly the
correct reading of a guard with two holes in it.

**Two further column-level defects at this table that the published audit does
not list:**

- `currency: Mapped[str] = mapped_column(String(3), default="NGN")`
  (`app/models/catalog.py:716`, repeated at `:767` `OfferPrice` and `:797`
  `AddOnPrice`). A currency literal as a column default. C5 forbids exactly this
  as an identifier or default in the shared module.
- `billing_cycle` on the price row (`:717`) is a **third** place cadence is
  persisted in Sub's catalogue, nullable, beside the version's `billing_cycle`
  (`:659`) and the offer's (`:545`). It must not travel.
- Sub is internally inconsistent on exact-money scale: `Numeric(10,2)` here,
  `Numeric(14,4)` on contract versions, lines and obligations. The module must
  declare one representation; it cannot port both.

### 1.4 Product meaning — **imprecise, and the imprecision has migration cost**

Published: Sub's side is "*service/access type, region, usage, SLA, policy,
RADIUS, speed, and portal visibility*".

Read against the source, that list conflates two tables. Sub's `OfferVersion`
carries `service_type`, `access_type`, `price_basis`, `billing_cycle`,
`contract_term`, `region_zone_id`, `usage_allowance_id`, `sla_profile_id`,
`policy_set_id` (`:656-676`). It carries **no** RADIUS profile, **no** speed
columns, **no** portal visibility, **no** `plan_category`, and **no** VAT.
Those live only on `CatalogOffer` (`:566-599`) and in `offer_radius_profiles`.

The disposition ("*none belongs in the generic tables; each assembly owns
plane-specific link tables*") is right either way. But the migration consequence
is real and should be stated in the adoption plan: **the tenant link table for
an offer VERSION is narrower than the one for an OFFER**, and S1's list in the
adoption plan reads as one flat set. Anything the version does not carry today
(speed, RADIUS, portal visibility, plan family) is inherited from the parent at
read time, and that inheritance is behaviour the migration must preserve or
explicitly retire.

### 1.5 Legal commercial agreement — **verified**

`Contract` (`src/vendor_cp/contracts/models.py:45-80`) has **14** business
columns and 1 relationship: `customer_ref` (explicitly "*NOT a tenant FK*"),
`legal_entity`, `currency_code`, `term_start`, `term_end`, `status`,
`activation_rule`, `content_hash`, `approval_policy_code`,
`approval_policy_version`, `submitter_id`, `activated_at`, `superseded_by_id`
(declared and unused), `last_reason`. Eight-state machine at `:31-42`.

Sub's `billing_contracts` (`app/models/billing_contract.py:191-245`) has **5**:
`account_id`, `subscription_id`, `authority`, `opened_at`, `closed_at`.

**The two headers overlap on zero business columns** — not one name and not one
meaning. That is a weaker overlap than the `offer_versions` pair already judged
insufficient to prove a shared contract. The published claim "*No equivalent*"
is verified at the strongest possible reading.

### 1.6 Subscription contract — **verified**

`BillingContractVersion` (`billing_contract.py:248-420`) is **38** business
columns, 2 relationships, 2 unique constraints, 3 indexes, 5 check constraints,
plus a PostgreSQL temporal exclusion constraint added in its migration. It
carries the whole composable cadence (`rate_basis`, `rate_unit`,
`rate_quantity`, `service_interval_unit/count`, `invoice_interval_unit/count`,
`collection_timing`, `alignment`, `anchor_day`, `end_of_month_rule`,
`timezone_name`, `proration_policy`), the half-open effective interval,
structural source (`source_kind`/`source_id`/`source_version`), supersession,
and command/idempotency evidence.

`BillingContractLine` (`:423-478`) is **11** business columns, with
`contract_line_key` (`:451`) as the stable lineage across supersession and
`UniqueConstraint(contract_version_id, charge_component, component_key)`.

Vendor has no counterpart to either. Verified.

### 1.7 Cadence, proration, rating, obligation, service lifecycle — **verified**

- **Cadence:** `app/services/billing/cadence.py` is **456** lines and
  `tests/test_billing_cadence.py` is **287** lines. Both figures in the published
  audit are exact. The module owns no records, opens no transaction and reads no
  session — already ADR-0023's persistence-free engine shape. One port delta the
  published audit does not name: it imports its vocabulary from
  `app.models.billing_contract` (`cadence.py:32-39`), so the module must own
  those types itself.
- **Proration:** `proration_factor` (`cadence.py:373`) over a declared
  `ProrationPolicy` (`billing_contract.py:116-122`). Verified.
- **Rating:** `app/services/billing/rating.py`, `OWNER = "billing.rating"`
  (`:50`), a pure resolver. Combines fixed rating, proration, tax
  (`_effective_tax_rate`, `:195`) and a `usage_metered` rate basis
  (`billing_contract.py:82`). Verified.
- **Recurring obligation:** `BillingObligation` (`billing_contract.py:481-703`)
  fuses schedule/rating with opening, settlement, credit, write-off and
  cancellation. Verified — and § 5 quantifies it.
- **Vendor has none of the above.** A grep for
  `cadence|proration|recurring|renewal` across the whole of
  `/Users/michaelayoade/Downloads/management/dotmac_vendor_control_plane/src/vendor_cp/`
  returns **zero files**. Vendor's contract term is a `term_start`/`term_end`
  `Date` pair, not a cadence.
- **Service/access lifecycle stays product-owned.** `Subscription`
  (`catalog.py:885-1006`) carries `status`, `access_state`, RADIUS/NAS/IP/bundle/
  provisioning and `splynx_service_id`/`router_id`. Verified.

---

## 2. Verification of "Source defects that must not be ported"

### 2.1 Defect 1 (parallel cadence owners) — **verified and badly understated**

The published audit names two helpers (`_add_months`, `_compute_next_billing_at`)
and the preset `BillingCycle` paths. The source has **five distinct month-add
implementations** and **nine parallel cadence owners**. Only eight modules import
the canonical `cadence.py`.

| # | Parallel owner | Evidence |
|---|---|---|
| 1 | `app/services/billing_automation.py:272 _add_months` | naive `min(day, monthrange(...))` clamp, **no `EndOfMonthRule`** |
| 2 | `app/services/billing_automation.py:288 _period_end` | full 5-branch `BillingCycle` fan-out (`:298-306`), **UTC-only, no timezone** |
| 3 | `app/services/billing_automation.py:2542-2557` | a *third* inline month-arithmetic block inside `generate_cancellation_credit` |
| 4 | `app/services/catalog/subscriptions.py:92 _add_months` | byte-identical duplicate of #1 |
| 5 | `app/services/catalog/subscriptions.py:135 _compute_next_billing_at` | 5-branch; writes `Subscription.next_billing_at` at `:983` (create) and `:1498` (update) |
| 6 | `app/services/catalog/subscriptions.py:485 _billing_cycle_start` | the same arithmetic run *backwards* |
| 7 | `app/services/web_catalog_calculator.py:19 _cycle_bounds` | its own docstring admits it "mirrors" two other owners; `timedelta(days=7)` for weekly at `:34` |
| 8 | `app/services/payment_arrangements.py:132 _add_month_clamped` / `:161 _calculate_next_due_date` | a fourth month-add, over a *separate* `PaymentFrequency` enum |
| 9 | `app/services/web_billing_overview.py:193 _add_months` | a fifth; reporting-only, but still a duplicate calendar implementation |

Secondary consumers that inherit the defect:
`app/services/customer_portal_flow_common.py:38-52` **loops
`_compute_next_billing_at` up to 240 times** to project a next bill date;
`app/services/account_lifecycle.py:1394` and `:1423` write `next_billing_at`
from it on resume; `app/services/subscription_billing_treatments.py:254` and
`app/services/prepaid_recovery_billing.py:349-351` re-import
`billing_automation._period_end`; `app/services/prepaid_service_renewals.py`
imports it at `:706`, `:2125`, `:2286` **despite** already using the canonical
`service_period` on its settlement path (`:171-176`).

**Two things the published defect list is right about and one it gets right by
omission:** `relativedelta` appears **nowhere** in Sub, and `timedelta(days=30)`
/ `days=365` / `days=90` are **never** used for billing cadence (every hit is an
analytics or retention window). C1's forbidden `days=30`/`days=365` shape is not
present. The defect is the enum fan-out and the duplicated month arithmetic, not
day-count approximation.

**This changes the retirement estimate materially**, and § 4 of the adoption
plan (S4) currently names only three targets — `BillingCycle` branches,
`_add_months`, `_compute_next_billing_at`, and the plan-change
`_calculate_proration`. The real ratchet has at least nine.

### 2.2 Defect 2 (mutable versions/prices) — **verified**, and narrower than described (§ 1.3).

### 2.3 Defect 3 (product vocabulary) — **verified**, with the offer/version
precision correction of § 1.4.

### 2.4 Defect 4 (obligation fuses recurrence with settlement) — **verified**;
§ 5 names the exact columns.

### 2.5 Defect 5 (rating reads mutable tax configuration) — **verified at rating
time; the published audit misses that Sub already solved it at replay time**

`rating.py:195 _effective_tax_rate` does read live tax configuration, and
`BillingObligation.rating_tax_rate_id` FKs `tax_rates` (`billing_contract.py:651-654`).
So the defect is real.

But the obligation also records `rating_tax_treatment_code`,
`rating_tax_rate_percent` and `rating_tax_inclusive` as **immutable provenance**,
protected by `ck_billing_obligation_rating_provenance_complete` and
`ck_billing_obligation_rating_tax_source` (`:526-562`), and
`replay_recorded_rating` (`obligations.py:309`) rates from the recorded
provenance rather than from live config. `tests/test_billing_rating.py::
test_recorded_policy_version_must_have_an_explicit_replay_implementation` and
`tests/test_billing_obligations.py::
test_replay_uses_recorded_tax_provenance_after_tax_configuration_changes`
prove it, and `test_corrupt_recorded_rating_fingerprint_fails_replay` proves the
fingerprint is load-bearing.

**This is an asset, not a defect, and it should be listed as one.** The
provenance-plus-fingerprint mechanism is precisely what
`RatedObligationOutputV1` needs, and it is already built and tested. The
published audit's disposition ("*Subscriptions records pre-tax rating
provenance; billing stamps tax/FX versions*") is correct — it just undersells
the source it is porting.

### 2.6 Defect 6 (service commits, raises HTTP) — **verified exactly**

`app/services/catalog/offers.py` calls `db.commit()` **11 times** (`:181`,
`:292`, `:393`, `:452`, `:479`, `:536`, `:603`, `:630`, `:680`, `:751`, `:779`)
and raises `HTTPException` **9 times** (`:203`, `:307`, `:333`, `:498`, `:579`,
`:586`, `:650`, `:726`, `:733`).

Sub's own newer code already knows this is wrong:
`app/services/web_catalog_offers.py:1177-1181` documents the opposite policy —
"*Typed rather than an HTTPException: this module is a service, and the
transport-boundary guard in tests/architecture forbids services reaching for
FastAPI*" — and defines `OfferNameConflict(DomainError)` at `:1174`. The module
must follow the newer file, not the older one.

### 2.7 Defect 7 (fail closed on missing terms) — **verified as a requirement,
and the literals it must refuse all exist**

Seven monthly defaults and one timezone literal are live in Sub today:
`app/services/billing/contracts.py:413 timezone_name="Africa/Lagos"`;
`billing_automation.py:1617` and `:604` `cycle or BillingCycle.monthly`;
`catalog/subscriptions.py:154` monthly fallthrough;
`prepaid_service_renewals.py:427-431` `or BillingCycle.monthly` then `continue`;
`web_catalog_offers.py:177` and `:212` monthly form defaults;
`catalog.py:546` `default=BillingCycle.monthly` on `CatalogOffer`;
`catalog.py:660` the same on `OfferVersion`. Plus the three `"NGN"` column
defaults of § 1.3.

The published requirement is correct and this is the list it must be measured
against.

---

## 3. Verification of the preserved-parity-test list

**Every path the published audit names exists.** Verified by `find`:

| Published entry | Real path | Tests | Verdict |
|---|---|---|---|
| Vendor offer tests | `dotmac_vendor_control_plane:tests/unit/test_offers.py` | 4 | exists; proves exact-Money persistence, undeclared-capability rejection, immutability, `command_id` idempotency |
| `tests/test_billing_cadence.py` | `dotmac_sub:tests/test_billing_cadence.py` | 18 | exists; covers leap year, month end, strict-same-day, half-open, timezone and alignment exactly as claimed |
| `tests/test_billing_contracts.py` | `dotmac_sub:tests/test_billing_contracts.py` | 14 | exists; covers immutable versions, contiguous supersession, line lineage, effective-at, mixed-currency refusal, idempotency |
| `tests/test_billing_rating.py` | `dotmac_sub:tests/test_billing_rating.py` | 11 | exists; 5 fixed/proration/determinism cases port here, 4 tax cases to billing, 1 usage case waits |
| `tests/test_billing_obligations.py` | `dotmac_sub:tests/test_billing_obligations.py` | 14 | exists; natural identity, replay, coverage conflict, consecutive periods all present |
| "Phase-2 durable shadow evidence" | `dotmac_sub:tests/test_billing_phase2_shadow.py` | 11 | exists; the audit did not name the file — it should |

**Two files missing from the published list that must be on it:**

1. **`dotmac_sub:tests/architecture/test_billing_target_architecture.py`** (13
   tests, 417 lines). This is ADR-0007's ratchet — `test_no_new_mutable_money_counter_writes`,
   `test_no_new_metadata_financial_authority_reads`,
   `test_no_new_scheduled_financial_sweep`, `test_sweep_baseline_is_sorted_and_unique`.
   It is the closest existing thing in the fleet to the two-directional ratchet
   ADR-0018 requires, and the module's own guards should be ported from it
   rather than designed fresh.
2. **`dotmac_sub:tests/test_subscription_billing_cadence.py`** (7 tests). The
   *second* cadence test file, covering cadence **precedence** — contracted
   cadence beats offer cadence, offer cadence is snapshotted when uncontracted,
   the biller prefers subscription over offer. Precedence is exactly the
   behaviour that disappears when the four parallel cadence stores retire, so
   these are the tests that prove the retirement did not change customer-visible
   behaviour. Omitting them would lose the only test-level record of the rule.

Also worth porting: `dotmac_sub:tests/test_catalog_billing_governance.py` (7
tests, proving a live offer's cadence/price cannot be mutated in place) and
`dotmac_sub:tests/test_offer_name_uniqueness.py` (6 tests, which prove the
analogous uniqueness rule for offer *names* **at the database** — showing the
pattern is understood in Sub and simply absent for versions).

---

## 4. Claims that could not be verified, and dispositions that do not follow

### 4.1 Could not verify from source

- **"Vendor CP adopts the platform plane first."** This is a sequencing decision
  by Michael recorded in ADR-0020 A4, not a source fact, and nothing in either
  repository confirms or refutes it. Recorded as a decision, not evidence.
- **The seven-tenant/seven-platform table names** in the published audit's plane
  table. These are design output, and the audit says so ("*names are design
  input, not a namespace allocation*"). Correctly flagged there.
- **Vendor's platform-plane isolation for the offer/contract tables is claimed
  but untested.** `REVOKE ALL ... FROM app_user` exists at
  `v002_offer_versions.py:60` and `v004_contracts.py:31`, but
  `tests/migration/test_vendor_migration_rehearsals.py::
  test_platform_role_access_and_tenant_role_denial` asserts denial only for
  `vendor_accounts` and ten licence tables. Under ADR-0023 § 3 "the REVOKE **is**
  the isolation" on the platform plane and must be checked as strictly as an RLS
  policy. **The module must not inherit this gap** — its platform canary must
  cover every declared platform table.
- **Vendor's `suspend`/`reinstate`/`terminate`/`expire` are implemented and
  untested.** `contracts/service.py:432`, `:446`, `:459`, `:491`;
  `tests/unit/test_contracts.py` (7 tests) covers submit, approve, reject and
  activate only. Nothing ports from here, but the gap matters because ADR-0020
  A4's boundary *depends* on suspension being a commercial-contract transition
  rather than a subscription one — and that claim is currently untested in its
  own repository.

### 4.2 A disposition that does not follow from its own evidence

The published audit's row on **versioned price** reads: "*Price rows are
immutable children of an immutable offer version. A price change publishes a new
offer version; no live-row mutation guard is needed as a substitute for
versioning.*"

The conclusion is right. The stated evidence — that Sub's guard restricts
changes to prices with no live subscription — does not support it, because § 1.3
shows the guard does not actually do that. The disposition is **correct on
better evidence than the one given**: the guard has two structural holes, which
is a stronger argument for replacing it with versioning than "it works but we
prefer versioning". Recorded so the reasoning is not repeated in weaker form.

### 4.3 A gap in the adoption plan, not the audit

Adoption plan S4 names four retirement targets. § 2.1 finds at least nine
parallel cadence owners plus five secondary consumers. The plan's shadow matrix
(daily/weekly/monthly/N-month/yearly; month-end 29/30/31; leap years;
strict-same-day; three alignments; DST; four proration policies) is the right
shape, but the ratchet's *baseline* must be built from the real nine-owner list
or it will pass while three owners survive.

---
## 5. DECISION GATE — the occurrence-field verdict

**This section is one of Michael's three active decision gates ("Team 1's
source-backed A2 and occurrence-field verdict"). It is the classification, not a
recommendation to delete.**

### 5.0 The boundary, as sharpened

"Recurrence carries no financial status" is too blunt to act on, and the
published audit's two-column *Subscriptions owns / Billing owns* table allocates
the **concepts** without allocating the **columns**. The operative boundary is:

- **Subscriptions MAY own** scheduling status — `scheduled`, `emitted`,
  `cancelled`, `replaced`, `superseded` — and the **immutable amount/price
  snapshot** needed to build `RatedObligationOutputV1`.
- **Subscriptions MUST NOT own** `paid`, `partially_paid`, outstanding balance,
  allocation, coverage, `overdue`, or any collections status.

Every amount or status field on Sub's recurring and occurrence models is
therefore assigned **exactly one** of three dispositions:

1. **stays** — scheduling, commercial term, identity, or rating provenance;
2. **moves to Billing** — a downstream financial fact;
3. **becomes an explicitly rebuildable projection** — a local read convenience
   with a named writer, provenance, drift detection and idempotent repair, and
   **never** an authority.

A field with no identified reader is flagged separately in § 5.6; it may simply
retire.

### 5.1 Who actually reads `BillingObligation` today

This is the fact that makes the classification tractable, and it is far smaller
than expected. A repository-wide sweep (`app/`, `scripts/`, `templates/`,
`src/`, excluding `tests/`) finds exactly **five** modules that touch a
`BillingObligation` row, plus one that touches only the event payload:

| Reader | Kind | Fields consumed |
|---|---|---|
| `app/services/collections/postpaid_policy.py:26-58` | collections policy | `accounting_treatment`, `state`, `due_at`, `gross_amount`, `resolved_amount`, `account_id`, `subscription_id`, `currency`, `id` |
| `app/services/collections/prepaid_policy.py:35-70` | collections policy | `accounting_treatment`, `state`, `gross_amount`, `resolved_amount`, `period_start`, `account_id`, `currency` |
| `app/services/collections/mode_policies.py:20` | shared constant | `ACTIONABLE_STATES = (ObligationState.open, ObligationState.partially_resolved)` — the **only** enum-member reference outside the owner |
| `app/services/billing/shadow_verification.py:1143-1164` | reconciler | `authority`, `net_amount`, `tax_amount`, `gross_amount`, `rating_input_fingerprint`, and all 16 `rating_*` **indirectly** via `replay_recorded_rating` |
| `scripts/billing/billing_target_shadow.py:102-116, :860-862, :885` | operator CLI | JSON dump of `state`, `gross_amount`, `resolved_amount`, `accounting_treatment`; and the only caller of the two policies |
| `app/services/events/handlers/billing_lifecycle_projection.py:811, :902, :967-981` | event adapter | calls `consume_contract_shadow` (a **writer** path) and extracts only `obligation_id` from the payload — **zero ORM column reads** |
| `app/models/sales_order_funding.py:85-116` | **projection table** | `obligation_id` as an opaque UUID plus its **own** `resolved`, `resolution_kind`, `resolved_event_id`, `resolved_at` — **event-fed, never a table read** |

Nothing in `app/web/`, `app/api/`, `app/tasks/`, `app/schemas/` or `templates/`
reads an obligation, and there is no raw SQL against `billing_obligations`
anywhere.

Four consequences fall straight out, and the last two are uncomfortable:

- **The only external consumers of obligation *financial* state are the two
  collections policies.** After cutover those are `dotmac-collections` reading
  **billing's** published receivable contract, not subscriptions'. Every field
  they read is category 2, and its replacement is named.
- **`SalesOrderFundingObligation` is already category 3, done correctly** — and
  it is even more decoupled than it first appears: it depends on **no**
  `BillingObligation` column at all. It stores `obligation_id` as an opaque FK
  and maintains its own `resolved` boolean; the resolution facts
  (`resolution_kind`, `resolved_at`, `resolved_event_id`) are **caller-supplied
  parameters** (`app/services/sales_order_funding.py:198-200`), never read from
  the obligation row. That is the template, and the cutover changes only its
  upstream.
- **The two collections policies are reachable only from the CLI.**
  `plan_postpaid_consequence` and `plan_prepaid_consequence` have no caller in
  any service, task or handler — only `scripts/billing/billing_target_shadow.py:860-862`
  and tests. Every "external reader" of obligation financial state is therefore
  operator-CLI-only today.
- **The funding gate is likewise dark.** `register_finite_obligations` and
  `record_obligation_resolution` have no non-test caller; the live
  `sales_order.funding_satisfied` path
  (`app/services/sales_orders.py:1254-1266` → `sales_lifecycle_projection.py:41`
  → `sales_fulfillment.py:391`) is still the legacy `SalesOrder.amount_paid`
  flow and never touches `SalesOrderFundingGate`.

**Read the last two carefully before using this section to size the work.**
ADR-0007 is in `SHADOWING`, so most of the obligation machinery is not yet load
bearing. That makes the split *cheap now* and is an argument for doing it before
these paths go live — but it also means the classification below cannot be
validated by production behaviour, only by the shadow harness.

### 5.2 `BillingObligation` — disposition per field

`app/models/billing_contract.py:481-703`, 44 business columns.

#### Category 1 — STAYS in `dotmac-subscriptions`

| Field | Line | Why it stays |
|---|---|---|
| `contract_id`, `contract_version_id`, `contract_line_key` | `:568`, `:573`, `:578` | natural identity; `contract_line_key` is the stable lineage across supersession |
| `charge_component`, `source_kind`, `source_id`, `source_version` | `:597`, `:600`, `:604`, `:605` | natural identity. `charge_component` and `source_kind` become ADR-0008 **declaration registries**, not enums |
| `period_start`, `period_end` | `:608`, `:611` | the half-open service period — the scheduling fact itself |
| `currency` | `:615` | natural identity, and required by both owners; carried on the output |
| **`net_amount`** | `:616` | **the immutable pre-tax rated snapshot Michael's rule explicitly permits.** Sub computes it from the contracted line, rate units and proration alone (`rating.py::_net_for_period`); tax is added *separately* into `gross_amount`. Renamed `pre_tax_amount` in the module so the name cannot be mistaken for a net-of-payments figure |
| `rating_provenance_complete`, `rating_policy_version`, `rating_coverage_start/end`, `rating_unit_price`, `rating_quantity`, `rating_rate_basis`, `rating_rate_unit`, `rating_rate_quantity`, `rating_timezone_name`, `rating_proration_policy`, `rating_rate_units`, `rating_proration_factor`, `rating_input_fingerprint` | `:629`–`:657` | fifteen columns of pre-tax rating provenance. This is the module's core asset, not a liability — it is what makes replay deterministic and what `RatedObligationOutputV1` carries |
| `collection_timing` | `:664` | the ONE field of C2. A contract term, not a financial state; also carried on the output because billing needs it |
| `is_finite` | `:667` | a schedule property — does this charge recur or fund one order. See § 5.6 |
| `corrects_id` | `:683` | supersession of a **rated fact**, not a financial reversal. Becomes `corrects_occurrence_id`: it names a prior fact so a consumer can reverse it, and carries no instruction about what the reversal should be |
| `command_id`, `correlation_id`, `idempotency_key` | `:690`, `:691`, `:694` | at-most-once evidence (ADR-0014) |
| `created_at`, `updated_at` | `:695`, `:698` | audit |
| `state` — **members `scheduled` and `canceled` only** | `:669` | see § 5.3 |

#### Category 2 — MOVES TO BILLING

For each: the Sub reader today, and what it reads instead afterwards.

| Field | Line | Reader today (verified sweep) | Reads instead afterwards |
|---|---|---|---|
| `tax_amount` | `:619` | `billing/shadow_verification.py:1145` only — a parity comparison against the re-rated result | billing computes and owns it; the parity check becomes a cutover comparison against billing's accepted obligation |
| `gross_amount` | `:622` | `shadow_verification.py:1146`; `collections/postpaid_policy.py:46` and `prepaid_policy.py:59` (both as `gross - resolved`); `billing_target_shadow.py:113` | `dotmac-collections` reads **billing's** receivable contract, which should publish the outstanding amount **directly** rather than making every consumer subtract two fields — two consumers already do that subtraction identically, which is a defect waiting to diverge |
| `resolved_amount` | `:658` | the same two policies + the CLI dump | same |
| `resolution_kind` | `:674` | **none.** `sales_order_funding.py:112` is an unrelated `String(60)` column on a different table, caller-populated | the projection's own column, fed by billing's resolution fact |
| `opened_at`, `resolved_at` | `:677`, `:678` | **none**, and the writes are unreachable in production — no non-test caller invokes `BillingObligations.open()` or `.resolve()` | billing's receivable lifecycle |
| `due_at` | `:679` | `collections/postpaid_policy.py:42-43` (the overdue test) and `prepaid_policy.py:112` — but **always NULL in production**: the only production `ScheduleObligationCommand` (`billing_lifecycle_projection.py:867-899`) omits it, so the overdue test always short-circuits | billing's receivable, derived from the contract's `payment_terms_days`. **Subscriptions never computes a due date** — that is the `overdue` half of Michael's exclusion |
| `accounting_treatment` | `:661` | `collections/postpaid_policy.py:38` and `prepaid_policy.py:50` — the first branch in both; `billing_target_shadow.py:116` | billing's receivable. The **declared code** still rides on the contract line and is forwarded opaquely; the module never branches on it. § 8's guard proves that with a sensitivity test |
| `reversed_by_id` | `:686` | **zero readers AND zero writers in the entire repository, tests included.** Only the model line and `alembic/versions/430_billing_contract_obligation_identity.py:388` | see § 5.6 — this is a candidate to retire rather than move |
| `rating_tax_treatment_code`, `rating_tax_rate_percent`, `rating_tax_inclusive` | `:650`, `:655`, `:656` | none directly; reachable only inside `replay_recorded_rating` | billing's applied-tax snapshot |
| `rating_tax_rate_id` | `:651` | none directly | **cannot survive in any form** — a foreign key from the recurrence row to `tax_rates`, crossing an owner boundary (ADR-0024 § 2) and a cross-plane FK on the platform side (ADR-0023 § 4) |
| `state` — members `open`, `partially_resolved`, `resolved`, `written_off` | `:669` | both policies via `mode_policies.py:20 ACTIONABLE_STATES`, which references **only** `open` and `partially_resolved`; `resolved` and `written_off` have no external reference at all | billing's receivable state. See § 5.3 |
| `account_id`, `subscription_id` | `:581`, `:586` | both policies, to scope a case | Sub product links held by the **assembly**, not module columns. Billing's receivable carries its own party reference |

**One cutover requirement falls out of this table and must not be missed.**
`collections/prepaid_policy.py:57` reads `obligation.period_start` — a
**category 1** field — on the same row as the financial fields. After the split
those two live in different modules. **Billing's receivable must therefore carry
the service period forward**, and it can: `period_start`/`period_end` arrive on
`RatedObligationOutputV1` and billing stamps them on acceptance. If that is
omitted, prepaid collections loses its "the service period has not started;
nothing is uncovered yet" guard and starts manufacturing cases for future
periods.

#### Category 3 — REBUILDABLE PROJECTION (named writer, provenance, drift, repair)

| Field / table | Line | Why it cannot simply move or die | The named writer afterwards |
|---|---|---|---|
| **`SalesOrderFundingObligation`** (`obligation_id`, `resolved`, `resolution_kind`, `resolved_event_id`, `resolved_at`) | `app/models/sales_order_funding.py:85-116` | Sub's funding gate must know whether an order's finite obligations are resolved and must not query another module's tables. It is **already** an event-fed projection with provenance, and it reads **no** `BillingObligation` column — `obligation_id` is an opaque FK and the resolution facts are caller-supplied (`app/services/sales_order_funding.py:198-200`) | Sub's funding-gate consumer, fed by **billing's** resolution facts instead of Sub's. `obligation_id` becomes the opaque correlation the assembly holds between module occurrence and billing receivable. Drift detection compares the projection to billing's published facts; repair replays from them. **Caveat:** the gate is dark in production today (§ 5.1), so its cutover is a wiring change, not a migration |
| **`Subscription.next_billing_at`** | `app/models/catalog.py:946` | **49 distinct modules** (44 Python + 5 templates), plus seven one-off scripts. The heaviest are `prepaid_service_renewals.py` (50 refs — it *selects the due cohort* on `next_billing_at <= evaluated_at` at `:1794-1802`, `:2093-2098`, `:2301-2306`), `service_extensions.py` (68 refs), `billing_cleanup_remediation.py` (29), `billing_automation.py` (20). `billing/payments.py:1159-1167` **advances** it on payment; `account_lifecycle.py:1417-1423` shifts it by pause duration. It cannot be dropped and must not remain an authority | one Sub writer that recomputes it from the module's `service_period(cadence, contract_start, index)`. Its **authority** dies; the **field** lives. Drift detection compares it to the module's next boundary; repair recomputes. Today at least four sites write it — `catalog/subscriptions.py:983`, `:1498`, `account_lifecycle.py:1417-1423`, `billing/payments.py:1159-1167` — which is the defect, and the ratchet is on **writers**, not readers |
| **`Subscription.unit_price`** | `:954` | **15 modules.** It is the actual money source for billing today: `billing_automation.py:390-393` (`base = subscription.unit_price`), `prepaid_recovery_billing.py:352`, `prepaid_service_renewals.py:348`, `prepaid_draft_reconciliation.py:903`. Also MRR (`mrr_snapshot.py:44`, `billing/reporting.py:1030`) and top-revenue reports | a projection of the effective contract version's line price. `app/services/prepaid_renewal_terms_backfill.py:5,13` **already documents** that `unit_price` stops being authoritative once `billing.contracts` cuts over — the intent is checked in, the cutover is not done |
| **`Subscription.billing_cycle`, `billing_mode`** | `:943`, `:933` | the precedence chain (`catalog/subscriptions.py:100-133 _resolve_billing_cycle`) and display surfaces | a projection of the effective contract version's cadence, or dropped where the survey finds only display readers. The precedence chain itself retires — `billing_profile.py` exists solely to referee it |
| **`SalesOrder.payment_status`, `amount_paid`, `balance_due`** | `app/models/sales.py:997`, `:1009`, `:1012` | receivable state on a sales order; the funding gate touches them | **not subscriptions' at all** — flagged here only because they sit on the same path. They become a Sub projection of billing's coverage, with one named writer, or retire into the funding gate |

### 5.3 `state` — the one field that genuinely splits

`ObligationState` (`billing_contract.py:145-154`) fuses two owners' lifecycles in
**one enum on one column**, with `_open` (`obligations.py:588`) and `_resolve`
(`:636`) transitions in the same service:

```
scheduled ──▶ open ──▶ partially_resolved ──▶ resolved
     │                                    └──▶ written_off
     └──▶ canceled
```

- `scheduled`, `canceled` — **scheduling status. Stays.**
- `open`, `partially_resolved`, `resolved`, `written_off` — **financial status.
  Moves.**

The module's occurrence vocabulary is therefore exactly
`scheduled → due → emitted`, plus `scheduled → cancelled`, plus `replaced` and
`superseded` for the correction path — and **nothing else**. That set is asserted
by a test, because a vocabulary that can grow by one member is a vocabulary that
will grow to include `paid`.

Both collections policies gate on `state not in ACTIONABLE_STATES`
(`postpaid_policy.py:40`, `prepaid_policy.py:52`), so this is the field whose
split has a live consumer. Afterwards they gate on billing's receivable state.

**The provenance constraint splits with it.**
`ck_billing_obligation_rating_provenance_complete` (`:526-544`) requires
`rating_tax_rate_percent IS NOT NULL` and `rating_tax_inclusive IS NOT NULL` in
the same predicate as `rating_unit_price`, `rating_quantity`,
`rating_proration_factor` and `rating_input_fingerprint`. The module's version
must be **re-derived over the pre-tax inputs only**, never copied — copying it
would force a tax column onto a pre-tax row and quietly re-import category 2.

### 5.4 Contract-layer amount and status fields

`BillingContractVersion` and `BillingContractLine` carry seven more fields the
published audit's table does not allocate.

**All seven have zero readers outside `app/services/billing/`** — nothing in
`app/web/`, `app/api/`, `scripts/`, `templates/` or `src/` touches them. Their
owner-side use is narrower than expected and changes two dispositions.

| Field | Line | Owner-side use today | Disposition |
|---|---|---|---|
| `discount_code`, `discount_amount` | `:392`, `:393` | `contracts.py:1272-1273` **copy-forward only. Rating never consumes either.** | **stays — but as an unwired term.** The intent is clearly that a discount changes the pre-tax figure, so if the module registers it, it must be inside the fingerprint input set (spec § 2.3) or two differently-discounted ratings collide as a replay. **If it stays unwired, do not register it** — hard rule 10's no-orphan principle: a declared field with no reader is a promise the module has not kept. Flag for a decision (§ 5.6) |
| `payment_terms_days` | `:389` | `contracts.py:1269` **copy-forward only — never used to compute anything, including obligation `due_at`.** Confirmed by `due_at` always being NULL in production | **stays as a commercial term; the consequence is billing's.** It is negotiated in the contract, so the module stores and forwards it and never computes a due date. That is the line between owning a term and owning `overdue`. Same unwired caveat as above: today nothing consumes it |
| `tax_treatment_code` (version `:390`, line `:471`) | | `rating.py:209` — `line.tax_treatment_code or version.tax_treatment_code`, a real fallback pair; `contracts.py:721, 966, 1270, 1305, 1482` copy-forward | **stays as an opaque declared code; the decision is billing's.** Forwarded, never interpreted. The line-over-version fallback is real behaviour that must be preserved wherever it ends up |
| `tax_inclusive` | `:391` | `rating.py:341` — `tax_inclusive=version.tax_inclusive` into provenance | **moves to Billing.** Unlike a code, this is a boolean the module must *act on* to compute a pre-tax figure. Sub's `_effective_tax_rate` and tax-inclusive back-out (`rating.py:195`) go to billing with it |
| `accounting_treatment` (line `:465`) | | `contracts.py:855, 940` idempotency compare; `:1303, 1480` copy-forward | **moves to Billing; declared code forwarded.** Note it participates in Sub's idempotency comparison, so the module's own comparison must be re-derived without it |
| `authority` (version `:312`, contract `:221`, obligation `:591`) | | migration machinery only | **retires — no category. See § 5.6** |

The opaque-code rule needs a guard, not a convention: § 8's sensitivity proof
adds a single `if` on a forwarded code's value inside the module and asserts the
build fails.

### 5.5 The rule, in the form a guard can assert

The subscriptions occurrence carries `currency` and **exactly one** money field
— the exact pre-tax rated amount — plus its rating provenance and fingerprint,
and **no** payment state, resolution, treatment, due date, outstanding balance,
coverage, allocation, tax field, or reversal link. Contract versions and lines
carry commercial terms and forward tax, discount and treatment codes as **opaque
declared codes with no local semantics**.

The guard enumerates the category-2 names and fails the build if any appears as
a column on a module model. `pre_tax_amount` must **pass** the same guard, which
is what makes it a classification rather than a ban on the word "amount".

### 5.6 Fields with no identified reader — flagged, not classified

The sweep found substantially more dead surface than expected. **This list is
the cheapest work in the whole programme: a column with no reader and no writer
does not need to be classified, migrated, shadowed or reconciled — it needs to
be deleted.** Doing that before the split shrinks the migration.

| Field | Line | Finding |
|---|---|---|
| `corrects_id` | `:683` | **Zero readers AND zero writers in the entire repository, tests included.** Only the model line and `alembic/versions/430_billing_contract_obligation_identity.py:383`. § 5.2 assigns it to *stays* because the module's correction protocol needs a supersession pointer — but that is a **new** design, not a port. Sub's column is unimplemented |
| `reversed_by_id` | `:686` | Same: zero readers, zero writers, anywhere. Do not migrate it; let billing design its own reversal chain. **Recommend: retire, do not move** |
| `collection_timing` (on the *obligation*) | `:664` | Written once (`obligations.py:536`, copied from the version) and **never read by anyone, including the owner.** Every real consumer reads `BillingContractVersion.collection_timing` instead (`shadow_verification.py:520, 542`; `contracts.py:700, 838, 1263, 1442, 1622`; `cadence.py:86`; `prepaid_service_renewals.py:227`). C2's one field belongs on the **contract version**; the occurrence copy is denormalisation with no consumer. Carry it on the *output* (billing needs it) but do not persist it on the occurrence row without a reader |
| `is_finite` (on the *obligation*) | `:667` | Identical shape: written once (`obligations.py:537`, copied from the line), never read by anyone. Every consumer reads `BillingContractLine.is_finite` (`shadow_verification.py:795`; `contracts.py:692, 856, 1170, 1304, 1481`; `addon_contract_backfill.py:316`). **The occurrence column retires; the line column stays** |
| `resolution_kind`, `opened_at`, `resolved_at` | `:674`, `:677`, `:678` | No reader outside the owner, and the **writes are unreachable in production** — no non-test caller invokes `BillingObligations.open()` or `.resolve()`. They are category 2 by meaning, but there is nothing to migrate |
| `due_at` | `:679` | Has two readers, and is **always NULL in production**: the only production `ScheduleObligationCommand` (`billing_lifecycle_projection.py:867-899`) omits it, so `postpaid_policy.py:43` always short-circuits. Category 2 by meaning; no data to migrate |
| 15 of the 16 `rating_*` columns | `:629`-`:656` | No **direct** external read. They are reachable only through `replay_recorded_rating`, which executes inside the owner. Only `rating_input_fingerprint` is read directly outside (`shadow_verification.py:1164`). This does **not** argue for dropping them — they are the module's core asset and the constraint that makes replay deterministic — but it does mean their only consumer today is the owner plus the shadow reconciler |
| `rating_provenance_complete` | `:629` | An expand-and-shadow flag: existing rows are explicitly incomplete and no migration guesses historical inputs. In the module every occurrence is complete by construction, so the **flag retires and the constraint becomes unconditional** |
| `authority` (on all three tables) | `:221`, `:312`, `:591` | ADR-0007's shadow/authoritative migration gate. Consumers are migration machinery only: `permitted_authority()` (`contracts.py:253`, `obligations.py:244`) reading the SOT registry's `MigrationContract`; `postpaid_entitlement_history_for_period` (`contracts.py:148-250`) refusing to establish entitlement from a shadow row; and `shadow_verification.py:1143`. **In the module a row's existence IS its authority.** No category — it retires with the migration |
| `ObligationState.scheduled`, `.resolved`, `.canceled`, `.written_off` | `:145-154` | No external reference to any of these four members. Only `open` and `partially_resolved` are consumed outside the owner, via `mode_policies.py:20 ACTIONABLE_STATES`. The split in § 5.3 therefore has **one** external consumer to repoint, not four |
| `payment_terms_days`, `discount_code`, `discount_amount` | `:389`, `:392`, `:393` | Copy-forward only; **never consumed by rating or by anything else**. See § 5.4 — either wire them or do not register them |

### 5.7 What this verdict changes

Nothing in the published A2b conclusion is refuted. What changes is that
"recurrence carries no financial status" becomes **executable**. Of
`BillingObligation`'s 44 business columns, the substantial majority stay — the
natural identity, the period, the pre-tax amount and fourteen rating-provenance
columns — **fifteen move to billing**, the `state` enum splits four members off,
four tables or columns become named rebuildable projections, and four fields are
flagged as possibly having no reason to exist at all. Two contract-layer fields
move and four more stay as forwarded commercial terms.

The two collections policies are the **only** external consumers that must be
repointed — and they are reachable only from an operator CLI today.
`SalesOrderFundingObligation` shows the projection pattern is already understood
in this codebase and depends on no obligation column at all.
`Subscription.next_billing_at`'s **49** reader modules are the reason category 3
has to exist at all, and `Subscription.unit_price` is still the actual money
source for four billing paths.

**The most useful thing this section found is how much of the obligation is
dead.** `corrects_id` and `reversed_by_id` have no reader and no writer anywhere
including tests; the occurrence copies of `collection_timing` and `is_finite`
are written once and never read by anyone; `resolution_kind`, `opened_at` and
`resolved_at` have unreachable writes; `due_at` is always NULL in production;
and `payment_terms_days`, `discount_code` and `discount_amount` are copy-forward
only. Deleting that surface **before** the split is the cheapest work in the
programme and shrinks the migration materially.

Three items need decisions elsewhere rather than in this audit:

1. **`reversed_by_id`** — retire rather than move (recommended), or let billing
   design its own reversal chain. Billing's call.
2. **`discount_code`/`discount_amount`** — wire them into the pre-tax rating and
   the fingerprint, or do not register them at all. Registering an unread field
   is exactly the no-orphan failure hard rule 10 exists to prevent.
3. **The occurrence copies of `collection_timing` and `is_finite`** — carry
   `collection_timing` on the *output* because billing needs it, but do not
   persist either on the occurrence row unless a reader is named.
---

## 6. Does the published conclusion honour Michael's 2026-08-12 ruling?

**Yes. Verified, not deferred to.**

The ruling has two halves. Testing each against the published text and the
source:

**A2(a) — vendor↔operator commercial contracts stay a distinct owner and
`dotmac-subscriptions` may not claim them.** The published audit states:
"*This conclusion does not merge Vendor's legal commercial agreement with a
subscription contract. `vendor_cp.contracts` owns proposal, content-bound
approval, countersignature/activation evidence, suspension, and termination…the
module never owns approval, licensing, allocation, deployment, or account
lifecycle.*" That is the ruling, honoured literally.

The source independently supports it on four grounds, any one of which is
sufficient:

1. **Zero business-column overlap** between the two headers (§ 1.5) — a weaker
   overlap than the `offer_versions` pair already judged insufficient.
2. **The lines mean different things.** Vendor's `contract_lines` **is** the
   offer selection (`offer_version_id` FK, `offer_code`, `offer_version`,
   `capability_code`); Sub's `billing_contract_lines` is a resolved charge
   component that never references an offer version. Merging them forces one
   table to be both the selection and its resolution.
3. **Approval would follow the contract into the module.** `content_hash`,
   `approval_policy_code`, `approval_policy_version`, `submitter_id`. ADR-0026
   assigns approval to `dotmac-approvals`; a recurrence module owning
   content-bound approval binding creates a second owner of it.
4. **There is no recurrence to fold.** Zero cadence/proration/recurring/renewal
   hits across all of `src/vendor_cp/`. Vendor's `term_start`/`term_end` is a
   date pair.

**A2(b) — only the reusable offer catalogue is detached, pending audit.** The
audit was taken, and it detaches the offer catalogue *plus* the recurring core
(contracts, cadence, proration, occurrence). That is broader than the literal
words "the reusable offer catalogue", and ADR-0020 A4 records Michael resolving
that widening on 2026-08-14 ("*the reusable recurring-commercial core belongs to
`dotmac-subscriptions`*"). So the widening is authorised, not assumed.

The widening is also the correct one on ADR-0006 § 5a: "*these implementations
do not share a contract*" never establishes "*this capability has no contract*",
and an audit concluding no single owner qualifies "*has found that the unit was
drawn wrong, not that the work stops*". Two of Vendor's five business columns
and two of Sub's eighteen carry coinciding meaning, so § 5's gate 1 fails on the
tables **as they stand** — and the remedy is to redraw the unit, which is
exactly what the published audit does by moving `capability_codes` and the nine
ISP columns into link tables. Strip those, and both sides reduce to the same
residue: a stable offer identity, an integer version, a half-open effective
interval, and exact money.

**One nuance worth recording, which is not a refutation.** The ruling's phrase
is "stay a distinct **module**". The published audit implements "stay a distinct
**owner**, in Vendor CP". Those are not the same claim, and the difference is
§ 7.

---

## 7. The open point: should `vendor_cp.contracts` eventually become its own
module?

**Recommendation: not now, and not on its own merits — but it is a legitimate
module candidate on one specific trigger, and the trigger is nameable.**

**Why leaving it in Vendor CP is the right end state for now.**

1. **ADR-0006 § 5 gate 1 needs two independent consumers of the same contract,
   and there is one.** Vendor CP is the only implementation of a
   commercial-agreement lifecycle in the fleet at these revisions. Sub has no
   counterpart — `app/models/contracts.py` is 77 lines and is a different
   subject (network/service contracts). Extracting now would produce a package
   with one consumer, which ADR-0020 § 6 already forbids: "*a pure contract with
   no consumer still counts as work in progress*".
2. **ADR-0006's 2026-08-12 amendment says a second consumer is evidence, not
   permission** — and that cuts both ways. One consumer is not even evidence.
3. **ADR-0017 makes adoption, not capability, the scarce resource.** Vendor CP
   is already scheduled as cutover 1 for `dotmac-subscriptions` platform-plane
   adoption. Giving it a second concurrent module cutover in the same window
   spends adoption budget that the programme does not have, on a capability with
   no second adopter waiting.
4. **The behaviour is small and already correct where it counts.** 14 columns, 8
   states, one service file, `process_once_platform` idempotency, atomic
   transition-plus-outbox, and 7 tests. There is no measured duplication to
   retire and no drift to repair. That is the profile of code that is fine where
   it is.
5. **The one part of it that genuinely generalises already has an owner.**
   Content-bound approval — `content_hash`, `approval_policy_code`,
   `approval_policy_version`, and the quorum/self-approval/fail-closed behaviour
   in `vendor_cp/approvals` — is `dotmac-approvals`' subject under ADR-0026,
   which the approvals audit has already taken. Extracting a
   commercial-contract module now would create a second claim on it.

**The trigger that would change the answer.** The fleet matrix's
`sales-agreements` row still holds rows from two repositories (CRM 13, Sub 24)
with the CRM→Sub consolidation unfinished, and wave 1 of the sequencing plan is
exactly that consolidation. **When Sub's consolidated sales-agreement owner
exists and needs the same proposal → content-bound approval → countersignature →
activation → suspension → termination lifecycle over a priced, frozen snapshot,
there are two independent consumers of one contract and gate 1 is met.** At that
point a `dotmac-commercial-agreements` dossier (name unallocated) should be
opened and audited on its own, against Vendor CP and the consolidated Sub owner
as the two sources — exactly the procedure `dotmac-approvals` and
`dotmac-subscriptions` followed.

**Three things must be true before that audit is worth taking, and they can be
checked cheaply:**

- Vendor CP's `suspend`/`reinstate`/`terminate`/`expire` acquire tests
  (§ 4.1) — an untested lifecycle is not a qualifying source.
- The approval boundary is settled first: `dotmac-approvals` owns content-bound
  approval binding, and the commercial-agreement owner *consumes* it rather than
  reimplementing `content_hash` + `approval_policy_version`.
- `superseded_by_id` (`contracts/models.py:72`, declared and unused) is either
  implemented or removed. Amendment/supersession is the single biggest
  unimplemented piece of that lifecycle, and a module extracted before it exists
  would freeze the wrong shape — the same mistake the offer audit avoided by
  noticing that neither product had complete versioning.

**Until then:** `vendor_cp.contracts` stays in Vendor CP, keeps its own tables
and lineage, and interacts with `dotmac-subscriptions` in exactly one direction
— on activation it submits approved recurring terms through the assembly, its
line keeps an **opaque** offer-version reference plus its frozen price snapshot,
and it acquires no cross-module foreign key. One existing coupling must be
broken at the offer cutover: **`contracts/service.py` imports
`vendor_cp.offers.models.OfferVersion` directly** (`_resolve_offer`, `:537`).
Inside one assembly that is legal today; once offers is a module, ADR-0024 § 2
requires the assembly to resolve a typed immutable snapshot and hand it in.

---

## 8. ISP sales orders — producers, not subscription writers

**What they are.** `SalesOrder` (`app/models/sales.py:967-1044`, 24 business
columns) and `SalesOrderLine` (`:1047-1080`, 8 columns). An order sits
downstream of `Quote` (`uq_sales_orders_quote_id`, one order per quote), is
numbered `SO-%06d` from `document_sequences`, points at `subscriber_id`, and
carries commercial totals (`currency`, `subtotal`, `discount_*`, `tax_total`,
`total`) plus acceptance evidence (`contract_signed`, `signed_at`,
`deposit_required`, `deposit_paid`) and a `status`/`payment_status` pair.

**Why they are producers.** Three source facts:

1. **No recurring vocabulary at all.** No cadence, rate basis, service or invoice
   interval, `collection_timing`, anchor, timezone, alignment, proration policy,
   effective half-open interval, supersession or version. Its
   `payment_due_date`, `amount_paid` and `balance_due` are receivable state —
   billing's, not subscriptions'.
2. **It does not write a contract row.** The writer is
   `BillingContracts.consume_sales_funding`
   (`app/services/billing/contracts.py:514`), a **consumer of a fulfilment
   output event** (`event_id`, `CommandContext`) receiving a typed
   `SalesFundingContractSnapshot` (`:326-340`). `_sales_funding_command` (`:395`)
   translates that snapshot into a `RecordContractVersionCommand`. The contract
   owner writes; the order emits.
3. **The link is source identity, not a foreign key.** The version records
   `source_kind = sales_order_line` with `source_id`/`source_version`, and the
   line's `component_key` is `str(sales_order_line_id)` (`:429`). That triple is
   the third, fourth and fifth element of the obligation's natural identity.
   ADR-0007 § 4 is explicit: "*Structural origin of a contract version. Never a
   metadata string.*"

`app/models/sales_order_funding.py` and
`dotmac_sub:tests/test_sales_order_funding.py` (6 tests) close the loop the other
way: the order's funding gate advances when its **finite** obligations are fully
funded. The order is a *funding subject* of obligations it did not create.

**The seam also carries two defects the module must refuse.** `_CYCLE_INTERVAL`
(`contracts.py:386-392`) maps the five presets onto `(IntervalUnit, count)` —
which usefully proves the composable cadence is strictly more expressive than the
enum, since every preset maps and `service_interval ≠ invoice_interval` has no
enum value — and `_sales_funding_command` then hardcodes
`timezone_name="Africa/Lagos"` (`:413`) and `ProrationPolicy.none` (`:415`).
Both must fail closed in the module, and the mapping is a migration adapter that
retires with Sub's last `BillingCycle` writer.

`dotmac-subscriptions` therefore never reads a sales order, never imports Sub's
sales package, and never gains a `sales_order_id` column. It accepts a typed
command carrying a declared source code, id and version, with `sales_order_line`
one registered `obligation_source` under ADR-0008 — never a hardcoded branch.

---

## 9. Dispositions ledger

Every source row examined, in the shape of
`docs/inventories/approval-workflow-dispositions.toml`. `evidence_state`:
`behavior-tested` (a named test file proves it), `structure-only` (read from
model/migration, no behavioural test found), `untested-invariant` (claimed in
code or docstring, nothing asserts it).

```toml
schema_version = 1
audit_id = "A2-V"
as_of = "2026-08-14"
verifies = "docs/inventories/subscriptions-sources.md"
decision_status = "published-conclusion-upheld"
adr = "docs/adr/0020-billing-owns-operational-receivables.md#a4-a2b-is-resolved-into-dotmac-subscriptions"
starter_revision = "5417e51"
sub_revision = "27c76aaeebb792f089000af764d80f4dfe45c104"
vendor_revision = "89848017d6b87e82dd4d6ffd0b2c9eaed5f9fee8"
headline = "The published A2b conclusion holds. Sub's offer_versions has no uniqueness constraint at all, which strengthens the ruling; fifteen obligation columns and seven contract columns are financial status that must be enumerated in a guard rather than split by convention."

# ---------------------------------------------------------------- offers

[[tables]]
repository = "dotmac_vendor_control_plane"
table = "offer_versions"
model = "dotmac_vendor_control_plane:src/vendor_cp/offers/models.py"
writer = "dotmac_vendor_control_plane:src/vendor_cp/offers/service.py"
migration = "dotmac_vendor_control_plane:alembic/versions/v002_offer_versions.py"
tests = ["dotmac_vendor_control_plane:tests/unit/test_offers.py"]
business_columns = 5
relationships = 0
evidence_state = "behavior-tested"
target = "dotmac-subscriptions"
disposition = "required-port-delta"
verification = "confirms-published"
reason = "The only implementation in either product where offer versioning is a DATABASE invariant — uq_offer_versions_code_ver, present in both the model (:31) and the migration (:55). Supplies write-once publish, exact-Money round-trip, declared-capability refusal and command_id idempotency as mandatory deltas over Sub. capability_codes does not travel: it becomes a Vendor-owned platform link table. CORRECTION TO THE PUBLISHED AUDIT: immutability here is service-only — v002 lines 58-59 grant UPDATE and DELETE to platform_api and app_admin — so the module must make it structural rather than inherit a convention."

[[tables]]
repository = "dotmac_sub"
table = "offer_versions"
model = "dotmac_sub:app/models/catalog.py"
writer = "dotmac_sub:app/services/catalog/offers.py"
guard = "dotmac_sub:app/services/catalog_billing_governance.py"
tests = ["dotmac_sub:tests/test_catalog_billing_governance.py", "dotmac_sub:tests/test_offer_contracted_amount.py", "dotmac_sub:tests/test_catalog_submodules.py"]
business_columns = 18
relationships = 7
evidence_state = "behavior-tested"
target = "dotmac-subscriptions"
disposition = "extract-source-partial"
verification = "confirms-published-with-material-addition"
reason = "Supplies effective_start/effective_end dating, which Vendor has no equivalent of, plus the mature downstream consumers. MATERIAL ADDITION: it has NO uniqueness constraint on (offer_id, version_number) — the class declares no __table_args__ and squashed_schema.sql carries only offer_versions_pkey plus four FKs — so nothing prevents two rows both claiming version 3. That disqualifies Sub as the source of the versioning primitive and is a stronger argument for the published disposition than the behavioural one given. CORRECTION: 7 relationships, not the 6 recorded in ADR-0020 A4 and in Knowledge. Its update path mutates in place, commits inside the service and raises HTTPException; all three corrected at the boundary. Its nine ISP columns and five presentation columns do not travel."

[[tables]]
repository = "dotmac_sub"
table = "catalog_offers"
model = "dotmac_sub:app/models/catalog.py"
writer = "dotmac_sub:app/services/catalog/offers.py"
tests = ["dotmac_sub:tests/test_offer_name_uniqueness.py", "dotmac_sub:tests/test_catalog_services.py", "dotmac_sub:tests/test_capped_offer_requires_fup_ladder.py"]
business_columns = 34
relationships = 13
evidence_state = "behavior-tested"
target = "dotmac-subscriptions + dotmac_sub tenant link tables"
disposition = "split-at-owner-boundary"
verification = "confirms-published-with-precision-correction"
reason = "Only FIVE columns are generic — name, code, description, status, is_active. The other twenty-nine are ISP product meaning. PRECISION CORRECTION: RADIUS profile, speed, portal visibility, plan_category and VAT live HERE and not on OfferVersion, so the version-level tenant link table is narrower than the offer-level one, and the inheritance of parent attributes at read time is behaviour the migration must preserve or explicitly retire. The sellable-name partial unique index is product policy, not a module invariant."

[[tables]]
repository = "dotmac_sub"
table = "offer_version_prices"
model = "dotmac_sub:app/models/catalog.py"
writer = "dotmac_sub:app/services/catalog/offers.py"
guard = "dotmac_sub:app/services/catalog_billing_governance.py"
tests = ["dotmac_sub:tests/test_offer_contracted_amount.py", "dotmac_sub:tests/test_catalog_billing_governance.py", "dotmac_sub:tests/test_catalog_price_bounds.py"]
business_columns = 8
relationships = 1
evidence_state = "behavior-tested"
target = "dotmac-subscriptions"
disposition = "extract-source-with-corrections"
verification = "published-evidence-overstated-conclusion-still-right"
reason = "The separate-price-child shape is right; Vendor's embedded single price is not general enough. THE PUBLISHED CLAIM that active prices can be changed only while no live subscription depends on them is OVERSTATED in two independent ways: the guard fires only when the changed field intersects _PRICE_CRITICAL_FIELDS (catalog_billing_governance.py:55-66), and it counts only Subscription.offer_version_id, which is NULLABLE (catalog.py:904) — a live subscription pinning the offer but not the version does not protect the price. Both holes strengthen rather than weaken the published disposition. Three mandatory corrections: currency must lose its \"NGN\" default (catalog.py:716); billing_cycle must not travel (a third parallel cadence store); one-active-price-per-(version, price_type) must become a database constraint instead of the Python checks at :397 and :354. Money scale must be declared once — Numeric(10,2) here versus Numeric(14,4) on contracts and obligations."

[[tables]]
repository = "dotmac_sub"
table = "offer_prices"
model = "dotmac_sub:app/models/catalog.py"
writer = "dotmac_sub:app/services/catalog/offers.py"
tests = ["dotmac_sub:tests/test_catalog_price_bounds.py"]
business_columns = 8
relationships = 1
evidence_state = "structure-only"
target = "none"
disposition = "retire-into-versioned-price"
verification = "not-covered-by-published-audit"
reason = "An unversioned price on the mutable offer parent, structurally identical to offer_version_prices minus the version, and carrying the same \"NGN\" default (catalog.py:767). It is the pre-versioning path; test_offer_contracted_amount.py::test_a_pinned_offer_version_price_wins already establishes the versioned row as the winner. Retires at Sub's cutover; nothing ports."

[[tables]]
repository = "dotmac_sub"
table = "add_on_prices"
model = "dotmac_sub:app/models/catalog.py"
writer = "dotmac_sub:app/services/catalog/offers.py"
tests = ["dotmac_sub:tests/test_catalog_billing_governance.py", "dotmac_sub:tests/test_billing_addon_contract_backfill.py"]
business_columns = 8
relationships = 1
evidence_state = "behavior-tested"
target = "none"
disposition = "product-owned"
verification = "not-covered-by-published-audit"
reason = "Add-on catalogue is Sub product vocabulary. Its recurring terms already reach the contract owner as a typed RecurringAddonPurchaseTermSnapshot through consume_recurring_addon_purchase, which is the correct seam and the pattern the module should require. The table itself does not move. Also carries the \"NGN\" default (catalog.py:797)."

# ------------------------------------------------------- commercial contract

[[tables]]
repository = "dotmac_vendor_control_plane"
table = "contracts"
model = "dotmac_vendor_control_plane:src/vendor_cp/contracts/models.py"
writer = "dotmac_vendor_control_plane:src/vendor_cp/contracts/service.py"
migration = "dotmac_vendor_control_plane:alembic/versions/v004_contracts.py"
tests = ["dotmac_vendor_control_plane:tests/unit/test_contracts.py", "dotmac_vendor_control_plane:tests/unit/test_approvals.py", "dotmac_vendor_control_plane:tests/architecture/test_deny_cases.py"]
business_columns = 14
relationships = 1
evidence_state = "behavior-tested"
target = "vendor_cp (distinct owner, not a module today)"
disposition = "stays-product-owned"
verification = "confirms-published-and-Michaels-2026-08-12-ruling"
reason = "ZERO business columns coincide with Sub's billing_contracts in name or meaning — a weaker overlap than the offer_versions pair already judged insufficient. It is a legal-agreement state machine: 8 states, content_hash approval binding, approval_policy_code/version, activation_rule, activated_at, suspension, termination, with term_start/term_end Date columns and no cadence, period, proration, obligation or renewal anywhere in src/vendor_cp (exhaustive grep, zero hits). Folding it into dotmac-subscriptions would import content-bound approval — dotmac-approvals' subject under ADR-0026 — and give a recurrence module a suspend transition. Whether it also becomes its OWN module is § 7's open point: gate 1 needs two consumers and there is one. Trigger: the CRM-to-Sub sales-agreement consolidation producing a second consumer."

[[tables]]
repository = "dotmac_vendor_control_plane"
table = "contract_lines"
model = "dotmac_vendor_control_plane:src/vendor_cp/contracts/models.py"
writer = "dotmac_vendor_control_plane:src/vendor_cp/contracts/service.py"
tests = ["dotmac_vendor_control_plane:tests/unit/test_contracts.py"]
business_columns = 8
relationships = 1
evidence_state = "behavior-tested"
target = "vendor_cp (distinct owner)"
disposition = "stays-product-owned"
verification = "confirms-published"
reason = "This line IS the offer selection — offer_version_id FK, offer_code, offer_version, capability_code — not a resolved charge component, which is what Sub's billing_contract_lines is. Merging the two would force one table to be both the selection and its resolution. Its frozen unit_amount/unit_currency_code snapshot at submit is the behaviour worth preserving and is proven by test_submit_freezes_exact_priced_snapshot. After the offer cutover the FK becomes an opaque module offer-version reference resolved through the assembly. BREAKING CHANGE REQUIRED: contracts/service.py:537 _resolve_offer imports vendor_cp.offers.models.OfferVersion directly; ADR-0024 § 2 forbids that once offers is a module."

[[tables]]
repository = "dotmac_sub"
table = "billing_contracts"
model = "dotmac_sub:app/models/billing_contract.py"
writer = "dotmac_sub:app/services/billing/contracts.py"
tests = ["dotmac_sub:tests/test_billing_contracts.py", "dotmac_sub:tests/test_billing_shadow_pipeline.py"]
business_columns = 5
relationships = 1
evidence_state = "behavior-tested"
target = "dotmac-subscriptions"
disposition = "extract-source"
verification = "confirms-published"
reason = "The stable subscription-contract identity, correctly holding no terms. Two columns do not travel: subscription_id (a Sub FK, becoming a tenant product link) and authority (ADR-0007's shadow/authoritative migration gate, which is Sub's cutover mechanism, not a module concept). Its UniqueConstraint(subscription_id) is Sub's product rule — one contract per subscription — and is not a module invariant."

[[tables]]
repository = "dotmac_sub"
table = "billing_contract_versions"
model = "dotmac_sub:app/models/billing_contract.py"
writer = "dotmac_sub:app/services/billing/contracts.py"
tests = ["dotmac_sub:tests/test_billing_contracts.py", "dotmac_sub:tests/test_billing_cadence.py", "dotmac_sub:tests/test_subscription_billing_cadence.py", "dotmac_sub:tests/architecture/test_billing_target_architecture.py"]
business_columns = 38
relationships = 2
evidence_state = "behavior-tested"
target = "dotmac-subscriptions"
disposition = "extract-source"
verification = "confirms-published-with-financial-column-carve-out"
reason = "The qualifying product-first source, with no counterpart anywhere in the fleet. Carries the whole composable cadence, the half-open effective interval with a partial unique index plus a PostgreSQL temporal exclusion constraint, structural source kind/id/version, supersession, and command/idempotency evidence. FOUR port deltas: source_kind becomes an ADR-0008 declaration registry rather than a five-value Enum; timezone_name loses its \"Africa/Lagos\" default and fails closed; the authority column does not travel; and FIVE FINANCIAL COLUMNS the published audit does not allocate — payment_terms_days (:389), tax_treatment_code (:390), tax_inclusive (:391), discount_code (:392), discount_amount (:393) — are billing's, or travel only as opaque declared codes the module never interprets."

[[tables]]
repository = "dotmac_sub"
table = "billing_contract_lines"
model = "dotmac_sub:app/models/billing_contract.py"
writer = "dotmac_sub:app/services/billing/contracts.py"
tests = ["dotmac_sub:tests/test_billing_contracts.py", "dotmac_sub:tests/test_billing_addon_contract_backfill.py"]
business_columns = 11
relationships = 1
evidence_state = "behavior-tested"
target = "dotmac-subscriptions"
disposition = "extract-source"
verification = "confirms-published-with-financial-column-carve-out"
reason = "contract_line_key (:451) is the load-bearing invention: a stable lineage surviving supersession and the first element of the obligation's natural identity, proven by test_line_lineage_survives_supersession. The (contract_version_id, charge_component, component_key) unique constraint and the unit_price/quantity check both travel. charge_component becomes an ADR-0008 declaration registry, never the seven-value Enum. TWO FINANCIAL COLUMNS carved out: accounting_treatment (:465) is billing's, and tax_treatment_code (:471) travels only as an opaque code."

# ------------------------------------------------------------- recurrence

[[tables]]
repository = "dotmac_sub"
table = "billing_obligations"
model = "dotmac_sub:app/models/billing_contract.py"
writer = "dotmac_sub:app/services/billing/obligations.py"
tests = ["dotmac_sub:tests/test_billing_obligations.py", "dotmac_sub:tests/test_billing_phase2_shadow.py", "dotmac_sub:tests/architecture/test_billing_target_architecture.py"]
business_columns = 44
relationships = 0
evidence_state = "behavior-tested"
target = "split: dotmac-subscriptions + dotmac-billing"
disposition = "split-at-owner-boundary"
verification = "confirms-published-but-the-split-must-be-enumerated-not-conventional"
reason = "Per-field classification is section 5, which is a decision gate in its own right; this row is its summary. STAYS: the natural identity (uq_billing_obligation_natural_identity :491-501, already exactly ADR-0020 C10's shape and containing no subscription_id), period_start/period_end, currency, net_amount (:616 — the immutable PRE-TAX rated snapshot, renamed pre_tax_amount, explicitly permitted), the fifteen rating_* provenance columns with rating_input_fingerprint, collection_timing, is_finite, corrects_id, and the scheduled/canceled members of state. MOVES TO BILLING: tax_amount (:619), gross_amount (:622), resolved_amount (:658), accounting_treatment (:661), resolution_kind (:674), opened_at (:677), resolved_at (:678), due_at (:679), reversed_by_id (:686), rating_tax_treatment_code (:650), rating_tax_rate_id (:651 — a cross-owner FK to tax_rates that cannot survive ADR-0023 § 4), rating_tax_rate_percent (:655), rating_tax_inclusive (:656), account_id/subscription_id (:581/:586), and the open/partially_resolved/resolved/written_off members of state. BECOMES A REBUILDABLE PROJECTION: SalesOrderFundingObligation (already event-fed with provenance) and Subscription.next_billing_at (39 reader modules). FLAGGED AS POSSIBLY READER-LESS: authority (:591), rating_provenance_complete (:629), is_finite (:667), reversed_by_id (:686). THE ONLY EXTERNAL READERS ARE THE TWO COLLECTIONS POLICIES (collections/postpaid_policy.py:26-58 and collections/prepaid_policy.py:35-70), which repoint to billing's receivable contract. ONE CUTOVER REQUIREMENT: prepaid_policy.py:57 reads period_start on the same row as the financial fields, so billing's receivable must carry the service period forward or prepaid collections loses its future-period guard. ObligationState fuses two lifecycles in one enum on one column, and ck_billing_obligation_rating_provenance_complete (:526-544) requires tax columns in the same predicate as pre-tax inputs; both are re-derived, never copied."

[[tables]]
repository = "dotmac_sub"
table = "subscriptions"
model = "dotmac_sub:app/models/catalog.py"
writer = "dotmac_sub:app/services/catalog/subscriptions.py + app/services/subscription_lifecycle*.py"
tests = ["dotmac_sub:tests/test_subscription_billing_cadence.py", "dotmac_sub:tests/test_catalog_submodules.py"]
business_columns = 33
relationships = 12
evidence_state = "behavior-tested"
target = "dotmac_sub (product-owned)"
disposition = "stays-product-owned-with-retirement"
verification = "confirms-published-with-a-higher-count"
reason = "Service and access state — status, access_state, RADIUS/NAS/IP/bundle/provisioning, splynx_service_id, router_id — stays entirely in Sub. But four columns are a parallel cadence authority retiring into the contract version at cutover: billing_mode (:933), billing_cycle (:943, nullable, documented \"NULL => inherit the offer/version price cadence\"), next_billing_at (:946) and unit_price (:954). ADR-0007 § Context names three cadence stores; the count from source is FOUR — catalog_offers, offer_versions, offer_version_prices and subscriptions — with billing_contract_versions the intended fifth and winner, still in shadow."

# ------------------------------------------------------- pure behaviour

[[modules]]
repository = "dotmac_sub"
unit = "app/services/billing/cadence.py"
kind = "persistence-free resolver"
loc = 456
tests = ["dotmac_sub:tests/test_billing_cadence.py"]
test_loc = 287
test_count = 18
evidence_state = "behavior-tested"
target = "dotmac-subscriptions"
disposition = "extract-source"
verification = "confirms-published-exactly"
reason = "Both LOC figures in the published audit verified exactly (456 and 287). The qualifying product-first source for the whole cadence value object: quarterly-is-three-calendar-months, annual across a leap year, month-end clamping under a declared rule, strict-same-day refusal, half-open contiguity, rate-unit independence from invoice interval, contract-timezone arithmetic with deterministic DST fold, and declared proration. Owns no records, opens no transaction, reads no session — already ADR-0023's persistence-free engine shape. PORT DELTA the published audit does not name: it imports its vocabulary from app.models.billing_contract (:32-39), so the module must own those types and expose the extensible ones as ADR-0008 registries."

[[modules]]
repository = "dotmac_sub"
unit = "app/services/billing/rating.py"
kind = "persistence-free resolver"
loc = 445
tests = ["dotmac_sub:tests/test_billing_rating.py"]
test_count = 11
evidence_state = "behavior-tested"
target = "split: dotmac-subscriptions + dotmac-billing"
disposition = "split-at-owner-boundary"
verification = "confirms-published-and-corrects-a-defect-listing"
reason = "TO SUBSCRIPTIONS: fixed-period rating, per-day aggregation into a monthly period, declared proration, deterministic replay and rating_input_fingerprint (:177). TO BILLING: _effective_tax_rate (:195) and the tax-inclusive back-out. TO THE LATER METERING MODULE: usage_metered. CORRECTION TO THE PUBLISHED DEFECT LIST: 'Sub's rating resolver reads mutable tax configuration' is true at rating time but Sub ALREADY fixed it at replay time — the obligation records tax provenance immutably and replay_recorded_rating rates from the record, proven by test_replay_uses_recorded_tax_provenance_after_tax_configuration_changes and test_corrupt_recorded_rating_fingerprint_fails_replay. That mechanism is an asset the module inherits, not a defect it must avoid, and it is exactly what RatedObligationOutputV1 needs."

[[modules]]
repository = "dotmac_sub"
unit = "nine parallel cadence owners (see section 2.1)"
kind = "legacy calendar arithmetic"
evidence_state = "structure-only"
target = "dotmac_sub (retire)"
disposition = "retire-after-cutover"
verification = "published-defect-verified-and-badly-understated"
reason = "The published audit names two helpers. Source has FIVE distinct month-add implementations — billing_automation.py:272, catalog/subscriptions.py:92, billing_automation.py:2542 inline, payment_arrangements.py:132, web_billing_overview.py:193 — and NINE parallel cadence owners, plus five secondary consumers including customer_portal_flow_common.py:38-52 which loops a next-billing calculation up to 240 times. Only eight modules import the canonical cadence.py. Adoption-plan S4's ratchet baseline must be built from the nine-owner list or it will pass while three owners survive. Confirmed absent and therefore NOT part of the defect: relativedelta appears nowhere in the repository, and timedelta(days=30/365/90) is never used for billing cadence — every hit is an analytics or retention window."

[[modules]]
repository = "dotmac_sub"
unit = "app/services/billing_automation.py legacy invoice-line dedupe"
kind = "pre-ADR-0007 duplicate-billing guard"
evidence_state = "structure-only"
target = "dotmac_sub (retire)"
disposition = "retire-after-cutover"
verification = "clarifies-C10s-stated-evidence"
reason = "C10's evidence — 'Sub's recurring run dedupes on a single subscription_id' — is accurate about the LEGACY path and must not be read as a criticism of the ADR-0007 obligation, which already implements C10's constraint. Legacy: :1782-1793 dedupes on InvoiceLine.subscription_id plus the line DESCRIPTION STRING plus the invoice period, where the description is built at :1777 as f'{offer_name} ({start} - {end})' — so renaming an offer defeats the guard; :2252-2258 dedupes on subscription plus period only; :1006-1031 disambiguates recurring add-ons by looping over JSON metadata in Python. The strongest mitigation is a partial unique index on a CONCATENATED STRING, uq_invoice_lines_active_billing_line_key (app/models/billing.py:1086-1092), built at :984-994, and it is postgresql_where only so the SQLite unit harness does not enforce it. The module ports the ADR-0007 constraint and retires this path."

[[modules]]
repository = "dotmac_sub"
unit = "prepaid versus postpaid recurring engines"
kind = "parallel subsystems (C2's evidence)"
evidence_state = "behavior-tested"
target = "dotmac-subscriptions (one engine, one collection_timing field)"
disposition = "retire-after-cutover"
verification = "confirms-C2s-stated-evidence-with-exact-source"
reason = "Sub's own SOT registry already records this as SHADOWING — app/services/sot_registry/domains/financial_access/billing.py:744-765, old_owner 'postpaid invoice-period generation and monthly-specific prepaid renewal decision forks', new_owner 'billing.obligations'. Postpaid: billing_automation.py:1420 run_invoice_cycle and :544 preview_postpaid_recurring_charge over all five cycles via _period_end. Prepaid: prepaid_service_renewals.py:2271 run_due_prepaid_service_renewals, whose candidate QUERY hard-filters CatalogOffer.billing_cycle == BillingCycle.monthly at :2296-2306, so a quarterly or annual prepaid subscription is never scanned at all; period hard-coded at :2351; price resolution refuses non-monthly at :427-431; explicit unsupported_cadence failure at :694-700; and the naming is monthly-baked (PrepaidMonthlyChargeDetail :258 and four resolve_prepaid_monthly_* functions). The fork point is a literal if/else at advance_renewal_invoicing.py:313-338. This is the measured defect C2 cites and it is worse than C2 states: it is not two implementations of one behaviour, it is one behaviour available only monthly on one side."

[[modules]]
repository = "dotmac_sub"
unit = "app/services/billing_profile.py"
kind = "runtime disagreement detector"
loc = 359
evidence_state = "behavior-tested"
target = "none"
disposition = "retire-after-cutover"
verification = "confirms-ADR-0007s-stated-context"
reason = "The service ADR-0007 § Context names — it detects billing-mode disagreement at runtime instead of one contract owning the effective term. _profile_from_modes (:137) resolves account versus subscription modes three ways: no collectible subscriptions uses the account flag; exactly one subscription mode wins over the account and flags drift (:163-165); mixed modes are INVALID and automation must not guess (:147-152). It fails closed at :127 require_effective_billing_mode. This is the strongest evidence that four cadence stores is a live operational problem and not a tidiness complaint — and it retires entirely once the contract version owns the term. Nothing ports."

[[modules]]
repository = "dotmac_vendor_control_plane"
unit = "src/vendor_cp/offers/catalog.py::offered_capability_catalogue"
kind = "declared-vocabulary gate"
tests = ["dotmac_vendor_control_plane:tests/unit/test_offers.py"]
evidence_state = "behavior-tested"
target = "vendor_cp (product-owned)"
disposition = "stays-product-owned"
verification = "confirms-published"
reason = "The REFUSAL behaviour is the mandatory port delta — an offer may not invent a code (CapabilityCatalogue.require, offers/service.py:90-91). The capability VOCABULARY is Vendor's and stays there. In the module this becomes a declared charge-model and obligation-source registry per ADR-0008, with the product supplying the codes."

# ------------------------------------------------------- evidence gaps

[[gaps]]
repository = "dotmac_vendor_control_plane"
subject = "platform-plane isolation of offer_versions, contracts, contract_lines"
evidence_state = "untested-invariant"
detail = "REVOKE ALL ... FROM app_user exists at alembic/versions/v002_offer_versions.py:60 and v004_contracts.py:31, but tests/migration/test_vendor_migration_rehearsals.py::test_platform_role_access_and_tenant_role_denial asserts denial only for vendor_accounts and ten licence tables. Under ADR-0023 § 3 the REVOKE IS the isolation on the platform plane and must be checked as strictly as an RLS policy. The module's platform canary must cover every declared platform table; this gap must not be inherited."

[[gaps]]
repository = "dotmac_vendor_control_plane"
subject = "contract suspend, reinstate, terminate, expire"
evidence_state = "untested-invariant"
detail = "Implemented at contracts/service.py:432, :446, :459, :491; tests/unit/test_contracts.py (7 tests) covers submit, approve, reject and activate only. Nothing ports from here, but ADR-0020 A4's boundary DEPENDS on suspension being a commercial-contract transition rather than a subscription one, and that claim is untested in its own repository. Section 7 makes closing this a precondition for ever auditing a commercial-agreement module."

[[gaps]]
repository = "dotmac_vendor_control_plane"
subject = "contract amendment and supersession"
evidence_state = "structure-only"
detail = "contracts/models.py:72 declares superseded_by_id and the comment says 'Reserved for amendment/supersession (a later slice); unset here.' Nothing writes it. This is the largest unimplemented piece of the commercial-agreement lifecycle and, per section 7, a module extracted before it exists would freeze the wrong shape — the same mistake the offer audit avoided by noticing neither product had complete versioning."

[[gaps]]
repository = "dotmac_sub"
subject = "offer version uniqueness"
evidence_state = "structure-only"
detail = "No database constraint and no test asserts that two rows cannot both be version N of one offer. tests/test_offer_name_uniqueness.py proves the analogous rule for offer NAMES including at the database (test_the_database_refuses_the_collision_too), which shows the pattern is understood in Sub and simply absent here."

[[gaps]]
repository = "dotmac_sub"
subject = "invoice-line duplicate-billing index is PostgreSQL-only"
evidence_state = "structure-only"
detail = "uq_invoice_lines_active_billing_line_key (app/models/billing.py:1086-1092) is declared postgresql_where only, so the SQLite unit harness does not enforce it. Any module-side uniqueness canary must therefore run against real PostgreSQL, which the adoption plan's validation matrix already requires — recorded here so the requirement has a named reason."
```

---

## 10. What this verification did not change

The published conclusion stands in full: one `dotmac-subscriptions` owning
stable offers, immutable offer/price versions, stable subscription contracts
with immutable effective-dated versions and lines, the `BillingCadence` value
object and calendar arithmetic, declared proration and fixed-recurring rating
provenance, and the unique recurring charge occurrence — on two declared
persistence planes, with Vendor CP adopting the platform plane first and Sub the
tenant plane through measured shadow-and-cutover, Sub remaining the product-first
source, Vendor's legal commercial agreement staying distinct, and Sub retaining
service and access lifecycle.

Nothing here is a gate. ADR-0020 § 6 and ADR-0017 P11 remain the gates. § 7 is
the only item this verification puts back to Michael, and it is a *future*
question — the recommendation is that no action is needed now.

---

## References

- `docs/inventories/subscriptions-sources.md` — the published A2b audit (subject of this verification)
- `docs/superpowers/plans/2026-08-14-subscriptions-vendor-sub-adoption.md` — the execution plan
- `docs/adr/0020-billing-owns-operational-receivables.md` (amendment A1–A6, 2026-08-14)
- `docs/adr/0006-white-label-product-foundation.md` § 5, § 5a, § 5b, and the 2026-08-12 amendment
- `docs/adr/0017-adoption-is-the-scarce-resource.md` (P11)
- `docs/adr/0023-dual-plane-modules-declare-both-persistence-planes.md`
- `docs/adr/0024-apps-compose-by-synchronizing-data.md`
- `docs/adr/0026-approvals-decide-approval-never-the-transition.md`
- `docs/superpowers/plans/2026-08-11-billing-subscriptions-collections.md` (C1–C10, Stage B′, Stage H)
- `docs/inventories/fleet-decomposition-matrix.md` (`commercial-offers`, `sales-agreements`, A2 row)
- `docs/inventories/approvals-workflow-source-audit.md`, `docs/inventories/approval-workflow-dispositions.toml` (precedent shape)
- `dotmac_sub:docs/adr/0007-end-to-end-billing-target-architecture.md`
- Companions: `docs/inventories/subscriptions-extraction-dossier.md`,
  `docs/superpowers/specs/2026-08-14-subscriptions-public-contracts.md`
