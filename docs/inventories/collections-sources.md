# Collections, dunning and enforcement sources

**As of:** 2026-08-15 (revision of the 2026-08-14 ADR-0020 measurement)

| Repository | Revision read | Note |
|---|---|---|
| `dotmac_starter_mt` | `e6ba2022f3d7` | branch `docs/adr-0030-cloud-commerce-composition`; kernel `0.1.0a63`. **See § 12 — the shared checkout moved to another branch mid-audit and this revision was completed in an isolated worktree.** |
| `dotmac_sub` | `27c76aaeebb7` | clean; re-verified in full on 2026-08-15 |
| `dotmac_erp` | `0f4b1698ddbf` | 67 local paths present; every ERP fact below is a revision-pinned `git show`/`git grep` read, and no cited ERP file is dirty |
| `dotmac_vendor_control_plane` | `89848017d6b8` | the pinned revision is a **descendant** of the clean worktree head `f9ca367c1161`, not a divergent branch; `f9ca367..89848017` is 17 files / +324/−57 and touches only the kernel/module repin and a provisioning testing-kit fix |
| `dotmac_crm` | `c64b5aa0f790` | 3 local paths present; CRM facts below are `git show HEAD` reads |
| `dotmac_integrator` | `d014116e63ad` | clean; zero collections/dunning matches |

**Decision:** [ADR-0030](../adr/0030-cloud-commerce-is-composed-from-complete-domain-owners.md)
§ 1 (the deliberately narrow Collections row), § 5.6 (build order), § 6 (the
owner-directed implementation exception naming `dotmac-collections`) and § 7 (the
application adoption matrix). **Extends** ADR-0020 § 4 and its 2026-08-14
amendment (A1, A2, A6), which remain the ownership boundary.
**Adoption sequence:** `docs/superpowers/plans/2026-08-14-collections-sub-vendor-cp-adoption.md`
**Contracts:** `docs/superpowers/specs/2026-08-14-collections-policy-consequence-and-timer-contracts.md`
**Dossier:** `docs/inventories/collections-extraction-dossier.md`
**Portfolio pass:** `docs/inventories/cloud-commerce-owner-sources.md`

Every hash is the commit the measurement was taken at, and is a baseline rather
than a claim about current state — re-run the counts rather than trusting them
(`docs/inventories/README.md`). This is characterization, not a mandate:
ADR-0030 § 6 lifts ADR-0017's moratorium for this name and lifts nothing else —
it does not relax the live PostgreSQL migration/plane gates, does not create a
consumer by assertion, and does not permit a package before its inventory and
`EXTRACTION.toml` are complete. Namespace allocation still happens in the change
that creates the stateful package, and `MIGRATION_OWNER_LEDGER` holds no
commercial short code today.

This inventory answers one question: **which Sub code decides and applies a
collections consequence today, where do the postpaid and prepaid paths actually
diverge, and what has to be retired before a shared module can own the
decision?**

**This file is a revision, not a new document.** The 2026-08-14 inventory was
committed in PR #163 (`96dd4cb`) and is the base this revision edits; the
committed text is preserved except where it was measurably wrong. Nothing in it
is replaced wholesale apart from § 9, whose three-sentence exclusion note became
the three-repository audit the ADR-0030 adoption matrix requires.

