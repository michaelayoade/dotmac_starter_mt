# Subscriptions sources

**As of:** 2026-08-15
**Starter:** kernel/`AGENTS.md` read at `1d6a5cda`; ADR-0030,
`cloud-commerce-owner-sources.md` and `numbering-sources.md` read at `e6ba202`
on branch `docs/adr-0030-cloud-commerce-composition`. This file is currently
written against the `fix/appdir-import-safety` working tree and belongs on the
ADR-0030 branch.
**Sub:** `27c76aaeebb7`
**Vendor CP:** `89848017d6b8` — NOT the checked-out `f9ca367c`; every Vendor
citation is `git show 89848017d6b8:<path>`
**ERP:** `0f4b1698ddbf` (revision-pinned; worktree had 67 local paths)
**CRM:** `c64b5aa0f790` (revision-pinned; worktree had 3 local paths)
**Decision:** ADR-0030 §5a authorizes `dotmac-subscriptions` as the fourth
business owner (build-order step 9, after Orders) and names Sub the
cadence/contract/recurrence source.

**Vendor CP is NOT the immutable-publication source.** The 2026-08-15 amendment
corrected that claim on this dossier's own evidence: `v002_offer_versions.py`
grants `UPDATE, DELETE` on `offer_versions` to the online API role, versions are
caller-asserted, and there is no digest or previous-version link. Vendor CP
remains evidence for exact money, platform-plane operation, `(offer_code,
version)` uniqueness and declared capability membership. Structural
immutability is **built** — the eight requirements are listed in ADR-0030 §5a
and in the shared-contract section below.

This supersedes the 2026-08-14 A2b offer-catalogue audit at this path, which
compared tables and writers but did not count callers, characterize the live
recurrence job, or test the money/snapshot defect claims. Plane design, parity
dispositions and retirement order stay in
[`subscriptions-extraction-dossier.md`](subscriptions-extraction-dossier.md);
this file rules only on sources.

## Verdict

`dotmac-subscriptions` is **product-first with a mandatory port delta**.

Sub is the mandatory code and test source for cadence, contract versioning,
proration, rating provenance and the recurring charge occurrence. Vendor CP's
append-only publish command is the mandatory delta for offer/price publication,
because Sub mutates published prices in place.

Two qualifications change how the port must be read:

1. **Sub's best code is shadow-only.** `contracts.permitted_authority()` returns
   `BillingRecordAuthority.shadow` because
   `app/services/sot_registry/domains/financial_access/billing.py:538` declares
   `state=AuthorityMigrationState.SHADOWING`. Those rows are migration evidence
   and produce no financial effect. Right design, good tests, no production
   traffic.
2. **Sub's live money path is the legacy one**, and it carries the defects
   below. The port takes the shadow design plus the legacy suite's *coverage* —
   never the legacy implementation.

Vendor CP is a source for one command shape and its four canaries, not for a
catalogue: no parent offer table, no effective dating, no version allocation,
no publication digest.

Neither product supplies the complete contract, and neither supplies any
isolation evidence. Sub has no `tenant_id` and no RLS anywhere
(`git grep -ln "ROW LEVEL SECURITY" 27c76aaeebb7 -- alembic/` → empty).
Vendor's real-PostgreSQL denial sweep enumerates `vendor_accounts` and ten
`licence_*` tables and omits `offer_versions`, `contracts` and `contract_lines`.

ERP and CRM contribute no code and no tests.

## Sub source

Mandatory paths:

- `app/services/billing/cadence.py` (456 lines);
- `app/models/billing_contract.py` (703) + `app/services/billing/contracts.py`
  (1669);
- `app/services/billing/rating.py` (445) +
  `app/services/billing/obligations.py` (728);
- `app/models/durable_timer.py` + `app/services/runtime_durable_timers.py`;
- `app/models/offer_availability.py` (saleability); and
- `tests/test_billing_cadence.py`, `tests/test_billing_contracts.py`,
  `tests/test_billing_rating.py`, `tests/test_billing_obligations.py`,
  `tests/test_billing_phase2_shadow.py`.

`cadence.py` is a pure resolver — owns no records, opens no transaction, reads
no session. It resolves a typed `BillingCadence` into half-open
`[starts_at, ends_at)` intervals in the *contract's* timezone and converts to
UTC at the boundary. A quarter is three calendar months. Rate unit and invoice
interval are independent, so a per-day rate aggregates into a monthly invoice.
Period `i` and `i+1` are both shifted from the original anchor, so a contract
starting on the 31st clamps to the 28th in February and *returns* to the 31st in
March. `strict_same_day_or_skip` raises `billing.cadence.skipped_month_boundary`
rather than silently moving money to another date. Naive datetimes and unknown
IANA zones fail closed at construction. This is the strongest artefact in either
repository.

