# Commercial retirement ledger

**As of:** 2026-08-14, **re-measured 2026-08-28** (§ 0.1)
**Starter:** `49f9ccf` (`origin/main`); the four teams' documents read from the
uncommitted working tree at `b55c9a5`
**Sub:** `origin/dev` `27c76aaee` · **ERP:** `origin/main` `4df1190d` ·
**Vendor CP:** `origin/main` `63acff1`
**Re-measurement coordinates (2026-08-28):** Starter `origin/main` `c072e1f5` ·
Sub `origin/dev` `ad3b32152` · Vendor CP `origin/main` `94c0b73`
**Gate:** ADR-0017 P11 is **MET** since 2026-08-17 — see
[`p11-adoption-status.md`](p11-adoption-status.md), which records Michael's
acceptance on Vendor CP production evidence. **The rows below may begin**, in
the order [ADR-0030's 2026-08-28 amendment](../adr/0030-cloud-commerce-is-composed-from-complete-domain-owners.md)
sets, which is assigned PER MODULE and not per programme. This ledger exists so the work is countable before it is authorised, per
ADR-0017 decision 5: *measure, freeze, then improve.*

> **The gate line above said UNMET until 2026-08-28, fourteen days after it
> stopped being true.** `p11-adoption-status.md` is guarded by
> `tests/architecture/test_p11_production_lineage_evidence.py`; this ledger is
> guarded by no test at all, which is exactly how it drifted. Treat every
> coordinate below as measured-then, not true-now, until § 0.1 says otherwise.

**Sources consolidated:** `billing-extraction-dossier.md` § 3 (R1-R9),
`collections-sources.md` § 7 (R1-R10),
`subscriptions-extraction-dossier.md` § 5 (V1-V5, S2-S8),
`document-rendering-sources.md` § 7.1 (R1-R13), plus each module's
`local_copy_retirement` TOML field and the three focused adoption plans.

An inventory is not a mandate. Facts go stale; every row carries the coordinate
its owning team measured, and a row that disagrees with the code is wrong.

---

## 0. The rule this ledger enforces

> **A slice is done when the local writer is DELETED, not when the module can
> also do the work.**

Billing's dossier states it and gives the reason: *"two writers of an allocation
is the failure the whole programme exists to remove."* ADR-0006's extraction
rule says the same from the other end — *"an extraction is not complete until
the source product retires its local owner, or the result is a third
implementation rather than a shared one"* — and ADR-0017 decision 1 makes it the
measure: adoption is *"contracts consumed in place of a product's own writer,
not installations counted."*

Two corollaries this ledger applies without exception:

1. **A feature flag leaving both writers enabled is not a cutover.** The
   subscriptions dossier's ratchet contract: *"Never leave both writers enabled
   behind a runtime flag."*
2. **Retaining old tables for retention does not retain their authority**
   (collections). A read-only archive table is not a writer; a table with a live
   writer is.

---

## 0.1 Re-measurement, 2026-08-28

Measured against Sub `origin/dev` `ad3b32152`. **No named writer has
disappeared or been renamed**, so every row below still describes real code.
What has changed is size and position, and three writers turn out never to have
been in any inventory at all.

### Coordinates that drifted

| Row | Ledger says | Measured 2026-08-28 |
|---|---|---|
| BIL-R4 | `payment_reconciliation.py` 811 L | **1300 L** (+60 %) |
| BIL-R4 | `PaymentUpdate.amount` at `app/schemas/billing.py:715` | **`:788`** — `:715` is now a different class. The field, and the hazard, still exist |
| BIL-R4 | `billing/payments.py` 6 731 L | 6 814 L |
| BIL-R5 | `opening_balance_history.py` 343 L | **570 L** (+66 %) |
| BIL-R5 | `billing/ledger.py` 342 L | 374 L |
| BIL-R6 | `topup_intents.py` 1 509 L | **1 919 L** (+27 %) |
| BIL-R7 | `billing/reporting.py` 1 559 L | **2 068 L** (+33 %) |
| BIL-R7 | `web_subscriber_details.py` `current_balance` `:385`, `float()` `:502` | unchanged, byte-exact |
| SUB-S5a | `BillingObligation` has **44** columns | **51** mapped columns. The 18 `rating_*` provenance columns pre-date 2026-08-14, so 44 was wrong when written, not drifted |
| SUB-S5b | dedupe string built at `billing_automation.py:1777` | `:1759-1760` |
| SUB-S6b | `account_lifecycle.py:1394`, `:1423` | `:194`/`:209` and `:1563`/`:1592` |
| SUB-S7b | `billing_profile.py` 359 L | **404 L** — it grew; there is no retirement progress |
| COL-R5 | credential writes `_core.py:918-921`, `:1475-1476` | `:939-940`, `:1495-1496` |

**Consequence:** any ratchet baseline copied from a line count above is stale.
Re-derive it from the code at the slice that installs it.

### Writers in no inventory at all

Each would be missed by a ratchet built from the files this ledger names.

| Writer | Belongs to | Why it matters |
|---|---|---|
| `app/services/gateway_topup_intents.py` (566 L) | **BIL-R6** | A sixth prepaid-funding writer. BIL-R6 is already flagged as *"the slice most likely to be under-scoped"*; this is the evidence. |
| `app/services/cutover_balance_audit.py` | **BIL-R5** | A **fifth** `ledger_entries` writer — it constructs `LedgerEntry(...)` at `:752`. BIL-R5 names four. |
| `app/services/billing/cadence.py` | **SUB-S4** | A **tenth** cadence implementation — and it is the source `dotmac_subscriptions/cadence.py` was ported from. See § 0.2. |

### One drift note is now falsified

§ 3 states, as a drift source the collections shadow must account for, that
*"Sub's postpaid run commits once for the whole run with no per-account error
isolation."* **That is no longer true.** Sub PR #2458 (`c017d44e6`, merged
2026-08-17 — three days after this ledger's measurement) rewrote
`DunningWorkflow.run` to stage each account through
`_run_dunning_account`/`DunningAccountRunCommand`, committing or rolling back
**per account**, with explicit `errors` counting and
`_record_dunning_account_failure` evidence on failure. The shadow no longer has
to model this divergence.

## 0.2 SUB-S4 is ten implementations, not nine

`app/services/billing/cadence.py` (`BillingCadence`, ADR-0007 Phase 1,
2026-07-27) is a tenth cadence owner that SUB-S4 does not name — and it is the
one the module came from. `dotmac_subscriptions/cadence.py`'s own docstring
says: *"The source is `dotmac_sub:app/services/billing/cadence.py` at
`27c76aaeebb7`"* — which is this ledger's own Sub baseline commit. The calendar
math is byte-identical apart from enum vocabulary and imports.

So the module already *is* Sub's code, round-tripped, and **the nine legacy
owners were never switched to either copy**. All nine, and all five secondary
consumers SUB-S4 names, are still live on `origin/dev`.

Two things the row does not say, and that gate it:

1. **Legacy `BillingCycle` (`app/models/catalog.py:46`) is flat** —
   `daily/weekly/monthly/quarterly/annual` — carrying no interval count,
   alignment, end-of-month rule, proration policy or timezone. `BillingCadence`
   requires all of them, so a mapping layer must synthesize a default for every
   existing subscription and offer row. **A wrong `EndOfMonthRule` default
   silently moves the billing date of every subscription anchored on the
   29th–31st**, which makes the default an owner decision, not an
   implementation detail.
2. **Nothing proves the nine agree with each other today.**
   `test_billing_cadence.py` and `test_subscription_billing_cadence.py` exercise
   the new and legacy paths *separately*, never against one another. A parity
   harness is owed before any of the ten is deleted.

### The mapping, ruled 2026-08-28

Legacy contracts map to **`clamp_to_month_end`, `Africa/Lagos`,
`CadenceAlignment.contract_anniversary`, `ProrationPolicy.none`**, with the
flat cycle expanded by interval count (`quarterly → (month, 3)`).

This is not a new invention — it is already the checked-in Sub shadow
convention, at `app/services/billing/contracts.py:398-415`
(`_CYCLE_INTERVAL` plus the `BillingCadence(...)` construction), and both legacy
`_add_months` implementations already clamp an overflowing day.

**These are adapter-supplied MIGRATION FACTS, not defaults in the shared
module.** `dotmac-subscriptions` must keep requiring every field explicitly; a
default in the module would silently answer the question for every future
adopter that has a different one. New contracts still declare all four.

### The anchor nuance — and the cohort it decides

**Derive the anchor from the original contract/subscription start, never from
the mutable `next_billing_at` cursor.** They are not equivalent, and the
difference is systematic:

- Legacy iterative clamping walks the cursor forward and **loses the day**:
  Jan 31 → Feb 28 → **Mar 28**.
- The target cadence anchors on the contract start and **recovers it**:
  Jan 31 → Feb 28 → **Mar 31**.

So every subscription anchored on the 29th–31st that has passed through a short
month has a cursor that disagrees with its own contract. Feeding the cursor in
as the anchor would preserve the drift and hide it inside a "no change"
result.

**Shadow every 29th–31st cohort and classify the difference explicitly.** Do not
silently preserve it, and do not silently correct it — the choice is a customer
billing-date change either way, and it needs to be a recorded decision with a
counted cohort, not a side effect of which input the adapter happened to read.

## 1. How to read a row

| Column | Meaning |
|---|---|
| **ID** | This ledger's id, `<module>-<team row>`. Team ids are preserved so a row can be traced back. |
| **Writer** | Repo and exact path as the owning team measured it. |
| **Replaced by** | The named module, or `nothing` where the answer is deletion. |
| **Shadow** | The comparison that must pass **before** the delete. |
| **Ratchet** | The ADR-0018 two-directional counter that proves the retirement, and its baseline artifact if one is named. |
| **State** | `not-started` for every row. There is no other value today. |
| **⚑** | Contested — another team claims the same writer. See § 6. |

**Every ratchet below must be two-directional** (fails on a rise *and* on a fall
not recorded by lowering the baseline in the same change) **and must carry a
sensitivity proof** (a planted violation the detector is shown to catch).
ADR-0018 decision 5: *"A newly-covered region that passes must be shown to FAIL
without its ratchet. Otherwise a clean run is indistinguishable from the guard
having stopped looking."* § 7 audits which ones actually do.

---

## 2. `dotmac_sub` — billing

Nine rows. Sub is the qualifying product-first source; Sub is cutover **2** for
billing (Vendor CP is 1). No baseline artifact is named for any billing row —
see § 7.1.

| ID | Writer (`dotmac_sub:`) | Replaced by | Shadow | Ratchet | ⚑ |
|---|---|---|---|---|---|
| **BIL-R1** | `app/services/billing/invoices.py` (2 987 L, `next_invoice_number` at `:303`); `billing/credit_notes.py` (2 452); `invoice_draft_authoring.py` (870); `advance_renewal_invoicing.py` (480); `invoice_discounts.py` (530) | module invoice/credit-note lifecycle + a bound **P4 `NumberingProvider`** | coverage-vs-`InvoiceStatus` disagreement enumeration; coverage parity matrix per coverage-writing path; advance/arrears same-owner traversal | call sites constructing/transitioning an `Invoice`/`CreditNote` outside the module adapter, **plus** `next_invoice_number` call sites (5 in production: `invoices.py:706,1519,2123`; `billing_automation.py:1902,2277`; +2 in `crm_api.py`) → zero before `invoices.py` is deleted | ⚑ |
| **BIL-R2** | `app/services/billing/obligations.py` (728) + the recurring-run dedupe path in `billing_automation.py` (2 610); table `billing_obligations` | module obligation acceptance under C10 uniqueness + `AcceptRatedObligationV1` idempotency | recurring-run replay counting C10 collisions the `subscription_id` dedupe missed | writes to `billing_obligations` outside the module adapter. *(`uq_billing_obligation_natural_identity` already exists at `models/billing_contract.py:501` — the ratchet is on the WRITER, not the constraint)* | **⚑⚑** |
| **BIL-R3** | `app/services/billing/rating.py` (445); `billing/tax.py` (117); invoice-side decisions in `tax_accounting.py` (1 189); `customer_tax_policies.py` (264) | module rating + `TaxProvider` seam | line net/tax/gross parity | sites computing a line net/tax/gross outside the module. **Statutory tax recognition stays ERP's and is out of this ratchet** | ⚑ |
| **BIL-R4** | `app/services/billing/payments.py` (6 731); `provider_payment_settlements.py` (437); `financial_import_batch_reversals.py` (727); `payment_reconciliation.py` (811); **`app/schemas/billing.py:715`** (`amount` on `PaymentUpdate`); tables `payment_allocations`, `payment_settlements`, `payment_refunds`, `payment_reversals` | `AcceptSettlementV1` + immutable posting groups; **structural absence** of any money-field update command | enumeration of settled payments whose `amount` differs from settlement evidence, and every `PaymentStatus.pending` payment that produced a never-reversed ledger credit — **"a cutover blocker requiring quarantine and a Finance decision"** | writes to the four tables outside the module, **plus a schema-level assertion** that no update contract exposes a money field on a settled fact | |
| **BIL-R5** | `app/services/billing/customer_subledger.py` (614); `subledger_opening.py` (642); `billing/ledger.py` (342); `customer_financial_position.py` (368); `customer_financial_ledger.py` (1 088); `opening_balance_history.py` (343); tables `customer_posting_groups`, `customer_position_effects`, `ledger_entries` | module posting groups + derived per-currency positions (`ReceivablePositionV1`) | exact per-account/per-currency position **rebuild hash**; three consecutive complete reconciliations with **zero** unclassified drift and **no money tolerance** | writes to the three tables outside the module, **plus a "no balance formula" detector** — any site computing a position by summing rows rather than reading the derived projection | |
| **BIL-R6** | `app/services/billing/account_credit.py` (1 710); `billing/adjustments.py` (1 233); `account_credit_deposits.py` (993); `quote_deposits.py` (1 066); `topup_intents.py` (1 509) | module credit + funding effects | per-account credit/funding delta table | sites creating available credit or prepaid funding outside the module. **"the slice most likely to be under-scoped"** — prepaid top-up is spread across five services | |
| **BIL-R7** | `app/services/web_subscriber_details.py` (1 015; `current_balance` at `:385`, `float()` at `:502`); `billing/reporting.py` (**2 068**) | three separate `Money` values on `ReceivablePositionV1`, **and no fourth combined field**. **The combined balance is DELETED, not replaced** — ruling 2026-08-28, § 2.1 | per-account `current_balance` delta table: changes at all / changes sign / multi-currency accounts | sites that add, subtract or combine two of {receivable, credit, prepaid funding}, and sites casting money to `float` → zero | |
| **BIL-R8** | `app/services/payment_provider_events.py` (1 335); `payment_webhook_commands.py` (654); `api_billing_webhooks.py` (244); `integrations/connectors/payment_gateway.py` (403); `payment_gateway_adapter.py` (354); `autopay.py` (467) | **the INTEGRATOR, not billing** (ADR-0024 § 6, ADR-0020 A3) | n/a — different programme | counts provider clients, credentials, signature verifiers and webhook routes in Sub's runtime. **This is the Integrator adoption's ratchet**; listed only so a billing cutover does not absorb it. The fleet-wide form already exists: `docs/inventories/external-connector-baseline.json` records `dotmac_sub` at `http_client 37, connector_task 18, webhook_surface 4, delivery_retry 8, sync_checkpoint 8, provider_credential 2` | |
| **BIL-R9** | `app/models/billing.py:856` (`InvoicePdfExport`, table `invoice_pdf_exports`) + `:71` (`InvoicePdfExportStatus`); `billing_invoice_pdf.py` (1 434); `invoice_bank_details.py` | **splits three ways**: relation → billing `document_artifacts`/`platform_document_artifacts`; rendering → the documents workstream; **job-state → NOTHING** (ADR-0014 forbids a second at-most-once mechanism) | see DOC rows | writers of `invoice_pdf_exports` and callers of `billing_invoice_pdf` → zero, and **no replacement job table on any side** | **⚑⚑** |

---

## 2.1 BIL-R7 ruling, 2026-08-28 — the combined balance is deleted

**BIL-R7 is not blocked.** An earlier reading treated the module's refusal to
carry a fourth combined field as a missing capability. It is the opposite: the
refusal is the decision, and it is already enforced —
`tests/architecture/test_billing_module.py` asserts

```python
assert not position_fields & {"balance", "current_balance", "funding_available"}
```

so `balance` and `current_balance` cannot enter `ReceivablePositionV1` by
construction. Adding a module symbol to restore them would be defeating a guard,
not satisfying a requirement.

**The formula being retired is wrong, not merely unowned.**
`web_subscriber_details.py:385` computes

```python
current_balance = balance_due + available_credit
```

which **adds positive credit to debt**. `collectible_receivable` already
reflects actual allocations. Subtracting unused credit instead would describe a
hypothetical allocation; adding it is simply incorrect.

**The portal shows three labelled lanes and no total:**

| Label | Source |
|---|---|
| Amount due | `collectible_receivable` |
| Available credit | `available_credit` |
| Prepaid funds | `prepaid_funding`, where relevant |

**If the product later needs "amount payable after applying eligible credit"**,
that is a Billing-owned **allocation decision or quote** — a real decision with
an owner, evidence and a transaction — never another arithmetic field on a
projection.

---

## 3. `dotmac_sub` — collections

Ten rows. Sub is cutover **1** for collections — the inverse of billing's order,
and deliberately so. Two live owners plus a complete tested-but-unwired ADR-0007
Phase 5 shadow implementation.

| ID | Writer (`dotmac_sub:`) | Replaced by | Shadow | Ratchet | ⚑ |
|---|---|---|---|---|---|
| **COL-R1** | `dunning_runner` (`services/scheduler_config.py:713-723`) + `DunningWorkflow` (`services/collections/_core.py:2522-2821`) | policy-driven cases + per-entity timers | per subject/reason/currency/source version: case existence, pinned policy, current/next step, exact next-action time, requested consequence + idempotency identity, close/reopen after settlement — **over one complete production ladder window per active policy** | name removed from **`tests/architecture/billing_scheduled_sweep_baseline.txt`** in the same change | |
| **COL-R2** | `prepaid_balance_sweep` (`scheduler_config.py:733-745`, `collections/prepaid_balance_sweep.py`, 758 L) + `prepaid_enforcement_planner.py` (725 L) | the same single lifecycle, driven by `advance`-timing policy data | additionally: every typed skip/shield reason, funded-restore outcomes, budget-deferred accounts, the full candidate cohort per cycle | same baseline file | |
| **COL-R3** | `PrepaidSweepCycleState` (`models/collections.py:267-293`) | **nothing** — a timer has no cycle, so the cursor has no successor | prove every account the cursor would have visited has a timer **or a typed no-timer reason** | table drop once the row count reaches zero and stays there; its docstring already marks it `TRANSITIONAL` | |
| **COL-R4** | `Subscriber.prepaid_low_balance_at`, `Subscriber.prepaid_deactivation_at`, `services/prepaid_enforcement_state.py` (111 L; writes `:59-72`, `:75-88`, `:99-100`) | one `DurableTimer` per `(owner, entity, purpose)` with a generation — **P3, which has no owner** | for every account with a non-null column, exactly one module timer with the same due instant; clearing the column ⇔ cancelling the timer | two-directional count over non-null occurrences of both columns | |
| **COL-R5 — RETIRED 2026-08-28** | ~~direct credential writes at `_core.py:918-921` (throttle) and `:1475-1476` (un-throttle)~~ (measured `:939-940`, `:1495-1496`); columns `credential.pre_throttle_radius_profile_id`, `credential.radius_profile_id` | a typed consequence request, split across **two** owners — see § 3.1 | the ported S1 invariants in `tests/test_access_enforcement_strays.py` assert remember/restore/re-throttle against the new owner; the 409 refusal wording is preserved unchanged | **ratchet scope corrected** — see § 3.1. `tests/architecture/test_collections_credential_writer_ratchet.py` + `collections_credential_writer_baseline.txt` | |
| **COL-R6** | `Invoices.mark_overdue_system(...)` called from the dunning scan (`_core.py:2562-2567`); `apply_prepaid_overlap_hold` (`_core.py:2554`) | **billing** owns invoice lifecycle; collections reads a position and writes nothing | prove no invoice changes status as a side effect of a collections run, and that overdue-ness is derived from the position | collections-module writes to `Invoice.*` | ⚑ |
| **COL-R7 — RETIRED 2026-08-28** | ~~`_throttle_account`/`_restore_throttle`; `DunningWorkflow.resolve_cases_for_account`; `"enforcement_health_blocked"`~~ — all four **deleted** | **nothing — dead code**, confirmed: zero production callers under `app/`, only tests that existed to exercise them | zero callers confirmed at the retirement commit, twice and independently | its OWN ratchet, not folded into COL-R5's: `test_retired_dead_symbols_have_no_references`, with a sensitivity proof | |
| **COL-R8** | tables/writers `dunning_cases`, `dunning_action_logs`, shadow `collections_cases` writers, `policy_dunning_steps`; the reading of mutable `PolicySet` fields (`models/catalog.py:368-403`, steps `:406-419`) | module-owned policy **versions**, cases and append-only step attempts | full-cohort parity per R1/R2 **plus a total row-disposition classifier with no default bucket** | imports of the local models outside an archive reader | |
| **COL-R9** | notice literals + subject-string dedupe (`_core.py:1780-1883`; dedupe SQL `:1852-1869`) | policy-declared `template_id` + `channel_preference` through `dotmac_kernel.channel_policy`, consent via `dotmac_kernel.consent`, derived idempotency key | per cohort notice: same recipient set, same channel decision, same suppression outcome, and a dedupe decision **that no longer depends on a subject string** | string literals used as notification subjects in the collections tree | |
| **COL-R10** | the shadow stack itself — `collections/lifecycle.py` (397 L), `collections/postpaid_policy.py` (61 L), `collections/prepaid_policy.py` (116 L), `collections_cases` writers | the module's single engine | its tests are ported and pass against the module | delete at cutover; **a product-local copy of the extracted engine is itself a ratchet entry** | ⚑ |

Also named in `local_copy_retirement` / plan S7 but without their own row:
`collections/mode_policies.py` (64 L); local payment-arrangement lifecycle and
account-wide shield queries; local grace decision copies and duplicate
timer/notice fields; direct financial-access, RADIUS, notification and service
writes from collections code.

**Retained deliberately:** Sub keeps product adapters only — local identity and
authorization, policy assignment, the communication adapter, and the consequence
executor. **`account_lifecycle` remains the SOLE writer of
`Subscription.status` / `.access_state`.**

**Two named drift sources the shadow must account for:** (a) Sub's postpaid run
commits **once for the whole run** with no per-account error isolation, so a
shadow and a live run can disagree merely because the live run aborted — every
comparison records whether the live run completed; (b) fleet-wide
`billing_health_reasons` still enters the live preview fingerprint at
`_core.py:714 → :741`, so a fleet-scoped observation invalidates fingerprints
for reasons outside the entity, and shadow fingerprints deliberately will not
move with it.

---

## 3.1 COL-R5 and COL-R7, as retired — and two corrections they forced

### The revision this claim is made against

| | |
|---|---|
| **Sub revision carrying the work** | `f7368650e7f392661ca28fa5e07d90894e819735` |
| Branch | `feat/collections-credential-consequence-owner` |
| Parent (`origin/dev` at rebase) | `0c6bf29ab54e42e37375fe5e709f7cc55a2e195e` |
| Merged? | **No — committed, not yet merged.** |
| CI verdict | **Pending.** Static gates pass locally; that is not test evidence. |

**Do not cite `ad3b32152` for these two rows.** That is the coordinate the
§ 0.1 re-measurement was taken at, and it *precedes* the work — it is where the
writers still existed. A retirement claim has to name a revision that actually
contains the retirement, or it is a claim about a tree nobody can inspect.

The rows are marked retired **on this branch**. They become a fleet fact when
the revision merges and CI is green; until then this table is the honest
statement of what has and has not happened.

Both corrections below are to THIS ledger, discovered by trying to execute its
rows.

### The consequence has two owners, not one

COL-R5 named `services/account_lifecycle.py` as the consequence owner. Measured,
that file contained **zero** occurrences of either credential column — it was
the planned owner, not a current one, so the row as written meant building the
capability from nothing. Meanwhile `app/services/radius_access_state.py` already
held `stage_subscription_radius_profile()`: a typed, validated, single-credential
profile writer whose docstring already stated the ownership boundary.

ADR-0030's own collections design resolves it, and both layers are real:
*"A collections request is not permission or a service-state write. The service
owner revalidates and permits/refuses/applies the transition."*

| Layer | Owner | What it does |
|---|---|---|
| Requests | `collections/_core.py` | Builds the preview and submits a typed `CredentialThrottleCommand` / `CredentialRestoreCommand`. Writes nothing. |
| **Decides** | `account_lifecycle` | Takes the row locks, revalidates against the state the preview promised, permits or refuses (`CredentialConsequenceRefused`). |
| **Applies** | `radius_access_state` | Writes the two profile columns, including the remember/restore anchor. |

Collections keeps its *reads* of both columns — the preview is a decision input,
and a read is not a write. The ratchet counts assignments only, and has a test
proving it does not count references.

The declarations live in Sub's `app/services/collections_authority.py`, and the
Sub SOT registry entries `access.subscription_lifecycle` and
`access.radius_state` were updated to say which half each holds. The
`access.radius_state` note previously claimed it *"writes neither lifecycle rows
nor external RADIUS"*, which was already understating a credential writer it had
before this slice.

### The ratchet as written would have fired on day one

COL-R5's ratchet — *"assignments to `radius_profile_id`/
`pre_throttle_radius_profile_id` outside `account_lifecycle`"* — was measured
across Sub `origin/dev`: **eighteen assignment sites in eight modules**, of which
only eight were collections'. The other ten belong to
`access_credential_binding` (2), `catalog/subscriptions` (2), `radius` (2),
`enforcement` (1, fair-use-policy speed throttle — a different enforcement
domain entirely), `pppoe_credentials` (1), `radius_access_state` (1) and
`web_catalog_subscriptions` (1). None is a collections consequence; none is
migrating. And since `account_lifecycle` wrote neither column, the literal
predicate counted all ten as debt immediately.

**The corrected predicate names the writer by location, not the column across
the tree**: assignments inside `app/services/collections/**` → zero, with the
other seven files recorded in a two-directional shrink-only baseline that names
each one's real owner. That is the difference between *"not collections' debt"*
and *"nobody looked"*, which ADR-0018 § 23 requires a guard to be able to state.

### One extra fix, because COL-R1/R2 rest on it

§ 8.2 recorded that `billing_scheduled_sweep_baseline.txt`'s guard had no
sensitivity proof. Still true on 2026-08-28. That baseline carries
`dunning_runner` and `prepaid_balance_sweep` — COL-R1's and COL-R2's entire
retirement evidence — so a detector that had silently stopped scanning would
have been indistinguishable from a clean run at the exact moment those rows
started depending on it. `scheduled_financial_sweeps()` gained an explicit
source seam and a planted-sweep proof in this slice, ahead of the rows that
need it.

### What this slice is NOT

It moves an in-process writer and deletes dead code. **No table changes owner**,
so ADR-0031's sealed-evidence protocol does not apply here — there is nothing to
lock, digest or seal. BIL-R5 is the first row that needs the full protocol.

---

## 4. `dotmac_sub` — subscriptions

Cutover **2** (Vendor CP is 1). The largest single row count, and the one whose
baseline is most at risk of being built wrong — see SUB-S4.

| ID | Writer (`dotmac_sub:`) | Replaced by | Shadow | Ratchet | ⚑ |
|---|---|---|---|---|---|
| **SUB-S2a** | `app/services/catalog/offers.py` — `Offers.create/update`, `OfferVersions.create/update/delete`, `OfferVersionPrices.create/update/delete` (**11 `db.commit()` at `:181,292,393,452,479,536,603,630,680,751,779`; 9 `HTTPException` at `:203,307,333,498,579,586,650,726,733`**) | module generic offer/version/price commands; ISP link writers stay in Sub | full cadence matrix + effective-at/supersession over the complete active cohort | generic-catalogue write call sites → zero. **ISP link writers stay and are excluded by name, not by directory** | |
| **SUB-S2b** | `catalog_billing_governance.py::assert_offer_version_update_safe` / `assert_offer_version_price_update_safe` | structurally unnecessary once versions are immutable | — | call sites → zero **and** the module's immutability canary green | |
| **SUB-S2c** | `OfferPrice` / table `offer_prices` (the unversioned pre-versioning path) | versioned price rows | — | zero remaining readers | |
| **SUB-S3** | local `BillingContract`/`Version`/`Line` writers in `app/services/billing/contracts.py` | `RecordSubscriptionContractVersionCommand`, promoted through Sub's SOT registry | — | one writer per transition in the registry; the old owner's `MigrationContract` moves past `SHADOWING` | |
| **SUB-S4** | **NINE parallel cadence owners**: `billing_automation.py:272 _add_months`, `:288 _period_end`, `:2542` inline; `catalog/subscriptions.py:92 _add_months`, `:135 _compute_next_billing_at`, `:485 _billing_cycle_start`; `web_catalog_calculator.py:19 _cycle_bounds`; `payment_arrangements.py:132 _add_month_clamped`; `web_billing_overview.py:193 _add_months` | module `BillingCadence` value object | full cadence matrix: daily/weekly/monthly/N-month/yearly; month-end 29/30/31; leap years; strict-same-day refusal; three alignments; timezone/DST; four proration policies | **ratchet C**, over the nine names **and** their five secondary consumers (`customer_portal_flow_common.py:38-52`; `account_lifecycle.py:1394,:1423`; `subscription_billing_treatments.py:254`; `prepaid_recovery_billing.py:349`; `prepaid_service_renewals.py:706,:2125,:2286`) | **⚑ + § 7.4** |
| **SUB-S4b** | the plan-change `_calculate_proration` owner | declared proration policy | four proration policies in the matrix | call sites outside the module adapter → zero | |
| **SUB-S5a** | `BillingObligation`'s recurrence half — **split the 44 columns** (`models/billing_contract.py:481-703`) | `RecurringChargeOccurrence` (subscriptions) + billing receivable | the pair reconstructs the legacy fact with **no double charge and no lost resolution**; neither module FKs the other; the assembly stores an opaque correlation | category-2 forbidden-name guard (below) | **⚑⚑** |
| **SUB-S5b** | legacy invoice-line dedupe: `billing_automation.py:1782-1793,:1931-1938,:2252-2258,:1006-1031` + index **`uq_invoice_lines_active_billing_line_key`** (`models/billing.py:1086-1092`) | the module's real natural-identity constraint | duplicate/missing/overlapping/fingerprint-conflict counts zero | — *(the legacy guard keys on the line **description string**, built at `:1777` as `f"{offer_name} ({start} - {end})"`, so renaming an offer defeats it)* | ⚑ |
| **SUB-S6a** | **monthly-only prepaid engine** (`prepaid_service_renewals.py:2271`, `:2017`, `:427-431`, `:694-700`, the four `resolve_prepaid_monthly_*`) and the **postpaid engine** (`billing_automation.py:1420 run_invoice_cycle`, `:544`) | ONE engine + one `collection_timing` field | recurrence switched per named cohort against Sub's existing `tests/test_billing_phase2_shadow.py` evidence | **Ratchet D**: no module symbol named for exactly one timing mode, and the same scenario under `advance` and `arrears` traverses the same owner functions. **The fork point `advance_renewal_invoicing.py:313-338` is deleted, not flagged** | ⚑ |
| **SUB-S6b** | `Subscription.billing_mode`, `billing_cycle`, `next_billing_at`, `unit_price` — **lose authority, do not die** | a rebuildable projection with **one** writer recomputing from `service_period(cadence, contract_start, index)` | — | ratchet on **writers**, not readers: today three (`catalog/subscriptions.py:983`, `:1498`, `account_lifecycle.py:1394`/`:1423`), afterwards one. *(`next_billing_at` alone is read by **39 distinct modules** — a reader ratchet would never close)* | |
| **SUB-S6c** | `SalesOrderFundingObligation` (`models/sales_order_funding.py:85-116`) | **nothing — already a correct rebuildable projection** | — | none. **It is the template for every other projection row** | |
| **SUB-S7a** | `collections/postpaid_policy.py`, `collections/prepaid_policy.py` — **repoint**, the only two external readers of `BillingObligation`'s financial fields | `dotmac-collections` reading **billing's** receivable contract | — | — | **⚑ + § 8.3** |
| **SUB-S7b** | `app/services/billing_profile.py` — **entirely, 359 lines** | nothing | — | *"it exists only to detect the four-way cadence disagreement the contract version replaces. Its retirement is the clearest single proof the cutover worked"* | |
| **SUB-S8** | `catalog_offers`' **29 ISP columns** and `offer_versions`' **nine**, contracted into Sub-owned link tables | product link tables | upgrade rehearsal | fallback-read count zero, **then** contract | |

**The category-2 forbidden-name guard** (a build-failing name set, not a
counter): on the occurrence — `tax_amount`, `gross_amount`, `resolved_amount`,
`accounting_treatment`, `resolution_kind`, `opened_at`, `resolved_at`, `due_at`,
`reversed_by_id`, `rating_tax_treatment_code`, `rating_tax_rate_id`,
`rating_tax_rate_percent`, `rating_tax_inclusive`, and the
`open`/`partially_resolved`/`resolved`/`written_off` members of any state
vocabulary; on the contract — `tax_inclusive`, `accounting_treatment`.
`pre_tax_amount` (Sub's `net_amount`, `models/billing_contract.py:616`) must
**pass**. `rating_tax_rate_id` (`:651`) cannot survive in any form (FK to
`tax_rates`). `authority` (`:221`, `:312`, `:591`) is in **no** category and
retires with the migration. `ck_billing_obligation_rating_provenance_complete`
must be **re-derived from pre-tax inputs only, never copied**.

---

## 5. `dotmac_sub` — document rendering

Thirteen rows, all Sub, all sequenced **after** billing lands the issuance
snapshots and P4 numbering. *"Ordering is not optional… the fallbacks are
deleted LAST, after the shadow proves the engine path."*

| ID | Writer (`dotmac_sub:`) | Replaced by | Ratchet category | ⚑ |
|---|---|---|---|---|
| **DOC-R1** | `services/billing_invoice_pdf.py:222-464` `_render_invoice_html` | a versioned template artifact + the renderer port | `engine_call_sites` | ⚑ |
| **DOC-R2** | `billing_invoice_pdf.py:471-519`, `:589-932`, `:529-586` — `_build_simple_pdf`, `_build_branded_fallback_pdf`, `_render_invoice_text_lines` | **nothing — deleted, not ported** (the divergent fallbacks truncate at **7** and **30** line items) | `fallback_renderer_paths` | ⚑ |
| **DOC-R3** | `billing_invoice_pdf.py:193-219` + `services/sales/quote_documents.py:520-541` — duplicated `_ensure_weasyprint_pydyf_compat` shim | the renderer adapter, **once** | `engine_call_sites` | |
| **DOC-R4** | `billing_invoice_pdf.py:1296-1309` `_build_pdf_bytes` | `DocumentRenderer.render` | `engine_call_sites` | ⚑ |
| **DOC-R5** | `billing_invoice_pdf.py:1064-1114` — cache metrics read-modify-written into `domain_settings` | ordinary metrics *(a read-modify-write counter in a settings table is a lost-update race and a settings-as-data abuse — ADR-0011)* | `render_path_settings_reads` | |
| **DOC-R6** | `models/billing.py` `InvoicePdfExport` + table `invoice_pdf_exports` + `app/tasks/invoice_pdf.py`; `maybe_finalize_stalled_export`; `STALE_EXPORT_SECONDS = 20` | **`dotmac_kernel.idempotency` + the outbox** *(a status column is a second at-most-once mechanism — ADR-0014, hard rule 23 — and it already has the predicted stuck-`processing` failure)* | — | **⚑⚑** |
| **DOC-R7** | `billing_invoice_pdf.py:1237`, `:1241`, `:1331-1341` — local-disk and direct-S3 branches | **`dotmac-files` only** | `render_path_storage_writes` | |
| **DOC-R8** | `services/billing_payment_receipts.py:327-710` — receipt render + fallbacks | the renderer port; and the receipt becomes a **persisted** artifact | `engine_call_sites`, `fallback_renderer_paths` | |
| **DOC-R9** | `services/billing/payment_receipt_identity.py:15` — fabricated `f"#RCP-{payment_id.hex[:8].upper()}"` | **P4 numbering, invoked by billing at issuance** — not rendering's, and not a UUID prefix | `fabricated_document_numbers` | |
| **DOC-R10** | `billing_payment_receipts.py:127` — render-time `application_summary(db, payment)` (→ `payments.py:3865-3885`, `:3821-3828`, `_payment_prepaid_service_amount` `:3831-3852`) | an immutable **receipt fact** from billing *(four of five receipt figures are recomputed at print time today)* | `render_time_money_derivations` | ⚑ |
| **DOC-R11** | `billing_invoice_pdf.py:141-167` (logo), `:247-254` (seller), `:266`, `:865` (bank details) — live settings lookups | snapshots on the fact. **The bank-details case is a money-misdirection defect** | `render_path_settings_reads` | ⚑ |
| **DOC-R12** | `billing_invoice_pdf.py:49` (`NAIRA_SIGN = "₦"`), `billing_payment_receipts.py:206`, `templates/customer/billing/receipt.html:6`, `services/web_billing_documents.py:43` — four currency-symbol reimplementations | the ISO-code stopgap, then **P9** *(a C5 forbidden name)* | `currency_symbol_literals` | |
| **DOC-R13** | `billing_payment_receipts.py:234,245,246,344,374,463,660,675` — raw `strftime` with a literal `"UTC"` label (8 sites) | the document's declared timezone | — | |

**Explicitly NOT retired by this workstream** (and therefore in no inventory —
see § 6): `services/sales/quote_documents.py`; `app/web/admin/reports.py:1887-1924`
(the NCC regulatory pack); `services/web_billing_documents.py`;
`document_delivery.py`; `web_document_discount_report.py`.

**Shadow rule, quoted because it is unusual and correct:** *"Rendering is
compared on the presentation model and the extracted text, **never on PDF
bytes**… assert every line description, every formatted amount and the document
number appear **exactly once** and that the rendered line count equals the fact's
line count, **which is what catches the legacy fallback truncation at 7 and 30
items**. There is no money tolerance… two deliberately non-deterministic fake
renderers must FAIL those tests or the comparison proves nothing."*

---

## 6. `dotmac_vendor_control_plane`

Vendor CP is cutover **1** for billing and subscriptions, cutover **2**
(demand-gated) for collections.

| ID | Writer (`dotmac_vendor_control_plane:`) | Replaced by | Shadow | Ratchet | State |
|---|---|---|---|---|---|
| **SUB-V3a** | `src/vendor_cp/offers/models.py`, `service.py`, `router.py`, `schemas.py`, `catalog.py` **and the `offer_versions` table** — in an expand/contract release | module `PublishOfferVersionCommand` on the platform plane | reconciler reports **zero** missing/extra/price/currency/version/capability-link drift for the accepted window; a missing term is a **blocking NULL**, never a monthly/Africa-Lagos/default-currency guess; an unclassified active contract is a **stop condition** | **ratchet A** — `vendor_cp.offers` imports and direct `offer_versions` reads → zero, baseline lowered in the same change | not-started |
| **SUB-V3b** | `src/vendor_cp/contracts/service.py:537 _resolve_offer` — direct import of `vendor_cp.offers.models.OfferVersion` | the offer snapshot arrives from the assembly as a typed value | — | **ratchet B** — an import-linter contract forbidding `contracts` → `offers` | not-started |
| **SUB-V2** | `capability_codes` column on `offer_versions` | a **Vendor-owned platform link table** | included in V2 backfill parity | — | not-started |
| **BIL-V** | **none — Vendor CP has no local financial writer.** Verified: an exhaustive grep of `src/` and `tests/` for invoice, payment, receivable, settlement, credit-note, allocation and ledger returns no table, model, service or writer | — | substituted by the V1 preview, the same-currency scenario matrix, the plane privilege split, one real production flow, an exact per-account/per-currency position rebuild hash, and an idempotent ERP accounting-fact receipt | *"its obligation is to add one only through the module, never a second generic invoice facade"* | n/a |
| **COL-V** | **none.** At `8984801` there is no invoice, overdue, dunning, arrangement or consequence code at all (grep for `dunning`/`overdue` across `src/` → zero files) | — | — | **"Installing empty tables is not adoption."** Behind a four-condition demand gate | n/a |

**A conformance hazard Vendor CP already carries, measured:** `REVOKE ALL … FROM
app_user` exists at `alembic/versions/v002_offer_versions.py:60` and
`v004_contracts.py:31`, but
`tests/migration/test_vendor_migration_rehearsals.py::test_platform_role_access_and_tenant_role_denial`
**asserts denial only for `vendor_accounts` and ten licence tables**. The
subscriptions dossier's instruction is exact: *"The module must not inherit this
gap."* That is an ADR-0018 coverage narrowing — the guard's scope is smaller
than its name implies.

---

## 7. `dotmac_erp`

**ERP retires nothing to any of the four modules.** ADR-0020 A6: ERP installs
none of them.

| ID | Writer | Replaced by | Note |
|---|---|---|---|
| **ERP-COV** | `dotmac_erp:app/services/finance/coverage.py` | **the kernel coverage slice** (billing plan Stage B) | *"a separate and independently adoptable cutover"* — it is P1's coverage sub-slice, not a commercial-module row. ERP has already fixed its own `InvoiceStatus`/coverage conflation, which is why it is the port source rather than a retirement target. |

ERP's own three ratchets exist and are **unrelated to this programme**, but they
are the reference implementations the commercial ratchets should copy, so they
are recorded here with their measured counts:

| ERP ratchet | Artifact | Count today | Shape |
|---|---|---|---|
| RLS coverage | `docs/rls-coverage-baseline.json` + `.md`, enforced by `scripts/architecture/rls_coverage_audit.py --enforce --baseline …` | **224** `known_gaps`; 414 tables, 309 organization-scoped, 85 fully protected (27.5 %), 158 unprotected, 66 unforced | *"a ratchet, not a target… The list may only shrink."* |
| Unscoped sessions | `tests/architecture/rls_scope_baseline.txt`, enforced by `test_script_rls_scope.py` | **7** scripts | *"This list is a RATCHET: it may shrink, never grow."* Its origin is ADR-0018's motivating incident. |
| `balance_due` | `tests/architecture/test_balance_due_is_not_rewritten.py` | **9** hand-written subtraction sites, down from 75 | **Converted from a ratchet to an ALLOWLIST with reasons** once the remainder stopped being backlog — three named groups (`_LIVE_SUBTRACTION_REQUIRED` 2, `_NOT_AN_INVOICE_MODEL` 4, `_NO_MODEL_IN_SCOPE` 4). This is ADR-0018 decision 4 executed correctly and is the model for how a commercial ratchet should end. |

---

## 8. Ratchet inventory, and the ADR-0018 conformance audit

### 8.1 Which rows have a named baseline artifact

| Module | Baseline artifact | Status |
|---|---|---|
| document rendering | **`docs/inventories/document-render-baseline.json`** + `scripts/document_render_sweep.py` (proposed): counts `engine_call_sites`, `render_path_storage_writes`, `render_time_money_derivations`, `render_path_settings_reads`, `fabricated_document_numbers`, `currency_symbol_literals`, `fallback_renderer_paths` | **Proposed, and the best-specified of the four.** It enumerates entry-point families (`app/services/**`, `app/web/**`, `app/tasks/**`, `scripts/**`, any CLI or worker module) rather than one directory — ADR-0018 decision 1 — and its sensitivity proof plants `HTML(string=x).write_pdf()`, a `get_s3_storage()` call and `f"#RCP-{uid.hex[:8]}"` under **each** family and asserts every count rose. It also **abstains rather than scoring zero** when `dotmac_sub` is not checked out. |
| collections | **`tests/architecture/billing_scheduled_sweep_baseline.txt`** (12 names incl. `dunning_runner`, `prepaid_balance_sweep`), enforced by `tests/architecture/test_billing_target_architecture.py::test_no_new_scheduled_financial_sweep` | **EXISTS — conformant since 2026-08-28.** Was non-conformant (no sensitivity proof); see § 8.2. |
| collections | **`tests/architecture/collections_credential_writer_baseline.txt`**, enforced by `test_collections_credential_writer_ratchet.py` | **EXISTS and conformant.** Two-directional on additions, removals and per-file count movement in both directions; two sensitivity proofs (a planted write, a revived retired symbol) plus a proof it counts assignments and not references. Added by COL-R5/COL-R7 (§ 3.1). |
| subscriptions | none named; reuses the mechanism of `test_billing_target_architecture.py::test_sweep_baseline_is_sorted_and_unique` | **Mechanism identified, artifact not created.** |
| billing | **none named for any of R1-R9** | **Gap.** Nine described counters with no frozen artifact. |

**Finding: three of the four modules have no baseline artifact.** A ratchet
without a frozen number is a described intention. ADR-0017 decision 5: *"A
convergence area that cannot state its current number is not ready to start."*

### 8.2 An existing ratchet that violated ADR-0018 — FIXED 2026-08-28

> **Resolved.** `scheduled_financial_sweeps()` (`scripts/architecture/
> billing_target_guards.py`) gained an explicit `source` seam, and
> `test_billing_target_architecture.py::test_scheduled_sweep_detector_is_sensitive`
> now plants a financial sweep and asserts the detector sees it — while a
> scheduled task outside the financial task prefixes is asserted NOT to be
> swept up, so the proof also shows the detector discriminates rather than
> matching every scheduled task it can find. Landed in the COL-R5/COL-R7
> slice (§ 3.1), deliberately ahead of the rows that depend on it. The
> finding below is kept as the record of why.

`tests/architecture/billing_scheduled_sweep_baseline.txt` in `dotmac_sub` was
two-directional but, as the collections team measured (`collections-sources.md`
§ 7.1):

> **"Sensitivity proof: NOT PRESENT.** No test in that file proves the detector
> still fires. A clean run is currently indistinguishable from
> `scheduled_sweep_names()` returning an empty set."

That was ADR-0018 decision 5 unmet, in a live guard, in the exact file two
retirement rows (COL-R1, COL-R2) depend on to prove they happened. Its own
header already says *"This is migration debt, not permission."*

It had to be fixed before COL-R1/R2 could be evidenced, and it was cheap —
which is why it was folded into the first slice rather than left for the rows
that needed it. The general lesson holds for every row still to come: a guard
whose sensitivity is never demonstrated is not evidence, and the cheapest
moment to prove it is before anything depends on it.

### 8.3 The conformance checklist every row's ratchet must meet

Drawn from ADR-0018 and from the one well-specified example above:

1. **Two-directional** — fails on a rise (new debt) and on an unrecorded fall
   (the detector stopped seeing something).
2. **Sensitivity-proven** — a planted violation the detector is shown to catch.
3. **Entry-point families, not a directory** — services, web, tasks, scripts,
   CLI, workers, cron. A guard scoped to one family is a guard with a known
   hole.
4. **Abstains, never scores zero,** when the measured repository is not checked
   out. Scoring a missing repo as zero reports the duplication as solved. The
   fleet precedent is
   `tests/architecture/test_external_connector_ratchet.py`.
5. **"Grandfathered" stays distinct from "reviewed and correct"** — a per-line
   marker meaning *this is genuinely fine here* must not be reachable by
   copying.
6. **Ends as an allowlist with reasons, or at zero** — ERP's `balance_due`
   conversion is the worked example.

---

## 9. Contested rows — SPLIT AND ASSIGNED, 2026-08-14

**These were the finding.** Each was a place where two independently-written
inventories retired the same file and neither named the other.

**Ruling, 2026-08-14: whole-file ownership by two teams is inadmissible.** Every
contested row is split by **symbol or by owned decision**, and each resulting row
has **exactly one lead owner** plus **named dependent teams**. A dependent may
require an ordering constraint or consume the lead's output; it may not run a
second ratchet over the lead's symbols.

The instrument for the column-level splits is the occurrence-field classification
in [`a2-commercial-offer-source-audit.md`](a2-commercial-offer-source-audit.md)
§ 5, which already assigns every amount and status field to stays / moves to
billing / rebuildable projection.

### 9.1 The split

| Row | Scope after split (symbol or decision) | **Lead owner** | Dependent teams and their obligation |
|---|---|---|---|
| **C1a** | `billing_obligations` financial columns + the financial paths of `billing/obligations.py` | **billing** | subscriptions — must not ratchet these columns; consumes them via `AcceptRatedObligationV1` |
| **C1b** | The recurrence columns carved out as `RecurringChargeOccurrence` (scheduling status, pre-tax snapshot) | **subscriptions** | billing — must not ratchet these columns. Dead columns (`corrects_id`, `reversed_by_id`, the occurrence copies of `collection_timing`/`is_finite`, `due_at`) are **deleted before the split**, not classified |
| **C2a** | `rating.py` pre-tax rating symbols | **subscriptions** | billing — ports 6 of 11 `test_billing_rating.py` cases per C10 |
| **C2b** | `rating.py` tax and gross computation symbols | **billing** | subscriptions — consumes the pre-tax result; no tax symbol in the module |
| **C3** | `billing/cadence.py` + `tests/test_billing_cadence.py` | **subscriptions** | billing — **corrects its own TOML**, which contradicts `billing-parity-tests.md` § 2 (*"ports to `dotmac-subscriptions`, not billing"*). The dossier was not updated to match the parity document |
| **C4a** | `billing_automation.py` recurring-run **dedupe** — i.e. obligation natural identity | **billing** | subscriptions — this is the duplicate-billing constraint (`uq_billing_obligation_natural_identity`), a financial invariant, so one team owns it and both described it |
| **C4b** | `billing_automation.py:1902`, `:2277` | **billing** | — |
| **C4c** | `billing_automation.py:272`, `:288`, `:2542`, `:1782-1793`, `:1931-1938`, `:2252-2258`, `:1006-1031`, `:1420`, `:544` | **subscriptions** | billing — ratchets are scoped to these line ranges, never to the file |
| **C5a** | `payment_arrangements.py` arrangement lifecycle + `tests/test_payment_arrangements.py` | **collections** | subscriptions — **ordering constraint: `_add_month_clamped` (`:132`) is extracted first** (C5b); collections may not delete or move the file before that |
| **C5b** | `payment_arrangements.py:132 _add_month_clamped` as one of the nine cadence owners | **subscriptions** | collections — its ratchet C baseline depends on this symbol surviving until extraction |
| **C6** | `collections/postpaid_policy.py`, `prepaid_policy.py` | **collections** | subscriptions — SUB-S7a *repoints* before COL-R10 *deletes*; sequence is repoint → verify → delete. **Still blocked by G1** (`period_start` absent from `ReceivablePositionV1`) |
| **C7a** | `billing_invoice_pdf.py` rendering paths and template selection | **documents** | billing — no rendering symbol in billing's ratchet |
| **C7b** | `InvoicePdfExport` + table `invoice_pdf_exports` (the job-state half) | **billing** | documents — the job state retires to **nothing**; at-most-once belongs to `dotmac_kernel.idempotency` (ADR-0014). Billing leads because it owns the official-artifact relation that replaces it |
| **C8** | `invoice_bank_details.py` + the live lookups at `billing_invoice_pdf.py:266`, `:865` | **billing** | documents — the fix is a `payment_instructions` snapshot on billing's fact, so the fix and the ratchet must sit with the same owner |
| **C9** | `tests/architecture/test_billing_target_architecture.py` | **billing** | collections, subscriptions — each names the specific cases it inherits; **the file has one owner** |
| **C10** | `tests/test_billing_obligations.py`, `tests/test_billing_rating.py` | **split per case, following C1/C2** | both — the per-case split currently exists only in prose and **must move into both TOMLs before either ships** |

Ten contested rows became sixteen assigned ones. Two carry ordering constraints
(C5, C6) and one remains blocked on a contract gap (C6/G1).

### 9.2 The original contested table, retained as evidence

| # | Contested writer | Team A | Team B | Why it mattered |
|---|---|---|---|---|
| **C1** | `app/services/billing/obligations.py` + table `billing_obligations` | **billing BIL-R2** — whole-writer retirement, counts *"writes to `billing_obligations` outside the module adapter"* | **subscriptions SUB-S5a** — splits `BillingObligation`'s 44 columns; the recurrence half becomes `RecurringChargeOccurrence` | Billing's dossier § 3 lists collections and subscription-lifecycle as *"deliberately absent"* but **does not carve the obligation recurrence half out of R2**. Two ratchets would count the same writes, and both could report zero while the other half survives. **Needs one owner statement.** |
| **C2** | `app/services/billing/rating.py` (445 L) | **billing BIL-R3** — *"sites computing a line net/tax/gross outside the module"* | **subscriptions § 4.1** — the engine is `cadence.py` plus *"the pre-tax half of `rating.py`"*; ports 6 of 11 `test_billing_rating.py` cases | Reconciled in prose (pre-tax vs tax) but **not in the ratchets** — both would count the same file's call sites. |
| **C3** | `app/services/billing/cadence.py` + `tests/test_billing_cadence.py` | **billing dossier `source_paths` / `preserved_tests`** claim both | **subscriptions** claims both as its foundation | **Billing's dossier contradicts billing's own parity document.** `billing-parity-tests.md` § 2: *"C1 — the `BillingCycle` enum. **Ports to `dotmac-subscriptions`, not billing** (P5); listed here so a billing port does not absorb it."* The TOML was not updated to match. |
| **C4** | `app/services/billing_automation.py` (2 610 L) | **billing BIL-R1** (`:1902`, `:2277`), **BIL-R2** (recurring-run dedupe) | **subscriptions SUB-S4** (`:272`, `:288`, `:2542`), **SUB-S5b** (`:1782-1793`, `:1931-1938`, `:2252-2258`, `:1006-1031`), **SUB-S6a** (`:1420`, `:544`) | **One file, four ratchets, two teams**, and both describe a *"recurring-run dedupe"* retirement. Highest baseline-collision risk in the ledger. |
| **C5** | `app/services/payment_arrangements.py` | **collections** — whole arrangement lifecycle, plus `tests/test_payment_arrangements.py` (980 L) in `preserved_tests` | **subscriptions ratchet C** counts `:132 _add_month_clamped` as one of nine cadence owners | If collections deletes or moves the file, **subscriptions' ratchet C baseline silently moves**. Neither document mentions the other. |
| **C6** | `app/services/collections/postpaid_policy.py`, `prepaid_policy.py` | **collections COL-R10** — *"delete at cutover"* | **subscriptions SUB-S7a** — *"repoint"* to `dotmac-collections` reading billing's receivable | Compatible in direction, uncoordinated in sequence. And it carries a **live contract gap** — see § 10.2. |
| **C7** | `app/services/billing_invoice_pdf.py` (1 434 L), `InvoicePdfExport`, table `invoice_pdf_exports` | **billing BIL-R9** — one ratchet counting *"writers of `invoice_pdf_exports` and callers of `billing_invoice_pdf`"* | **rendering DOC-R1-R7, R11-R13** — line-by-line, under `document-render-baseline.json` | **Two independent ratchets over the same file and the same table.** Both agree the job-state half retires to nothing/kernel idempotency; **neither names the other's ratchet.** |
| **C8** | `app/services/invoice_bank_details.py` and the bank-detail lookups at `billing_invoice_pdf.py:266`, `:865` | **billing BIL-R9** lists the service in its retirement set | **rendering DOC-R11** retires the live lookups into a `payment_instructions` snapshot on billing's fact | Same defect, two rows, two owners. Consistent in direction — but the fix lands in billing while the ratchet lives in rendering. |
| **C9** | `tests/architecture/test_billing_target_architecture.py` (417 L) | **billing** `preserved_tests` | **collections** `preserved_tests`; **subscriptions** *"must port as guard design"* | **Three teams port the same architecture test file.** Only one can own it. |
| **C10** | `tests/test_billing_obligations.py`, `tests/test_billing_rating.py` | **billing** `preserved_tests` — whole files | **subscriptions** `preserved_tests` — whole files, with a per-case split only in prose | Both claim whole files; the case-level split exists in one document and not in the TOML either team would ship. |

---

## 10. Writers in no inventory, and contract gaps that block a row

### 10.1 In no commercial inventory at all

| Writer | Why it is uncovered | Consequence |
|---|---|---|
| `dotmac_sub:app/services/sales/quote_documents.py` | Rendering explicitly excludes it — yet DOC-R3 retires the **pydyf shim it duplicates**, so the file will still hold half a duplicated compatibility hack after DOC-R3 lands | Half a retirement. Either the shim's second copy is in scope or DOC-R3 is not complete. |
| `dotmac_sub:app/web/admin/reports.py:1887-1924` (NCC regulatory pack) | Rendering excludes it | A second document-generation path survives the rendering cutover. Not wrong, but it must not be counted as retired. |
| `dotmac_sub:app/services/web_billing_documents.py` | Rendering excludes it — but **DOC-R12 retires its currency symbol at `:43`** | Same half-retirement shape as the quote-documents row. |
| `dotmac_sub:app/services/document_delivery.py`, `web_document_discount_report.py` | Excluded, no owner named | Unowned document surface. |
| **Collections and document generation as measurement buckets** | `scripts/fleet_decomposition_sweep.py` has **no `collections` and no `documents` family**: `dunning` is folded into `billing-revenue` (`:154`) and `document_sequences`/`generated_document`/`legal_documents` into `sales-agreements` (`:201-203`), `document_template` into `branding-templates` (`:236`) | `docs/inventories/fleet-decomposition-baseline.json` **cannot show collections or document-rendering duplication shrinking**. Two of the four modules have no fleet-level frozen number at all. |
| ERP's five numbering implementations | Named in ADR-0017's Context; owned by **P4, which has no owner** | Every commercial numbering row (BIL-R1's `next_invoice_number`, DOC-R9's `#RCP-` prefix) retires into a facility that does not exist. |

### 10.2 Contract gaps that make a row un-retirable as specified

| # | Gap | Blocks |
|---|---|---|
| **G1** | **`prepaid_policy.py:57` reads `period_start`** — a service-period field — on the same row as the financial fields. Billing's `ReceivablePositionV1` (spec § 2.3) carries only three `Money` values plus provenance, and **no service period**. | **SUB-S7a and COL-R10.** Without it, prepaid collections loses its *"the service period has not started"* guard and manufactures cases for future periods. The requirement appears only in the subscriptions audit; it is not in billing's contract. |
| **G2** | **Two different `ReceivablePositionV1`s.** Billing: identity `(scope, billing_account_id, currency)` + `as_of_version`, third field `prepaid_funding`, delivered as a **published fact**. Collections: identity `(source_owner, exposure_ref, source_version)`, third field `funding_available`, delivered by a **synchronous port** returning `Ok`/`Unavailable`/`Unknown`/`AuthorityMismatch`. **Same version name, different identity granularity, different field name, different transport.** Neither document acknowledges the other's shape. | **BIL-R5, BIL-R7, COL-R1, COL-R2, COL-R6.** The whole billing→collections seam. |

**Resolution recorded 2026-08-23 (G1/G2).** ADR-0030's 2026-08-23 amendment
makes Billing the sole `ReceivablePositionV1` owner and carries the missing
service-period/due-date evidence. Collections now declares the differently
named `ReceivableObservationV1`; its input contains only Billing's already
funded collectible amount, preserves financial state/authority/projection
provenance, and has no `funding_available` or competing money type. The
assembly mapping and its conformance canary replace the incompatible same-name
contracts. This resolves these two contract gaps; it is not cutover evidence.
| ~~**G3**~~ **RESOLVED** | ~~Two contradictory official-artifact relations~~ — **the shipped code picked one, and it is Billing's Part 5 shape.** Ruled by ADR-0030's *"Contract-completion follow-up — 2026-08-23"* amendment (*"Repair appends a new current relation and supersedes the former row in the same transaction… This replaces both contradictory draft key compositions"*), and confirmed shipped in `dotmac-billing==0.1.0a1`: partial unique `uq_artifacts_tenant_current` on `(tenant_id, document_fact_id, media_type) WHERE superseded_at IS NULL` (`models.py:1067-1073`); repair appends and supersedes rather than updating in place (`service.py:2651-2730`); the idempotency key **includes** the checksum (`service.py:2664`); the digest field is `presentation_model_digest`; `withdrawn_at`/`withdrawal_reason`/`supersession_reason` all exist. Rendering's `projection_digest` survives only as that package's own internal render-determinism field — billing owns the relation (ADR-0030 Q2), so this is an assembly-boundary translation, not a live collision. | ~~BIL-R9, DOC-R6~~ — unblocked. |
| **G4** | **`InvoiceArtifactReconciler` has no module owner** — both teams say it is assembly-owned, and rendering § 6.6 rejects that as a resting place: *"An assembly-owned relation table is assembly-local state with no module owning its tests, its migration or its drift repair."* Billing's plan makes it **required before the Vendor CP cutover**. | The whole Vendor CP billing cutover. |
| ~~**G5**~~ **SPLIT 2026-08-28** | The row bundled three unrelated collisions with one status, so it could be neither closed nor tracked. Split into **G5a** (resolved) and **G5b** (open) below. A gap whose sub-items disagree must be split, or its single status is wrong for at least one of them. | — |
| **G5a** **RESOLVED** | Naming collisions the shipped packages settled. (i) `RatedObligationOutputV1` vs `RecurringObligationDueV1` vs `subscriptions.recurring_obligation_due.v1` — `dotmac-subscriptions==0.1.0a3` ships `RatedObligationOutputV1`, `dotmac-billing==0.1.0a1` ships `AcceptRatedObligationV1`, the two agree, and the other two names appear in neither package. (ii) `document_profile_code` (billing) vs `template_profile_code` (rendering) — `template_profile_code` appears **nowhere in `packages/`**; `document_profile_code` is used by BOTH `dotmac-billing` and `dotmac-document-rendering`. Billing's name won and rendering's shipped package adopted it. | — |
| **G5b** **OPEN** | `ConsequenceRequestV1` (collections spec) vs `CollectionActionRequested` (collections plan). **The code converged; the spec did not.** `dotmac-collections==0.1.0a1` ships `CollectionActionRequestedV1` exclusively and `ConsequenceRequestV1` exists in no package — but `docs/superpowers/specs/2026-08-14-collections-policy-consequence-and-timer-contracts.md` § 11 still lists it as an open question *("Blocks the outbound contract")*, and no ADR amendment closes it. *"One name must win before any code"* was satisfied by the code shipping first and the spec never being amended after the fact — which is the failure mode, not the fix. Amend the spec to `CollectionActionRequestedV1` and close. | Every contract-bearing row that cites the consequence request. |
| ~~**G6**~~ **RESOLVED** | ~~Byte-for-byte equivalence is not achievable~~ — **the correction was already made, and this row was recording a request that had already been granted.** `docs/superpowers/specs/2026-08-14-billing-authority-profile-contract.md:503-514` carries the corrected wording in its own text, with its own note: *"Corrected 2026-08-14, at the Documents workstream's request. An earlier draft said 'byte-for-byte-equivalent'. That is not achievable."* No live "byte-for-byte" requirement exists anywhere. Confirmed in shipped code: `dotmac-document-rendering`'s `COMPATIBILITY.md` states it *"does not guarantee byte-for-byte PDF identity"* and guarantees an *"invariant semantic projection"*; its `conformance.py` asserts projection-digest identity, never byte equality between two renders. | ~~DOC-R1, DOC-R4~~ — unblocked. |
| **G7** | **Case lifecycle has three vocabularies for one concept**: the plan's `active\|paused\|resolved\|cancelled`, the live `DunningCaseStatus` `open\|paused\|resolved\|closed` (`models/collections.py:25-29`), and the shadow `CollectionsCaseState` `open\|warned\|escalated\|consequence_requested\|closed` (`models/collections_case.py:41-47` — **five** members; this row previously listed four, omitting `closed`). A **total classifier with no default bucket** is owed at cutover stage S0. **Still open after the module shipped**, and the module does not close it: `dotmac-collections==0.1.0a1` ships `CaseLifecycle = active\|paused\|resolved\|cancelled`, confirming the plan's vocabulary is the survivor — but the classifier mapping the two Sub vocabularies onto it remains Sub's to write, and no module symbol supplies it. | **COL-R8.** |
| **G8** | **ERP does not consume `AccountingFactV1` and its checked-in contract forbids the mechanism.** ERP has zero occurrences of `AccountingFact`. Its live integration is a **document-level HTTP pull** (`app/tasks/dotmac_sub.py` → `sync_invoices`/`sync_payments` → `post_unposted_*`, watermarked by `models/finance/ar/dotmac_sub_sync_watermark.py`), and `docs/dotmac_sub_tax_accounting_contract.md` says: *"ERP **pulls** immutable or versioned source facts from Sub… **No second push/outbox path is permitted for the same accounting decisions.**"* Billing's `AccountingFactV1` is push-after-commit through the kernel outbox. **G8 is TWO gates, not one — see § 10.3.** | Every row that ends with billing owning the accounting effects — and it is an **authority-migration decision**, not an integration detail. |

---

## 10.3 G8 is two gates — ruled 2026-08-28

An earlier draft proposed re-pointing Sub's sync endpoints at Billing-owned
projections and called G8 closed. That closes the **direct-table dependency**
only. It does not close G8, because it leaves ERP's pull as an independent
accounting writer — the exact second writer the programme exists to remove.

### Gate 1 — the compatibility re-point (new, and a prerequisite)

`GET /invoices/sync` and `GET /payments/sync` read Sub's local tables today, and
more deeply than this ledger recorded: `PaymentSyncRead`
(`app/schemas/billing.py:988`) **embeds `allocations` and `settlement`**, and
`PaymentSettlementRead` carries `unallocated_ledger_entry_id`,
`prepaid_ledger_entry_id` and `billing_account_ledger_entry_id`. So the pull
reads **BIL-R4's tables directly and BIL-R5's transitively**.

Re-point both endpoints' READERS at Billing-owned projections, responses held
byte-identical.

**BIL-R4 and BIL-R5 are therefore coupled behind ONE watermark.** They cannot
cut over separately: `/payments/sync` exposes settlement and allocation together
in a single response, so a split cutover would serve one from the module and one
from the legacy tables, in the same payload, to an accounting consumer.

### Gate 2 — the transport migration, already accepted

ADR-0020's amendment table already rules this and fixes its order:

> Pull remains authoritative → push shadow comparison → single watermark cutover
> → disable pull posting → enable inbox posting → retire the pull decision path.
> *"A pull reconciler may afterwards detect gaps and request replay, but **it may
> never post independently** — that would restore the second writer the sequence
> exists to remove."*

So the compatibility endpoints may survive gate 1 **as diagnostics**, and must
not keep posting once gate 2 lands. G8 closes at the end of gate 2, not gate 1.

---

## 11. Summary counts

| | Rows | With a named baseline | Contested | State |
|---|---|---|---|---|
| billing (Sub) | 9 | 0 | 5 | not-started |
| collections (Sub) | 10 (+5 unrowed) | 2 (one now sensitivity-proven) | 2 | **2 retired** (COL-R5, COL-R7), 8 not-started |
| subscriptions (Sub) | 14 | 0 | 5 | not-started |
| subscriptions (Vendor CP) | 3 | 0 | 0 | not-started |
| document rendering (Sub) | 13 | 1 (proposed) | 5 | not-started |
| ERP | 1 (to the kernel, not to a commercial module) | n/a | 0 | not-started |
| **Total** | **53** (§ 0.1) | **3** | **0 contested** (10 split into 16 assigned, § 9.1) | **2 retired on a branch, 0 merged** (§ 3.1) |

Fifty local writers, two frozen baselines, eight contract gaps, and one gate —
**as measured on 2026-08-14.** Three of those numbers have since moved, and the
fourth is the reason this paragraph is no longer the last word.

**Restated 2026-08-28:**

- **Fifty-three writers**, not fifty. § 0.1 found three in no inventory at all
  (`gateway_topup_intents.py`, `cutover_balance_audit.py`,
  `billing/cadence.py`).
- **Four open contract gaps**, not eight, and not the five an earlier draft of
  this re-measurement claimed. Re-audited against shipped package code on
  2026-08-28, because a gap count assembled from prose is not evidence:
  - **Resolved:** G1, G2 (ADR-0030 amendment 2026-08-23); **G3** (same
    amendment's contract-completion follow-up, confirmed shipped in
    `dotmac-billing==0.1.0a1`); **G6** (corrected in the spec's own text on
    2026-08-14 — the row was recording a request that had already been
    granted); **G5a** (both naming collisions settled by the shipped packages).
  - **Open:** **G4**, **G5b**, **G7**, **G8**. G7 is **not** closed by the
    module existing — it ships the winning vocabulary but no classifier. G8 is
    **two gates**, not one (§ 10.3).
  - The earlier draft's list — *"G3, G4, G6, G7 and G8"* — was wrong on two of
    five. It was assembled by re-reading the 2026-08-14 rows rather than by
    checking what the packages ship, which is the same failure that left the
    P11 banner stale for fourteen days.
- **The gate is MET.** ADR-0017 P11 has been met since 2026-08-17. The header's
  *"not one row below may begin"* was false for fourteen days.
- **Two retired on a branch; zero merged.** `COL-R5` and `COL-R7` (§ 3.1) are
  the first rows to reach `retired` — the local writer is gone in both, one
  moved to its owner and one deleted outright — at Sub revision
  `f7368650e7f392661ca28fa5e07d90894e819735`, which is **not merged and has no
  CI verdict yet**. Fifty-one to go, and these two are not a fleet fact until
  that revision lands green.

So the accurate current sentence is: *two writers are retired on an unmerged
branch, and the rest may now start* — in the order [ADR-0030's 2026-08-28 amendment](../adr/0030-cloud-commerce-is-composed-from-complete-domain-owners.md)
sets: Sub is first cutover for **Billing and Payments** (and remains so for
Collections and Orders), while **Vendor CP remains first cutover for
Subscriptions** — it holds a real `offer_versions` writer to retire, per
`packages/dotmac-subscriptions/EXTRACTION.toml`. Recording that
accurately is this ledger's whole job, and the fourteen-day-stale gate line is
the argument for giving it a guard rather than a re-read.

What changed on 2026-08-14 is ownership, not progress: the ten contested claims
were split by symbol or owned decision into sixteen rows with exactly one lead
owner each (§ 9.1). Two carry ordering constraints and one is still blocked on a
contract gap. **A row with an owner is not a row that is done** — a row is done
when its local writer is deleted, and exactly two are.

**What executing the first two rows cost this ledger:** two corrections to its
own text (§ 3.1) — a named owner that did not exist, and a ratchet predicate
that would have failed on unrelated work the day it was switched on. Both were
found by trying to run the row, not by re-reading it. Expect the same rate from
the rows that follow; the coordinates in § 0.1 are measurements, not warranties.