**What the 2026-08-15 revision adds.** § 1–§ 12 are the 2026-08-14 measurement,
re-verified at the same Sub revision and corrected in place where it was wrong
(`grace_policy.py`'s real path; the invoice-overdue transition being an owner API
call rather than a raw column write; `DunningWorkflow.run`'s exact range). The
section numbers are preserved because `commercial-retirement-ledger.md:14` cites
"§ 7 (R1-R10)" and `:267` cites § 7.1. Everything ADR-0030 asked for that the
earlier pass did not carry is new: § 2.4/§ 2.5 measure callers and the shield and
grace ladders, § 5 gains five defects, § 8.4 states what the test suite
structurally cannot prove, § 9 is a fresh three-repository audit (vendor CP at a
later revision, ERP revision-pinned, CRM for the first time), § 10 gains the real
kernel delivery API and a correction about durable timers, and § 13–§ 17 supply
the verdict's other half — what not to port, what version one owns and does
**not** own, the kernel floor, the fresh proof, and the adoption slice.

---

## Verdict

`dotmac-collections` is **product-first with a mandatory port delta**. Sub is the
single qualifying source; no other repository in the fleet has a delinquency
case, a versioned grace/escalation policy, a payment arrangement or a consequence
request, and the searches that establish that are listed in § 9.

The delta is not a polish pass. Four properties ADR-0030 § 1 makes definitional
are **absent from both of Sub's stacks** and must be built, not ported:

1. **Exact receivable/exposure membership.** Neither `DunningCase` nor the shadow
   `CollectionsCase` records which receivables a case covers. Membership is
   recomputed from a fresh invoice query on every run (§ 5.13).
2. **Immutable policy versions.** `PolicySet` has no version column and a case
   pins the mutable set (§ 5.1).
3. **A consequence request rather than a write.** Suspend and reject already
   delegate to the access owner; **throttle does not** — it assigns RADIUS
   profile columns inline, live, in four places (§ 4).
4. **A refused outcome as a first-class persisted result.** Collections' *own*
   refusals are excellent durable rows with a 14-value outcome vocabulary; the
   *access owner's* refusal is a bare `ValueError` raised before any row exists
   (§ 5.14).

Two further facts bound the claim honestly. Sub's collections tables carry **no
`tenant_id`, and its migration tree contains no RLS at all** (§ 5.16), so the
module's tenant plane is new design with zero source evidence; and every
behavioural collections test runs on SQLite in the default lane (§ 8.4), so the
suite is a behaviour source, not isolation or concurrency proof.

---

## 1. The topology: Sub has two collections stacks, and only one of them runs

| | **Live stack** (`financial.dunning`) | **Target stack** (ADR-0007 Phase 5, `collections.lifecycle`) |
|---|---|---|
| Postpaid decision | `app/services/collections/_core.py` (3,121 L) | `app/services/collections/postpaid_policy.py` (61 L) |
| Prepaid decision | `app/services/collections/prepaid_balance_sweep.py` (758 L) + `app/services/prepaid_enforcement_planner.py` (725 L) | `app/services/collections/prepaid_policy.py` (116 L) |
| Case owner | `DunningCase` / `DunningWorkflow` (`_core.py:2521-2857`) | `CollectionsCase` / `CollectionsLifecycle` (`app/services/collections/lifecycle.py`, 397 L) |
| Timers | none (postpaid); two nullable datetime columns on `Subscriber` (prepaid) | `runtime.durable_timers` (`app/services/runtime_durable_timers.py`, 325 L) |
| Trigger | Celery beat: `dunning_runner`, `prepaid_balance_sweep` | a durable timer generation |
| Status | **acts in production** | `AuthorityMigrationState.SHADOWING` (`app/services/sot_registry/domains/financial_access/collections.py:311`) |

Path corrections (2026-08-15): grace lives at
`app/services/collections/grace_policy.py` (322 L), **not**
`app/services/grace_policy.py`, which does not exist.
`app/services/collections/scheduled.py` is 642 L and
`app/services/dunning_staff_actions.py` is 219 L. `app/services/collections.py`
(61 L) is tracked in git but **unimportable** — the `app/services/collections/`
package shadows it in module resolution, so no interpreter ever loads it.

The target stack **has no production caller**. `CollectionsLifecycle`,
`plan_postpaid_consequence`, `plan_prepaid_consequence` and `mode_policies.py`
are imported from outside the package by exactly two files:
`tests/test_collections_target_lifecycle.py` and
`scripts/billing/billing_target_shadow.py:51-53` — a hand-run operator CLI
(runbooks `docs/runbooks/CUSTOMER_SUBLEDGER_CUTOVER.md:36,109`), not a scheduled
task. `app/services/sot_registry/domains/financial_access/collections.py` only
declares them as registry metadata. Nothing routes
`collections.consequence_requested` (produced at `lifecycle.py:228` via
`stage_owner_output`, **zero handlers**; the intended consumer
`access.subscription_lifecycle` is documented at Sub's ADR-0007 `:415` and
unbuilt) or `collections.case_action_due` (`lifecycle.py:282` is its **only
reference in the entire repository**). The creating migration says so itself —
`alembic/versions/433_durable_timers_collections_cases.py:3-5`: *"Tables only.
The dunning_runner and prepaid_balance_sweep schedules keep running unchanged;
timers and cases are shadow evidence until the Phase 5 parity gate passes."* So
the corrected behaviour is implemented and tested but cannot cause a consequence.

**What this means for the extraction.** The source is one product with two
evidence layers, and the module may treat neither as complete:

- the **live** stack defines behaviour that cannot silently regress, and is where
  every non-conformance in § 5 lives;
- the **target** stack supplies the corrected owner boundaries, per-entity
  timers, reason-scoped cases and consequence-request shape — but is unproven
  end to end, because nothing consumes its outputs.

Sub's own migration record already says as much: `fallback_retirement =
"dunning_runner, prepaid_balance_sweep, duplicate notice/timer fields, and
parallel access actions are removed after cutover."`
(`app/services/sot_registry/domains/financial_access/collections.py:329`).

---

## 2. Owner map — who decides, what they write, what triggers them

### 2.1 Postpaid dunning (live)

| Owner | Decides | Writes | Trigger |
|---|---|---|---|
| `app/services/scheduler_config.py:712-723` | nothing — registers the job | `ScheduledTask(name="dunning_runner", task_name="app.tasks.collections.run_billing_enforcement")` | startup sync; interval `collections.dunning_interval_seconds` (spec `settings_spec.py:1098-1105`, default 86400, min 60, env `DUNNING_INTERVAL_SECONDS`), floor hardcoded `max(..., 60)` at `:716`; `enabled=True` unconditionally |
| `app/tasks/collections.py:5-7` → `app/services/collections/scheduled.py:21-53` | nothing — session/transaction shell | commits what the reconciler staged (`:47`) | Celery beat, queue `billing` (`celery_app.py:153`), soft limit 1740 s |
| `BillingEnforcementReconciler.run` (`_core.py:2975-3011`) | run ordering: settle credit, then dun | counters only | above |
| `_settle_due_credit_before_dunning` (`_core.py:2871-2972`) | which accounts get credit applied before a dunning decision, and whether that makes them restorable | credit application rows; calls `restore_account_services` (`:2943`); commits per account (`:2958`) | above |
| `DunningWorkflow.run` (`_core.py:2523-2821`, 299 L; class `:2521-2857`) | **the escalation decision**: which accounts are overdue candidates, which policy step fires, whether a case opens/advances | `DunningCase` (`:2672-2680`), `DunningActionLog` (`:2744-2753`), `case.current_step` (`:2754-2755`), `case.policy_set_id` (`:2697`), events `dunning_started` (`:2683`) / `dunning_action_executed` (`:2758`), **and an invoice transition to `overdue` by calling the invoice owner's `Invoices.mark_overdue_system(...)` (`:2561-2567`)**; one `db.commit()` at `:2814` | above |
| `_execute_dunning_action_with_evidence` (`_core.py:2015-2086`) | maps a policy step's `DunningAction` to a `FinancialAccessAction` and requests it | delegates; queues notices (`:2079-2085`) | `DunningWorkflow.run:2735` |
| `preview_/confirm_financial_access_consequence` (`_core.py:459-757`, `:813-1041`) | **the consequence eligibility decision** and its application | `FinancialAccessConsequence` + `...Evidence`, `EnforcementLock` via `account_lifecycle.suspend_subscription` (`:869-885`), audit, events | above, and the prepaid sweep |
| `dunning_staff_actions.py` + `app/web/admin/billing_dunning.py` | staff pause / resume / close | `DunningCase.status`, `DunningActionLog`, `AuditEvent` — **no access state** | admin routes `POST /admin/billing/dunning/...` |

Exact ranges in `_core.py`, re-measured — four functions of 220–300 lines each,
which is where the extraction cost lives:
`preview_financial_access_consequence` `:459-757` (299),
`confirm_financial_access_consequence` `:813-1041` (229),
`preview_financial_access_restoration` `:1078-1299` (222),
`confirm_financial_access_restoration` `:1302-1594` (293),
`_execute_dunning_action_with_evidence` `:2015-2086` (72),
`BillingEnforcementReconciler` `:2860-3011`.

The step ladder itself **is data**: `PolicyDunningStep` rows
(`app/models/catalog.py:406-419`, `day_offset` + `action`) selected by
`if candidate.day_offset <= max_days` (`_core.py:2712-2715`). There is no seeded
default sequence anywhere — `grep "PolicyDunningStep("` finds only the model and
the admin CRUD writer (`app/services/catalog/policies.py:130`). This is the one
place the live stack is already close to C4's shape, and it is why C4's problem
is not "the ladder is hardcoded" but "the ladder is **mutable and unversioned**"
(§ 5.1).

### 2.2 Prepaid enforcement (live)

| Owner | Decides | Writes | Trigger |
|---|---|---|---|
| `app/services/scheduler_config.py:736-745` | nothing — registers the job | `ScheduledTask(name="prepaid_balance_sweep")` | interval `collections.prepaid_balance_sweep_interval_seconds`, default 3600, min 300, **max 3600** (`settings_spec.py:1106-1114`); unlike the dunning/overdue/notification jobs, **no hardcoded floor is applied in `scheduler_config`** |
| `app/services/collections/scheduled.py:598-642` | run ordering: coverage repair → renewal-terms repair → sweep → snapshot | commits per stage | Celery beat, default queue, 840 s soft limit, 720 s self-budget (`scheduled.py:91`, `:580-595`) |
| `prepaid_enforcement_planner.plan_prepaid_account` (`:371-628`) | **the whole prepaid ladder** — stale-timer repair, billing-profile validity, coverage/renewal resolution, funded/restore, drift, warn, waiting, deferred, shielded, suspend | **nothing** — pure (`:1-6`) | called by the sweep (`prepaid_balance_sweep.py:357`, `:374`) and by a read-only script |
| `prepaid_balance_sweep.run_prepaid_balance_sweep` | dispatch, notice-outcome deferral (`:286-287`), and whether to arm the deactivation marker (`:317-330`) | `Subscriber.prepaid_low_balance_at` / `prepaid_deactivation_at` (via `prepaid_enforcement_state.py:59-103`), `Notification`/`CommunicationIntentRecord`, `Finding` work items (`:181-204`), `PrepaidSweepCycleState` cursor (`:699-722`); consequences via the shared owner | above |
| `prepaid_enforcement_state.py` (111 L) | first-write-wins on the two timer columns (`:67`, `:83`) | those two columns; `prepaid_enforcement_timer_changed` events (`:40-56`); flush-only, never commits | the sweep, and `_clear_prepaid_dunning_flags` (`_core.py:3014-3022`) |

Both schedules are pinned as permanent by
`tests/architecture/test_permanent_customer_financial_lifecycle.py` (82 L) — they
cannot be toggled off, which is itself a retirement constraint (§ 7).

### 2.3 The shared tail

Exactly five things are shared between the two paths, and all five sit **below**
the decision:

1. `preview_/confirm_financial_access_consequence` (`_core.py:459`, `:813`) —
   with reason-branched internals (`:511-580`) and gates that fire for only one
   mode (§ 3.6);
2. `preview_/confirm_financial_access_restoration` (`:1078`, `:1302`);
3. `account_lifecycle.suspend_subscription` / `restore_subscription_detailed` /
   `resolve_stale_lock_without_restoration` — `app/services/account_lifecycle.py`
   is the **sole writer** of `Subscription.status` / `.access_state` /
   `Subscriber.status`, confirmed by the AST scan in § 4;
4. `_bulk_dunning_shield_reasons` (`_core.py:1914-1938`),
   `collections/grace_policy.py::resolve_grace_decision` (`:306`),
   `billing_profile.resolve_billing_profile`,
   `enforcement_window.resolve_enforcement_window_decision` (`:134`);
5. `FinancialAccessConsequence` + `FinancialAccessConsequenceEvidence`, and the
   fingerprint/idempotency machinery (`:201-203`, `:760-810`).

`docs/FINANCIAL_ACCESS_ENFORCEMENT.md:52-53` names the two owners: the
`financial.dunning` access-consequence owner "locks, recomputes, fingerprints,
applies, and evidences" the consequence, and `access.subscription_lifecycle` is
"sole writer of reason-scoped locks, account status, and child-service access
state in one transaction."

### 2.4 How widely it is used — measured callers (2026-08-15)

| Symbol | Tracked files with a call site | Non-test production callers |
|---|---|---|
| `preview_financial_access_consequence` | 2 | **`_core.py` only** (`:847`, `:1056`, `:2043`, `:2720`) |
| `confirm_financial_access_consequence` | 2 | **`_core.py` only** (`:1063`, `:2067`) |
| `preview_financial_access_restoration` | 4 | `_core.py:1334,1603,3073`; `billing/unwall_paid_accounts.py:176`; `prepaid_balance_sweep.py:235` |
| `confirm_financial_access_restoration` | 4 | `_core.py:1609,3077`; `prepaid_balance_sweep.py:240`; `prepaid_draft_reconciliation.py:3798` (via the `_for_owner` wrapper at `_core.py:1597`) |

**The consequence half has exactly one production consumer surface, and it is
`_core.py` itself.** Only restoration is called from outside. That is the most
encouraging measurement in this inventory: the *request* boundary a shared module
needs is already narrow, and the work is to make it typed rather than to invent
it.

The model surface is wider. `DunningCase` is referenced by **24** tracked files,
including `app/models/subscriber.py` (relationship), `crm_api.py`,
`customer_timeline.py`, `notification_template_conditions.py`,
`web_customer_details.py` and `web_subscriber_details.py` — CRM, timeline,
notification templating and three web projections. `DunningActionLog` 12
(including a back-ref from `Invoice`/`Payment` in `app/models/billing.py`),
`PolicySet` 10, `FinancialAccessConsequence` 7, `PolicyDunningStep` 5,
`CollectionsCase` **4** (the model, the package `__init__`, its own lifecycle
module and one test — zero other consumers). `DunningCase`'s tendrils, not the
enum count, are the real extraction surface.

**51 distinct tracked files import from `app.services.collections`**: 8 inside
the package, 1 dead shim, 1 Celery task, 2 admin/web routes
(`app/web/admin/billing_dunning.py:16`, `app/web/admin/customers.py:236`), 1 JSON
API route (`app/api/me.py:343`), 14 services, 3 scripts and 21 tests.

Two coupling facts a shared module has to answer for:

- **Seven production modules import the private `_core`**, and
  `prepaid_enforcement_planner.py:37` imports the private
  `_bulk_dunning_shield_reasons`. `app/web/admin/billing_dunning.py:16` and
  `app/services/web_billing_dunning.py:26` do it as
  `from app.services.collections import _core as dunning_owner`. Whatever the
  module publishes must cover these, or the consumers break at cutover.
- **`service_status.py:41-42` and `stale_overdue_lock_reconcile.py:35` read
  `has_overdue_balance` from collections.** That is a receivable answer served by
  the collections module, and under ADR-0020 § 1 it is billing's. It is also
  precisely the shape § 14's `NOT` boundary forbids: no field a caller can read
  as "the balance".

### 2.5 Shields and grace, in precedence order

Both ladders are strict, both are consumed by the postpaid and prepaid paths, and
both are pinned by architecture tests — this is the part of the source already
close to the right shape.

**Dunning shield** — `_dunning_shield_reason` (`_core.py:1886-1911`) and its bulk
form `_bulk_dunning_shield_reasons` (`:1914-1938`), three sources in strict
order:

1. `active_arrangement_shield_reason(db, account_id)` (`:1897`) — **`status ==
   active AND is_active` only; a `pending` arrangement does not shield**;
2. a `PaymentProof` with `status == submitted` (`:1900-1908`);
3. `extension_shield_reason(db, account_id)` (`:1909-1911`).

Applied at three points: `preview_financial_access_consequence` (`:583-586`,
`eligible = False`, `outcome = "shielded"`, persisted as `"shield_reason"`
evidence at `:713`); `_execute_dunning_action_with_evidence` (`:2028-2029`) — so
a shield suppresses even the `notify` action; and the bulk run (`:2636-2638`), so
a shielded account never opens a `DunningCase` at all. Non-advancement is a
declared set, `_NON_ADVANCING_DUNNING_OUTCOMES` (`:1946-1955`), applied at
`:2754` — the ladder **freezes**, it does not skip.
`tests/architecture/test_financial_action_boundaries.py:503-508` asserts
collections consumes the shield through functions and never imports
`app.models.payment_arrangement`. That is the correct dependency direction and
must survive the port.

**Payment arrangements** are a real, separate owner:
`app/models/payment_arrangement.py` (150 L — `PaymentArrangement`,
`PaymentArrangementInstallment`, `ArrangementStatus`
pending/active/completed/defaulted/canceled) and
`app/services/payment_arrangements.py` (1,220 L — `approve:751`,
`record_installment_payment:883`, `check_overdue_installments:934` with the
default path at `:987-990` and `EventType.arrangement_defaulted` at `:1013-1025`,
`cancel:1034`, `apply_payment_to_arrangement:1151`), with
`payment_arrangement_staff_actions.py` (190 L), a Celery task
(`app/tasks/arrangements.py:15`) and an event handler
(`app/services/events/handlers/arrangements.py:23`). Surfaces are admin HTML
(`app/web/admin/billing_arrangements.py:151-258`, permission
`billing:arrangement:write`) and the customer portal
(`app/web/customer/routes.py:3248-3403`); **there is no JSON API router for
arrangements.** Beware the unrelated `SubscriptionBillingArrangement`
(`app/models/subscription_billing_treatment.py:50`) — sponsored/comp billing, a
different concept.

**Grace** — `app/services/collections/grace_policy.py`, two independent ladders:

- policy-set selection (`:128-195`): `account.policy_set_id` → reseller →
  collectible subscriptions by status priority then newest, `offer_version`
  before `offer` → `collections.default_{prepaid,postpaid}_policy_set_id`;
- grace days (`:204-252`): `account.grace_period_days` → an **active**
  `PolicySet.grace_days` → `billing.{prepaid|postpaid}_default_grace_period_days`.

`_grace_days` (`:104-118`) rejects booleans, coerces non-ints to `-1` and raises
`GracePolicyError("financial.grace_policy.invalid_grace_days")` on any negative:
**a missing mode default is an error, not a silent zero-day grace.** By contrast
`decide_grace` (`:255-303`) never raises — `starts_at is None` yields
`NOT_STARTED` with `elapsed_days_after_grace = 0`, so an unarmed clock means no
enforcement. Write-loud, read-degrade, and the split is deliberate. Grace and the
enforcement window are **sequential, not nested**: grace decides *past due
enough*, the window decides *acceptable wall-clock moment*, and grace is always
evaluated first (`_core.py:525`/`:566` before `:595-606`; planner `:565-567`
before `:568-572`). `enforcement_window.py` (165 L) is fail-open throughout.

---

## 3. The measured postpaid/prepaid divergence — C2's central evidence

Sub's `docs/adr/0007-end-to-end-billing-target-architecture.md:66-68` records:

> Postpaid dunning and prepaid enforcement have different account scans, timers,
> notices, commits, and error handling even though both eventually ask the
> shared access-lifecycle owner to act.

Measured against the code at `27c76aaee`, **the statement is accurate and
understated**: four of the five axes are entirely separate implementations with
zero shared code, and the fifth (commits) differs in transaction granularity by
the whole run.

### 3.1 Account scan — different, zero shared code

| | Postpaid | Prepaid |
|---|---|---|
| Entry query | `_core.py:2525-2546` — `Invoice` where `balance_due > 0`, `due_at <= run_at`, `is_active`, `collectible_ar_invoice_filter()`, status ∈ {issued, partially_paid, overdue} | `prepaid_enforcement_planner.py:208-262` — `Subscriber` cohort, three UNIONed subqueries |
| Unit of scan | **invoice**, grouped to accounts at `:2547-2560` | **account** |
| Secondary filters | postpaid subscriptions `:2571-2586`; `automation_safe` `:2626-2632`; shields `:2636-2638`; policy set `:2640-2643`; steps `:2649-2651`; `max_days > 0` `:2654-2668` | funding candidates `planner.py:265-290`, minus `prepaid_funding_incomplete_source_account_ids` (`prepaid_balance_sweep.py:609-613`) |
| Pre-pass | credit settlement over a **different** invoice query (`_core.py:2879-2906`) | coverage-evidence repair + renewal-terms repair (`scheduled.py:615`, `:624`) |
| Ordering / fairness | none (dict iteration order) | sorted by `str(uuid)` + persistent keyset cursor `PrepaidSweepCycleState` (`:619-629`, `:699-722`) |
| Budget | none | wall-clock deadline, 720 s default, `budget_deferred` (`:646-657`) |
| Prefetch | per-account N+1 | run-level `_SweepPrefetch` (`:465-526`) |

Shared: `_bulk_dunning_shield_reasons` and `resolve_billing_profile`. Nothing else.

### 3.2 Timer — different, zero shared code, and **neither uses the durable-timer facility**

- **Postpaid has no persisted timer at all.** The clock is `Invoice.due_at` plus
  `resolve_grace_decision(...).elapsed_days_after_grace` (`_core.py:369-396`)
  compared against `PolicyDunningStep.day_offset`, ratcheted by
  `DunningCase.current_step` (`:2718`). The next step happens when the next
  `dunning_runner` tick — **default every 86,400 s** — finds a larger offset.
- **Prepaid uses two nullable datetime columns on `Subscriber`**:
  `prepaid_low_balance_at` armed once (`prepaid_enforcement_state.py:59-72`) and
  `prepaid_deactivation_at` written after a successful suspend (`:75-88`), plus a
  derived `deactivation_due_at` (`planner.py:460-472`). The next step happens on
  the next sweep tick — **default every 3,600 s**.
- **`runtime.durable_timers` exists and neither path uses it.** `schedule_timer`
  has exactly one collections caller: the unwired `lifecycle.py:274-285`. Sub's
  own ADR-0007 invariant 18 (`:620-621`) is therefore unmet on both sides.

The consequence of the cadence gap is concrete:
`app/services/enforcement_window.py:10-13` warns that a daily beat makes a
time-of-day enforcement window unusable — and a daily beat is exactly the
postpaid cadence.

### 3.3 Notice — different, zero shared code, different template mechanism, different channel set

| | Postpaid | Prepaid |
|---|---|---|
| Warning | `_create_suspension_warning_notification` (`_core.py:1811-1836`) — emits an **event**, hardcoded `"grace_hours": "0"` (`:1830`), `"reason": "dunning"` (`:1831`) | `_queue_notice` (`prepaid_balance_sweep.py:90-165`) — direct `communication_intents.submit` |
| Suspension notice | `_create_suspension_notification` (`:1839-1883`) — subject `"Account Suspended"` and body hardcoded in Python (`:1879-1880`), **email only** (`:1875`) | subject/body from settings `collections.prepaid_deactivation_subject`/`_body`, fail-closed if blank (`planner.py:188-205`) |
| Throttle notice | `_create_throttle_notification` (`:1780-1808`) — hardcoded copy, email only | n/a — prepaid never throttles |
| Channels | `NotificationChannel.email` only | `(email, sms, whatsapp)`, push deliberately excluded (`:135-142`) |
| Dedupe | ad-hoc SQL on **the literal subject string** within N hours (`:1852-1869`), N = `collections.suspension_notification_dedupe_hours` (default 24, min 1, max 168). **Throttle notices and warning events have no dedupe at all.** | none — no `dedupe_key` is passed; relies on the timer being armed once |
| Unreachable customer | logged warning and return (`:1789-1793`, `:1846-1850`) — invisible | typed `no_contact_route` + durable `Finding` with a 72 h SLA (`:168-204`) |
| Fault shield | none | outage / infrastructure-down shield **before** queuing (`planner.py:293-313`) |

### 3.4 Commit boundary — different granularity

- **Postpaid:** one transaction for the whole run. `DunningWorkflow.run` mutates
  every account in the loop and commits **once** at `_core.py:2813-2814`
  (savepoints only for the restoration reconciliation, `:2796-2804`). The credit
  pre-pass commits per account (`:2958`) with `db.rollback()` on error (`:2960`).
- **Prepaid:** strictly per account — row lock, process, `db.commit()` inside the
  loop (`prepaid_balance_sweep.py:658-677`), documented at `:12-14`: "Every
  account is processed in its own committed unit so one bad row cannot abort the
  batch." Plus independent commits for the prefetch (`:638`), the cycle
  checkpoint (`:723`) and finding resolution (`:753`).

### 3.5 Error handling — different

| | Postpaid | Prepaid |
|---|---|---|
| Per-item failure | **none** — one exception aborts the whole daily run and rolls it back (`scheduled.py:49-51`) | caught per account, `_safe_rollback`, `errors += 1`, continue (`:692-698`) |
| Poisoned connection | plain `session.rollback()` | `_safe_rollback` with `db.invalidate()` fallback (`:544-561`) |
| Soft time limit | unhandled; relies on the 1740 s ceiling | explicit `SoftTimeLimitExceeded` handler that defers the remainder and still publishes (`:678-691`) |
| Failed consequence | non-advancing outcomes leave `current_step` unchanged (`:2754-2755`); a 409 from confirm propagates and kills the run | `_suspend_account` returning `False` deliberately leaves `prepaid_deactivation_at` unset so the next sweep retries (`:317-330`) |
| Restore failure | **swallowed** — bare `except Exception:` + `logger.exception` (`:2805-2812`, `:2947-2957`), no row, no counter, no retry | propagates into the per-account handler, counted, retried next run |
| Observability | log lines; 4 + 5 counters | 17 counters + 4 cycle metrics + `publish_state_snapshot("prepaid_enforcement", …)` with 22 signals (`scheduled.py:515-577`) |

### 3.6 Even the "shared" consequence owner is reason-branched

Two gates fire for exactly one mode:

- **Enforcement window** is enforced only for `EnforcementReason.overdue`
  (`_core.py:594-606`). It is *computed* for prepaid and recorded in
  `decision_inputs` (`:716-717`), but never gates. Prepaid's only window check is
  in the planner (`planner.py:566-571`), prefetched **once per run**
  (`prepaid_balance_sweep.py:525` → `:369-370`). This contradicts
  `docs/FINANCIAL_ACCESS_ENFORCEMENT.md:417-419`, which states the window "is
  checked again inside the locked consequence preview/confirmation. A manual run,
  retry, or duplicate schedule cannot bypass it." That guarantee holds only for
  postpaid.
- **Minimum enforcement age** returns `None` for prepaid by design
  (`_core.py:1990-1991`), and `_suspend_account` never passes `overdue_days`
  (`:1056-1074`), so the gate at `:587-593` is dead for prepaid.

Both windows also default to unset (`settings_spec.py:1258-1271`, seeded `""`),
so out of the box there is no time-of-day gate at all.

### 3.7 Quantified separation

| | Prepaid | Postpaid | Shared |
|---|---|---|---|
| Dedicated modules | 4 (`prepaid_balance_sweep.py` 758, `prepaid_enforcement_planner.py` 725, `prepaid_enforcement_state.py` 111, prepaid half of `collections/scheduled.py` ~470) | ~1.5 (postpaid half of `_core.py` ~900, `scheduled.py:21-53`) | `_core.py` consequence half ~1,150; `collections/grace_policy.py` 322; `enforcement_window.py` 165 |
| Top-level defs | ~37 | ~25 | ~13 + 2 helpers |
| Classes / dataclasses | 13 | 7 | 5 |
| Persisted workflow state | `Subscriber.prepaid_low_balance_at`, `.prepaid_deactivation_at`, `PrepaidSweepCycleState` | `DunningCase` (+`current_step`, `policy_set_id`, `status`), `DunningActionLog`, `PolicyDunningStep` | `FinancialAccessConsequence` (+ evidence), `EnforcementLock` |
| Scheduled task | `prepaid_balance_sweep`, 3600 s (min 300 / max 3600), default queue, 840 s limit, 720 s self-budget, keyset cursor | `dunning_runner`, 86400 s (min 60), `billing` queue, 1740 s limit, no budget, **no overlap lock** | — |

### 3.8 The reading

The divergence is not two policies. It is two **implementations of the same five
mechanisms** — select, wait, notify, commit, recover — where only the final
`suspend()` call is shared. C2's claim that collection timing is one contract
field is supported directly by Sub's own corrected planners: at
`postpaid_policy.py:25-58` and `prepaid_policy.py:35-113` the two decisions
differ by exactly four predicates —

1. `AccountingTreatment.receivable` vs `.prepaid_consumption`;
2. anchor `due_at <= now` vs `period_start <= now`;
3. coverage `outstanding > 0` vs `outstanding > prepaid_funding_reserved +
   unapplied_customer_credit`;
4. reason `postpaid_overdue` vs `prepaid_underfunded`;

plus prepaid's authority-cutover refusal (`:69-93`). All four are policy **data**
— a declared receivable/coverage predicate, an anchor code, a funding-source
list and a reason code — which is exactly C4's `applies_to`. **~2,000 lines of
duplicated scan/timer/notice/commit/error machinery exist to express four
predicates.**

What Sub has not done is delete the second module: `postpaid_policy.py` and
`prepaid_policy.py` are modules named for exactly one timing mode each, which
C2's architecture test rejects on the name alone.

---

## 4. Second writers — consequences applied by direct write

Every entry below is a consequence applied by writing state directly rather than
requesting it from the owning service. These are the writers the module exists to
remove.

Method (2026-08-15): an AST scan over every tracked `.py` for attribute
assignments to `status`, `access_state`, `radius_profile_id` and
`pre_throttle_radius_profile_id`, plus greps for bulk `.update()`,
`sa.update(Subscription)` and raw `UPDATE subscriptions/subscribers` — **zero**
occurrences of the last three anywhere in `app/`. The scan is what makes the list
below a closed set rather than a sample.

| # | Site | What it writes directly | Live? | Owner it bypasses |
|---|---|---|---|---|
| 1 | `_core.py:919-920` (throttle, inside `confirm_financial_access_consequence`) | `credential.pre_throttle_radius_profile_id`, `credential.radius_profile_id = change.profile_after_id` | **yes** — reached from `_execute_dunning_action_with_evidence:2067` on `DunningAction.throttle` | the access owner; suspend/reject on the adjacent branch correctly call `account_lifecycle.suspend_subscription` (`:869-885`) |
| 2 | `_core.py:1475-1476` (un-throttle, inside `confirm_financial_access_restoration`) | same two columns | **yes** — reached from `restore_account_services` (`:3077`), `prepaid_balance_sweep.py:240`, `prepaid_draft_reconciliation.py:3798` | same |
| 3 | `_core.py:1692-1693`, `:1748-1749` (`_throttle_account` / `_restore_throttle`) | same two columns | no production caller outside `_core.py`; exercised only by `tests/test_access_enforcement_strays.py`; still compiled in | same; dead code carrying the pattern |
| 4 | `_core.py:2561-2567` — the dunning **scan** calls `Invoices.mark_overdue_system(..., reason="dunning_candidate_resolution")` | an invoice transition to `overdue`, through the invoice owner's API | **yes** | billing. Under ADR-0020 § 1 the invoice lifecycle is billing's; a collections scan must not *trigger* it. Also mutates invoice metadata via `apply_prepaid_overlap_hold` (`:2552-2554`) |
| 5 | `prepaid_enforcement_state.py:69`, `:85`, `:99-100` (called from `prepaid_balance_sweep.py:291`, `:330`) | `Subscriber.prepaid_low_balance_at`, `.prepaid_deactivation_at` | **yes** | not access state, but collections **timer** state persisted on a product identity row. ADR-0007 § 8 assigns exact timers to `collections.lifecycle`, which is not consulted |

**Rows 1–3 are the one hard blocker** to ADR-0030 § 1's "a request is not
permission and not a state write". Suspend and reject already delegate; throttle
is applied in-module. Any claim that Sub's stack is already request-shaped is
false on exactly these four lines.

**Row 4 is a call, not a raw write, and the distinction shapes the retirement.**
The AST scan finds **zero** `invoice.status = ...` assignments anywhere in
`app/services/collections/**`, `prepaid_enforcement_planner.py`,
`prepaid_enforcement_state.py` or `app/tasks/collections.py`. Collections
respects the owner's API and still causes the transition — which is why R6 is
about removing the *call*, not about hunting a rogue assignment. There are
already **two** paths that mark an invoice overdue: this one, and the invoice
owner's own `overdue_checker` / `app.tasks.billing.mark_invoices_overdue`
(`scheduler_config.py:690-696`). Retiring row 4 removes a duplicate; it does not
remove the capability.

Row 5 is a **soft boundary leak** worth naming separately: two enforcement-clock
columns live on `subscribers`, a product identity table, and are owned by
collections. `tests/architecture/test_prepaid_enforcement_state_boundary.py`
pins `TIMER_FIELDS = {"prepaid_low_balance_at", "prepaid_deactivation_at"}` to
that one writer — good discipline, wrong table. A shared module needs those
columns in its own plane.

Not a bypass, recorded to avoid a false positive: `fup_enforcement.py` decides
Fair-Use throttling and its handler calls `account_lifecycle.suspend_subscription`
(`app/services/events/handlers/enforcement.py:503-514`) — a non-financial reason
routed through the correct single access writer. `app/services/enforcement.py`
writes only RADIUS/session **projections** (`:1706`, `:1718`, `:1834`, `:1936`),
never `Subscription.status`.

**`account_lifecycle.py` is the sole writer, confirmed by the same scan.**
`Subscription.status` is assigned at `account_lifecycle.py:379, 441, 820, 1019,
1113, 1183, 1288, 1351, 1415, 1727` and in exactly one other file,
`web_system_restore_tool.py:398` (disaster restore replaying a
`subscription_snapshot`, immediately followed by `record_current_state_baseline`
— not payment-driven). `Subscriber.status`: `account_lifecycle.py:2039` and
`web_customer_actions.py:3156` (CRM contact→subscriber conversion).
`Subscription.access_state`: **one** writer in `app/`,
`account_lifecycle.py:1989`, as `app/services/radius_access_state.py:1-7`
documents. Collections reaches the owner by lazy import at four points
(`_core.py:426`, `:869`, `:1341`, `:2928`). The other `radius_profile_id` writers
(`access_credential_binding.py:67`, `catalog/subscriptions.py:388,405`,
`enforcement.py:1455`, `pppoe_credentials.py:222`, `radius.py:988,1639`,
`radius_access_state.py:45`, `web_catalog_subscriptions.py:1240`) are
provisioning or staff-driven, not financial-condition-driven.

Case-state writes inside `_core.py` (`:1420-1421`, `:2299`, `:2314`,
`:2318-2319`, `:2416`, `:2697`, `:2755`, `:2842-2843`), `lifecycle.py:208-240`,
`:356-358` and the `PrepaidSweepCycleState` cursor at
`prepaid_balance_sweep.py:704-711` are collections writing its own rows, and are
not second writers.

---

## 5. Non-conformances the extraction must not carry forward

Recorded per the product-first procedure's step 3 (ADR-0006's 2026-08-08
amendment; AGENTS.md rule 24). ADR-0020 § 5 requires these to be corrected at the
shared boundary rather than preserved as compatibility behaviour.

