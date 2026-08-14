# Collections, dunning and enforcement sources

**As of:** 2026-08-14
**starter:** `5417e51` (branch `docs/whatsapp-connector-extraction-dossier`, plus
the uncommitted ADR-0020 amendment and the concurrent Stage-I plans)
**Sub:** `27c76aaee`
**ERP:** `0f4b1698`
**vendor CP:** `8984801`
**Decision applied:** ADR-0020 § 4 and its 2026-08-14 amendment (A1, A2, A6)
**Adoption sequence:** `docs/superpowers/plans/2026-08-14-collections-sub-vendor-cp-adoption.md`
**Contracts:** `docs/superpowers/specs/2026-08-14-collections-policy-consequence-and-timer-contracts.md`
**Dossier:** `docs/inventories/collections-extraction-dossier.md`

Every hash is the commit the measurement was taken at, and is a baseline rather
than a claim about current state — re-run the counts rather than trusting them
(`docs/inventories/README.md`). This is characterization, not a mandate: nothing
here authorises a package, a namespace, or a lineage. ADR-0017's P11 gate is
closed.

This inventory answers one question: **which Sub code decides and applies a
collections consequence today, where do the postpaid and prepaid paths actually
diverge, and what has to be retired before a shared module can own the
decision?**

---

## 1. The topology: Sub has two collections stacks, and only one of them runs

| | **Live stack** (`financial.dunning`) | **Target stack** (ADR-0007 Phase 5, `collections.lifecycle`) |
|---|---|---|
| Postpaid decision | `app/services/collections/_core.py` (3,121 L) | `app/services/collections/postpaid_policy.py` (61 L) |
| Prepaid decision | `app/services/collections/prepaid_balance_sweep.py` (758 L) + `app/services/prepaid_enforcement_planner.py` (725 L) | `app/services/collections/prepaid_policy.py` (116 L) |
| Case owner | `DunningCase` / `DunningWorkflow` (`_core.py:2521-2821`) | `CollectionsCase` / `CollectionsLifecycle` (`app/services/collections/lifecycle.py`, 397 L) |
| Timers | none (postpaid); two nullable datetime columns on `Subscriber` (prepaid) | `runtime.durable_timers` (`app/services/runtime_durable_timers.py`, 325 L) |
| Trigger | Celery beat: `dunning_runner`, `prepaid_balance_sweep` | a durable timer generation |
| Status | **acts in production** | `AuthorityMigrationState.SHADOWING` (`app/services/sot_registry/domains/financial_access/collections.py:311`) |

