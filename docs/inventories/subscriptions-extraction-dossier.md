# `dotmac-subscriptions` — extraction dossier content, parity tests, planes, retirement

**As of:** 2026-08-18
**Status:** `audit-complete`; M0 extraction implemented. No release or adopter
cutover is claimed.
**Source audit:** `docs/inventories/subscriptions-sources.md` (published A2b
conclusion) and `docs/inventories/a2-commercial-offer-source-audit.md`
(independent verification)
**Decision:** ADR-0020 amendment A4, 2026-08-14
**Execution plan:** `docs/superpowers/plans/2026-08-14-subscriptions-vendor-sub-adoption.md`
**Public surface:** `docs/superpowers/specs/2026-08-14-subscriptions-public-contracts.md`

## What this document is

The durable source audit behind the implemented
`packages/dotmac-subscriptions/EXTRACTION.toml`. The package now owns the
`subscriptions` / `su` / `mod_subscriptions` allocation and its selectable
dual-plane lineage. This document remains the detailed parity, cutover and
retirement evidence; the checked-in TOML is the machine-readable dossier.

It does not restate the source comparison. Read `subscriptions-sources.md` for
the published conclusion and `a2-commercial-offer-source-audit.md` for the
verification, its corrections, and the dispositions ledger.

---

## 1. Dossier content