`BillingContractVersion` gives terms an immutable, effective-dated identity:
`UniqueConstraint(contract_id, version)`, a partial unique index allowing at
most one open-ended `effective` version, a `supersedes_id` chain, and — on
PostgreSQL only — a `btree_gist` `EXCLUDE USING gist` temporal non-overlap
constraint at
`alembic/versions/430_billing_contract_obligation_identity.py:451`.
`BillingContractLine.contract_line_key` is a stable lineage identity across
supersession. Source is structural (`source_kind`/`source_id`/`source_version`),
never a metadata join.

`BillingObligation` is the recurring charge occurrence under another name. Its
`uq_billing_obligation_natural_identity` is a nine-column natural key
(`contract_line_key, contract_version_id, charge_component, source_kind,
source_id, source_version, period_start, period_end, currency`) — the "same
period never produces two occurrences" rule enforced by the database, not by a
worker check. Sixteen `CheckConstraint`s cover sign, half-open period, resolved
bound, and an all-or-nothing `rating_provenance_complete` gate over fifteen
provenance columns plus `rating_input_fingerprint`.
`rating.py::rate_from_provenance` replays from the *stored* inputs, and
`test_replay_uses_recorded_tax_provenance_after_tax_configuration_changes`
proves a later tax-config change cannot alter a historical charge. The
captured-snapshot behaviour the module needs already exists here.

`app/models/offer_availability.py` decomposes saleability already:
`OfferResellerAvailability`, `OfferLocationAvailability`,
`OfferCategoryAvailability`, `OfferBillingModeAvailability`, plus
`CatalogOffer`'s partial unique index `uq_catalog_offers_sellable_name`
`WHERE is_active AND available_for_services` — whose inline comment records the
incident behind it: two active offers named "25 Mbps Fiber", one ₦537,500 and
one ₦0.00.

`DurableTimer` carries `owner`, `entity_kind`, `entity_id`, `purpose`,
`generation`, `due_at`, `expected_source_version` — a generation-safe per-entity
wake-up used by 14 application files, and already the recurrence wake-up in
`advance_renewal_invoicing.py`. This is the shape of
`dotmac_kernel.durable_timers`.

### How widely it is used

The shadow stack has almost no callers — `BillingObligations` 3 application
files, `BillingContracts` 6, `BillingCadence` 14, the `rating.py` surface 4.

The live path is the opposite. **Eleven application files assign
`subscription.next_billing_at`**: `account_lifecycle.py`, `billing/payments.py`,
`billing/reconcile_unposted.py`, `billing_automation.py`,
`billing_cleanup_remediation.py`, `billing_prepaid_overlap_repair.py`,
`catalog/subscriptions.py`, `prepaid_billing_calendar_reconciliation.py`,
`prepaid_service_renewals.py`, `service_extensions.py`,
`subscription_billing_grants.py`. The cadence cursor has no canonical writer.
Twenty-one files reference the preset `BillingCycle` enum.

### What the tests actually cover

- `test_billing_cadence.py` — 18 tests, 287 lines: quarterly-is-three-months,
  annual across a leap year, declared month-end clamp, strict-same-day failing
  closed, contiguous half-open periods, rate unit independent of invoice
  interval, annual-service/quarterly-invoice, calendar-period walk, declared
  calendar-day proration, `none` billing the full period, covered-outside-period
  refusal, calendar alignment, contract-timezone arithmetic, naive-datetime and
  unknown-zone refusal. Pure functions; ports unchanged.
- `test_billing_contracts.py` — 14 tests, 589 lines: idempotency replay writing
  one version, contiguous supersession, line lineage surviving supersession,
  effective-version resolution across the boundary, cadence round-trip,
  mixed-currency refusal, backdating refusal, duplicate-component refusal, and
  `test_owner_command_rejects_a_caller_owned_transaction` — a real
  transaction-ownership canary.
- `test_billing_obligations.py` — 14 tests, 458 lines: one obligation per exact
  period, natural-identity replay, recorded-provenance replay after tax config
  changes, same identity/different coverage failing closed, corrupt fingerprint
  failing replay, consecutive periods neither gapping nor overlapping,
  idempotent open, over-application refusal, resolve-before-open refusal.
- `test_billing_rating.py` — 11 tests, 405 lines: deterministic rating,
  unimplemented policy version failing closed, per-day aggregation into a month,
  inclusive back-out, no-active-rate and multiple-active-rate failing closed,
  usage-metered-without-observation failing closed.
- `test_billing_phase2_shadow.py` — 11 tests, 787 lines: durable exact
  period/amount parity, base-plus-add-on identity, missing target obligation
  blocking approval, gap detection without attempted repair, replay returning
  the same evidence. Cutover-evidence design, used but not ported.
- `test_billing_automation_services.py` — 100 tests, 3214 lines over the
  *legacy* job. Strong negative coverage (no price, future date, inactive
  subscription/account, ended subscription, same-day reactivation not
  duplicating). Its coverage is the requirements list; its code is not the port.

**Every one runs on SQLite.** `tests/conftest.py` sets a deliberately
unreachable `DATABASE_URL` (port `9`) and builds in-memory SQLite engines with
UUID/JSONB shims. The PostgreSQL `EXCLUDE USING gist` constraint — the invariant
the model docstring calls PostgreSQL-only — is proven by no test at all, and
there is no concurrency, rollback or isolation proof anywhere in the stack.