The target stack **has no production caller**. `CollectionsLifecycle`,
`plan_postpaid_consequence` and `plan_prepaid_consequence` are referenced only by
`tests/test_collections_target_lifecycle.py`, the SOT manifest, and
`scripts/billing/billing_target_shadow.py:860-867`. Nothing routes
`collections.consequence_requested` or `collections.case_action_due`
(`lifecycle.py:228`, `:282` are the only producers; there is no handler). So the
corrected behaviour is implemented and tested but cannot cause a consequence.

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
| `app/services/scheduler_config.py:713-723` | nothing — registers the job | `ScheduledTask(name="dunning_runner", task_name="app.tasks.collections.run_billing_enforcement")` | startup sync; interval `collections.dunning_interval_seconds` (default 86400, floor hardcoded `max(..., 60)` at `:716`) |
| `app/tasks/collections.py:5-7` → `app/services/collections/scheduled.py:21-53` | nothing — session/transaction shell | commits what the reconciler staged (`:47`) | Celery beat, queue `billing`, soft limit 1740 s (`celery_app.py:153`, `scheduler_config.py:256`) |
| `BillingEnforcementReconciler.run` (`_core.py:2974-3011`) | run ordering: settle credit, then dun | counters only | above |
| `_settle_due_credit_before_dunning` (`_core.py:2870-2972`) | which accounts get credit applied before a dunning decision, and whether that makes them restorable | credit application rows; calls `restore_account_services` (`:2943`); commits per account (`:2958`) | above |
| `DunningWorkflow.run` (`_core.py:2522-2821`) | **the escalation decision**: which accounts are overdue candidates, which policy step fires, whether a case opens/advances | `DunningCase` (`:2672-2680`), `DunningActionLog` (`:2744-2753`), `case.current_step` (`:2754-2755`), `case.policy_set_id` (`:2697`), events `dunning_started` (`:2683`) / `dunning_action_executed` (`:2758`), **and `Invoice.status` via `Invoices.mark_overdue_system(...)` (`:2562-2567`)**; one `db.commit()` at `:2814` | above |
| `_execute_dunning_action_with_evidence` (`_core.py:2015-2086`) | maps a policy step's `DunningAction` to a `FinancialAccessAction` and requests it | delegates; queues notices (`:2082-2085`) | `DunningWorkflow.run:2735` |
| `preview_/confirm_financial_access_consequence` (`_core.py:459-757`, `:813-1041`) | **the consequence eligibility decision** and its application | `FinancialAccessConsequence` + `...Evidence`, `EnforcementLock` via `account_lifecycle.suspend_subscription` (`:869-885`), audit, events | above, and the prepaid sweep |
| `dunning_staff_actions.py` + `app/web/admin/billing_dunning.py` | staff pause / resume / close | `DunningCase.status`, `DunningActionLog`, `AuditEvent` — **no access state** | admin routes `POST /admin/billing/dunning/...` |

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
| `app/services/scheduler_config.py:733-745` | nothing — registers the job | `ScheduledTask(name="prepaid_balance_sweep")` | interval `collections.prepaid_balance_sweep_interval_seconds`, default 3600, min 300, **max 3600** (`settings_spec.py:1106-1114`) |
| `app/services/collections/scheduled.py:598-642` | run ordering: coverage repair → renewal-terms repair → sweep → snapshot | commits per stage | Celery beat, default queue, 840 s soft limit, 720 s self-budget (`scheduled.py:91`, `:580-595`) |
| `prepaid_enforcement_planner.plan_prepaid_account` (`:371-628`) | **the whole prepaid ladder** — stale-timer repair, billing-profile validity, coverage/renewal resolution, funded/restore, drift, warn, waiting, deferred, shielded, suspend | **nothing** — pure (`:1-6`) | called by the sweep (`prepaid_balance_sweep.py:357`, `:374`) and by a read-only script |
| `prepaid_balance_sweep.run_prepaid_balance_sweep` | dispatch, notice-outcome deferral (`:286-287`), and whether to arm the deactivation marker (`:317-330`) | `Subscriber.prepaid_low_balance_at` / `prepaid_deactivation_at` (via `prepaid_enforcement_state.py:59-103`), `Notification`/`CommunicationIntentRecord`, `Finding` work items (`:181-204`), `PrepaidSweepCycleState` cursor (`:699-722`); consequences via the shared owner | above |
| `prepaid_enforcement_state.py` (111 L) | first-write-wins on the two timer columns (`:67`, `:83`) | those two columns; `prepaid_enforcement_timer_changed` events (`:40-56`); flush-only, never commits | the sweep, and `_clear_prepaid_dunning_flags` (`_core.py:3014-3022`) |

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
   `Subscriber.status`, confirmed by repo grep;
4. `_bulk_dunning_shield_reasons` (`_core.py:1914-1938`),
   `grace_policy.resolve_grace_decision`, `billing_profile.resolve_billing_profile`,
   `enforcement_window.resolve_enforcement_window_decision`;
5. `FinancialAccessConsequence` + `FinancialAccessConsequenceEvidence`, and the
   fingerprint/idempotency machinery (`:201-203`, `:760-810`).

`docs/FINANCIAL_ACCESS_ENFORCEMENT.md:52-53` names the two owners: the
`financial.dunning` access-consequence owner "locks, recomputes, fingerprints,
applies, and evidences" the consequence, and `access.subscription_lifecycle` is
"sole writer of reason-scoped locks, account status, and child-service access
state in one transaction."

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
| Dedicated modules | 4 (`prepaid_balance_sweep.py` 758, `prepaid_enforcement_planner.py` 725, `prepaid_enforcement_state.py` 111, prepaid half of `collections/scheduled.py` ~470) | ~1.5 (postpaid half of `_core.py` ~900, `scheduled.py:21-53`) | `_core.py` consequence half ~1,150; `grace_policy.py` 322; `enforcement_window.py` 165 |
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