```toml
schema_version = 1
package = "dotmac-subscriptions"
classification = "optional-module"
status = "audit-complete"
source_mode = "product-first"

owner = "The reusable recurring-commercial core on explicit tenant and platform planes: stable offers, immutable offer and price versions, stable subscription contracts with immutable effective-dated versions and lines, the composable cadence value object and its calendar arithmetic, declared proration, and the unique replayable recurring charge occurrence with its pre-tax rating provenance."

contract = "Publish immutable offer versions and their immutable price children beneath a stable offer identity, selected by a required TenantScope or PlatformScope; record immutable effective-dated subscription contract versions and lines with stable line lineage across supersession; resolve half-open service and invoice periods, declared proration factors and rate units from a persistence-free BillingCadence; and generate a uniquely identified, replayable recurring charge occurrence carrying exact pre-tax money, its complete rating provenance and a stable input fingerprint, emitted as a typed immutable command for a consuming assembly to deliver. NOT invoices or credit notes, NOT the operational receivable subledger, NOT payments, settlement, allocation, coverage or refunds, NOT tax or FX determination or application, NOT dunning, collections cases, payment arrangements or grace, NOT vendor accounts, commercial-agreement approval, entitlement allocation, licence issuance or revocation, NOT product access, service or RADIUS state mutation, NOT PSP or provider integration of any kind, NOT usage metering or usage rating, NOT document rendering, numbering or storage, NOT the general ledger."

source_repositories = [
  "dotmac_sub",
  "dotmac_vendor_control_plane",
]
source_revisions = [
  "dotmac_sub:27c76aaeebb7",
  "dotmac_vendor_control_plane:89848017d6b8",
]
source_paths = [
  "dotmac_sub:app/services/billing/cadence.py",
  "dotmac_sub:app/services/billing/contracts.py",
  "dotmac_sub:app/services/billing/rating.py",
  "dotmac_sub:app/services/billing/obligations.py",
  "dotmac_sub:app/models/billing_contract.py",
  "dotmac_sub:app/models/catalog.py",
  "dotmac_sub:app/services/catalog/offers.py",
  "dotmac_sub:app/services/catalog_billing_governance.py",
  "dotmac_vendor_control_plane:src/vendor_cp/offers/models.py",
  "dotmac_vendor_control_plane:src/vendor_cp/offers/service.py",
  "dotmac_vendor_control_plane:alembic/versions/v002_offer_versions.py",
]
preserved_tests = [
  "dotmac_sub:tests/test_billing_cadence.py",
  "dotmac_sub:tests/test_subscription_billing_cadence.py",
  "dotmac_sub:tests/test_billing_contracts.py",
  "dotmac_sub:tests/test_billing_rating.py",
  "dotmac_sub:tests/test_billing_obligations.py",
  "dotmac_sub:tests/test_billing_addon_contract_backfill.py",
  "dotmac_sub:tests/test_billing_phase2_shadow.py",
  "dotmac_sub:tests/test_billing_shadow_pipeline.py",
  "dotmac_sub:tests/architecture/test_billing_target_architecture.py",
  "dotmac_sub:tests/test_catalog_billing_governance.py",
  "dotmac_sub:tests/test_offer_contracted_amount.py",
  "dotmac_sub:tests/test_offer_name_uniqueness.py",
  "dotmac_vendor_control_plane:tests/unit/test_offers.py",
  "dotmac_vendor_control_plane:tests/migration/test_vendor_migration_rehearsals.py",
]
contract_consumers = []
candidate_consumers = ["dotmac_vendor_control_plane", "dotmac_sub"]

composition_boundary = "ADR-0024 § 2: dotmac-subscriptions, dotmac-billing, dotmac-collections, dotmac-durable-timers and dotmac-orders are peers over dotmac-kernel and none imports another. Each adopter installs its own copy of each selected migration lineage, owns its own rows, and wires every cross-module outcome through its assembly as a versioned command, event or typed port. Publishing a recurring obligation NEVER imports or calls billing: the module emits RatedObligationOutputV1 and the consuming assembly maps it to billing's AcceptRatedObligationV1. Timer scheduling and cancellation stop at DurableTimerPort, and accepted orders enter only through the subscriptions command surface. Vendor CP's commercial-agreement owner and Sub's service/access owner submit approved recurring terms INTO the module through the assembly and consume contract facts back out of it; neither gains a cross-module foreign key. Product semantics — Vendor capability codes, Sub ISP service/access type, region, usage allowance, SLA, policy set, RADIUS, speed, portal visibility, plan family — live in product-owned link tables created by the module's plane-specific link helpers in the ADOPTER's schema and lineage, never as columns in the module's tables. Charge models and obligation sources are ADR-0008 declaration registries supplied by the product; the module invents no vocabulary and branches on no product, provider, currency, plan name or deployment profile."

inventory_evidence = [
  "docs/inventories/subscriptions-sources.md",
  "docs/inventories/a2-commercial-offer-source-audit.md",
  "docs/inventories/fleet-decomposition-matrix.md",
  "docs/adr/0020-billing-owns-operational-receivables.md",
  "docs/superpowers/plans/2026-08-11-billing-subscriptions-collections.md",
  "docs/superpowers/plans/2026-08-14-subscriptions-vendor-sub-adoption.md",
]

first_cutover = "dotmac_vendor_control_plane is cutover 1, on the PLATFORM plane, by Michael's ordering recorded in ADR-0020 A4. It has the smallest offer source (one table, five business columns, one writer), it is the assembly that forces the platform plane to exist at all, and it has no recurring engine to retire — so the first cutover exercises the plane split and the offer contract without also carrying the recurrence risk. It is NOT a greenfield cutover: existing offer_versions rows and the publish_offer_version writer must be migrated and removed, and its capability_codes column must become a Vendor-owned platform link table. dotmac_sub is cutover 2, on the TENANT plane, and is the qualifying product-first source for cadence, contract versioning, proration and recurrence; it moves one owner slice at a time through measured shadow-and-cutover because hundreds of catalog, billing, service and provisioning callers read mixed generic and ISP fields. First adopter and implementation source are deliberately different claims. Cutover still requires released subscriptions, durable-timer and assembly-wired billing contracts even though their coordinated implementations now exist."

shadow_and_drift = "Vendor: before any write, characterize offer row counts, distinct codes, versions and currencies, duplicate or gapped versions, invalid exact-money values, every contract_lines.offer_version_id reference and any orphan, undeclared capability codes, and whether each active contract intended to recur has explicit cadence, timezone, collection timing, effective start and price — a missing term is a blocking NULL, never a monthly, Africa/Lagos or default-currency guess, and an unclassified active contract is a stop condition. Then backfill each local version into a stable platform Offer, one platform OfferVersion and one immutable price child preserving exact money, source identity, version and timestamps as provenance, and shadow-read every legacy and module snapshot; the reconciler reports missing, extra, price, currency, version and capability-link drift and repairs idempotently only from the declared authoritative side. Sub: write the cross-tenant PostgreSQL isolation canary before the first model or migration change, then shadow every sellable and contracted offer at an effective instant, with ambiguous active prices, mutable live terms, missing currency and unversioned contracted prices becoming explicit remediation cohorts rather than silent defaults. Compare effective-at resolution and supersession over the complete active cohort. Route every boundary through the module cadence and accept the shadow matrix — daily, weekly, monthly, N-month and yearly intervals; month-end 29/30/31; leap years; strict-same-day refusal; contract-anniversary, calendar-start and fixed-anchor alignment; timezone and DST boundaries; none, full, calendar-day and elapsed-time proration including no-coverage and out-of-period failures — before retiring any legacy helper. Known customer-visible differences are measured and approved, never normalized away to make parity green. Recurrence switches per named cohort against Sub's existing Phase-2 durable shadow evidence, comparing exact period, pre-tax amount, proration factor, source identity and billing acceptance, and expands only when duplicate, missing, overlapping and fingerprint-conflict counts are zero."

local_copy_retirement = "Vendor CP must delete vendor_cp/offers models, service and routes and drop its offer_versions table in an expand/contract release after parity is zero; contracts/service.py:537 _resolve_offer must stop importing vendor_cp.offers.models and receive an assembly-resolved typed immutable snapshot instead. Sub must remove generic offer, version and price writes from app/services/catalog/offers.py while keeping its ISP link writers; delete the local billing-contract, version and line writers in app/services/billing/contracts.py after promoting the module writer through Sub's SOT registry; retire NINE parallel cadence owners, not the three the plan currently names — billing_automation.py:272 _add_months, :288 _period_end, :2542 inline month arithmetic, catalog/subscriptions.py:92 _add_months, :135 _compute_next_billing_at, :485 _billing_cycle_start, web_catalog_calculator.py:19 _cycle_bounds, payment_arrangements.py:132 _add_month_clamped, web_billing_overview.py:193 _add_months — plus their five secondary consumers; retire the monthly-only prepaid renewal engine in prepaid_service_renewals.py and the postpaid engine in billing_automation.py into one collection_timing field; retire the legacy invoice-line dedupe at billing_automation.py:1782, :1931, :2252 and :1006 together with the concatenated-string index uq_invoice_lines_active_billing_line_key; retire Subscription.billing_mode, billing_cycle, next_billing_at and unit_price as cadence authority; and retire billing_profile.py entirely, since it exists only to detect the four-way cadence disagreement the contract version replaces. Every retirement is a two-directional ratchet with a sensitivity proof: the count must fail when it rises AND when it falls without the baseline being lowered in the same change. No permanent parallel writer, no runtime flag leaving both enabled, and no local copy retained as a fallback."

next_action = "Complete M0 proof and land this package with the coordinated billing, durable-timer, orders and collections slices. Then release exact versions and wire the producer, timer and billing contracts in the first adopting assembly. contract_consumers stays [] until Vendor CP's local offer writer is actually deleted; it becomes reuse-proven only when both products exercise the same released contract with their local owners gone."
```