Port the guard *design* too:
`tests/architecture/test_billing_target_architecture.py` runs three shrink-only,
two-directional baselines that fail when a count rises **or** falls without the
baseline being lowered in the same change. `billing_runner` sits on
`billing_scheduled_sweep_baseline.txt`, whose header states the replacement:
"ADR 0007 section 7 and invariant 18 replace business-wide scans with durable
per-entity timers created by the owning transition."

## Vendor CP source

Mandatory paths: `src/vendor_cp/offers/models.py`,
`src/vendor_cp/offers/service.py`, `tests/unit/test_offers.py`.

`publish_offer_version` is an append-only command: it selects
`(offer_code, version)`, raises `ConflictError("… versions are immutable")` if
present, and runs inside `process_once_platform`. Money never touches float —
the column is `String(40)` round-tripped through the kernel's `Money`, whose
`Money.of` raises `MoneyError("refusing to build Money from float; use
str/Decimal")`. Capability codes are validated against a declared catalogue
before the handler runs. `git grep -nE '\bFloat\b|Numeric\(|float\('
89848017d6b8 -- src alembic` and the same for
`utcnow|datetime\.now\(\)|datetime\.today` both return nothing: no float money,
no naive datetimes.

The related precedent, not itself a subscriptions source:
`src/vendor_cp/contracts/service.py:304-307` freezes
`ln.unit_amount = ov.amount` at submit and binds approval to a SHA-256
`content_hash` over a canonical JSON snapshot of terms and lines.

`tests/unit/test_offers.py` is four tests — exact-money round-trip, undeclared
capability rejected, republish under a *different* `command_id` conflicting,
same-`command_id` publish returning `was_duplicate` with one row. All SQLite.

The delta is narrow, and the source is barely used. `git grep -l
'vendor_cp.offers' 89848017d6b8` finds 17 files, of which exactly one
application file calls the service (`offers/router.py`).
`contracts/service.py:537` bypasses the owner and queries `OfferVersion`
directly, and the repository has no import-linter configuration to stop it.
Take the command shape and the four canaries; take none of the storage.

## ERP and CRM

Neither is a source. Both are surveyed because ADR-0030 §7 gives ERP none of the
seven modules and retires CRM's parallel commercial writers.

**ERP has one genuine parallel commercial catalogue, already broken inside ERP.**
`app/models/inventory/price_list.py` (`inv.price_list` + `inv.price_list_item`)
is effective-dated at two levels, supports quantity breaks, percent and absolute
discounts, base-list inheritance with `markup_percent` and priority ordering;
`app/services/inventory/price_list.py::resolve_price` filters those dates
against `as_of_date or date.today()`. It is the closest thing in the fleet to
the surface this module will own, and it is not a source for three reasons.
Prices are overwritten, never superseded — `add_item_price` does
`existing.unit_price = input.unit_price` … `existing.effective_from = …`, and
`update_price_list` is a blanket `setattr` loop with a two-key denylist; `git
grep -niE 'price_list_version|price_history|price_version' 0f4b1698ddbf --
app/ alembic/` returns nothing. The domain service is orphaned — three tracked
references (itself, its package `__init__`, its test) and **zero production
callers of `resolve_price`**, because the quote and sales-order services take
the price from caller input (`app/services/finance/ar/quote.py:229`,
`.../sales_order.py:228`). And the live writer is an untested web adapter,
`app/services/operations/inv_web.py::create_price_list_response`. ERP already
contains the dead-authority/live-bypass split this programme exists to prevent;
`Item.list_price` (`Numeric(20,6)`, undated) is a third price surface in the
same repo.

ERP has **no subscription, offer, billing-cycle, renewal or proration
implementation**. `app/services/finance/automation/recurring.py` (9 references)
is a *document* scheduler generating AR invoices/AP bills/expenses/journals from
a `template_data` JSONB — no subscriber, plan, offer, proration or renewal
decision. `app/models/finance/ar/contract.py` is IFRS-15 revenue recognition and
`app/models/finance/lease/lease_contract.py` is IFRS-16. `git grep -iE
'billing_cycle|renewal|prorat|rate_card|tariff' 0f4b1698ddbf -- app/` matched
only payroll, lease and expense-card code; `catalog` over `app/models/` and
`app/services/` returned zero.

**CRM already implements the correct pattern and must not regress.**
`app/services/selfcare.py:704::fetch_offers` reads the catalogue from Sub, its
docstring stating the rule outright: the plan catalogue comes from `dotmac_sub`
"(the source of truth) … instead of the CRM keeping a parallel plan list." A
chosen plan persists only as an opaque reference, `metadata_ =
{"sub_offer_id": …}` on the quote line, held by
`tests/test_quote_plan_picker.py`, `test_search_catalog_offers.py`,
`test_sales_order_plan_line.py` and `test_selfcare_fetch_offers.py`. `git grep
-nE 'effective_from|effective_to|price_version' c64b5aa0f790 -- app/models/`
finds only labour rates and a vendor document date.