| # | Site | What it writes directly | Live? | Owner it bypasses |
|---|---|---|---|---|
| 1 | `_core.py:918-921` (throttle, inside `confirm_financial_access_consequence`) | `credential.pre_throttle_radius_profile_id`, `credential.radius_profile_id` | **yes** | the access owner; suspend/reject on the adjacent branch correctly call `account_lifecycle.suspend_subscription` (`:869-885`) |
| 2 | `_core.py:1475-1476` (un-throttle, inside `confirm_financial_access_restoration`) | same two columns | **yes** | same |
| 3 | `_core.py:1692-1693`, `:1748-1749` (`_throttle_account` / `_restore_throttle`) | same two columns | no — tests only (`tests/test_access_enforcement_strays.py`) | same; dead code carrying the pattern |
| 4 | `_core.py:2562-2567` — the dunning **scan** calls `Invoices.mark_overdue_system(..., reason="dunning_candidate_resolution")` | `Invoice.status` → `overdue` | **yes** | billing. Under ADR-0020 § 1 the invoice lifecycle is billing's; a collections scan must not move it. Also mutates invoice metadata via `apply_prepaid_overlap_hold` (`:2554`) |
| 5 | `prepaid_enforcement_state.py:69`, `:85`, `:99-100` (called from `prepaid_balance_sweep.py:291`, `:330`) | `Subscriber.prepaid_low_balance_at`, `.prepaid_deactivation_at` | **yes** | not access state, but collections **timer** state persisted on a product identity row. ADR-0007 § 8 assigns exact timers to `collections.lifecycle`, which is not consulted |

Not a bypass, recorded to avoid a false positive: `fup_enforcement.py` decides
Fair-Use throttling and its handler calls `account_lifecycle.suspend_subscription`
(`app/services/events/handlers/enforcement.py:503-514`) — a non-financial reason
routed through the correct single access writer. `app/services/enforcement.py`
writes only RADIUS/session **projections** (`:1706`, `:1718`, `:1834`, `:1936`),
never `Subscription.status`.

Case-state writes inside `_core.py` (`:1420-1421`, `:2299`, `:2314`,
`:2318-2319`, `:2842-2843`) are collections writing its own rows, and are not
second writers.

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
    168), `grace_policy.py:290` (`timedelta(days=policy.days + 1)`),
    `scheduler_config.py:716` (`max(..., 60)`), `prepaid_balance_sweep.py:64`
    (`_NO_CONTACT_SLA_HOURS = 72`), `scheduled.py:82`/`:86`/`:91` (`200`, `72`,
    `720`), `dunning_staff_actions.py:23` (`MAX_SELECTED_CASES = 100`), plus the
    fixed notice copy at `_core.py:1803-1805` and `:1879-1880` — where the
    subject string is also the dedupe join key.