### Why `status = "audit-complete"` and not `approved`

The source question is answered and the boundary is decided (ADR-0020 A4). M0
is now built because P11 and the coordinated implementation gates passed. What
is not yet true is adoption or reuse: neither candidate consumer has pinned a
release and retired its local writer. `audit-complete` is the honest state — the same state
`packages/dotmac-files/EXTRACTION.toml` carries with a far more advanced
adoption story.

`contract_consumers = []` for the same reason, and it is not a placeholder: a
consumer counts when its local owner is **deleted**, not when it installs the
package. ADR-0006's 2026-08-12 amendment — a second consumer is evidence, not
permission — is the rule being honoured.

---

## 2. What the module does not own, restated as testable exclusions

The `owner` and `contract` fields above carry these; they are repeated here in
the form a guard can be written against, because a boundary stated only in prose
is a boundary nobody can fail.

| Excluded | Owner | The check |
|---|---|---|
| Invoices, credit notes | `dotmac-billing` | no module model named `*invoice*` / `*credit_note*`; no document numbering |
| Operational receivables, funding, coverage | `dotmac-billing` | no `receivable`, `balance`, `available_credit`, `prepaid_funding` symbol |
| Payments, settlement, allocation, refund, reversal | `dotmac-billing` | no `payment`, `settle*`, `allocat*`, `refund`, `reversal` symbol on a module model |
| Tax and FX determination or application | `dotmac-billing` | no `tax_rate*`, `fx_*`, `tax_amount`, `gross_amount` column; no FK to a tax table |
| Dunning, collections cases, arrangements, grace | `dotmac-collections` | no `dunning`, `case`, `arrangement`, `grace`, `overdue` symbol |
| Vendor accounts, commercial-agreement approval | `vendor_cp` + `dotmac-approvals` | no `approval*`, `content_hash`, `policy_version`, `countersign*` symbol |
| Entitlement allocation, licence issue/revoke | `vendor_cp` | no `allocation`, `licence`, `entitlement`, `grant` writer |
| Product access, service, RADIUS state | `dotmac_sub` `access.subscription_lifecycle` | no `access_state`, `radius*`, `nas*`, `suspend_service` symbol; no `suspend` transition on any module aggregate |
| PSP / provider integration | Integrator connector plugin (ADR-0020 A3) | no HTTP client, credential reference, webhook verifier; no provider or currency name as identifier or default |
| Usage metering and usage rating | the later fourth module | `usage_metered` is a *declared* rate basis the module refuses to rate, not an implemented path |
| Document rendering, numbering, storage | P8a owner + `dotmac-files` | no renderer, no sequence, no byte persistence |
| General ledger, journals, periods, statutory books | ERP | no GL concept in any model or service |

Three of these are the ones actually at risk, because Sub's source has them
today and the port would carry them in by default: **tax**, **financial status**
and **service suspension**. § 4.4 names them by column, and the per-field
classification behind it — including which fields legitimately **stay** and
which become **rebuildable projections** rather than disappearing — is
`docs/inventories/a2-commercial-offer-source-audit.md` § 5.

---

## 3. Parity-test inventory

Every path below was verified to exist at the revisions in the header. Test
counts are from reading the files' test-function names.

### 3.1 Must port, and must keep passing