CRM's retiring commercial writers: `app/services/crm/sales/service.py` (quote
totals, tax, line amounts, accept→sales-order), `app/services/sales_orders.py`
(order totals, `payment_status` machine, order numbering, commission accrual,
payment sync), `app/services/crm/web_quotes.py`,
`app/services/field/sales_orders.py`, `app/services/crm/portal_quotes.py`,
`app/services/reseller_commissions.py`, `app/services/inventory.py`
(`InventoryItem.unit_price` — an undated local price book overwritten by a
generic `setattr` loop and never populated by the ERP item sync, so it drifts by
construction). Two matter to this owner:

- `app/services/events/handlers/selfcare_customer.py:482::push_sales_order_subscription_to_selfcare`
  sends `unit_price=getattr(line, "unit_price", None)` to Sub's
  `POST /subscriptions`. CRM can set a subscription's recurring price to a value
  that is not the catalogue offer's price, from a hand-editable form field. This
  is the sharpest ADR-0030 §7 retirement item.
- `app/services/reseller_commissions.py` is the one place in any of the four
  repositories that snapshots correctly: `basis_amount`, `rate` and `amount` are
  frozen onto the accrual row, and `tests/test_reseller_commissions.py` proves
  idempotent accrual, no double-claim, no in-place void and mixed-currency
  refusal. A commission plan is not a catalogue, so it is not a source — but it
  is the behaviour the offer/price snapshot must match.

## Do not port

**Closed enums that must become open registries (ADR-0008).**
`app/models/billing_contract.py` alone declares thirteen PostgreSQL enums.
Calendar mechanics (`IntervalUnit`, `EndOfMonthRule`, `CadenceAlignment`) are
genuinely finite and may stay typed. Business vocabularies become registered
strings: `ChargeComponent` in particular is an ISP list (`installation`,
`activation`, `addon`, `equipment`), and a new charge component must not require
a release. Same for `RateBasis`, `ProrationPolicy`, `AccountingTreatment`,
`BillingContractSourceKind`, and `app/services/events/types.py::EventType`.

**Preset cycles and their parallel cadence owners.** `BillingCycle`
(daily/weekly/monthly/quarterly/annual) plus four separate `_add_months`
implementations — `billing_automation.py:272`,
`catalog/subscriptions.py:92`, `web_billing_overview.py:193`, and the correct
one at `billing/cadence.py:144`. `catalog/subscriptions.py::_compute_next_billing_at`
clamps with a bare `value.replace(...)`, ignores timezone, and has no declared
end-of-month rule.

**Technical specification as schema.** `CatalogOffer`/`OfferVersion` carry
`service_type`, `access_type`, `guaranteed_speed_type` and FKs to `region_zones`,
`usage_allowances`, `sla_profiles`, `policy_sets`; `Subscription` carries
`radius_profile_id`, `provisioning_nas_device_id`, `access_state`, `bundle_id`;
Vendor CP embeds `capability_codes` as a JSON list on the offer version. None of
it enters the generic tables — see "The reference rule" below.

**Provider names in the schema.** `Subscription.splynx_service_id` and
`Subscription.router_id` are provider identifiers in a commercial table; Sub also
has `app/services/billing/providers.py` and a `150_splynx_billing_transactions`
migration. A provider is an Integrator connector binding, never a column.