12. **Dead code that reads as policy.** `"enforcement_health_blocked"` remains in
    `_NON_ADVANCING_DUNNING_OUTCOMES` (`_core.py:1953`) with nothing able to
    produce it; `DunningWorkflow.resolve_cases_for_account` (`:2823-2857`) has no
    caller; `_throttle_account`/`_restore_throttle` are tests-only.

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
| R1 | `dunning_runner` (`scheduler_config.py:713-723`) and `DunningWorkflow` (`_core.py:2522-2821`) | policy-driven cases + per-entity timers in the module | per subject/reason/currency/source version: case existence, pinned policy, current/next step, exact next-action time, requested consequence + idempotency identity, close/reopen after settlement — over one complete production ladder window per active policy | name removed from `tests/architecture/billing_scheduled_sweep_baseline.txt` in the same change (§ 7.1) |
| R2 | `prepaid_balance_sweep` (`scheduler_config.py:733-745`, `prepaid_balance_sweep.py`) | the same single lifecycle, driven by `advance`-timing policy data | additionally: every typed skip/shield reason, funded-restore outcomes, budget-deferred accounts, and the full candidate cohort per cycle | same baseline file |
| R3 | `PrepaidSweepCycleState` (`app/models/collections.py:267-293`) | nothing — a timer has no cycle, so the cursor has no successor | prove every account that the cursor would have visited in a cycle has a timer or a typed no-timer reason | table drop after the count of rows reaches zero and stays there; its own docstring already marks it `TRANSITIONAL` |
| R4 | `Subscriber.prepaid_low_balance_at`, `.prepaid_deactivation_at` and `prepaid_enforcement_state.py` | one `DurableTimer` per `(owner, entity, purpose)` with a generation | for every account with a non-null column, exactly one module timer with the same due instant, and clearing the column ⇔ cancelling the timer | two-directional count ratchet over non-null occurrences of both columns |
| R5 | Direct credential writes at `_core.py:918-921` and `:1475-1476` | a typed consequence request whose owner is the access-lifecycle service | the owner's receipt matches the previously-written credential state for every throttle/un-throttle in the cohort | count ratchet over assignments to `radius_profile_id` / `pre_throttle_radius_profile_id` outside `account_lifecycle` |
| R6 | `Invoices.mark_overdue_system(...)` called from the dunning scan (`_core.py:2562-2567`) and `apply_prepaid_overlap_hold` (`:2554`) | billing owns invoice lifecycle; collections reads a position and writes nothing | prove no invoice changes status as a side effect of a collections run, and that overdue-ness is derived from the position instead | count ratchet over collections-module writes to `Invoice.*` |
| R7 | `_throttle_account` / `_restore_throttle` (`_core.py:1637-1777`), `DunningWorkflow.resolve_cases_for_account` (`:2823-2857`), `"enforcement_health_blocked"` (`:1953`) | nothing — dead | none needed; confirm zero callers at the retirement commit | included in R5's count ratchet; the dead outcome string is a one-line delete |
| R8 | Local `dunning_cases`, `dunning_action_logs`, and the shadow `collections_cases` **writers**; local `policy_dunning_steps` and the collections reading of mutable `PolicySet` fields | module-owned policy versions, cases and append-only step attempts | full-cohort parity per R1/R2 plus a total row-disposition classifier with no default bucket | two-directional count ratchet over imports of the local models outside an archive reader |
| R9 | Notice literals and the subject-string dedupe (`_core.py:1780-1883`) | policy-declared `template_id` + `channel_preference`, resolved through `dotmac_kernel.channel_policy`, with consent via `dotmac_kernel.consent` and a derived idempotency key | for each cohort notice: same recipient set, same channel decision, same suppression outcome, and a dedupe decision that no longer depends on a subject string | count ratchet over string literals used as notification subjects in the collections tree |
| R10 | The shadow stack itself — `collections/lifecycle.py`, `postpaid_policy.py`, `prepaid_policy.py`, `collections_cases` writers | the module's single engine | its tests are ported (§ 8) and pass against the module | delete at cutover; a product-local copy of the extracted engine is itself a ratchet entry (adoption plan S7) |

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
  known-wrong list and there is no per-line "this is fine" marker.
- **Sensitivity proof: NOT PRESENT.** No test in that file proves the detector
  still fires. A clean run is currently indistinguishable from
  `scheduled_sweep_names()` returning an empty set. Under ADR-0018 § 5 and
  AGENTS.md rule 25 the module's ports of these ratchets must add one, and the
  new ratchets in § 7 (R4, R5, R6, R8, R9) must ship with theirs.
- **Entry-point families, not directories:** the sweep baseline is keyed on
  scheduled-task names, which covers the Celery beat family only. The direct-write
  ratchets (R5, R6) must scan services, tasks, scripts, CLI and web handlers
  together, since `_throttle_account` reached production behaviour through a web
  helper before it was retired.

---

## 8. Tests available to port

Every path below was confirmed to exist at `27c76aaee`. The behaviour column is
what the test proves, not what it is named.

### 8.1 The adoption plan's S1 set — all present