1. **Policy is mutable and unversioned; a case pins a pointer, not a version.**
   `PolicySet` (`app/models/catalog.py:368-403`) has no version column — only
   `updated_at` with `onupdate`. `PolicyDunningStep` is a mutable child
   collection. `DunningCase.policy_set_id` (`app/models/collections.py:66-68`)
   pins the mutable set, and `_core.py:2647` re-reads its steps live on every
   run. **Editing a policy set mid-case silently rewrites the ladder of every
   live case.** The target `CollectionsCase` pins nothing about policy at all.
   No test anywhere asserts a case is decided by the policy as of open. This is
   C4's core defect and the reason § 4.2 of the contracts spec makes the pinned
   version *and* its fingerprint mandatory.
2. **The shadow case ladder is a fixed four-state chain.**
   `CollectionsCaseState` = `open | warned | escalated | consequence_requested`
   with per-state timestamp columns (`app/models/collections_case.py:41-47`,
   `:99-103`) walked by a literal `_NEXT_STATE` dict (`lifecycle.py:63-67`). A
   five-step, two-step, or second-notice ladder is unrepresentable. The live
   stack is better here (`day_offset` rows), which is unusual — the corrected
   implementation regressed on ladder arity.
3. **`FinancialAccessAction` is an enum**, not a declaration registry
   (`app/models/collections.py:32-37`: `suspend | reject | throttle | restore`).
   ADR-0008 and AGENTS.md rule 12 require an open registry.
4. **Idempotency keys embed a truncated preview fingerprint**, so they are
   state-derived rather than stable business keys: `dunning:{case}:{action}:
   {day}:{fp[:20]}` (`_core.py:2075`), `financial-suspend:{account}:{reason}:
   {fp[:24]}` (`:1071`), `financial-restore:...{fp[:24]}` (`:3081-3085`). Any
   input drift produces a new key and therefore a new consequence row. ADR-0014
   requires the fingerprint to be its own column beside a stable key, and the
   kernel's `execute_once` already provides exactly that split
   (`packages/dotmac-kernel/src/dotmac_kernel/idempotency.py:26-31`).
5. **The shadow stack's consequence key contains `uuid4()`** —
   `f"collections:{case.id}:{uuid4()}"` (`lifecycle.py:216-218`). Stable within
   one case row, but not derivable, so it cannot dedupe a re-created case.
6. **A failed consequence is swallowed, not recorded.** `_core.py:2805-2812` and
   `:2947-2957` catch `Exception`, log, and continue. `FinancialAccessConsequence`
   has `eligible`, `outcome` and `result` but **no attempt count, no
   `next_retry_at`, no failed status**. `tests/test_collections_dunning_services
   .py:1313` asserts the swallow is correct (`credit_settlement_errors == 0`
   while the restore raised). Sub's own design doc lists "exception swallowing
   and ambiguous partial success" among retired paths
   (`docs/designs/DUNNING_STAFF_SAFE_ACTIONS.md:67`) — for the staff path only.
7. **A postpaid consequence is account-wide even when the debt is not.**
   `_core.py:650-674` selects **every** subscription on the account in
   `COLLECTIBLE_SERVICE_STATUSES` for `EnforcementReason.overdue`; the narrowing
   clause at `:670-674` applies only to the prepaid reason. One overdue invoice
   for one service suspends the whole account. Neither `DunningCase.account_id`
   nor `FinancialAccessConsequence.subscriber_id` carries an obligation or
   contract id.
8. **A consent suppression is indistinguishable from a broken transport.**
   `prepaid_balance_sweep.py:151-166` classifies any suppression reason not
   ending in `:missing_address` as `delivery_unavailable`, which by design does
   **not** defer progression (`:66-81`, `:286-287`). Only `policy_suppressed` (a
   customer-impact shield) defers. So an unsubscribe and an SMTP outage produce
   the same collections outcome, and no test covers it.
9. **Zero-grace suspends with no prior notice.** `_reconcile_low` skips
   `_queue_notice` when `suspend_now`, arming and suspending in one unit
   (`prepaid_balance_sweep.py:271-344`); the deactivation notice is emitted only
   after the suspension succeeded (`:333-341`).
10. **Fleet-wide billing health is still inside every consequence fingerprint.**
    See § 6 — this is the correction the brief asked to verify, and it is only
    half done.
11. **Hardcoded thresholds, money and currency literals** that C4 forbids:
    `_core.py:1998`/`:2000` (`minimum_days = 3` fallback, twice, beside a setting
    that already defaults to 3), `:2003` (`if overdue_days < minimum_days`),
    **`:2170` — `str(receivable["currency"] or "NGN")`**, `:240`/`:2171`/`:2912`/
    `:2914`/`:2984` (`Decimal("0.00")`, `"0.00"`), `:283-285` (`24`, min 1, max
    168), `collections/grace_policy.py:290` (`timedelta(days=policy.days + 1)`),
    `scheduler_config.py:716` (`max(..., 60)`), `prepaid_balance_sweep.py:64`
    (`_NO_CONTACT_SLA_HOURS = 72`), `scheduled.py:82`/`:86`/`:91` (`200`, `72`,
    `720`), `dunning_staff_actions.py:23` (`MAX_SELECTED_CASES = 100`), plus the
    fixed notice copy at `_core.py:1803-1805` and `:1879-1880` — where the
    subject string is also the dedupe join key.
12. **Dead code that reads as policy.** `"enforcement_health_blocked"` remains in
    `_NON_ADVANCING_DUNNING_OUTCOMES` (`_core.py:1953`) with nothing able to
    produce it; `DunningWorkflow.resolve_cases_for_account` (`:2824-2857`) has no
    caller; `_throttle_account`/`_restore_throttle` have no production caller
    outside `_core.py`. Add `app/services/collections.py` (61 L), which is
    tracked in git and **unimportable** because the package of the same name
    shadows it — a re-export shim no interpreter has ever loaded.

The five below are new in the 2026-08-15 pass. They are the ones ADR-0030 § 1
makes definitional, so they are not extra polish — a version one without them
does not implement the row.