**Host coupling.** FKs from `billing_contracts`/`billing_obligations` to
`subscribers.id` and `subscriptions.id`; `BillingObligation.rating_tax_rate_id`
FK to `tax_rates` (tax is Billing's, ADR-0030 §1);
`BillingContractVersion.timezone_name` defaulting to `"Africa/Lagos"`; `currency
default "NGN"` on `OfferPrice`/`OfferVersionPrice` and `currency or "NGN"` at
`billing_automation.py:1830`.

**Framework errors and service-level commits.** `catalog/offers.py` raises
`HTTPException(404/409)` and calls `db.commit()` in every CRUD method;
`subscription_changes.py::apply` raises four and commits. Module services flush
and raise typed domain errors; the assembly owns the transaction and the
transport mapping. Sub's own
`test_owner_command_rejects_a_caller_owned_transaction` already proves the
correct direction exists in the shadow stack.

**Second writers.** Eleven writers of `subscription.next_billing_at`; the cadence
precedence chain implemented twice
(`catalog/subscriptions.py::_resolve_billing_cycle` and
`billing_automation.py::_resolve_price`); `Subscription.unit_price` written by
five files including `vendor_project_records.py`.

**Silent fallbacks.** `_resolve_billing_cycle` ends `else BillingCycle.monthly`;
`_resolve_price` and `prepaid_service_renewals.py::_newest_price` resolve
ambiguity by taking the newest active row with a `logger.warning`, then charge.

**The combined obligation aggregate.** `BillingObligation` also carries
`ObligationState`, `resolved_amount`, `ObligationResolutionKind` and tax columns.
Subscriptions owns scheduled/due/cancelled and pre-tax provenance only.

**The plan-change aggregate as it stands.**
`SubscriptionChangeRequest` holds FKs to `service_qualification_id`,
`field_fee_offer_id`, `work_order_id`, `service_order_id`,
`provisioning_readiness_decision_id`, `remote_radius_profile_id`,
`remote_radius_user_id`, `field_fee_invoice_id`, `field_fee_payment_id`,
`credit_note_id`, `account_adjustment_id`, `ledger_entry_id`. One row is
simultaneously a commercial change, a field-work order, a provisioning record
and a billing-document writer.

## Known defects/deltas

1. **A published price is mutable in place.**
   `catalog/offers.py::OfferVersionPrices.update` does `for key, value in
   data.items(): setattr(price, key, value)` then `db.commit()`. Its only guard,
   `catalog_billing_governance.py::assert_offer_version_price_update_safe`,
   permits the mutation whenever `_live_version_subscription_count(...) == 0`.
   A published price with no *currently* live subscription can be rewritten,
   silently changing what the historical record says was offered. `OfferVersion`
   and `OfferPrice` have the same shape. This is the ADR-0030 §5a mandatory
   delta.
2. **That guard is a read-then-write race.** The count is an unlocked `SELECT`
   followed by `setattr` and `commit`. A subscription created concurrently
   attaches to a price that changed underneath it.
3. **A historical charge is recomputed from the live price row.**
   `billing_automation.py::_resolve_price` selects `OfferVersionPrice …
   is_active.is_(True) … order_by(created_at.desc()).limit(2)` at charge time and
   returns `version_price.amount`. `run_invoice_cycle` and
   `preview_postpaid_recurring_charge` both consume it; the prepaid owner's
   `_resolve_prepaid_monthly_charge_details` reads the same rows. With defect 1,
   editing a catalogue price changes the next recurring charge with no contract
   supersession. The shadow stack does *not* have this defect — which is exactly
   why it, and not the live path, is the port.
4. **An upgrade mutates the subscription rather than superseding a version.**
   `subscription_changes.py::apply` calls
   `catalog_service.subscriptions.update(… SubscriptionUpdate(offer_id=
   request.requested_offer_id …))`. Prior terms survive only in the change
   request's `confirmation_snapshot` JSON.
5. **A missed recurrence run is not repairable from the contract.**
   `billing_automation.py:1670-1676`, verbatim: "Skip wholly-past billing periods
   instead of invoicing every missed month … We fast-forward next_billing_at to
   the current period and bill only that. Historical arrears require an explicit
   reviewed repair …". The loop then assigns `subscription.next_billing_at =
   period_start`. Repair is possible only from the mutable worker cursor, and the
   discarded periods leave no occurrence row.
6. **Recurrence idempotency is a check-then-insert over a string key with a
   conditional index.** `_billing_line_key` builds
   `f"subscription:{id}:{period_start.isoformat()}:{period_end.isoformat()}:{component}"`;
   `_billing_line_key_exists` queries it; the backing index
   (`174_invoice_line_subscription_idempotency.py`) is `UNIQUE … WHERE is_active
   AND billing_line_key IS NOT NULL`. Soft-deleting the line releases the key and
   permits a second charge for the same period. Concurrent runs are untested.
   The shadow stack's unconditional nine-column natural key has neither problem.
7. **Live-path proration is implicit.** `_prorated_amount` prorates by elapsed
   seconds unconditionally, consulting no `ProrationPolicy`. `cadence.py::
   proration_factor` refuses to infer one. The two disagree.
8. **Four money precisions in one commercial stack.** `Numeric(10,2)` on
   `OfferPrice`/`OfferVersionPrice`/`AddOnPrice`, `Numeric(12,2)` on
   `Subscription.unit_price`, `Numeric(14,4)` on contract and obligation money,
   `Numeric(38,28)` on rating factors. A price captured at four decimals cannot
   round-trip through the catalogue.
9. **`offer_versions` has no uniqueness on `(offer_id, version_number)`.** Sub's
   model declares no `__table_args__`, and `git grep -n "offer_versions"
   27c76aaeebb7 -- alembic/ | grep -iE "unique|index"` returns nothing. Vendor CP
   has the constraint but no parent offer table, so `offer_code` is an
   unconstrained `String(120)` and a typo silently starts a new lineage.
10. **Saleability is expressed three overlapping ways.** `OfferVersion` carries
    `status: OfferStatus`, `is_active: bool` *and* nullable
    `effective_start`/`effective_end`; `OfferVersions.update` reconciles
    status/active only for `CatalogOffer`, and nothing enforces the interval.
    Vendor CP has none of the three: `git grep -niE
    'effective_from|effective_to|valid_from|tstzrange' 89848017d6b8` → zero.
11. **Vendor CP's immutability is a comment plus a unique index.**
    `alembic/versions/v002_offer_versions.py:58` grants `SELECT, INSERT, UPDATE,
    DELETE ON offer_versions TO platform_api` — the role the running API uses.
    No trigger, rule, `REVOKE UPDATE`, or ORM listener. `v004_contracts.py:29-30`
    does the same for `contracts`/`contract_lines`, i.e. for the frozen
    `unit_amount` and the `content_hash` approvals bind to. `updated_at` even
    carries `onupdate` on a table documented as write-once.
12. **Vendor CP's version numbers are caller-asserted.** `schemas.py:15` is
    `version: int = Field(ge=1)`, used verbatim. Nothing enforces contiguity or
    monotonicity; publishing v7 with no v1–v6 succeeds. No digest, no
    previous-version pointer, no delta record — a "publication delta" cannot be
    reconstructed from the rows.
13. **Vendor CP's idempotency has no fingerprint column.**
    `process_once_platform` keys on `command_id` alone and `PlatformInboxRecord`
    carries no payload hash. Replaying one `command_id` with a different
    `(offer_code, version)` skips the handler, and the unconditional
    `.scalar_one()` at `offers/service.py:135-140` raises `NoResultFound` — a
    500, not a typed conflict. Contradicts `AGENTS.md` rule 23.
14. **The strongest invariant in either repo is untested.** The `EXCLUDE USING
    gist` temporal non-overlap constraint is added by migration 430 and exercised
    by nothing: `__table_args__` is kept portable for the SQLite harness, and
    every contract test runs on SQLite.
15. **No isolation proof exists in either source.** Sub has no `tenant_id` and no
    RLS. Vendor's denial sweep in
    `tests/migration/test_vendor_migration_rehearsals.py` omits `offer_versions`,
    `contracts` and `contract_lines`; deleting the `REVOKE ALL … FROM app_user`
    line from `v002`/`v004` would leave CI green.
16. **No float money and no naive datetimes in the commercial *persistence* of
    any of the four repositories** — reported as a clean result, not an
    omission. No `Float`/`REAL`/`DOUBLE PRECISION` money column exists in Sub,
    Vendor CP, ERP or CRM; every `Float` match is latitude/longitude, accuracy,
    dBm, fibre loss, length, or a model confidence score.
17. **Float money does appear one layer above the column, in ERP and CRM.**
    `dotmac_crm/app/services/subscriber_reports.py:2023::_parse_balance_amount`
    parses to `Decimal` then discards the exactness — `return
    round(float(Decimal(cleaned)), 2)` — and is re-exported by
    `billing_risk_reports.py:33` for balances, MRR and deposits.
    `crm/web_leads.py:173,181` accumulates pipeline value as `total_value = 0.0`
    … `+= float(...)` while `crm/reports.py:870` computes the same headline in
    `Decimal`. `revenue_service_report.py:949,969` sums SLA credit exposure in
    float; `dotmac_erp/app/tasks/finance.py:1357` does the same for
    payment-provider totals. Not source paths — but the exact failure mode the
    shared owner must not reproduce.
18. **CRM recomputes a historical charge from a live price row.**
    `revenue_service_report.py::_row_from_extension_transaction` prefers the
    historical transaction price but falls back to `_fallback_service_price`
    (line 718), which calls `selfcare.fetch_customer_internet_services(...)` —
    the price in Sub *today* — then computes `credit = monthly_fee *
    Decimal(days) / Decimal(days_in_month)`. `build_downtime_log(db, year=…,
    month=…)` takes an explicit historical month, so a March credit is computed
    against today's plan price whenever the March row lacks a price. Nothing is
    written back, but the figures are presented as credit-note amounts an
    operator acts on. Same class as defect 3, in a second repository — which is
    why the captured-snapshot rule belongs in the shared contract.
19. **ERP mutates a price-list item in place, and its own test asserts that as
    the intended behaviour.**
    `tests/ifrs/inv/test_price_list_service.py:383::test_updates_existing_item_price`
    asserts `existing_item.unit_price == Decimal("125.00")` and `mock_db.add
    .assert_not_called()  # Should update existing, not add new`. The suite is
    `MagicMock`-backed, nothing asserts effective-date behaviour end to end, and
    nothing asserts price history survives.
20. **CRM recalculates the totals of an already-converted quote.**
    `crm/sales/service.py:420::_recalculate_quote_totals` recomputes from stored
    line amounts and the stored `quote.tax_rate` — correctly, no live price — but
    `CrmQuoteLineItems.create/update` carries no status guard, so editing a line
    on a quote already `accepted` and converted into a `SalesOrder` silently
    desynchronises the two. Not a price recompute; the reason offer and contract
    versions must be immutable from publication rather than by convention.

## Shared contract

Version one **owns**:

- a stable `Offer` identity distinct from its versions, keyed by a registered
  open `offer_code`;
- immutable, effective-dated `OfferVersion` rows. ADR-0030 §5a fixes what
  "immutable" has to mean structurally, because no source supplies it — all
  eight are BUILT:
  1. database refusal of `UPDATE` and `DELETE` for published versions;
  2. an immutable-row trigger or equivalent structural guard;
  3. module-assigned version numbers taken under a lock, contiguous, never
     caller-asserted;
  4. `previous_version_id`;
  5. a canonical content digest;
  6. same-key/same-fingerprint replay returning the original result;
  7. conflict on changed publication input for a reused key; and
  8. append-only publication history, with no meaningful `updated_at` on an
     immutable version;
- immutable `OfferVersionPrice` children carrying exact money, a registered
  charge model, and the cadence offered for that charge;
- saleability — whether a version may be *sold* now, on declared dimensions the
  consuming assembly supplies as data; withdrawal prevents a new contract and
  never changes an existing one;
- a stable `SubscriptionContract` with immutable, effective-dated
  `SubscriptionContractVersion` rows and stable per-line lineage keys across
  supersession;
- the `BillingCadence` value object and its calendar arithmetic;
- declared proration policy, coverage interval and deterministic factor;
- upgrades and downgrades *as version supersession*, with effective date and
  proration consequence both declared, never inferred;
- the renewal decision — whether the next period is contracted at all, and on
  what terms; and
- one unique `RecurringChargeOccurrence` per `(contract_line_key,
  contract_version_id, period_start, period_end, currency)`, with complete
  immutable pre-tax rating provenance and a replay fingerprint, published
  through the durable outbox.

It does **not** own: invoices, credit notes or any document; settlement,
payment, allocation, receivable, funding or account position; tax rates, tax
application, FX rates or conversion; collections, dunning, delinquency or any
consequence of non-payment; provider provisioning, connector I/O, credentials or
webhooks; technical hosting, domain, RADIUS, NAS or network state; customer,
subscriber, account or party identity; orders, quotes or the sales path; the
general ledger and statutory accounting.

### The reference rule that keeps the matrix coherent

A commercial offer **REFERENCES a service owner's technical specification
version by opaque reference**. Subscriptions owns price and saleability; the
service owner — `dotmac-hosting`, `dotmac-domains` — owns what the specification
technically delivers. The reference is stored, compared for equality and pinned
into the contract version's immutable snapshot. It is never parsed, never
interpreted, never joined. No import, no shared table, no FK across owners.

This is the one thing both sources get wrong. Sub puts the technical
specification *in* the offer, as FKs to `region_zones`, `usage_allowances`,
`sla_profiles`, `policy_sets` plus `service_type`/`access_type`. Vendor CP puts
it in as a `capability_codes` JSON list. Under the rule both become an opaque
`(spec_owner, spec_id, spec_version)` triple, and the ISP or capability
semantics live in a link table in the *consuming assembly's* lineage — which is
also what makes one module installable by Sub, Vendor CP and Cloud without any
of them seeing the others' vocabulary.

## Kernel floor

Consumed capabilities, named now so the floor can later be proven both
sufficient and necessary:

| Capability | Why this owner needs it |
|---|---|
| `dotmac_kernel.db` transaction authority (rule 8) | contract version, lines and the staged occurrence commit together; the module flushes, the assembly commits |
| `dotmac_kernel.money` | exact `Money`/`Currency`; `Money.of` already refuses float. Every price, unit price and pre-tax amount |
| `dotmac_kernel.idempotency` (rule 23) | one owner for at-most-once; `execute_once`/`execute_once_platform` with the fingerprint in its own column — the fix for defect 13 |
| `dotmac_kernel.messaging` outbox | `enqueue_event`/`enqueue_platform_event` for the recurring charge occurrence; the assembly's relay translates it into Billing's accepted-obligation input |
| `dotmac_kernel.durable_timers` | **does not exist** — confirmed absent from `packages/dotmac-kernel/src/dotmac_kernel/`. Cadence wake-ups block on it. Sub's `DurableTimer` (14 caller files) is its shape; subscriptions must not ship a second scheduler ledger |
| `dotmac_kernel.audit` | offer publication, contract supersession, upgrade/downgrade and cancellation are declared audited actions |
| `dotmac_kernel.settings_resolver` | declared defaults only — contract timezone, default end-of-month rule. Never a price, never a currency |
| `dotmac_kernel.planes` (rule 27, ADR-0023) | tenant + platform, DECLARED. Tenant tables `tenant_id NOT NULL` with FORCEd RLS; platform tables no tenant column, REVOKEd from `app_user` across every table and column privilege. No FK across planes, no nullable `tenant_id`, no sentinel tenant, no polymorphic scope column |
| `dotmac_kernel.namespaces` + migrations (rule 14) | one `mod_<code>` schema and one lineage, allocated in the same change that creates the stateful package |
| ADR-0008 declaration registries | charge model, occurrence source, saleability dimension, proration policy, accounting treatment |

Not consumed, deliberately: no tax engine, no FX provider, no numbering
allocation (Billing chooses the series), no document rendering, no file storage.

## Fresh proof required

Numbered because none of it exists in any source.

1. Tenant-plane RLS: a contract, version, line, price and occurrence created
   under tenant A are invisible and unwritable from tenant B.
2. Platform plane: every platform table REVOKEd from `app_user` across all table
   *and column* privileges and reachable by the online platform role, with the
   sweep enumerating tables from the manifest so a deleted `REVOKE` cannot leave
   CI green.
3. Both planes run the same behaviour suite and produce identical cadence,
   proration and occurrence output for otherwise identical input.
4. PostgreSQL temporal non-overlap: two effective contract versions cannot
   overlap; the `EXCLUDE` constraint is exercised, not merely created.
5. Concurrency: two simultaneous recurrence runs over the same contract and
   period produce exactly one occurrence and no unhandled integrity error.
6. Concurrency: two simultaneous supersessions produce one effective version and
   one typed conflict.
7. Rollback: an occurrence staged inside a consuming transaction that rolls back
   leaves no occurrence, no outbox row and no consumed idempotency key.
8. Idempotent replay: the same period replays to the same occurrence; the same
   key with a *different* fingerprint conflicts rather than returning a stale
   result (defect 13).
9. Repair from the contract: a run missed for N periods is reconstructed from
   the contract versions alone — no worker cursor, no scheduler state —
   producing exactly the occurrences owed and no duplicates (defect 5).
10. Immutability under the operator path: no API, admin surface, script or
    migration can rewrite a published offer version, a published price, a
    superseded contract version, or an occurrence's rating provenance. Proven at
    the database, not only in Python: the runtime role must not hold
    `UPDATE`/`DELETE` on the append-only tables.
11. Price snapshot: changing a catalogue price after a contract exists changes
    no existing contract version, no scheduled occurrence, and no replayed
    historical charge.
12. Out-of-order and lost delivery: the assembly receiving occurrence N+1 before
    N, and losing N entirely, converges through reconciliation without a second
    charge.
13. Drift: a reconciler rebuilds the expected occurrence set from contract
    versions, reports the difference against what was emitted, and repairs it
    idempotently.
14. Cadence boundaries ported wholesale from `tests/test_billing_cadence.py`,
    plus DST transitions in a non-UTC contract timezone, which that suite
    touches only indirectly.
15. Fail-closed: a missing price, missing cadence, unknown timezone, undeclared
    charge model, ambiguous active price, or unresolvable specification
    reference raises a typed error. No `monthly`, no `NGN`, no `Africa/Lagos`,
    no "newest active row".
16. Sensitivity: each guard fails when its invariant is deliberately broken
    (ADR-0018). A check over an empty set passes for the wrong reason.

## Adoption and retirement

**Vendor CP is the first adopter, on the platform plane.** Smallest surface —
one router, zero external callers of the offer service, four tests — so the
cutover is a real authority migration rather than a rewrite. Migrate
`offer_versions` into the module's platform plane behind a real parent offer
identity, repoint `contracts/service.py`'s two direct `OfferVersion` queries at
the module's published read surface, and retire `src/vendor_cp/offers/` in the
same change. Adding an import-linter contract to that repository is part of the
cutover, not a follow-up.

**Sub follows on the tenant plane, one slice at a time**, in an order forced by
the defects:

1. `BillingCadence` — pure behaviour, no rows, `test_billing_cadence.py` ports
   unchanged. Delete `_compute_next_billing_at` and the three duplicate
   `_add_months` helpers as each caller moves.
2. Offer and price publication, because defect 1 is the one that corrupts
   evidence. Sub's ISP semantics move to a Sub-owned link table keyed by the
   opaque specification reference.
3. Contract versions and lines, replacing `Subscription.billing_cycle`,
   `unit_price`, `contract_term` and the eleven writers of `next_billing_at`.
   The contract version becomes the single cadence authority; the cursor
   disappears rather than acquiring a twelfth writer.
4. The recurring charge occurrence, replacing `run_invoice_cycle`'s base line
   generation and the prepaid renewal charge resolution. `billing_runner` leaves
   `billing_scheduled_sweep_baseline.txt` in the same change that lands the
   durable-timer wake-up — the baseline is two-directional, so the line must be
   deleted deliberately.

Sequence-blocked: step 4 cannot land before `dotmac_kernel.durable_timers`
exists, and the occurrence's output contract cannot freeze before Billing's
accepted-obligation input does (ADR-0030 build-order step 7). Sub's Phase-2 shadow machinery
(`billing/shadow_verification.py`, 3644 lines, and
`tests/test_billing_phase2_shadow.py`) is the measurement instrument for every
slice — used, not ported.

**ERP and CRM adopt nothing and retire on their own repositories' schedules.**
CRM's `push_sales_order_subscription_to_selfcare` must stop sending
`unit_price`: a quote line references an offer version and the price comes from
that version. That is a precondition of Sub's step 2, because a catalogue whose
price can be overridden by an inbound API call is not a catalogue. ERP's
`inv.price_list` is a retiring authority; because `resolve_price` has zero
production callers the retirement is mostly deletion, but the live `inv_web.py`
writer and its templates are real and untested, so it needs its own shadow phase
and its own decision record in ERP. `dotmac-subscriptions` must not be reported
as ERP's replacement — ADR-0030 §7 gives ERP none of the seven.

The package is not adopted until one application runs the exact released version
and its displaced local writer is deleted. A green test suite is not a cutover,
and neither is a reference-assembly migration.