| Test | Lines | Proves |
|---|---|---|
| `tests/test_collections_target_lifecycle.py` | 324 | exact-overdue and underfunded proposals with exact `Decimal`; partial settlement arithmetic; prepaid ignores receivables; **fail-closed on an incomplete opening source with a work-item fingerprint**; each step replaces the exact next-action timer (generation 2, one superseded); close cancels timers and stages restore evidence; terminal replay returns the same consequence id |
| `tests/test_collections_dunning_services.py` | 1,471 | the live postpaid ladder end to end: notice-runway gates, day-0 policies using real overdue age, arrangement and prepaid-credit shields, reconciliation holds, non-collectible residuals, paused cases never escalating, payment resolving open-but-not-paused cases, credit settled before dunning in any currency, dedupe-window honouring |
| `tests/test_collections_services.py` | 136 | CRUD + the imported-deposit exclusions (`Decimal("-87500.00")`, `Decimal("0.00")`, native `Decimal("500.00")`) |
| `tests/test_payment_arrangements.py` | 980 | arrangement lifecycle |
| `tests/test_payment_arrangement_safe_actions.py` | 187 | arrangement preview/fingerprint/confirmation |
| `tests/test_dunning_staff_safe_actions.py` | 271 | exact eligible/skipped membership; explicit confirmation required; atomic transition+log+audit; audit failure rolls back **every** selected transition; stale preview rejection; close gated on canonical per-currency receivables `(("NGN", Decimal("250.00")),)` |
| `tests/test_durable_timers.py` | 267 | scheduling outside an owner command fails closed; reschedule supersedes and bumps generation; cancel is idempotent; firing emits only the declared trigger; a superseded timer never fires; the due scan is bounded — **this is the Timer contract suite, and belongs to P3, not to collections** |
| `tests/test_prepaid_enforcement_planner.py` | 407 | the prepaid ladder as a pure plan: mode-change lock repair, terminal-service stale locks, drift reporting without mutation, future-anchor blocking, materialized funding owner, zero-grace suspending even when the notice is fault-suppressed, non-zero grace not starting until the warning is queued |
| `tests/test_financial_access_consequence_evidence.py` | 412 | preview→confirm creates exactly one lock + one evidence row + one audit event and links the action log; **replay with the same key returns the same consequence with no second lock**; stale preview → 409 with zero side effects; an *ineligible* consequence is still durably recorded; restore emits exactly `{lock_resolved, credential_restored, dunning_case_resolved}`; restore never guesses a missing pre-throttle profile |

### 8.2 Additional suites the parity work needs

`tests/test_prepaid_balance_sweep.py` (969) — 30 named behaviours including
zero-grace immediate suspension, grace timers not resetting on rerun, weekend as
an ordinary enforcement day, arrangement and payment-proof shields, stale-timer
repair, and `test_suspend_blocked_leaves_timer_unarmed_and_retries`.
`tests/test_prepaid_threshold_resolver.py` (467) — threshold provenance,
currency-mismatch rejection, fail-closed missing price, batch cost independent of
account count. `tests/test_grace_policy_sot.py` (183) — precedence
account→policy→mode default, offsets beginning after grace end, explicit zero
grace preserved, invalid settings failing closed with stable typed codes.
`tests/test_prepaid_notice_progression.py` (165) — no-contact-route arming the
timer while opening an SLA'd work item, phone-only customers warned over
non-email channels, a notice shield expiring after max hours.
`tests/test_notification_queue_suppression.py` (127) — the marketing/transactional
scope rule proven at the transport (an unsubscribed address still gets its
invoice; a hard bounce stops even the invoice).
`tests/test_prepaid_sweep_budget.py` (158), `tests/test_enforcement_window.py`
(89), `tests/test_enforcement_window_gate.py` (69),
`tests/test_prepaid_enforcement_state_owner.py` (72),
`tests/test_prepaid_flag_clear_on_restore.py` (34),
`tests/test_financial_access_restore.py` (191). Architecture boundary tests under
`tests/architecture/`: `test_financial_action_boundaries.py`,
`test_financial_ownership.py`, `test_grace_policy_boundary.py`, seven
`test_prepaid_*_ownership/boundary.py` files, `test_thin_financial_tasks.py`,
`test_billing_target_architecture.py`.

### 8.3 Behaviours with NO adequate source test

These are the areas where porting proves nothing, because there is nothing to
port. Each needs a new test written against the module.

| Behaviour | Status in Sub |
|---|---|
| A case pinned to the policy version that opened it | **No test and no mechanism.** `policy_version` does not exist anywhere in the collections tree (§ 5.1) |
| Cancelling/replacing a specific pending timer on settlement | Proven only in the **dead** shadow stack (`test_collections_target_lifecycle.py:225`, `:257`). The live paths have no timer identity to cancel |
| A failed consequence being durable and retryable | **No test; the code swallows** (§ 5.6), and `test_collections_dunning_services.py:1313` asserts the swallow is correct. The nearest coverage is event-level (`test_events_enforcement_services.py:1773`) or achieved by *not writing state* (`test_prepaid_balance_sweep.py:938`) |
| Consent honoured before a dunning/prepaid notice | **Partial.** Proven at the queue runner (`test_notification_queue_suppression.py`), never at the collections decision. No test drives a dunning or prepaid notice through a consent-suppressed contact — and § 5.8 shows the sweep would treat it as a transport failure |
| No account-wide consequence for a scoped debt | **No test, and the live code is account-wide** (§ 5.7). Not even the shadow stack asserts "debt on service A leaves service B online" |
| Exact money as a rule | Held in practice (`Decimal` throughout; the only `float()` calls are metric counters at `scheduled.py:522-533`) but **no architecture test forbids float in money paths**, and no rounding/repr test exists. Currency mixing is rejected only in the threshold resolver (`test_prepaid_threshold_resolver.py:308`) |
| The same scenario under `advance` and `arrears` | **No parity test exists.** The two shadow planners are tested for mutual *exclusion* (`test_collections_target_lifecycle.py:154`) — the opposite of parity |
| The target stack causing a real consequence | Impossible today: `collections.consequence_requested` and `collections.case_action_due` have producers and **no consumer** |