13. **A case has no exposure membership, and no database identity.**
    `DunningCase` (`app/models/collections.py:54-92`) is `id`, `account_id` (DB
    column `subscriber_id`), `policy_set_id`, `status`, `current_step`,
    `started_at`, `resolved_at`, `notes`, timestamps — **no currency, no reason,
    no receivable set, no subscription**. There is **no unique constraint**
    enforcing one open case per account:
    `alembic/versions/squashed_schema.sql:7506-7510` shows only
    `dunning_cases_pkey PRIMARY KEY (id)`, and the invariant is application-only
    (`_core.py:2597-2616` picks the newest open/paused case with `setdefault`).
    Membership is **recomputed every run** from a fresh invoice query
    (`:2546-2560`), and only the *oldest* invoice is stamped onto the action log
    (`oldest_invoice = min(...)`, `:2707-2710`, passed at `:2741`). The
    consequence is quotable — `resolve_cases_for_account` (`:2824-2857`) closes
    **every** open case for an account on any payment, with no check that the
    paid invoice is the one that opened the case:

    ```python
        cases = (
            db.query(DunningCase)
            .filter(DunningCase.account_id == account_id)
            .filter(DunningCase.status == DunningCaseStatus.open)
            .all()
        )
    ```

    The shadow `CollectionsCase` (`app/models/collections_case.py:56-124`) fixes
    most of the identity problem — `(account_id, subscription_id, reason)` with a
    **partial unique index** `uq_collections_case_live` (`:62-70`,
    `WHERE state != 'closed'`), plus `currency` (`:86`) and `authority` (`:87`) —
    but records **one** `source_kind`/`source_id` pair (`:96-97`), not a set. So
    neither model can answer "which receivables does this case cover", and
    ADR-0030's *recorded not recomputed* is unimplemented in both.

14. **The refusal contract is asymmetric: collections' refusals are receipts, the
    owner's refusal is an exception.** `FinancialAccessConsequence` is written
    **unconditionally**, refusal path included — `_core.py:938-978` sets
    `eligible=preview.eligible` and an `outcome` string before `db.add` at
    `:978`, with evidence rows and an audit event at `:1002`. Its columns are
    `id`, `account_id` (DB `subscriber_id`, FK RESTRICT), `dunning_case_id`,
    `action`, `requested_reason`, `access_mode`, `origin`, **`eligible`**,
    **`outcome`**, `preview_fingerprint`, `idempotency_key` (unique),
    `decision_inputs` JSON, `result` JSON, `created_at`; the evidence table
    (`:197-264`) carries a `ck_financial_access_evidence_exactly_one_target`
    CHECK plus three per-target uniques. The refusal vocabulary persisted with
    `eligible=False` is 14 values: `account_not_found`, `account_canceled`,
    `dedicated_bundle`, `billing_profile_invalid`, `balance_cleared`,
    `prepaid_coverage_unresolved`, `prepaid_renewal_terms_unresolved`,
    `prepaid_balance_available`, `shielded`, `notice_grace_active`,
    `outside_enforcement_window`, `throttle_failed`,
    `no_credentials_to_throttle`, `no_eligible_subscriptions`. **Port this
    verbatim** — it is the best thing in the stack and it is exactly ADR-0030's
    "a refused outcome is a first-class, persisted result, not an error". The
    defect is the other side: `account_lifecycle.suspend_subscription`
    (`app/services/account_lifecycle.py:310-321`, documented `Raises: ValueError`
    at `:345-346`, raise at `:352-353`) is invoked at `_core.py:869-875` —
    **before** the consequence row is constructed at `:947`. An owner-side
    refusal therefore produces **no row at all**; the exception propagates and
    the transaction unwinds. For a module whose entire contract is "typed
    request, receipted outcome", closing that asymmetry is version-one work.

15. **Three notice mechanisms coexist, and one of them is a send.** Postpaid path
    1 is a request — `_create_suspension_warning_notification`
    (`_core.py:1811-1836`) emits `EventType.subscription_suspension_warning` at
    `:1819` so notification policy owns delivery. Postpaid path 2 is a **send**:
    `_create_throttle_notification` (`:1780-1808`) and
    `_create_suspension_notification` (`:1839-1883`) call
    `notifications_svc.queue_customer_notification` at `:1795`/`:1867` with the
    English subject and body composed **inline in collections**, and dedupe by
    querying `Notification` rows matching the literal subject —
    `.filter(Notification.subject == "Account Suspended")` (`:1857-1865`).
    Prepaid is already right: `prepaid_balance_sweep.py:119-155` submits a typed
    `CommunicationIntent` and consumes a typed `PrepaidNoticeOutcome` (`:67-97`,
    consumed `:286-303`). The prepaid shape ports; postpaid path 2 does not.

16. **No tenant column, no RLS, nowhere.** Sub is single-tenant per deployment
    for all customer/business data, and that must not be mistaken for a porting
    detail. No collections model carries `tenant_id`
    (`app/models/collections.py`, `collections_case.py`, `durable_timer.py`);
    `tenant_id` appears in only four model files repo-wide (`auth.py`, `rbac.py`,
    `domain_settings.py`, `domain_setting_history.py` — staff/operator auth and
    settings, inherited from the kernel). The migrations agree:
    `squashed_schema.sql:3194-3222` creates `dunning_action_logs` and
    `dunning_cases` with no tenant column;
    `433_durable_timers_collections_cases.py:60-140` and
    `299_financial_access_consequence_evidence.py:63,112` likewise.
    `grep -ci 'ROW LEVEL SECURITY' alembic/` is **zero** across the whole
    directory, and two migration tests assert RLS is *absent*
    (`test_credential_party_binding_migration.py:31-32`,
    `test_roles_r1_migration.py:33-34`). The only `app.current_tenant` use is
    `app/services/operator_tenant.py:85`, a transaction-local GUC for the staff
    plane. **The module's tenant plane is new design with zero source evidence.**

17. **The published surface has to be designed from the measured import list.**
    Beyond § 5.4's keys, seven production modules import the private `_core` and
    one imports the private `_bulk_dunning_shield_reasons` (§ 2.4). A module that
    publishes only the four preview/confirm functions leaves those consumers with
    nothing to call.

---

## 6. The billing-correction rule, and the 2026-08-05 claim

### 6.1 The standing Dotmac rule this module must not break

A billing correction is **entity-scoped**; unaffected enforcement and service
work continues; and the owning correction reaches a **terminal outcome within one
non-resettable billing cycle**.

Sub honours the shape in three places and breaks it in one:

- **Honoured — entity-scoped refusal with a work item.**
  `prepaid_policy.py:80-93` raises
  `collections.prepaid_policy.opening_source_incomplete` for one account, with
  `details["work_item_fingerprint"] = f"prepaid-funding:opening-debt:{account_id}"`,
  rather than manufacturing an adverse decision from post-cutover facts. The
  fingerprint is what makes the correction addressable and non-duplicating.
- **Honoured — a non-resettable deadline.**
  `prepaid_balance_sweep.py:181-204` opens a `Finding` with fingerprint
  `prepaid-enforcement:no-contact-route:<id>`, `details["owner"] ==
  "support-collections"` and an `sla_due_at`; `scheduled.py:86` sets
  `_QUARANTINE_SLA_HOURS = 72`. `tests/test_prepaid_notice_progression.py:115`
  proves the work item resolves when the account leaves the cohort.
- **Honoured — unaffected work continues (prepaid).** Per-account commit and
  per-account error isolation (`prepaid_balance_sweep.py:658-698`) are exactly
  the "one bad row cannot abort the batch" property the rule requires.
- **Broken — unaffected work does not continue (postpaid).** One exception in
  `DunningWorkflow.run` aborts and rolls back the entire daily run
  (`scheduled.py:49-51`), and the run commits once at `_core.py:2813-2814`.
  A single bad account stops the fleet's dunning for a day. **This is the
  clearest single reason the shared module must own the loop.**

### 6.2 The 2026-08-05 billing-health correction — verified, and the claim is wrong in two ways

The brief states that `app/services/collections/_core.py` was corrected on
2026-08-05 so that fleet billing-health observations no longer enter
`FinancialAccessConsequence` preview inputs and fingerprints, and asks that this
be verified in source and preserved. Verified at `27c76aaee`:

**(a) The date is wrong.** No 2026-08-05 commit touches `_core.py`. The last
commit to touch it is `f402315fd` (2026-08-04, "repair paid prepaid invoice
coverage", #1988), whose diff adds only the restoration-participant command and
says nothing about health. The correction is **`83af90635` (2026-07-22, "Make the
customer-financial lifecycle permanent", #1530)**, branch commit `bb19d05c7`,
found by `git log -S "Health is operator-visible evidence"`. The gate it removed
had been introduced a week earlier by `8b08acf2c` (2026-07-15, #1311).

**(b) The substance is half right.** The correction removed the **gate**, not the
**input**. It deleted:

```python
        if eligible:
            health = billing_enforcement_health(db)
            health_reasons = list(health.reasons)
            if not health.ok:
                eligible = False
                outcome = "enforcement_health_blocked"
```

and replaced it with `_core.py:607-611`:

```python
        from app.services.billing_enforcement_guards import billing_enforcement_health

        # Health is operator-visible evidence, not a global customer-lifecycle
        # switch. Current account facts decide the consequence.
        health_reasons = list(billing_enforcement_health(db).reasons)
```

But `health_reasons` is **still** in the preview's `inputs` dict at
`_core.py:714` (`"billing_health_reasons": health_reasons`), and `inputs` is
embedded in `fingerprint_payload` at `:741` and hashed at `:733-742`. It is also
persisted into `FinancialAccessConsequence.decision_inputs` (`:964`, `:1510`).
**A fleet-wide health-reason change therefore still invalidates every outstanding
preview fingerprint and still lands in every consequence row.** The residual
`"enforcement_health_blocked"` string at `:1953` is the fossil that proves the
gate is gone.

**What to preserve, and what to finish.** Preserve the ruling: a fleet-wide
observation is operator evidence and must never be a customer-lifecycle switch
(the same intent is documented at `billing_enforcement_guards.py:1-7` and
`billing_health.py:1-8`). Finish it in the module: a fleet-scoped observation must
not enter a per-entity decision fingerprint either, because a fingerprint that
moves for reasons outside the entity turns every concurrent preview stale and
makes the staleness check fire for a cause the operator cannot act on. The
contracts spec's `state_fingerprint` is defined over entity-scoped fields only
for this reason.

---

## 7. Retirement inventory

Each row names what replaces it, the shadow comparison that must pass before it
is removed, and the ratchet that proves the retirement. Sequencing is the
adoption plan's (S5 full-cohort parity → S6 bounded cohort → S7 retirement); this
table is the *what*, not the *when*.

| # | Retire | Replaced by | Shadow comparison that must pass first | Ratchet |
|---|---|---|---|---|
| R1 | `dunning_runner` (`scheduler_config.py:712-723`) and `DunningWorkflow` (`_core.py:2521-2857`) | policy-driven cases + per-entity timers in the module | per subject/reason/currency/source version: case existence, pinned policy, current/next step, exact next-action time, requested consequence + idempotency identity, close/reopen after settlement — over one complete production ladder window per active policy | name removed from `tests/architecture/billing_scheduled_sweep_baseline.txt` in the same change (§ 7.1) |
| R2 | `prepaid_balance_sweep` (`scheduler_config.py:736-745`, `prepaid_balance_sweep.py`) | the same single lifecycle, driven by `advance`-timing policy data | additionally: every typed skip/shield reason, funded-restore outcomes, budget-deferred accounts, and the full candidate cohort per cycle | same baseline file |
| R3 | `PrepaidSweepCycleState` (`app/models/collections.py:267-293`) | nothing — a timer has no cycle, so the cursor has no successor | prove every account that the cursor would have visited in a cycle has a timer or a typed no-timer reason | table drop after the count of rows reaches zero and stays there; its own docstring already marks it `TRANSITIONAL` |
| R4 | `Subscriber.prepaid_low_balance_at`, `.prepaid_deactivation_at` and `prepaid_enforcement_state.py` | one `DurableTimer` per `(owner, entity, purpose)` with a generation | for every account with a non-null column, exactly one module timer with the same due instant, and clearing the column ⇔ cancelling the timer | two-directional count ratchet over non-null occurrences of both columns |
| R5 | Direct credential writes at `_core.py:919-920` and `:1475-1476` | a typed consequence request whose owner is the access-lifecycle service | the owner's receipt matches the previously-written credential state for every throttle/un-throttle in the cohort | count ratchet over assignments to `radius_profile_id` / `pre_throttle_radius_profile_id` outside `account_lifecycle` |
| R6 | `Invoices.mark_overdue_system(...)` called from the dunning scan (`_core.py:2561-2567`) and `apply_prepaid_overlap_hold` (`:2552-2554`) | billing owns invoice lifecycle; collections reads a position and writes nothing. The invoice owner's `overdue_checker` (`scheduler_config.py:690-696`) already exists, so this removes a duplicate path | prove no invoice changes status as a side effect of a collections run, and that overdue-ness is derived from the position instead | count ratchet over collections-module calls that transition `Invoice.*` |
| R7 | `_throttle_account` / `_restore_throttle` (`_core.py:1637-1777`), `DunningWorkflow.resolve_cases_for_account` (`:2824-2857`), `"enforcement_health_blocked"` (`:1953`) | nothing — dead | none needed; confirm zero callers at the retirement commit | included in R5's count ratchet; the dead outcome string is a one-line delete |
| R8 | Local `dunning_cases`, `dunning_action_logs`, and the shadow `collections_cases` **writers**; local `policy_dunning_steps` and the collections reading of mutable `PolicySet` fields | module-owned policy versions, cases and append-only step attempts | full-cohort parity per R1/R2 plus a total row-disposition classifier with no default bucket | two-directional count ratchet over imports of the local models outside an archive reader |
| R9 | Notice literals and the subject-string dedupe (`_core.py:1780-1883`) | policy-declared `template_id` + `channel_preference`, resolved through `dotmac_kernel.channel_policy`, with consent via `dotmac_kernel.consent` and a derived idempotency key | for each cohort notice: same recipient set, same channel decision, same suppression outcome, and a dedupe decision that no longer depends on a subject string | count ratchet over string literals used as notification subjects in the collections tree |
| R10 | The shadow stack itself — `collections/lifecycle.py`, `postpaid_policy.py`, `prepaid_policy.py`, `collections_cases` writers | the module's single engine | its tests are ported (§ 8) and pass against the module | delete at cutover; a product-local copy of the extracted engine is itself a ratchet entry (adoption plan S7) |
| R11 | Collections serving a receivable answer: `has_overdue_balance` consumed by `app/services/service_status.py:41-42` and `app/services/stale_overdue_lock_reconcile.py:35` | the billing owner's published position, read through the assembly's `ReceivablesReader` binding — collections holds no field a caller can read as a balance (§ 14) | both consumers return the same answer for the full cohort, per currency, when re-pointed at the billing position | count ratchet over imports of any receivable-answering symbol from the collections tree |
| R12 | The unimportable `app/services/collections.py` shim (61 L) and the seven production imports of the private `_core`, including `prepaid_enforcement_planner.py:37`'s import of `_bulk_dunning_shield_reasons` | the module's published surface, designed from the measured import list in § 2.4 | every current private-`_core` consumer compiles and behaves identically against the published surface | delete the shim outright; count ratchet over `from app.services.collections import _core` and any `_`-prefixed import from the package |

R11 and R12 are new in this pass. `commercial-retirement-ledger.md:14` cites this
section as "§ 7 (R1-R10)" and now owes two rows; that ledger is not edited here.

### 7.1 The existing ratchet, and what ADR-0018 still requires

Sub already carries a scheduled-sweep baseline:
`tests/architecture/billing_scheduled_sweep_baseline.txt`, 12 names including
`dunning_runner` and `prepaid_balance_sweep`, enforced by
`tests/architecture/test_billing_target_architecture.py::
test_no_new_scheduled_financial_sweep` (`:117-132`). Its header is exactly the
right posture: *"This is migration debt, not permission."*

Measured against ADR-0018:

- **Two-directional: yes.** `added` fails on a new sweep (`:121-127`) and
  `retired` fails when a name stops being scanned without being deleted from the
  file in the same change (`:128-132`). The sibling money/metadata baselines use
  the stricter count form (`_assert_count_baseline`, `:41-73`), which fails when
  a count rises **or** falls without the baseline being lowered.
- **Grandfathered vs reviewed: yes**, structurally — the baseline file is the
  known-wrong list and there is no per-line "this is fine" marker. The same
  posture appears in `tests/architecture/test_financial_ownership.py:109-113`,
  whose allowlists are documented as *"existing debt; they are not authorization
  for new writers and should only shrink"*, and in
  `tests/architecture/sot_writer_baseline.txt:26`, which still lists
  `app.services.collections.prepaid_balance_sweep` as an **undeclared writer**.
- **Sensitivity proof: NOT PRESENT.** No test in that file proves the detector
  still fires. A clean run is currently indistinguishable from
  `scheduled_sweep_names()` returning an empty set. Under ADR-0018 § 5 and
  AGENTS.md rule 25 the module's ports of these ratchets must add one, and the
  new ratchets in § 7 (R4, R5, R6, R8, R9, R11, R12) must ship with theirs.
- **Entry-point families, not directories:** the sweep baseline is keyed on
  scheduled-task names, which covers the Celery beat family only. The direct-write
  ratchets (R5, R6) must scan services, tasks, scripts, CLI and web handlers
  together, since `_throttle_account` reached production behaviour through a web
  helper before it was retired.

---

## 8. Tests available to port

Every path below was confirmed to exist at `27c76aaee`. The behaviour column is
what the test proves, not what it is named. Read § 8.4 first — it states the lane
these tests run in, and therefore what they may be cited for.

### 8.1 The adoption plan's S1 set — all present

| Test | Lines | Proves |
|---|---|---|
| `tests/test_collections_target_lifecycle.py` | 324 | exact-overdue and underfunded proposals with exact `Decimal`; partial settlement arithmetic; prepaid ignores receivables; **fail-closed on an incomplete opening source with a work-item fingerprint**; each step replaces the exact next-action timer (generation 2, one superseded); close cancels timers and stages restore evidence; terminal replay returns the same consequence id |
| `tests/test_collections_dunning_services.py` | 1,471 | the live postpaid ladder end to end: notice-runway gates, day-0 policies using real overdue age, arrangement and prepaid-credit shields, reconciliation holds, non-collectible residuals, paused cases never escalating, payment resolving open-but-not-paused cases, credit settled before dunning in any currency, dedupe-window honouring |
| `tests/test_collections_services.py` | 136 | CRUD + the imported-deposit exclusions (`Decimal("-87500.00")`, `Decimal("0.00")`, native `Decimal("500.00")`) |
| `tests/test_payment_arrangements.py` | 980 | arrangement lifecycle — create/approve/record/overdue/default, automatic progression from payments, cancel restrictions (`test_payment_on_defaulted_arrangement_does_not_complete_it`) |
| `tests/test_payment_arrangement_safe_actions.py` | 187 | arrangement preview/fingerprint/confirmation |
| `tests/test_dunning_staff_safe_actions.py` | 271 | exact eligible/skipped membership; explicit confirmation required; atomic transition+log+audit; audit failure rolls back **every** selected transition; stale preview rejection; close gated on canonical per-currency receivables `(("NGN", Decimal("250.00")),)` |
| `tests/test_durable_timers.py` | 267 | scheduling outside an owner command fails closed; reschedule supersedes and bumps generation; cancel is idempotent; firing emits only the declared trigger; a superseded timer never fires; the due scan is bounded — **this is the Timer contract suite, and belongs to P3, not to collections** |
| `tests/test_prepaid_enforcement_planner.py` | 407 | the prepaid ladder as a pure plan: mode-change lock repair, terminal-service stale locks, drift reporting without mutation, future-anchor blocking, materialized funding owner, zero-grace suspending even when the notice is fault-suppressed, non-zero grace not starting until the warning is queued |
| `tests/test_financial_access_consequence_evidence.py` | 412 | preview→confirm creates exactly one lock + one evidence row + one audit event and links the action log; **replay with the same key returns the same consequence with no second lock**; stale preview → 409 with zero side effects; an *ineligible* consequence is still durably recorded; restore emits exactly `{lock_resolved, credential_restored, dunning_case_resolved}`; restore never guesses a missing pre-throttle profile |

### 8.2 Additional suites the parity work needs

`tests/test_prepaid_balance_sweep.py` (969) — 29–30 named behaviours including
zero-grace immediate suspension, grace timers not resetting on rerun, weekend as
an ordinary enforcement day, arrangement and payment-proof shields, stale-timer
repair, and `test_suspend_blocked_leaves_timer_unarmed_and_retries`.
`tests/test_events_enforcement_services.py` (2,520) — the event-handler side
produces exactly one access consequence plus one staged notification per
enforcement event. `tests/test_prepaid_threshold_resolver.py` (467) — threshold
provenance, currency-mismatch rejection, fail-closed missing price, batch cost
independent of account count. `tests/test_grace_policy_sot.py` (183) —
precedence account→policy→mode default, offsets beginning after grace end,
explicit zero grace preserved, invalid settings failing closed with stable typed
codes. `tests/test_prepaid_notice_progression.py` (165) — no-contact-route arming
the timer while opening an SLA'd work item, phone-only customers warned over
non-email channels, a notice shield expiring after max hours.
`tests/test_notification_queue_suppression.py` (127) — the marketing/transactional
scope rule proven at the transport (an unsubscribed address still gets its
invoice; a hard bounce stops even the invoice).
`tests/test_prepaid_sweep_budget.py` (158), `tests/test_enforcement_window.py`
(89), `tests/test_enforcement_window_gate.py` (69),
`tests/test_prepaid_enforcement_state_owner.py` (72),
`tests/test_prepaid_flag_clear_on_restore.py` (34),
`tests/test_financial_access_restore.py` (191),
`tests/test_walled_garden_policy.py` (276),
`tests/test_walled_account_healing.py` (377).

### 8.3 Behaviours with NO adequate source test

These are the areas where porting proves nothing, because there is nothing to
port. Each needs a new test written against the module.

| Behaviour | Status in Sub |
|---|---|
| A case pinned to the policy version that opened it | **No test and no mechanism.** `policy_version` does not exist anywhere in the collections tree (§ 5.1) |
| Exact receivable membership on a case | **No test and no mechanism** in either model (§ 5.13) |
| Cancelling/replacing a specific pending timer on settlement | Proven only in the **dead** shadow stack (`test_collections_target_lifecycle.py:225`, `:257`). The live paths have no timer identity to cancel |
| A failed consequence being durable and retryable | **No test; the code swallows** (§ 5.6), and `test_collections_dunning_services.py:1313` asserts the swallow is correct. The nearest coverage is event-level (`test_events_enforcement_services.py:1773`) or achieved by *not writing state* (`test_prepaid_balance_sweep.py:938`) |
| An owner-side refusal producing a persisted outcome | **No test, and the code raises** (§ 5.14) |
| Consent honoured before a dunning/prepaid notice | **Partial.** Proven at the queue runner (`test_notification_queue_suppression.py`), never at the collections decision. No test drives a dunning or prepaid notice through a consent-suppressed contact — and § 5.8 shows the sweep would treat it as a transport failure |
| No account-wide consequence for a scoped debt | **No test, and the live code is account-wide** (§ 5.7). Not even the shadow stack asserts "debt on service A leaves service B online" |
| Exact money as a rule | Held in practice (`Decimal` throughout; the only `float()` calls are metric counters at `scheduled.py:522-533`) but **no architecture test forbids float in money paths**, and no rounding/repr test exists. Currency mixing is rejected only in the threshold resolver (`test_prepaid_threshold_resolver.py:308`) |
| The same scenario under `advance` and `arrears` | **No parity test exists.** The two shadow planners are tested for mutual *exclusion* (`test_collections_target_lifecycle.py:154`) — the opposite of parity |
| The target stack causing a real consequence | Impossible today: `collections.consequence_requested` and `collections.case_action_due` have producers and **no consumer** |

### 8.4 What the suite proves, and what it structurally cannot (2026-08-15)

The counts above are real — roughly 13,000 lines across ~85 files, plus 18
architecture files pinning single-writer rules by source inspection. What was not
measured before is the **lane** those tests run in, and it changes what the suite
may be cited for.

`tests/conftest.py:298-320` has one session-scoped `engine` fixture that branches
on `TEST_DATABASE_URL`: set → a real migrated PostgreSQL
(`require_migrated_schema`); unset → `sqlite+pysqlite://` in-memory with
`Base.metadata.create_all`. Its own comment at `:313-314` is the honest
description: *"Fast non-authoritative unit lane. SQLite/model metadata is useful
for service logic but is never integration or deployed-schema evidence."* There
is no `db` fixture, no `@pytest.mark.postgres` and no testcontainers anywhere.

Consequences, stated plainly:

- **Every** behavioural collections/dunning/arrangement/consequence test above is
  dual-lane and runs on SQLite by default. None is pinned to PostgreSQL. The only
  unconditionally-PG tests live under `tests/integration/` (gated by
  `tests/integration/conftest.py:25`), and the only collections-adjacent one there
  concerns bank *collection accounts*, not dunning.
- **No test anywhere proves RLS or cross-tenant isolation on a collections
  table.** Searched: `ROW LEVEL SECURITY`, `RLS`, `set_config`,
  `app.current_tenant`, `current_setting`, and
  `dunning_case|collections_case|financial_access_consequence|dunning_action_log`
  under `tests/integration/`. The only tenancy hits are
  `tests/integration/test_operator_tenant_transaction_scope.py` (55 L, proves the
  GUC is transaction-local and names no table) and
  `tests/test_operator_tenant.py:130` (a string assertion on a compiled
  statement). The single `tests/integration/` mention of a collections table is a
  table-existence entry, `test_migrations_423_to_head.py:35`.
- Genuinely mock-only, no database at all: `test_enforcement_gaps.py`,
  `test_enforcement_event_policy.py`, `test_enforcement_terminal_polling.py`,
  `test_enforcement_window_gate.py`, `test_fup_enforcement_hardening.py`,
  `test_enforcement_reconciler_scheduled.py`.

So the suite is a **behaviour** source of unusual depth and **not** isolation,
concurrency or deployed-schema evidence. Every item in § 16 has to be written
fresh against PostgreSQL.

The architecture tests are worth naming individually, because they are the part
of Sub's discipline the module must inherit rather than re-derive:

| Path | Lines | What it pins |
|---|---|---|
| `tests/architecture/test_financial_ownership.py` | 652 | the sole-writer allowlists — `:109-111` `APPROVED_FINANCIAL_ACCESS_CONSEQUENCE_WRITERS = {Path("app/services/collections/_core.py")}`, `:113` the arrangement equivalent, `test_only_collections_owner_constructs_financial_access_evidence:412` |
| `tests/architecture/test_financial_action_boundaries.py` | 542 | `:503-508` collections consumes arrangement shields through functions and never imports the model; `:511-542` forces the four preview/confirm functions into `_core.py` and asserts `"restore_account_services("` and `"has_overdue_balance("` are absent from billing automation |
| `tests/architecture/test_permanent_customer_financial_lifecycle.py` | 82 | `run_billing_enforcement` and `prepaid_balance_sweep` are permanent and non-toggleable; `:48` allows only `enforcement_window_start/_end` to survive |
| `tests/architecture/test_prepaid_enforcement_state_boundary.py` | 67 | `TIMER_FIELDS = {"prepaid_low_balance_at","prepaid_deactivation_at"}` → one writer, no adapter calls |
| `tests/architecture/test_grace_policy_boundary.py` | 61 | one typed read-only grace owner; asserts the `invalid_grace_days` code literal |
| Also | | `test_grace_walled_garden_ownership.py` (69), `test_prepaid_enforcement_policy_ownership.py` (84), `test_prepaid_draft_reconciliation_ownership.py` (169, `:95` pins the `_for_owner` call), `test_action_form_ownership.py` (181, `:144` dunning templates render only projected safe actions), `test_thin_financial_tasks.py`, `test_billing_target_architecture.py`, and seven `test_prepaid_*_ownership/boundary.py` files |

---

## 9. ERP, vendor CP and CRM — the 2026-08-15 exclusion and parallel-authority audit

The 2026-08-14 pass recorded ERP and vendor CP as exclusions in three sentences.
ADR-0030 § 7 makes all three dispositions load-bearing — vendor CP adopts a
platform plane, ERP adopts nothing and keeps GL authority, CRM's parallel
commercial writers retire — so each was re-audited properly. CRM was not audited
at all before, and it holds the one genuine parallel delinquency classifier in
the fleet.

### 9.1 ERP (`0f4b1698ddbf`) — a requirements and negative-test source, not a port source

**Ruling: (b) requirements / negative-test source only.** There is no delinquency
case, no versioned grace/escalation policy, no payment arrangement, no
notification intent and no consequence request anywhere in the tracked tree.

Searched (case-insensitive, whole tracked tree, revision-pinned): `dunning`,
`delinquen`, `collection`, `overdue`, `arrears`, `ag(e)?ing`, `aging_bucket`,
`past.due`, `credit.hold`, `credit.limit`, `grace.period`, `chase`, `write.off`,
`bad.debt`, `doubtful`, `allowance`, `suspend|suspension`, `promise.to.pay|PTP`,
`instal?lment|payment.plan|payment_arrangement`,
`expected_credit_loss|ECL|impairment|provision_matrix`. **Zero hits** for
`delinquen`, `doubtful`, `allowance for doubtful`, `promise to pay`,
`payment_arrangement`, `payment plan`. `chase` matched only inside `purchase`;
`arrears` only IFRS 16 lease annuity timing
(`app/services/finance/lease/lease_calculation.py:88`). All ten `dunning` hits
are prose — including `ARCHITECTURE_REVIEW.md:88`, which lists
"❌ Dunning letter automation" as **missing**.

What `reminder_service.py` actually is: 833 lines covering **five** unrelated
concerns (fiscal close `:80-186`, tax filing `:192-438`, bank reconciliation
`:444-632`, AR `:638-752`, subledger discrepancy `:758-807`). The AR part is
~115 lines and three methods — `get_overdue_invoices:638`,
`get_invoice_aging_bucket:676` (a **fifth**, private bucket scheme, different
from `aging_helper.py`'s `AGING_BUCKETS`), `send_collection_reminder:697`. It
writes **one `notification` row per recipient and nothing else**; recipients are
*internal staff* resolved from roles
`accountant|ar_clerk|finance_manager|collections` (`app/tasks/finance.py:463`,
`:1113-1118`) for which **no seed exists in `alembic/`** — so in a deployment
without those roles the task does nothing (`:1120-1121` `continue`). Severity is
recomputed from `days_overdue` on every run with nothing recording the last tier
reached, so it **cannot escalate monotonically**. It is
`NotificationChannel.IN_APP` (`:746`, comment *"Don't email for every invoice"*)
and there is no AR email template in `templates/emails/`. There is **no case, no
state machine, no transition, no history** — the only persistence is a 7-day
dedupe window (`:725-732`, added against a "3.2M+ rows" incident). There is **no
test at all** for `send_collection_reminder`, `get_overdue_invoices`,
`get_invoice_aging_bucket` or `process_ar_collection_reminders`.

Ageing is a **report plus inert audit evidence**, not an authority.
`aging_helper.py:25-30` defines the buckets, `ar/ar_aging.py` (518 L) computes
them on the fly, `api/finance/ar_routes/aging.py:17-30` serves them.
`ar.ar_aging_snapshot` exists — docstring line 3, *"Point-in-time aging for audit
evidence"* — written only by `create_aging_snapshot` (`ar_aging.py:328-379`) from
fiscal-period soft close (`gl/fiscal_period.py:268-270`), and **read by exactly
one filtered list query**. No decision, guard, task or writer reads it.

Credit control is the cautionary evidence. `ar.customer.credit_limit`,
`credit_terms_days` and `credit_hold` exist
(`app/models/finance/ar/customer.py:93-95`), and **`credit_hold` has no reader
anywhere in `app/`** — all ten hits are writes from a form, an export or a
template. `check_credit_limit` (`customer.py:579-630`) has **no production
caller**, only three tests. `RiskCategory` is hardcoded `MEDIUM` on every
creation path. ERP's own review agrees: `ARCHITECTURE_REVIEW.md:87` "❌ Credit
limit enforcement (model exists but service logic incomplete)". *(`:84` claims
"✅ Collection management dashboard"; no such route or template exists — treat
that line as unsubstantiated.)* This is precisely the "declared but unenforced"
failure a consequence-request contract must make impossible.

Bad debt: `ar.expected_credit_loss` is created by
`alembic/versions/create_ifrs_schemas.py:1666-1697` and has **no SQLAlchemy model
and no service** — a dead table. `payment_allocation.write_off_amount` exists and
its only writer hardcodes `Decimal("0")` (`exact_match_allocation.py:197`). Real
impairment code ERP does own and keeps: IAS 36 fixed-asset impairment, goodwill
impairment, VAT bad-debt-relief flags on AR invoice lines, WHT write-off tax
reversal.

Tenancy: ERP uses `organization_id` exclusively — `git grep -c tenant_id --
app/models/` returns nothing across 240 model files — with a real FK to
`core_org.organization` and per-schema tables. `ar.ar_aging_snapshot` carries
`organization_id` but omits it from `uq_ar_aging`
(`ar_aging_snapshot.py:33-39`), which the starter's hard rule 11 would reject.

**The one parallel authority, and it is real.** `get_overdue_invoices`
(`reminder_service.py:638`) filters on status, due date and balance and **nothing
else** — it does not exclude
`source_document_type IN ('dotmac_sub_invoice','dotmac_sub_credit_note')`, the
marker ERP itself sets at `app/services/dotmac_sub/sync/_invoices.py:298` and
filters on at `:623`. Once ISP subscriber invoices are mirrored into
`ar.invoice`, the daily 08:00 master task (`app/tasks/finance.py:1246-1247`,
`:1277`; the AR task's own beat entry is seeded `enabled: False`) independently
classifies them into private ageing buckets and raises its own severity tiers, in
parallel with whatever the shared module decides. The blast radius is small today
— in-app only, probably-unseeded roles — but **the query is the authority claim,
not the channel.** Two latent items to fence: `ARInvoiceService.mark_overdue`
(`ar/invoice.py:1427-1465`) is an uncalled bulk status writer that `db.commit()`s,
and `apply_payment_status` plus its repairer (`app/tasks/data_health.py:217-252`)
write `InvoiceStatus.OVERDUE` on Sub-mirrored invoices too.

**The boundary to write down:** ERP owns the GL consequence of a receivable —
recognition, ageing as a report and as period-close audit evidence,
ECL/impairment provisioning, bad-debt write-off posting, and the tax treatment of
a write-off. ERP does not own the delinquency case, the grace/escalation policy,
the arrangement, or any consequence request. Collections decides; ERP posts what
the decision implies. Two enforceable clauses follow: the AR reminder methods and
`process_ar_collection_reminders` must exclude
`source_document_type LIKE 'dotmac_sub_%'` or be retired outright, and
`ar.customer.credit_hold` must not gain an enforcement reader in ERP — a hold is
a collections consequence, and ERP's column becomes a read-only projection of it.

ERP already demonstrates the right boundary in a test:
`tests/services/test_dotmac_sub_money_boundary.py:800-801` parses Sub's
`"awaiting_dunning_review"` invoice status and asserts it passes through
untouched. ERP treats another owner's dunning state as opaque data. That is the
negative test to preserve.

One reusable primitive worth naming and **not** porting:
`app/services/finance/coverage.py` (`coverage_of`/`coverage_case`), whose
docstring at `:29,120` already names ageing and dunning as its downstream
consumers, with a real-PostgreSQL parity test
(`tests/integration/test_coverage_parity.py`, 99 L) asserting the Python and SQL
rules agree. A collections module consumes a coverage answer; it never re-derives
"is this unpaid".

### 9.2 Vendor CP (`89848017d6b8`) — the platform-plane adopter, re-audited

**Ruling: adopter, not a source.** Zero hits for `delinquen`, `dunning`,
`arrears`, `overdue`, `past due`, `unpaid`, `invoice`, `billing`, `charge`,
`refund`, `credit note`, `lapse`. `collection*` matches only
`from collections.abc import Mapping`. `payment` appears exactly twice, both as
**prohibitions**: `docs/design/domain-foundation.md:109` puts *"a side effect of
a payment webhook"* in the NOT column for allocation/licence lifecycle, and
`:103` says contract lifecycle *"must not become … a status field written by a
billing webhook"*. The adoption plan's demand gate is therefore still unmet at a
**later** revision than the one originally measured, and its V0 instruction to
re-audit at the moment of demand has now been executed once.

`src/vendor_cp/` is eight in-repo `FeatureManifest` packages (`accounts`,
`offers`, `approvals`, `contracts`, `allocations`, `licensing`, `provisioning`,
`console`). At the pinned revision it pins `dotmac-kernel 0.1.0a46` plus
`dotmac-release-catalog 0.1.0a1` and `dotmac-entitlement-allocation 0.1.0a1`
(`pyproject.toml:18-33`), composed at `src/vendor_cp/assembly.py:12-13,46-52`
with lineages added in `src/vendor_cp/migrations.py:29-75`.

**What ADR-0030 § 7 calls its retained "consequence execution" is, precisely:**

| Site | What it does | Kind |
|---|---|---|
| `contracts/service.py:432` `suspend` | `active → suspended` + audit + `contract.suspended` outbox event; docstring: *"Projects to allocation RESTRICTION only (never data deletion) — the projection is owned by Allocation/data-plane, not here"* | decision |
| `contracts/service.py:446` `reinstate`, `:459` `terminate` (refuses without `impact_acknowledged` + `effective_date`, `:466-469`) | state + event | decision, with a refusal gate |
| `contracts/service.py:491` `expire` | guarded on `as_of > term_end` — and has **zero callers**: no route, no scheduler, no test | decision, unreachable |
| `licensing/revocation.py:91` `revoke_licence` | appends an immutable `LicenceRevocationEntry` with a mandatory reason | decision |
| `licensing/revocation.py:157` `publish_revocation_list` | signs a **cumulative** snapshot at a strictly increasing version and refuses a non-superset (`RevocationListRegressionError`, `:173-182`) | execution of publication |
| `licensing/service.py:187` `_lineage` / `:214` `_is_revoked` | refuses to reissue into a revoked lineage; mints a new generation | execution over its own resource |
| `licensing/projection.py:477` `ingest_acknowledgement` | seven-step revalidate-or-refuse, append-only record for **every** verdict, typed `AckOutcome`; dispositions `unknown_licence`, `unknown_digest`, `unverified_identity`, `deployment_mismatch`, `rejected_by_receiver`, `stale`, `duplicate`, `accepted` | execution — the closest existing "apply or REFUSE, return a receipt" |
| `accounts/models.py:27` `AccountStatus.SUSPENDED` | declared enum value with **no writer** (`create_account` hardcodes `ACTIVE` at `:96`) | dead vocabulary |

None of it is payment- or delinquency-driven. Revocation and suspension are
platform-admin HTTP actions (`licensing/router.py:140,157`,
`contracts/router.py:115,129,136`).

Two gaps in the receipted-outcome half worth recording, because a collections
module depends on that half: **`ingest_acknowledgement` takes no row lock** — the
only `with_for_update` in the tree is `transport.py:349` (`skip_locked` on the
replay claim), so its check-then-set on `LicenceDeliveryState` (`:654-673`) is
unserialised; and the thing being applied-or-refused lives in the product data
plane, so Vendor CP records the receipt but is not itself the service owner
(`licensing/service.py:21`: *"NEVER writes a product data plane's
`tenant_entitlement_grants`"*).

Approvals gate exactly one thing and never a financial condition: versioned
immutable `ApprovalPolicy`, content-bound `record_approval`, `evaluate()`
counting **distinct** approvers and **failing closed** on a missing policy
(`approvals/service.py:180-210`), consumed only by `contracts.service.approve`
(`:342-355`). `reject` clears the `content_hash` (`:380-382`), so a changed
contract must be re-approved. There is no amount threshold, no balance and no
payment state in `approvals/` or `contracts/`, and
`tests/architecture/test_deny_cases.py:154` forbids plan/mode branching there.

**Planes — what its platform plane needs.** Vendor CP is platform-only: **zero
`tenant_id` columns, zero `ENABLE ROW LEVEL SECURITY`, zero `CREATE POLICY`
across all ten migrations**. Every migration `v001`–`v010` repeats the same
helper:

```python
def _grants(table: str) -> None:
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO platform_api;")
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO app_admin;")
    op.execute(f"REVOKE ALL ON {table} FROM app_user;")
```

and `tests/migration/test_vendor_migration_rehearsals.py:194
test_platform_role_access_and_tenant_role_denial` proves both directions on a
real PostgreSQL — `platform_api` can insert and select, `app_user` gets
`permission denied` across all ten licence tables (`:240-256`), each check on a
fresh connection because *"a permission error aborts the transaction"*. **That
test is the template for § 16.2.** Note its scope: it covers `public.*` only —
**no test at that revision touches `mod_rel` or `mod_ealloc`**, so the two newly
composed module planes are unproven in that repo.

Three concrete requirements for the module's platform plane fall out:

1. **The plane must be DECLARED, and the floor must allow it.** Vendor CP's own
   eight packages are `FeatureManifest`s, which have no `tables`/`platform_tables`
   fields at all, and at kernel `0.1.0a45`/`a46` `ModuleManifest` had only
   `tables` — the two composed modules therefore declare `tables=` and get their
   plane from DDL alone. The starter kernel at `0.1.0a63` **does** have
   `platform_tables` and `platform_requires`
   (`packages/dotmac-kernel/src/dotmac_kernel/modules.py:196-207`, guard at
   `:290`), so ADR-0023's declaration is available today and Vendor CP's kernel
   floor has to move before it can adopt one. Declared-not-inferred is not
   satisfiable at Vendor CP's current pin.
2. **There is no timer, and the one clock-driven transition already written is
   unreachable for exactly that reason.** No Celery, no beat, no cron, no
   scheduler of any kind — `contracts.service.expire()` is clock-guarded and has
   no caller; `pipeline_health()` takes `now` as an injected parameter; the
   replay driver has `FOR UPDATE SKIP LOCKED` and no worker
   (`licensing/ops.py:10-14`: `never_attempted` means *"the replay worker is not
   running"*). Any grace-period expiry on this plane needs a durable-timer owner
   that does not exist there.
3. **There is no consent ledger, no delivery receipt for a person, and no
   notification transport.** Nine matches for
   `consent|notif|email|smtp|sms|webhook|template|comms`, all false positives.
   What exists is a *document* transport (`licensing/transport.py`,
   `LoggingTransport` / `OfflineBundleTransport`, append-only
   `LicenceDeliveryAttempt`, a **closed** `TransportErrorCode` vocabulary at
   `:69-100` so a leaked bearer token cannot become a stored error code).
   `docs/design/domain-foundation.md:119` names a future `SupportConsentService`;
   nothing implements it. This re-confirms § 10's plane gap at a later revision:
   **the only "receipt" concept on that plane is a machine-to-machine
   `LicenceAckRecord` from a deployment.**

Money is exact and provider-free: `Money.of(amount, currency(code))`
(`offers/router.py:15-19,36`), stored as a quantized decimal **string** plus an
ISO-4217 code (`offers/models.py:37-39`), **zero** occurrences of `float`,
`Float`, `Numeric` or `Decimal` in `src/` or `alembic/`, tested by
`test_publish_persists_exact_money_and_capabilities` and
`test_submit_freezes_exact_priced_snapshot`. There is no FX and no tax
jurisdiction. Idempotency and the outbox are used pervasively and correctly —
`process_once_platform` at 12 sites including all eight contract transitions,
`enqueue_platform_event` at 6 — always through `dotmac_kernel.messaging`.

Three shapes here are **prior art for the module, not source code to copy**: the
versioned-policy + content-bound-quorum structure in `approvals/service.py` is
the right skeleton for a versioned grace/escalation policy with an explainable
decision and stable reason codes; `ingest_acknowledgement` → `AckOutcome` is the
right skeleton for a receipted, append-only refusal; and the cumulative-superset
publication rule (`revocation.py:173-182`) encodes a hard-won invariant —
*monotonic versions alone do not prevent silent un-enforcement*.

One thing to resolve before Collections lands next to it: the composed
`dotmac-entitlement-allocation` module duplicates Vendor CP's own in-repo
`allocations` feature — same two table names (`mod_ealloc.allocations` /
`allocation_entries` vs `public.allocations` / `allocation_entries`), same
staging concept — and both model sets are imported into the same
`Base.metadata` at `alembic/env.py:19-20`. Schema separation is not owner
separation; that is two allocation writers to reason about.

### 9.3 CRM (`c64b5aa0f790`) — a parallel delinquency classifier, not a source

New in this pass, and it changes CRM's disposition from "nothing to say" to "a
retirement item". CRM has ~7,000 lines of billing-risk machinery:
`app/services/billing_risk_reports.py` (2,261 L),
`app/web/admin/billing_risk.py` (3,754 L),
`app/services/billing_risk_cache.py` (957 L), over a persisted table
`subscriber_billing_risk_snapshots` (`app/models/subscriber.py:293`, *"Cached
billing-risk report row built from live billing/provider data"*).

That table persists a **delinquency classification**: `risk_segment` (NOT NULL),
`is_high_balance_risk`, `days_past_due`, `days_to_due`,
`days_since_last_payment`, `blocked_for_days`, `blocked_date`, `balance`,
`next_bill_date`, `invoiced_until`. It is refreshed by a scheduled task
`billing_risk_cache_refresh` → `app.tasks.subscribers.refresh_billing_risk_cache`
(`app/services/scheduler_config.py:334-337`, interval floored at 600 s) and it
drives outreach: `create_billing_risk_outreach_campaign`
(`app/services/crm/web_campaigns.py:981`) plus retention engagements, called from
the admin surface at `billing_risk.py:3439,3515`.

Three findings for the dossier:

1. **It is a second delinquency classifier over synchronized data.** CRM
   recomputes `days_past_due` and a `risk_segment` from its own mirrored
   `Subscriber` rows rather than receiving the owner's answer. Under ADR-0024 it
   may hold a *rebuildable projection* — but a projection has a named local
   reader and reconciler and does not invent the classification. The segment
   labels (`active|overdue|due_soon|suspended|churned|pending`,
   `billing_risk_cache.py:19-26`) are its own vocabulary, not the owner's.
2. **Money is annotated `float`.**
   `balance: Mapped[float] = mapped_column(Numeric(14, 2), ...)`, and the same
   for `mrr_total` and `total_paid`. The DB type is exact; the Python contract is
   not. That is the "money is exact, never float" line being crossed in a read
   model.
3. **It is operator-driven outreach, not automated dunning.** No suspension, no
   grace, no case, no escalation ratchet — `blocked_for_days` observes a block
   applied elsewhere. So CRM is **not** a source and implements no consequence;
   it is a customer-experience projection that must be re-pointed at the owner's
   published segmentation when the module ships, per ADR-0030 § 7's "its parallel
   sales-order and commercial writers retire".

### 9.4 Integrator and the starter

`dotmac_integrator` (`d014116e63ad`): zero matches for
`dunning|delinquen|collections_case|overdue|arrears|grace_period` across tracked
Python. It is the thin assembly for `dotmac-integration` and holds transport
evidence only (ADR-0030 § 7).

The starter owns nothing here. No collections package, namespace or lineage
exists; `MIGRATION_OWNER_LEDGER`
(`packages/dotmac-kernel/src/dotmac_kernel/namespaces.py`) holds owners for
application-directory, approvals, assembly, files, imports, integration, kernel,
ticketing, release-catalog and entitlement-allocation — **and no commercial
module at all**. Allocation happens in the package-creation diff, not before.

---

## 10. What the kernel already supplies

Present, released, and to be consumed rather than restated. Restating any of
these in a collections module is a review failure.

| Need | Kernel owner | Note |
|---|---|---|
| At-most-once execution | `idempotency.py` (340 L) + ADR-0014 | Nothing reserved before the effect; the fingerprint is its own column; retention is the product's. Sub's overloaded-column and truncated-fingerprint-in-key patterns (§ 5.4) are unrepresentable here |
| Transactional outbox + relay, idempotent inbox | `messaging/` (~1,120 L across 10 files, incl. `platform.py`, `platform_relay.py`, `platform_worker.py`) | The consequence request travels this, not a bespoke queue. The platform variants exist, which is what lets the platform plane emit without a second engine |
| Consent + suppression | `consent.py` (445 L), `consent_models.py` | Marketing vs transactional scope already decided; transactional-unless-declared |
| Channel policy | `channel_policy.py` (190 L) | A settings document with a typed reader; no table, no service |
| Delivery receipts + the bounce→consent loop | `delivery.py` (341 L), `delivery_models.py` (170 L) | The loop that exists in neither product |
| The one send path | `delivery_providers.py` | See § 10.1 — this is the boundary Collections stops at |
| Exact money | `money.py` (264 L) | `Money`, `Currency`, `ExchangeRate`, `allocate()`; no float |
| Settings resolution | `settings_resolver.py` (1,434 L), ADR-0011/0012 | Policy selection and every threshold |
| Declaration registries | `modules.py`, ADR-0008 | `action_codes` as an open registry, not `FinancialAccessAction` |
| Declared persistence planes | `modules.py` `tables`/`platform_tables` (`:196-207`), `planes.py`, ADR-0023/0028 | Present at kernel `0.1.0a63`. The plane is declared on the manifest and selected by the assembly |
| Namespace + lineage | `namespaces.py`, ADR-0006 D1 | One `mod_<short>` allocation, in the diff that creates the package |

### 10.1 The delivery boundary, from the real kernel API

Collections emits a notification **intent**. It does not render, choose a
channel, call a provider, or record a receipt. The kernel already owns every one
of those, with one call site each, and the API makes the boundary checkable
rather than aspirational:

- **May we contact this address, on this channel, for this category?** —
  `dotmac_kernel.consent.may_send(...)` / `suppression_reason(...)` /
  `filter_eligible(...)`. A suppressed address is a typed outcome, never a
  silent skip.
- **Which channels does this class of message go out on?** —
  `channel_policy.resolve_channels(db, spec, tenant_id=…, event=…, category=…)`,
  most specific first (`events` → `categories` → `default` → the caller's
  fallback). Validation happens on **write** (`validate_policy_document` is the
  spec's validator); a malformed stored document degrades to the fallback rather
  than raising on the send path. Sub's fifth, legacy per-event override is
  documented as deliberately **not ported** (`channel_policy.py:32-36`).
- **The send itself** — `delivery_providers.send(db, tenant_id, *, provider,
  message)`. Its docstring states the sequence: replay a completed dispatch, ask
  consent, call the provider, record the receipt, return the outcome. It returns
  `Sent(receipt=...)` or `Suppressed(reason=...)`, and *"it does not queue or
  retry"* (`messaging` owns that), *"it does not render"* (Template Studio owns
  that) and *"it does not choose a channel"* (`channel_policy` owns that). The
  kernel ships a `DeliveryProvider` **Protocol and no client**.
- **What the provider said** — `delivery.record_receipt(...)` is the only writer,
  idempotent on `(tenant, provider, provider_message_id, status)`, and a
  `bounced`/`complaint` verdict suppresses the address with scope `all` **in the
  same transaction**. The soft-vs-hard bounce judgement is the adapter's, by
  design (`delivery.py:26-36`).

So the module's outbound notice contract ends at a typed intent carrying a
`template_id`, a `category`, a `channel_preference` and a derived idempotency
key. Everything after that is an existing owner. `OutboundMessage` (frozen
dataclass, `dispatch_id`, `channel`, `address`, `body`, **required** `category`,
optional `subject`) shows why: it is *"already rendered, already addressed"* —
Collections holds none of that state, and `category` is required precisely so a
caller cannot accidentally get the marketing rule applied to a dunning notice.
This is what § 5.15 and R9 retire the postpaid path into.

**Two gaps that bear on this module specifically:**

1. **P3 durable timers is missing from the kernel and gap-listed**
   (`billing-sources.md` P3; ADR-0020 A5; ADR-0030 § 4 names
   `dotmac_kernel.durable_timers` as an enabling owner and § 5.1 sequences it
   *before* the business modules). Correction to the 2026-08-14 text: Sub's
   reference implementation is not merely present, it is **production-proven by
   eight other owners** — `access_invitations.py:103,125`,
   `advance_renewal_invoicing.py:39`, `billing/contracts.py:726,971`,
   `billing/unwall_paid_accounts.py:341`,
   `customer_experience_handoffs.py:382`, `sla_assignment.py:188`,
   `support.py:2213`, `team_inbox_commands.py:840`, dispatched by
   `app/tasks/durable_timers.py:24` on the `durable_timer_dispatch_runner`
   schedule (`scheduler_config.py:1943-1949`). Its API is
   `schedule_timer(db, ScheduleTimerCommand, *, context)` / `cancel_timer` /
   `current_timer` / `fire_due_timers`, and `DurableTimer`
   (`app/models/durable_timer.py:48-114`) carries `owner`, `entity_kind`,
   `entity_id`, `purpose`, `generation`, `due_at`, `expected_source_version`,
   `output_event_type`, `status`, with `uq_durable_timer_generation` and a
   partial unique `uq_durable_timer_current WHERE status = 'scheduled'` — **and
   no `tenant_id`**. Collections is the one owner that has *not* cut over.
   Collections declares a port and a fake; **it does not build the facility, and
   it must not invent a second scheduler ledger inside its own schema.**
2. **`consent` and `delivery` are tenant-plane only.** Both models carry
   `tenant_id NOT NULL` (`consent_models.py:117`, `delivery_models.py:133`),
   `delivery_providers.send` takes a required `tenant_id: UUID`, and there is no
   platform variant; `channel_policy.resolve_channels` accepts
   `tenant_id: UUID | None` and degrades to its fallback. A platform-plane
   (Vendor CP) collections notice therefore has no consent ledger and no receipt
   loop — re-confirmed against Vendor CP at `89848017d6b8` in § 9.2, which has no
   consent or notification code of its own either. This is an open question, not
   a thing to work around; the contracts spec § 11.3 recommends deferring it
   behind the demand gate rather than inventing a platform consent ledger before
   a real Vendor notice exists.

---

## 11. Consistency with the parallel adoption plan

`docs/superpowers/plans/2026-08-14-collections-sub-vendor-cp-adoption.md` was
authored concurrently and is not edited by this document. This inventory is its
evidence base and does not restate its sequencing. Two points of difference are
recorded in the contracts spec § 9 (inbound seam shape; outbound contract name);
neither affects anything in this file, and both remain open — the contracts spec
§ 11 lists them as blocking the inbound and outbound contracts respectively. The
one provenance note carried from the 2026-08-14 pass: that plan's evidence
snapshot cites starter `1b1d62b`, while the original measurement was taken at
`5417e51` — one commit later, with no collections-relevant change between them.

`commercial-retirement-ledger.md` § 10.2 records two further cross-owner contract
gaps this module sits on, and they are not resolved by this audit: **G1**,
`prepaid_policy.py:57` reads `period_start` — a service-period field billing's
`ReceivablePositionV1` does not carry, without which prepaid collections loses
its "the service period has not started" guard; and **G2**, billing and
collections have specified **two different `ReceivablePositionV1`s** — different
identity granularity, a differently named third field, and a published-fact
versus synchronous-port transport. G2 is the whole billing→collections seam and
must be settled before any code. **G7** (three vocabularies for one case
lifecycle) is owed a total classifier with no default bucket at cutover stage S0.

---

## 12. Index entry owed, and a working-tree collision

`docs/inventories/README.md` has no row for this file, nor for
`numbering-sources.md` or `cloud-commerce-owner-sources.md`. All three rows must
be added in the change that lands them; the README was deliberately not touched
here.

**Working-tree collision, 2026-08-15.** While this revision was being written, a
concurrent session sharing the `dotmac_starter_mt` checkout moved it from
`docs/adr-0030-cloud-commerce-composition` (`e6ba2022f3d7`) to
`fix/appdir-import-safety` (`1d6a5cd`) and discarded the working-tree edits, which
had to be reapplied. The revision was therefore completed in an isolated git
worktree on the ADR-0030 branch, and the shared checkout was restored to its
committed state so nothing of this work lands in the unrelated
application-directory change. Two consequences for whoever commits this: the file
must be committed from the ADR-0030 branch, and the measurements above were taken
against `e6ba2022f3d7`, not against whatever the shared checkout has since become.

---

## 13. Do not port

Named anti-patterns, each with the site that carries it. The extraction starts
from the corrected shape; none of these is compatibility behaviour to preserve.

**Closed vocabularies that must become open registries (ADR-0008, rule 12).**
`FinancialAccessAction` (`app/models/collections.py:32-37`) is an enum of four —
`action_codes` is a manifest declaration, and a vendor code is added in the same
change as its first real consumer, never predeclared. `CollectionsCaseState`'s
fixed four-state chain and its `_NEXT_STATE` dict (`lifecycle.py:63-67`) cannot
express a five-step or two-step ladder. `DunningCaseStatus`,
`CollectionsCaseState` and the adoption plan's set are three vocabularies for one
concept (retirement ledger G7) and need a total classifier, not a favourite.

**Product vocabulary.** `Subscriber`, `Subscription`, RADIUS profiles,
credentials, walled garden, `AccessRestrictionMode`, throttle-as-a-speed-change,
`prepaid_low_balance_at` — all ISP-shaped. The module's subject is reached
through a product-owned link helper per plane, never a foreign key into a product
table: Sub's `FinancialAccessConsequence.subscriber_id → subscribers.id`
(`app/models/collections.py:146-151`) is exactly the FK a module may not have.
ERP's `organization_id` and its `ar.` schema coupling likewise.

**Provider and transport names.** No PSP, no SMTP/Twilio/Meta, no
`NotificationChannel.email` hardcoded as the only channel
(`_core.py:1875`), no provider branch anywhere. A provider is an Integrator
connector binding.

**Host coupling.** Settings lookups from inside the decision owner; service-level
commits (`_core.py:2814`, `:2958`, `prepaid_balance_sweep.py:658-677`,
`ARInvoiceService.mark_overdue`'s `db.commit()`); FastAPI/product error types; a
Celery task name in the owner; and the two Celery schedules themselves, which
Sub's own architecture test currently pins as permanent
(`test_permanent_customer_financial_lifecycle.py`) — the module ships **no**
scheduler, no `while True`, no cron registration and no periodic scan.

**Second writers.** The four RADIUS credential writes (§ 4 rows 1–3); the
invoice-overdue call from the dunning scan (row 4); the two enforcement-clock
columns on `subscribers` (row 5); ERP's `get_overdue_invoices` sweeping
Sub-mirrored invoices (§ 9.1); CRM's recomputed `risk_segment`/`days_past_due`
(§ 9.3).

**Silent fallbacks and swallowed failures.** `str(receivable["currency"] or
"NGN")` (`_core.py:2170`); the duplicated `minimum_days = 3` fallback beside a
setting that already defaults to 3 (`:1998`/`:2000`); the bare
`except Exception` restore swallow (`:2805-2812`, `:2947-2957`); classifying an
unknown suppression reason as `delivery_unavailable` so an unsubscribe and an
SMTP outage produce the same outcome (`prepaid_balance_sweep.py:151-166`); and
`decide_grace`'s fail-open is correct for *reading* an unarmed clock but must
never become a fail-open for a missing policy — `_grace_days`' loud raise is the
half to keep.

**Second balance calculations.** Anything that lets a caller read "the balance"
off a collections row, including `has_overdue_balance` (§ 2.4, R11), and CRM's
`Mapped[float]` money columns (§ 9.3).

**Dead vocabulary that reads as policy.** `"enforcement_health_blocked"`
(`_core.py:1953`), Vendor CP's writerless `AccountStatus.SUSPENDED`, ERP's
`credit_hold` with no reader and its modelless `ar.expected_credit_loss` table.
A declared-but-unenforced consequence is worse than an absent one, because a
reviewer believes it.

---

## 14. Shared contract — what version one owns, and what it does not

Positive contract (ADR-0030 § 1's Collections row; the adoption plan's ownership
boundary). Version one **owns**:

- **immutable policy versions** — an ordered, arbitrary-length step ladder as
  versioned data, with a fingerprint, pinned to a case at open and never re-read
  live;
- **delinquency cases** — one live case per `(scope, subject, service, reason,
  currency)`, enforced by a database constraint, with pause/resume,
  resolve/reopen and an append-only action trail;
- **exact receivable/exposure membership** — which receivables a case covers,
  **recorded, not recomputed**, so settlement closes the case the payment
  actually belongs to and a scoped debt cannot produce a subject-wide
  consequence;
- **grace** — policy and explicitly granted, with provenance, anchor and expiry;
- **payment arrangements** — proposal, approval/activation, exact exposure
  membership, instalment schedule, fulfillment, default, cancellation, and a
  shield narrowly scoped to the admitted exposures;
- **escalation steps** — each with an exact due instant, a generation and a
  derived idempotency identity;
- **notification intents** — a typed request naming a `template_id`, `category`
  and `channel_preference`, and nothing else;
- **typed consequence requests** — `(request_id, case_id, policy_version_id,
  policy_step_code, step_attempt_ordinal, exposure_ref, source_version,
  action_code, effect_scope)` with a derived idempotency key and a separate
  fingerprint column;
- **service-owner receipts and refused outcomes** — `applied`,
  `refused(reason_code)`, `deferred(retry_after)`, `failed(retryable)`, each a
  persisted `CollectionCaseAction` attempt. **A refusal is a first-class result
  the ladder may branch on, never an exception and never a swallow;**
- **timers and reconciliation** — the decision about which exact timer to
  schedule, replace or cancel, and the ability to rebuild derived case state and
  repair a missed delivery.

It does **not** own, and the gate must refuse:

- **changing an invoice balance** or any amount — no total, no rounding, no
  tolerance, no de-minimis epsilon, and **no field a caller can read as "the
  balance"**;
- **accepting payments** — settlement, allocation, deallocation, reversal,
  refund, credit or prepaid funding are billing's;
- **directly suspending, throttling, restoring or revoking anything** — no
  service, access, RADIUS, entitlement, allocation or licence write exists in the
  package. Suspension is a request; the service owner locks, revalidates, applies
  or refuses, and returns a receipt. This is ADR-0029's three-owner shape applied
  to a financial trigger: **a request is not permission and not a state write**;
- **provider calls** — no PSP, registrar or panel client, credential, webhook
  verification, retry or checkpoint; those are Integrator connector plugins;
- **sending notifications** — no rendering, no channel selection, no transport,
  no delivery-status enum, no queue in the collections schema (§ 10.1);
- **the invoice, subscription, contract, offer or licence lifecycle**;
- **durable-timer infrastructure** — P3 owns generations and firing; collections
  declares a port, ships a fake and a contract suite, and consumes the trigger;
- **approval as a generic workflow** — ADR-0026; a product that needs
  maker-checker composes the approval decision and then asks collections to
  recheck and perform its own transition;
- **ERP's general ledger, journals, fiscal periods, tax returns, bad-debt
  provisioning or statutory collections reporting** (§ 9.1).

**The narrowness is the point, and it is ADR-0030 § 1's deliberate row.**
Collections may request **only** a consequence justified by its own delinquency
case. Customer cancellation, abuse, security and operator action have their own
initiating owners and must not be routed through this module — a shared "apply a
consequence" entry point would quietly turn a financial policy engine into the
fleet's suspension bus, which is exactly the alternative ADR-0030 rejects.

**Cross-owner flow, and the one hard rule about it.** The module imports no
sibling business module and reads no sibling's tables (ADR-0024, rule 28). Its
only read path is a declared `ReceivablesReader` port; the **assembly** binds
Billing's published receivable/coverage facts to it, and until Sub adopts
`dotmac-billing` the same assembly binds Sub's existing authoritative financial
facts to the same port. A later billing cutover changes the producer behind the
port, not the collections owner. The command carries identity and source version;
the port supplies the amount at decision time, including at every timer fire, so
no stale money is ever acted on — and during the coupled billing authority switch
the reader returns `Unavailable(retryable=True)`, no case advances, and **no
consequence request may be emitted**.

**Planes (ADR-0023/0028, rule 27).** Dual-plane, declared, from revision one: one
persistence-free behaviour engine (ladder evaluator, case machine, grace
calculator, arrangement coverage, consequence-request builder — value objects in,
value objects out) plus `tables` (tenant: `tenant_id UUID NOT NULL`, composite
uniques, RLS ENABLEd **and** FORCEd in the creating migration) and
`platform_tables` (no tenant column, no RLS, `REVOKE ALL` from the tenant app
role across every table and column privilege, reachable by the online platform
role through schema `USAGE` plus row DML). A table appears in exactly one plane;
**no foreign key crosses the planes**; nullable `tenant_id`, sentinel tenants and
polymorphic scope columns are refused. Sub needs the tenant plane and has no
source evidence for it (§ 5.16); Vendor CP is platform-only and cannot declare
one at its current kernel pin (§ 9.2).

---

## 15. Kernel floor

The capabilities this owner consumes, named now so the floor can later be proven
both sufficient and necessary. Each is a consumption, not a restatement.

| Capability | Kernel surface | What Collections uses it for |
|---|---|---|
| Transaction authority | `dotmac_kernel.db` (rule 8), `conflict_savepoint` (rule 9) | every write flushes into the caller's transaction; the module never commits, and an expected conflict uses a savepoint rather than `db.rollback()` |
| At-most-once execution | `idempotency.execute_once` / `execute_once_platform`, `fingerprint_of`, `IdempotencyConflict` (ADR-0014, rule 23) | one notice, one consequence request, one step attempt per derived key; the fingerprint is a **separate column**, and nothing is reserved before the effect |
| Durable outbox + inbox | `messaging.enqueue_event` / `enqueue_platform_event`, `relay`, `inbox.process_once`, `platform.process_once_platform` | every non-transactional effect leaves through the outbox after commit; inbound assembly commands are deduplicated by the inbox |
| Exact money | `money.Money`, `Currency`, `allocate()` | every amount on a position, an arrangement instalment or a consequence request; `float` is unrepresentable in the contract type |
| Settings resolution | `settings_resolver` (ADR-0011/0012) | policy selection, thresholds, the enforcement window, and every value § 5.11 currently hardcodes — read from rows and defaults, never the environment |
| Declaration registries | `modules.ModuleManifest` (ADR-0008, rule 12) | `action_codes`, reason codes, case-state codes, notice purposes, setting domains, audit actions — declared, referenced only when declared, and consumed |
| Declared planes | `modules.tables` / `platform_tables`, `planes.ModulePlaneSelection` (ADR-0023/0028, rule 27) | the dual-plane declaration and the assembly's explicit per-module plane selection |
| Namespace + lineage | `namespaces`, `MIGRATION_OWNER_LEDGER` (ADR-0006 D1, rule 14) | one `mod_<short>` schema, one migration prefix, one branch label, allocated in the package-creation diff |
| Audit | `audit.write_audit_event` | every case transition, policy pin, request and receipt, against declared action codes |
| Consent | `consent.may_send` / `suppression_reason` | eligibility before a notice intent; consent is not a preference and never loses |
| Channel policy | `channel_policy.resolve_channels` | which channels a notice class uses; a policy step's `channel_preference` is a preference, and a disagreement is recorded on the step attempt |
| Delivery receipts and the send path | `delivery.record_receipt`, `delivery_providers.send` | consumed by the product's communication adapter, **not** by this module (§ 10.1) — named here so "collections does not send" is checkable against a real API |
| Durable timers | `dotmac_kernel.durable_timers` — **does not exist** | the escalation wake-up. ADR-0030 § 4 names it an enabling owner and § 5.1 sequences it before the business modules; until it exists Collections declares a `Timer` port, ships a fake and a parametrized contract suite, and **must not invent a second scheduler ledger** in its own schema |

Two floor facts that are easy to get wrong. The kernel's platform-plane
`messaging` and `idempotency` variants exist, so the platform plane needs no
second engine — but `consent`, `delivery` and `delivery_providers.send` are
tenant-plane only (§ 10.1 gap 2), so a platform-plane notice has no consent
ledger and no receipt loop and that must be answered, not worked around. And the
floor is a **minimum kernel version**, not a wish list: the plane declaration
this module depends on landed at `0.1.0a63`, which is above Vendor CP's current
pin.

---

## 16. Fresh proof required

Numbered, and none of it is portable — § 8.4 establishes that the source suite is
SQLite-by-default and proves no isolation, no concurrency and no deployed schema.
Every item below is written new, against a real migrated PostgreSQL.

1. **Tenant-plane RLS isolation.** Every collections table carries
   `tenant_id NOT NULL` with RLS ENABLEd **and** FORCEd in the creating
   migration; a cross-tenant canary proves tenant A cannot read, update or delete
   tenant B's case, exposure, arrangement, step attempt or consequence request,
   including through a join and through an aggregate count.
2. **Platform-plane revocation and reachability.** No collections platform table
   has a tenant column or a policy; `app_user` is denied on **all seven table
   privileges and their column-level forms**, each check on a fresh connection
   (Vendor CP's `test_platform_role_access_and_tenant_role_denial` is the
   template); and the online platform role holds schema `USAGE` plus at least one
   row DML privilege — declared-and-unreachable fails too.
3. **No foreign key crosses the planes**, in either direction, and no module
   table points at a product table; the per-plane link helpers each refuse an
   unusable configuration.
4. **Concurrency.** Two workers advancing the same case produce exactly one step
   attempt and one consequence request; two settlements arriving together close
   the case once; concurrent policy activation and case advance cannot interleave
   into a case decided half by each version.
5. **Rollback with the consuming transaction.** A failed consuming transaction
   leaves no case, no timer, no step attempt and no outbox row — and, inversely,
   a transition that requires a future action cannot commit without its timer.
6. **Idempotent replay and fingerprint conflict.** Same key and same fingerprint
   replays the recorded outcome with no second effect and no second lock; same
   key with a different fingerprint raises `IdempotencyConflict` and blocks the
   case rather than creating a second consequence.
7. **Out-of-order and superseded delivery.** A lower `source_version` is ignored,
   not applied; the same version with a different `state_fingerprint` is a
   conflict, not a silent update; a timer delivery carrying a superseded
   generation is a no-op; a late receipt for an older request never regresses a
   newer applied one.
8. **Lost callback and the refused outcome.** A consequence request whose receipt
   never arrives is retried under the same derived key and produces one effect; a
   `refused` receipt is persisted, advances nothing, and is a value the ladder can
   branch on; a `failed(retryable=True)` receipt is durable and retried on the
   policy's ladder, never swallowed and never recorded as success.
9. **The scope guard.** A case holding one service's overdue obligation cannot
   emit a request whose `effect_scope` is `subject` — with a sensitivity proof
   that widening one exposure's scope makes the same request permitted.
10. **Producer unavailability.** While the `ReceivablesReader` returns
    `Unavailable(retryable=True)`, no case advances, no consequence request is
    emitted, and a timer firing into it reschedules its own identity with a
    bumped generation rather than consuming the step or silently granting grace.
11. **Consent and delivery outcomes stay distinct.** A suppressed address yields
    `no_contact_route`, which neither grants extra grace nor authorises the next
    consequence — proven in both directions, with a sensitivity proof that
    removing the branch lets the case advance.
12. **Policy immutability and replay.** A published policy version cannot be
    edited; editing a policy mid-case cannot change the ladder of an open case;
    replaying a case against its pinned version reproduces the same decisions.
13. **No periodic scan.** An AST/import scan over the package finds no
    `while True`, `threading.Timer`, `sched`, looped `asyncio.sleep`, scheduler
    registration, or function selecting over the module's own case/exposure
    tables without a bounding identity — with a sensitivity proof that a planted
    `run_due_cases_sweep(db)` fails it.
14. **Drift and reconciliation.** Derived case state can be rebuilt from
    append-only evidence; a missed timer delivery is repaired without duplicating
    an effect; a reconciler run over an unchanged world is a no-op.
15. **Exact money.** No `float` anywhere in a money path (a type-level property,
    plus an architecture test); no cross-currency comparison; no epsilon; a
    multi-currency subject yields several positions and several cases.
16. **Both planes run the same behaviour suite** and produce the same decisions
    for otherwise identical input.
17. **Every new ratchet ships a sensitivity proof** (ADR-0018, rule 25) — R4, R5,
    R6, R8, R9, R11 and R12, and the ported sweep baseline, which currently has
    none (§ 7.1).

---

## 17. Adoption and retirement

**Sequence.** ADR-0030 § 5 puts Collections **sixth**, after numbering and
durable timers close, after Billing freezes its receivable/coverage contracts, and
after Subscriptions, Orders, Domains and Hosting. That ordering is not
bureaucratic: this module's entire input is Billing's published position and its
entire output is a request to a service lifecycle owner, so building it before
either contract stabilises would hardcode guesses about both. The two live
contract gaps are already recorded — G2's two incompatible
`ReceivablePositionV1`s and G1's missing `period_start` (§ 11) — and neither is
resolved by this audit.

**First adopter: Sub, tenant plane.** Sub is both the qualifying source and
cutover 1, which is the right ordering here for the same reason it is for
Billing: Sub has **two** live owners to retire plus a complete tested-but-unwired
shadow implementation, and it is the only place the shared contract meets real
cases, real policy sets, real grace, real arrangements and a real access
lifecycle owner. Slice it as the adoption plan does — S0 classify every live row
and writer with a **total** classifier and no default bucket; S1 port contracts
and canaries before models or routes; S2–S4 land policy/cases/consequence
requests, then arrangements, then grace, **in shadow only**, where no notice and
no consequence may escape; S5 full-cohort parity per subject/reason/currency/
source version over one complete production ladder window per active policy; S6 a
bounded cohort; S7 expand, then retire.

Two drift sources are specific to this module and must be watched by name during
S5: Sub's postpaid run commits **once** for the whole run with no per-account
error isolation, so a shadow run and a live run can disagree simply because the
live run aborted — every parity comparison must record whether the live run
completed; and fleet-wide `billing_health_reasons` still enters the live preview
fingerprint (`_core.py:714` → `:741`), so live fingerprints move for reasons
outside the entity while shadow fingerprints deliberately will not (§ 6.2).

**What retires, and when.** § 7's R1–R12, each behind its own two-directional
ratchet with a sensitivity proof, and the baseline lowered in the same change as
the removal. The hard ones first: R5 (throttle becomes a request — the one
blocker to the § 1 row), R4 (the two `Subscriber` timer columns become module
timers, which needs P3), R6 (the invoice-overdue call, which needs the billing
seam), R8 (the local case/policy tables). Note the standing obstacle: both
schedules are currently pinned as *permanent* by
`tests/architecture/test_permanent_customer_financial_lifecycle.py`, so R1 and R2
each require changing an architecture test that exists to stop exactly that — a
deliberate, reviewed change, not a quiet edit.

**Second adopter: Vendor CP, platform plane only**, behind the existing
four-condition demand gate — an authoritative production receivables owner, at
least one non-test receivable past its due time with a positive exact collectible
amount, a named consequence owner, and an action code declarable together with
its real consumer. This re-audit at `89848017d6b8` (§ 9.2) confirms **none** of
the four holds at a revision later than the original measurement, and adds two
prerequisites: Vendor CP's kernel floor must move to a version whose
`ModuleManifest` has `platform_tables`, and a durable-timer owner must exist,
because Vendor CP has no scheduler at all and its one clock-driven transition is
already unreachable for that reason. Installing empty platform tables is not
adoption.

**ERP and CRM adopt nothing** (§ 9.1, § 9.3), but each owes a boundary change
that is not a module adoption: ERP's AR reminder query must exclude Sub-mirrored
invoices or retire, and its `credit_hold` column must not gain an enforcement
reader; CRM's `risk_segment`/`days_past_due` must become a projection of the
owner's published segmentation rather than its own recomputation.

**A green test suite is not a cutover.** The dossier becomes `reuse-proven` only
after a real case exercises the shared contract end to end — pinned policy,
scheduled timer, notice route, consequence request, an applied **or refused**
receipt from the owning service, and resolution — while the product-specific
consequence stays outside the module, and the displaced local writer is deleted.
A reference-assembly migration test, a synthetic overdue fixture, or an empty
greenfield install is test evidence, not reuse evidence.