| Test file | Tests | What it proves | Port note |
|---|---|---|---|
| `dotmac_sub:tests/test_billing_cadence.py` | 18 | The whole cadence value object: quarterly is three calendar months not ninety days; annual is twelve months across a leap year; month-end anniversary clamps under the declared rule; strict-same-day fails closed instead of silently shifting; consecutive periods are contiguous and half-open; **rate unit is independent of invoice interval**; an annual service period can be invoiced quarterly; `period_containing` walks calendar periods; a moment before contract start fails closed; calendar-day proration is declared not inferred; `none` bills the full period; a covered interval outside the period fails closed; calendar alignment snaps to the month boundary; periods compute in the contract timezone; naive datetimes are refused; an unknown timezone fails at construction; fixed-anchor alignment requires an anchor day; an interval must end after it starts. | **Port in full, with one explicit defect correction.** The source's day-number comparison rates a clamped 31 January–28 February interval as zero monthly units. The module compares the resolved calendar anniversary and adds a regression canary proving that interval is one unit. This is the module's foundation suite. |
| `dotmac_sub:tests/test_subscription_billing_cadence.py` | 7 | Cadence **precedence**: contracted cadence beats offer cadence; offer cadence is snapshotted when uncontracted; the biller prefers subscription over offer and falls back when unset; next-billing computation for quarterly; cadence-aware price suffix. | **Port the precedence cases.** Precedence is exactly the behaviour that disappears when the four parallel cadence stores retire, so these are the only tests proving the retirement did not change customer-visible behaviour. The published parity list omits this file; it must be on it. |
| `dotmac_sub:tests/test_billing_contracts.py` | 14 | Immutable versions; replay of one idempotency key writes one version; supersession closes the previous version **contiguously**; **line lineage survives supersession**; effective-at resolves exactly one row across a boundary; cadence round-trips through the stored version; mixed currency between contract and line is refused; a version cannot start before the current effective one; duplicate charge component on one version is refused; recording requires an idempotency key; the owner command rejects a caller-owned transaction; live add-on purchases coalesce without resetting contract cadence. | **Port all but the add-on cases**, which are Sub product behaviour and become an adopter test over the module's contract. `test_owner_command_rejects_a_caller_owned_transaction` becomes the module's transaction-authority test. |
| `dotmac_sub:tests/test_billing_rating.py` | 11 | Fixed-period rating is the contracted line amount; rating is deterministic; a recorded policy version must have an explicit replay implementation; a per-day rate aggregates into a monthly period; declared calendar-day proration narrows the charge; rating an unknown line fails closed; usage-metered rating without an observation fails closed. Plus four tax cases. | **Port the six non-tax cases.** The four tax cases (`test_tax_is_added_from_the_named_active_rate`, `test_tax_inclusive_price_backs_the_net_out`, and the two fail-closed tax-rate cases) move to `dotmac-billing`. `test_usage_metered_rating_without_observation_fails_closed` ports **as a refusal test** — the module declares the rate basis and refuses to rate it. |
| `dotmac_sub:tests/test_billing_obligations.py` | 14 | Scheduling creates one occurrence for an exact period; replaying the same natural identity returns one row; **the same natural identity with different coverage fails closed**; a **corrupt recorded rating fingerprint fails replay**; consecutive periods do not gap or overlap; scheduling requires an existing contract version; replay uses recorded provenance after configuration changes. Plus settlement cases. | **Port the seven identity/replay/period cases.** Opening, partial-then-full settlement, over-application refusal, typed non-cash resolution, resolve-before-open refusal, account-and-currency scoping, and separate-tax-into-gross move to `dotmac-billing`. |
| `dotmac_vendor_control_plane:tests/unit/test_offers.py` | 4 | Publish persists **exact `Money`** and capability codes; an **undeclared capability is rejected**; **versions are immutable**; **publish is idempotent on `command_id`**. | **Port all four as the mandatory port deltas.** The capability-code case becomes a link-table test rather than a generic offer-column test, and the immutability case must be strengthened to a database constraint (Vendor's is service-only — its migration grants `UPDATE, DELETE`). |
| `dotmac_sub:tests/test_billing_addon_contract_backfill.py` | 6 | A recurring purchase is versioned into the contract and drives occurrences exactly once; a partial period fails before capture; a live purchase joins the existing draft before the boundary; an **ambiguous recurring price fails closed**; a changed quantity rejects a confirmed preview. | **Port the ambiguity and once-only cases** as module behaviour over a generic line. The add-on domain itself stays in Sub. |

### 3.2 Must port as guard *design*, not as tests

| Test file | Tests | Why |
|---|---|---|
| `dotmac_sub:tests/architecture/test_billing_target_architecture.py` | 13 | ADR-0007's ratchet: `test_no_new_mutable_money_counter_writes`, `test_no_new_metadata_financial_authority_reads`, `test_no_new_scheduled_financial_sweep`, and — crucially — `test_sweep_baseline_is_sorted_and_unique`. This is the closest existing implementation in the fleet of the two-directional ratchet ADR-0018 requires. The module's own retirement ratchets should be **derived from this file's mechanism** rather than designed fresh. Also proves the Phase-1/2/3 shadow chain and that shadow obligations take money only from rating. Omitted from the published parity list; it must be on it. |
| `dotmac_sub:tests/test_catalog_billing_governance.py` | 7 | Proves a live offer's cadence, price and price unit cannot be mutated in place, and that a duplicate active price is rejected. In the module these become **structurally unnecessary** — an immutable published version cannot be mutated at all — so they port as the *specification of the behaviour being replaced*, and the module's equivalent asserts the stronger property directly. |
| `dotmac_sub:tests/test_offer_name_uniqueness.py` | 6 | Includes `test_the_database_refuses_the_collision_too`. Sub already knows how to prove uniqueness **at the database** for offer names; it simply never did it for offer versions. The module's `(scope, offer, version)` uniqueness canary is this test's shape applied to the thing that actually needed it. |

### 3.3 Shadow and cutover evidence, used but not ported

| Test file | Tests | Role |
|---|---|---|
| `dotmac_sub:tests/test_billing_phase2_shadow.py` | 11 | Sub's durable Phase-2 parity evidence: exact postpaid period and amount parity; base-plus-recurring-add-on identity; add-on parity requires structural target line identity; multiple active add-on prices are **not** parity; exact prepaid monthly parity; prepaid recurring add-on exclusion is a cutover blocker; a new prepaid quarterly cadence is an **explicit expected difference**; a missing target obligation blocks approval; incomplete legacy provenance is unresolved; a gap is detected **without attempting repair**; replay returns the same evidence. This is the comparison harness Sub's cutover 2 runs against — it stays in Sub. |
| `dotmac_sub:tests/test_billing_shadow_pipeline.py` | 4 | Phase-1 evidence: cohort approvals separable; an unlinked active subscription blocks approval; runs use the version effective at cutoff; idempotency keys cannot be reused across identities. Stays in Sub. |
| `dotmac_vendor_control_plane:tests/migration/test_vendor_migration_rehearsals.py` | 7 | `test_platform_role_access_and_tenant_role_denial` is the platform-plane isolation pattern the module's canary follows — **and it currently covers only `vendor_accounts` and ten licence tables, not `offer_versions`, `contracts` or `contract_lines`.** The module must not inherit that gap; its canary covers every declared platform table. Also `test_two_head_topology` and `test_kernel_advance_keeps_vendor_head_independent`, which are the lineage-independence patterns. |

### 3.4 Deliberately not ported

`dotmac_sub:tests/test_catalog_contracts.py` is a **false friend** — catalog
overview UI KPI contracts, not billing or catalog offer contracts.
`tests/test_billing_alignment_audit.py` (43), `test_billing_integrity_audit.py`
(17), `test_billing_runner_drift.py` (13), `test_billing_mode_audit.py` (4) and
`test_billing_path_coverage.py` (3) are Sub's read-only drift harnesses over its
*current* owners; they measure the thing being retired and retire with it.
`dotmac_vendor_control_plane:tests/unit/test_allocations.py`,
`test_approvals.py` and `test_accounts.py` prove Vendor-owned consequences that
stay Vendor's — but `test_stage_writes_no_product_ws2_grant` is the exact shape
of the "entitlement effects are outputs, never writes" canary the module needs
on its own side.

---

## 4. Dual-plane design note (ADR-0023)

### 4.1 One behaviour engine, zero persistence

`dotmac_sub:app/services/billing/cadence.py` is already the right shape and is
the reason this module can be dual-plane at all: 456 lines, owns no records,
opens no transaction, reads no session, and its docstring says so. The module's
engine is that file plus the pre-tax half of `rating.py` and the lifecycle
guards from `contracts.py`, with **two deliberate deltas**: `cadence.py:32-39`
imports its vocabulary from `app.models.billing_contract`, so the engine owns
those types itself; and monthly rate-unit counting compares the resolved
calendar anniversary instead of raw day numbers, so a clamped 31 January–28
February interval is one unit rather than zero. If the engine imports
persistence, the "one behaviour" claim is false and the guards cannot be reused
on the other plane — ADR-0023 § 1 says exactly this.

Extensible vocabularies (charge model, obligation source) are ADR-0008
declaration registries supplied by the product. Closed vocabularies (interval
unit, collection timing, alignment, end-of-month rule, proration policy) stay
closed enums in the engine, because they are calendar facts and not product
vocabulary — this is the same split C1 draws when it forbids a cycle enum while
allowing "a closed interval-unit vocabulary plus a quantity".

### 4.2 The two planes

| | Tenant plane | Platform plane |
|---|---|---|
| Declared as | `ModuleManifest.tables` | `ModuleManifest.platform_tables` |
| `tenant_id` | `UUID NOT NULL` on every table | **absent** from every table |
| Isolation | RLS `ENABLE` **and** `FORCE`, tenant policy, in the same migration that creates the table | no RLS; `REVOKE ALL` from the tenant app role across **every table and column privilege**; schema `USAGE` plus at least one of `SELECT`/`INSERT`/`UPDATE`/`DELETE` for the online platform role |
| Uniqueness | composite, `tenant_id` first | control-plane-wide |
| Foreign keys | composite, include `tenant_id` | single-column |
| Link helper | tenant-scoped, composite FK, emits the isolation policy | no tenant column, single-column FK, emits the revoke |
| Scope value | required `TenantScope` at every service entry point | required `PlatformScope` at every service entry point |
| First adopter | `dotmac_sub` (cutover 2) | `dotmac_vendor_control_plane` (cutover 1) |

**No foreign key crosses the planes, in either direction** (ADR-0023 § 4). A
table appears in exactly one plane; both is rejected at manifest construction
and again in the registry.

The plane is **declared, never inferred**. Inferring it from a missing
`tenant_id` is the load-bearing rejection: a tenant table that merely *forgot*
its column would reclassify itself as platform and lose its isolation silently.

**On the platform plane the `REVOKE` is the isolation** and is checked as
strictly as an RLS policy. Declared-and-unreachable is equally a violation: the
online platform role must have schema `USAGE` and real row DML. § 3.3 records
that Vendor CP's existing platform test does **not** cover its offer or contract
tables; the module's canary must cover every declared platform table, or it
inherits a hole.

### 4.3 Explicitly rejected

Each of these has been proposed somewhere in the fleet and each is refused by the
gate, not merely discouraged:

- **`platform=True` (or any boolean/mode flag on one table set).** A flag makes
  the two opposite contracts a runtime branch; whichever check ran second would
  decide. Two declared sets, held to their own contracts, is the decision.
- **Nullable `tenant_id`.** The column stops being an isolation key: the RLS
  predicate either denies the platform rows to everyone or needs a second, wider
  policy. The kernel already carries one documented exception of this shape
  (`domain_settings`) and its cost is a split read/write policy pair. ADR-0017's
  own amendment records a kernel defect where exactly this nullability let a row
  persist that the resolver could not reach.
- **A sentinel or fake tenant** for the vendor control plane. Every query and
  every report would then have to know which tenant id means "not a tenant".
  That knowledge is unwritten, spreads by copy-paste, and is wrong the first time
  someone forgets it. ADR-0020 A6 says it in one line: Vendor CP, **no fake
  tenant**.
- **A polymorphic scope column** (`scope_kind` + nullable `scope_id`). A UUID
  PostgreSQL does not know means anything: referential integrity is gone and the
  isolation predicate becomes a conditional on data rather than a structural
  property.
- **Two modules.** They would duplicate the cadence engine, the lifecycle, the
  supersession rules, the natural identity and the fingerprint — the entire
  reason the module exists — to avoid duplicating fourteen `CREATE TABLE`
  statements.

### 4.4 The financial-field classification, as a guard rather than a convention

**The authority for this is `a2-commercial-offer-source-audit.md` § 5, which is a
decision gate in its own right.** "Recurrence carries no financial status" is too
blunt to build against, so every amount and status field on Sub's recurring and
occurrence models carries exactly one of three dispositions: **stays**
(scheduling, commercial term, identity, rating provenance); **moves to Billing**
(a downstream financial fact); or **becomes an explicitly rebuildable
projection** (a local read convenience with a named writer, provenance, drift
detection and repair — never an authority). The operative line is that
subscriptions **may** own `scheduled`/`emitted`/`cancelled`/`replaced`/
`superseded` and the immutable pre-tax amount snapshot, and **must not** own
`paid`, `partially_paid`, outstanding balance, allocation, coverage, `overdue`,
or any collections status.

The module ships a guard that fails the build if any **category 2** name appears
as a column on a module model:

```
occurrence:  tax_amount, gross_amount, resolved_amount, accounting_treatment,
             resolution_kind, opened_at, resolved_at, due_at, reversed_by_id,
             rating_tax_treatment_code, rating_tax_rate_id,
             rating_tax_rate_percent, rating_tax_inclusive,
             and the open / partially_resolved / resolved / written_off
             members of any state vocabulary
contract:    tax_inclusive, accounting_treatment
```

`pre_tax_amount` — Sub's `net_amount` (`app/models/billing_contract.py:616`)
under a name that cannot be mistaken for net-of-payments — must **pass** this
guard. That is what makes it a classification rather than a ban on the word
"amount", and the sensitivity proof asserts both directions: `tax_amount` fails,
`pre_tax_amount` passes.

Two contract-layer fields need a rule rather than a ban, because the *commercial*
fact is legitimately the contract's:

- **`tax_treatment_code`** travels on the contract version and line as an
  **opaque declared code with no local semantics** — the module stores and
  forwards it and never reads it. A code the module interprets is a tax decision
  the module made. The guard's sensitivity proof adds a single `if` on the code's
  value inside the module and asserts the build fails.
- **`payment_terms_days`** stays as a negotiated commercial term the module
  stores and forwards; the module never computes a due date from it. That is the
  line between owning a term and owning `overdue`.
- **`discount_code` and `discount_amount`** stay outright: a discount changes the
  **pre-tax** amount, so it is a rating input and must be inside the fingerprint's
  input set, or two differently-discounted ratings would collide as a replay.
- **`rating_tax_rate_id`** is a foreign key from the recurrence row to
  `tax_rates`. It cannot survive in any form: it crosses an owner boundary
  (ADR-0024 § 2) and would be a cross-plane FK on the platform side (ADR-0023
  § 4).
- **`authority`** (`billing_contract.py:221`, `:312`, `:591`) is in **no**
  category. It is ADR-0007's shadow/authoritative migration gate; in the module a
  row's existence is its authority, so it retires with the migration rather than
  moving anywhere.

The `state` column needs the same treatment for a different reason:
`ObligationState` fuses `scheduled`/`canceled` (schedule lifecycle, the module's)
with `open`/`partially_resolved`/`resolved`/`written_off` (financial lifecycle,
billing's) in **one** enum on **one** column, with `_open` and `_resolve`
transitions in the same owner. The module's occurrence state is
`scheduled → due → emitted` plus `scheduled → cancelled`, and **nothing else** —
enforced by a test asserting the state vocabulary is exactly those four values.

Likewise `ck_billing_obligation_rating_provenance_complete` requires
`rating_tax_rate_percent IS NOT NULL` and `rating_tax_inclusive IS NOT NULL` in
the same predicate as `rating_unit_price`, `rating_quantity`,
`rating_proration_factor` and `rating_input_fingerprint`. The module's
provenance-complete constraint is **re-derived from the pre-tax inputs only**,
never copied, or it will force a tax column onto a pre-tax row.

---

## 5. Retirement inventory

A cutover that leaves the old writer installed beside the new one is not a
cutover. Each item below names what dies, in what order, and the ratchet that
proves it.

### 5.1 Vendor CP — platform plane (cutover 1)

| Order | What retires | Evidence it is gone |
|---|---|---|
| V1 | nothing yet — pin, compose the lineage, platform session only | a fake tenant anywhere in the composition is a failed cutover |
| V2 | nothing yet — expand: backfill `offer_versions` into platform `Offer`/`OfferVersion`/price child; `capability_codes` into a Vendor-owned platform link table | shadow reconciler reports zero missing, extra, price, currency, version and capability-link drift for the accepted window |
| V3 | `src/vendor_cp/offers/models.py`, `service.py`, `router.py`, `schemas.py`, `catalog.py` and the `offer_versions` table, in an expand/contract release | **ratchet A**: count of `vendor_cp.offers` imports and of direct `offer_versions` reads reaches zero, and the baseline is lowered in the same change |
| V3 | `contracts/service.py:537 _resolve_offer`'s direct import of `vendor_cp.offers.models.OfferVersion` | **ratchet B**: an import-linter contract forbidding the contracts package from importing the offers package; the offer snapshot arrives from the assembly as a typed value |
| V4-V5 | nothing retires — Vendor gains recurrence it never had | no contract, subscription or billing action writes a product data plane; timer replay and concurrent generation create **one** occurrence and **one** billing acceptance |

Vendor's cutover is small precisely because it has one writer, five columns and
no recurrence. That is why it goes first: it proves the plane split and the offer
contract without carrying the recurrence risk.

### 5.2 Sub — tenant plane (cutover 2), in dependency order

| Order | What retires | Ratchet |
|---|---|---|
| S2 | generic offer/version/price **writes** in `app/services/catalog/offers.py` — `Offers.create/update`, `OfferVersions.create/update/delete`, `OfferVersionPrices.create/update/delete` (11 `db.commit()` calls, 9 `HTTPException` raises) | count of generic-catalogue write call sites reaches zero; ISP link writers stay and are excluded by name, not by directory |
| S2 | `app/services/catalog_billing_governance.py::assert_offer_version_update_safe` and `assert_offer_version_price_update_safe` | structurally unnecessary once versions are immutable; the ratchet is that the guard's call sites reach zero **and** the module's immutability canary is green |
| S2 | `OfferPrice` / `offer_prices` (the unversioned pre-versioning path) | zero remaining readers; `test_a_pinned_offer_version_price_wins` already establishes the versioned row as the winner |
| S3 | local `BillingContract`/`Version`/`Line` writers in `app/services/billing/contracts.py`, after promotion through Sub's SOT registry | one writer per transition in the registry; the old owner's `MigrationContract` moves past `SHADOWING` |
| S4 | **nine parallel cadence owners** — `billing_automation.py:272 _add_months`, `:288 _period_end`, `:2542` inline; `catalog/subscriptions.py:92 _add_months`, `:135 _compute_next_billing_at`, `:485 _billing_cycle_start`; `web_catalog_calculator.py:19 _cycle_bounds`; `payment_arrangements.py:132 _add_month_clamped`; `web_billing_overview.py:193 _add_months` | **ratchet C**, two-directional, over the nine names **and** their five secondary consumers (`customer_portal_flow_common.py:38-52`, `account_lifecycle.py:1394`/`:1423`, `subscription_billing_treatments.py:254`, `prepaid_recovery_billing.py:349`, `prepaid_service_renewals.py:706`/`:2125`/`:2286`). The adoption plan currently names three targets; the baseline must be built from the real list or the ratchet passes while three owners survive |
| S4 | the plan-change `_calculate_proration` owner | zero call sites outside the module adapter |
| S5 | `BillingObligation`'s recurrence half — split the 44 columns per § 4.4 | the pair reconstructs the legacy fact with no double charge and no lost resolution; neither module FKs the other; the assembly stores an opaque correlation |
| S5 | legacy invoice-line dedupe: `billing_automation.py:1782-1793`, `:1931-1938`, `:2252-2258`, `:1006-1031`, and the concatenated-string index `uq_invoice_lines_active_billing_line_key` (`app/models/billing.py:1086-1092`) | replaced by the module's real natural-identity constraint. This retirement is high-value on its own: the legacy guard keys on the line **description string**, built at `:1777` as `f"{offer_name} ({start} - {end})"`, so renaming an offer defeats it |
| S6 | the **monthly-only prepaid engine** (`prepaid_service_renewals.py:2271 run_due_prepaid_service_renewals`, `:2017 apply_due_prepaid_service_after_funding_change`, `:427-431`, `:694-700`, and the four `resolve_prepaid_monthly_*` functions) and the **postpaid engine** (`billing_automation.py:1420 run_invoice_cycle`, `:544 preview_postpaid_recurring_charge`) | both collapse into one engine and one `collection_timing` field. **Ratchet D**: no module symbol is named for exactly one timing mode, and the same scenario under `advance` and `arrears` traverses the same owner functions. The fork point `advance_renewal_invoicing.py:313-338` is deleted, not flagged |
| S0 | **dead obligation surface, deleted BEFORE the split** — `corrects_id` and `reversed_by_id` (zero readers and zero writers anywhere, tests included), the occurrence copies of `collection_timing` and `is_finite` (written once, never read by anyone), and `rating_provenance_complete` | the cheapest work in the programme: a column with no reader and no writer needs deleting, not classifying, migrating, shadowing and reconciling. Ratchet: the column count on `billing_obligations` falls and the baseline falls with it |
| S6 | `Subscription.billing_mode`, `billing_cycle`, `next_billing_at`, `unit_price` lose **authority** — they do **not** simply die | **Category 3, not deletion.** `next_billing_at` alone is read by **49 distinct modules** (44 Python + 5 templates) plus seven one-off scripts; `prepaid_service_renewals.py` selects its whole due cohort on it (`:1794-1802`, `:2093-2098`, `:2301-2306`) and `billing/payments.py:1159-1167` advances it on payment. `unit_price` is still the actual money source for four billing paths (`billing_automation.py:390-393`, `prepaid_recovery_billing.py:352`, `prepaid_service_renewals.py:348`, `prepaid_draft_reconciliation.py:903`). Each becomes a Sub-owned **rebuildable projection** with exactly ONE writer recomputing from the module's `service_period(cadence, contract_start, index)` or the effective contract line, plus drift detection and idempotent repair. **The ratchet is on writers, not readers**: today at least four sites write `next_billing_at` (`catalog/subscriptions.py:983`, `:1498`, `account_lifecycle.py:1417-1423`, `billing/payments.py:1159-1167`); afterwards there is one. `prepaid_renewal_terms_backfill.py:5,13` already documents this intent for `unit_price` |
| S6 | `SalesOrderFundingObligation` (`app/models/sales_order_funding.py:85-116`) | **nothing retires — it is already category 3 done correctly.** It holds `obligation_id`, `resolved`, `resolution_kind`, `resolved_event_id`, `resolved_at`, fed by an **event** with provenance and never by a table read. It only changes upstream: fed by **billing's** resolution facts instead of Sub's, with `obligation_id` becoming the opaque correlation the assembly holds. It is the template for every other category-3 projection |
| S7 | `app/services/collections/postpaid_policy.py` and `prepaid_policy.py` repoint | the **only** two external readers of `BillingObligation`'s financial fields, and both are reachable only from `scripts/billing/billing_target_shadow.py:860-862` today — no service, task or handler calls them. They move to `dotmac-collections` reading **billing's** receivable contract. **Cutover requirement:** `prepaid_policy.py:57` reads `period_start` — a category-1 field — on the same row as the financial ones, so billing's receivable must carry the service period forward from `RatedObligationOutputV1`, or prepaid collections loses its "the service period has not started" guard and manufactures cases for future periods. **Second requirement:** both policies compute `outstanding = gross - resolved` identically (`postpaid_policy.py:46`, `prepaid_policy.py:59`); billing's receivable should publish the outstanding amount directly rather than let two consumers re-derive it |
| S7 | `app/services/billing_profile.py` **entirely** (359 lines) | it exists only to detect the four-way cadence disagreement the contract version replaces. Its retirement is the clearest single proof the cutover worked: there is nothing left to disagree |
| S8 | `catalog_offers`' 29 ISP columns and `offer_versions`' nine, contracted into Sub-owned link tables | upgrade rehearsal plus fallback-read count zero, **then** contract. Sub's SOT registry, relationship map, ADR-0007 migration state and operator runbook update in the same cutover |

### 5.3 The ratchet contract (ADR-0018)

Every ratchet above is **two-directional**: it fails when the count rises **and**
when it falls without the baseline being lowered in the same change. Each carries
a **sensitivity proof** — a deliberately introduced violation that the check must
catch — because a guard that cannot fail is not a guard. "Grandfathered" stays
distinct from "reviewed and correct", and each detector enumerates entry-point
families (services, tasks, scripts, CLI, workers, cron, web handlers), never one
directory. `tests/architecture/test_billing_target_architecture.py::
test_sweep_baseline_is_sorted_and_unique` is the existing implementation of this
mechanism and is where the module's version should come from.

**Never leave both writers enabled behind a runtime flag.** Switch the owner once
per cohort, disable the legacy path, delete it in the same release train.

---

## 6. Open items this dossier does not close

1. **`vendor_cp.contracts` as its own module.** Recommended *not now* —
   `a2-commercial-offer-source-audit.md` § 7 gives the evidence and names the
   trigger (a second consumer from the CRM→Sub sales-agreement consolidation)
   and three preconditions.
2. **The `RatedObligationOutputV1` ↔ `AcceptRatedObligationV1` pairing.**
   Both producer and consumer implementations are being landed in the same
   coordinated train; the adopting assembly must still map them and prove the
   contract suite before recurring output becomes financially effective.
3. **P3 durable timers** now has a product-first owner on the coordinated timer
   worktree. `dotmac-subscriptions` consumes only `DurableTimerPort`; the
   adopting assembly must bind that port to the released timer package and
   prove generation/replay behavior. A cron scan remains inadmissible.
4. **Vendor CP's untested platform revokes, untested contract suspension, and
   unimplemented `superseded_by_id`** (`contracts/models.py:72`). None blocks
   this module; all three block ever auditing a commercial-agreement module.