---

## 9. ERP and vendor CP — exclusion evidence

- **ERP (`0f4b1698`) is not an extraction source and not an adopter.**
  `app/services/finance/reminder_service.py` (833 L) sends AR reminders. There is
  no dunning case, no arrangement, no grace owner, no consequence state machine.
  ADR-0020 A6 assigns ERP none of the three commercial modules; it remains the GL
  and statutory-accounting authority.
- **Vendor CP (`8984801`) has no collections owner at all** — no invoice,
  overdue, dunning, arrangement or consequence code. It is a future
  platform-plane adopter behind a demand gate, not a source.
- **The starter owns nothing here.** No collections package, namespace or
  lineage exists, and none may be created before P11.

---

## 10. What the kernel already supplies

Present, released, and to be consumed rather than restated. Restating any of
these in a collections module is a review failure.

| Need | Kernel owner | Note |
|---|---|---|
| At-most-once execution | `idempotency.py` (340 L) + ADR-0014 | Nothing reserved before the effect; the fingerprint is its own column; retention is the product's. Sub's overloaded-column and truncated-fingerprint-in-key patterns (§ 5.4) are unrepresentable here |
| Transactional outbox + relay, idempotent inbox | `messaging/` (~1,120 L across 10 files) | The consequence request travels this, not a bespoke queue |
| Consent + suppression | `consent.py` (445 L), `consent_models.py` | Marketing vs transactional scope already decided; transactional-unless-declared |
| Channel policy | `channel_policy.py` (190 L) | A settings document with a typed reader; no table, no service |
| Delivery receipts + the bounce→consent loop | `delivery.py` (341 L), `delivery_models.py` | The loop that exists in neither product |
| Exact money | `money.py` | `Money`, `Currency`, `allocate()`; no float |
| Settings resolution | `settings_resolver.py` (1,434 L), ADR-0011/0012 | Policy selection and every threshold |
| Declaration registries | `modules.py`, ADR-0008 | `action_codes` as an open registry, not `FinancialAccessAction` |
| Namespace + lineage | `namespaces.py`, ADR-0006 D1 | One `mod_<short>` allocation, in the diff that creates the package |

**Two gaps that bear on this module specifically:**

1. **P3 durable timers is missing from the kernel and gap-listed**
   (`billing-sources.md` P3; ADR-0020 A5). Sub has the reference implementation
   (`runtime_durable_timers.py` + `app/models/durable_timer.py` +
   `tests/test_durable_timers.py`). Collections declares a port and a fake; it
   does not build the facility.
2. **`consent` and `delivery` are tenant-plane only.** Both models carry
   `tenant_id NOT NULL` (`consent_models.py:117`, `delivery_models.py:133`) and
   there is no platform variant; `channel_policy.resolve_channels` accepts
   `tenant_id: UUID | None` and degrades to its fallback. A platform-plane
   (Vendor CP) collections notice therefore has no consent ledger and no receipt
   loop. This is an open question, not a thing to work around — see the contracts
   spec § 11.3.

---

## 11. Consistency with the parallel adoption plan

`docs/superpowers/plans/2026-08-14-collections-sub-vendor-cp-adoption.md` was
authored concurrently and is not edited by this document. This inventory is its
evidence base and does not restate its sequencing. Two points of difference are
recorded in the contracts spec § 9 (inbound seam shape; outbound contract name);
neither affects anything in this file. The one factual note: that plan's evidence
snapshot cites starter `1b1d62b`, while this measurement was taken at `5417e51` —
one commit later, with no collections-relevant change between them.

---

## 12. Index entry owed

`docs/inventories/README.md` has no row for this file. It is being edited
concurrently by another session and was deliberately not touched here; the row
must be added in the same change that lands this inventory.
