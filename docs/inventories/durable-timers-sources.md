# Durable timers — source revalidation

**As of:** 2026-08-18
**Subject:** `dotmac-durable-timers`, ADR-0030 §5 build-order step 6.
**Authorizing decision:** ADR-0030 §6 — "Where a dossier is incomplete, the
exception permits completing the audit; it does not turn missing evidence into
permission to greenfield." ADR-0030 §5 build order places step 6 immediately
after step 5 (`dotmac-numbering`, landed at starter `0b8d47a`, PR #193).

This document began as **source evidence**. Its inventory remains the provenance
for the implementation now materialized at `packages/dotmac-durable-timers`.
The former Billing overlap is resolved in one integrated Starter branch: kernel
`0.1.0a70` allocates both independent namespaces and exposes one explicit
multi-module SQLite composition seam. Billing owns operational receivables;
Durable Timers owns only timing mechanics. Neither imports the other.

The implementation preserves the source scenarios and adds eleven
PostgreSQL-first concurrency, plane-isolation, replay, immutability and
retention proofs. `make check` and the focused PostgreSQL suite passed on
Observe before integration; the final combined exact-commit validation is the
release gate. No release, deployment, Sub adoption or production authority
switch has occurred.

## Revalidation 2026-08-17 — decision and released prerequisite

The 2026-08-15 source audit remains valid, but its placement recommendation and
draft extraction block predated Michael's accepted ruling later that day.
ADR-0017 and ADR-0030 now authoritatively assign this capability to the
selectable dual-plane `dotmac-durable-timers` module. The module owns timer
identity and history; kernel `outbox_relay.v1` owns every claim, lease,
stale-lease recovery, retry, backoff and dead-letter operation. There is no
timer due-row scanner.

The prerequisite gate is now met. Annotated release tag
`dotmac-kernel-v0.1.0a67` points to
`ed3ac864b350d4556808a69496f999f764682442`; its published package version is
`0.1.0a67`, it exports `OUTBOX_RELAY_V1` with name `outbox_relay.v1`, and its
live-catalog verifier is registered as `verify_outbox_relay`. A module root
migration must declare and verify that prerequisite, while each composing
application binds it to kernel provider revision `0012_platform_outbox`.

Fresh dedicated worktrees pin Starter at
`7e0543004864845f0035c9ec325e3f5064c281cc` and Sub at
`4489ca1712f3c263d914f2af0ebfcf044aa70605`. The Sub model, service, task and
preserved behavior tests are byte-identical to the original `27c76aa` dossier
pin. `scheduler_config.py` has an unrelated nine-line change; the timer
dispatcher and legacy sweep registrations remain present.

## Heads resolved for the original 2026-08-15 revalidation

Every citation below is a revision-pinned read against `origin/main`
(`git show origin/main:<path>`, `git grep <pattern> origin/main`), never a
working tree, except in `dotmac_starter_mt` where the working tree is the
subject of the work.

| Repository | Local checkout | `origin/main` | Gap |
| --- | --- | --- | --- |
| `dotmac_starter_mt` | `ed9dcc0` (working tree) | `1e9c433` | local is 1 ahead |
| `dotmac_sub` | `27c76aa` (detached) | `f336170b6f136e74401561677e3b40de35b8f7ee` | **112 commits behind** |
| `dotmac_erp` | `0f4b1698` | `9d67c3990e01e20409ab118badb5dfdf7ce045a7` | **37 commits behind** |
| `dotmac_crm` | `c64b5aa0` | `57e112f0757edcee6b9ad625ee3e13ebff5c7d71` | **121 commits behind** |
| `dotmac_vendor_control_plane` | `427f89cd` | `80d3f3478d9204a113ee2e8dd9d01cc10f0ddb35` | 1 behind, 5 ahead |

The four durable-timer paths in Sub (`app/models/durable_timer.py`,
`app/services/runtime_durable_timers.py`, `app/tasks/durable_timers.py`,
`tests/test_durable_timers.py`) are **byte-identical** between the dossier pin
`27c76aa` and `origin/main` across those 112 commits (`git diff` returns an
empty patch for each). This is the numbering lesson repeating: byte-identity is
not reassurance. Two of the collections dossier's citations *have* drifted —
`support.py:2213` → `:2288` and `team_inbox_commands.py:840` → `:987` — so the
dossier's line numbers were correct when written and are wrong now.

The 2026-08-17 pins and byte-identity result above supersede this historical
checkout table for implementation. The original table remains as provenance
for the citations and findings below.

---

## 1. Headline verdict

**Product-first, with a mandatory port delta, and with a source split that the
existing dossiers do not record.**

Three findings drive the verdict.

**(a) Sub's facility is real, used, and running.** This is not the numbering
`reconcile_next_value` case. `schedule_timer` has **ten production callsites in
nine files**, across **eight distinct `owner` strings** and **nine distinct
`(owner, purpose)` timer families**. Eight of those nine families have a real
consumer wired through a lifecycle projection handler. The dispatch task
`app.tasks.durable_timers.fire_due_durable_timers` is registered with
`enabled=True` and a 60-second default cadence
(`app/services/scheduler_config.py:1947-1953`,
`app/services/settings_spec.py:1538-1547`) and is declared permanent
infrastructure. Sub's timer table, generation model and decision-free fire path
are a genuine source.

**(b) The eight-owner claim is one too high, and "production-proven" overstates
the state Sub itself declares.** See §3.

**(c) Sub's facility supplies the identity half and NOT the claiming half — and
the claiming half already exists in this kernel.** `runtime_durable_timers.py`
has no lease, no attempt counter, no dead-letter, no stale-claim reclaim, and no
per-timer failure isolation. `dotmac_kernel.messaging.relay` plus kernel
migrations `0011`/`0012` already implement exactly that engine — a
`SECURITY DEFINER` `FOR UPDATE SKIP LOCKED` claim with stale-lease reclaim,
bounded exponential backoff, retained dead-letter, on **both planes**, behind
least-privilege dispatcher roles, and proved on real PostgreSQL across two
independent connections. A durable-timer facility that ships its own claim loop
would be a **second scheduler ledger inside the same kernel** — precisely what
ADR-0030 §4 says the capability exists to prevent, applied one level up.

So the ruling is:

| Half of the capability | Source | Mode |
| --- | --- | --- |
| Timer identity, generation, supersede/cancel, one-current-per-identity, decision-free fire | `dotmac_sub` (mandatory paths, §2) | product-first extraction |
| Claim, lease, stale reclaim, bounded retry, dead-letter, plane split, dispatcher role | `dotmac_kernel.messaging.relay` + migrations `0011`/`0012` (in-repo) | **reuse, not re-implementation** |
| Lease columns on a per-entity schedule row | `dotmac_sub:app/models/subscription_lifecycle_schedule.py` | secondary reference, port with deltas |
| `tenant_id` + FORCE RLS, the platform plane, fingerprint/idempotency binding, retention, cancellation-vs-fire proof | none | greenfield, written fresh |

ADR-0017's sentence "Sub's facility … is **complete and tested**" is wrong on
both adjectives and should be amended: it is *used* but incomplete (no claiming,
no retry, no isolation, no tenancy) and its entire behaviour suite runs on
SQLite, where the row lock and `SKIP LOCKED` it depends on are no-ops.

---

## 2. Per-source revalidation

### 2.1 `dotmac_sub:app/models/durable_timer.py` (114 lines)

Present, unchanged from the pin. Table `durable_timers`.

Columns (`:79-108`): `id`, `owner` `String(120)`, `entity_kind` `String(80)`,
`entity_id` `UUID`, `purpose` `String(80)`, `generation` `Integer`, `due_at`
`DateTime(timezone=True)`, `expected_source_version` `Integer`,
`output_event_type` `String(100)`, `status` (native PG enum `timerstatus`),
`fired_at`, `fired_event_id`, `command_id`, `correlation_id`, `created_at`,
`updated_at`.

**There is no `tenant_id` column, and there is no RLS.** The migration
(`alembic/versions/433_durable_timers_collections_cases.py:60-101`) creates the
table with no `ENABLE ROW LEVEL SECURITY`, no policy, and no `GRANT`/`REVOKE`
of any kind. Sub is one deployment per operator; the kernel is not.

What the shape proves and is worth porting:

- `UniqueConstraint("owner","entity_kind","entity_id","purpose","generation",
  name="uq_durable_timer_generation")` (`:53-60`) — a generation is never
  reused for an identity.
- Partial unique `uq_durable_timer_current` on
  `(owner, entity_kind, entity_id, purpose) WHERE status = 'scheduled'`
  (`:62-71`) — **at most one current timer per identity, enforced by the
  database, not by convention.** This is the single most valuable line in the
  source.
- `Index("ix_durable_timer_due", "status", "due_at")` (`:73`) — the bounded due
  scan is indexed.
- `CheckConstraint("generation >= 1")` (`:74`).
- All timestamps are `DateTime(timezone=True)` and all Python defaults are
  `datetime.now(UTC)` (`:105-108`). **The naive-datetime defect the brief warns
  about is NOT present here.** Report it as clean.

### 2.2 `dotmac_sub:app/services/runtime_durable_timers.py` (325 lines)

Present, unchanged from the pin. Public surface (`__all__`, `:317-325`):
`schedule_timer`, `cancel_timer`, `current_timer`, `fire_due_timers`,
`ScheduleTimerCommand`, `FiredTimer`, `DurableTimerError`.

- `schedule_timer` (`:123-173`) — rejects naive `due_at` (`:136-140`) and an
  empty `output_event_type` (`:141-145`); locks the newest generation
  (`_latest_timer`, `:86-102`); bumps `generation` (`:154`); marks a still-
  scheduled predecessor `superseded` (`:155-157`); inserts and flushes
  (`:171-172`). It never commits.
- `cancel_timer` (`:176-194`) — locks the current scheduled row
  (`_current_timer`, `:105-120`), sets `canceled`, flushes; returns `False`
  when there is nothing to cancel. Idempotent.
- `current_timer` (`:197-215`) — read-only, unlocked. **Zero production
  callers.** Its only reference in the repository outside its own definition is
  `tests/test_durable_timers.py:104`. This is the numbering
  `reconcile_next_value` pattern in miniature; do not credit it as proven.
- `fire_due_timers` (`:231-258`) / `_fire_due` (`:261-314`) — rejects a naive
  `now` (`:245-249`); scans `status = scheduled AND due_at <= now`
  `ORDER BY due_at LIMIT batch_limit` with `.with_for_update(skip_locked=True)`
  (`:268-279`); for each row emits one event and sets
  `status = fired`, `fired_at = now`, `fired_event_id` (`:283-301`); one
  `db.flush()` for the whole batch (`:313`). It performs no business decision —
  the invariant that makes this a shared owner at all, and it holds.

**Caller inventory — every scheduling owner, named.**

| # | Scheduling callsite (`origin/main`) | `owner` | `entity_kind` | `purpose` | Declared trigger | Consumer |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | `app/services/access_invitations.py:130` | `auth.access_invitations` | `access_invitation` | `invitation_expiry_due` | `auth.access_invitation_expiry_due` | `events/handlers/identity_lifecycle_projection.py:33,47` → `access_invitations.consume_invitation_expiry` |
| 2 | `app/services/advance_renewal_invoicing.py:218` | `financial.advance_renewal_invoicing` | `subscription` | `advance_renewal_invoice_due` | `financial.advance_renewal_invoice_due` | `events/handlers/billing_lifecycle_projection.py:77` |
| 3 | `app/services/billing/contracts.py:731` | `billing.contracts` | `billing_contract` | `pending_terms_effective` | `billing.contracts.pending_terms_effective_due` | `billing_lifecycle_projection.py:784` → `contracts.py:1014` |
| 4 | `app/services/billing/contracts.py:976` | `billing.contracts` | *(second entry point, same family as 3)* | | | |
| 5 | `app/services/billing/unwall_paid_accounts.py:363` | `financial.walled_account_healing` | `subscriber` | `walled_account_healing_due` | `financial.walled_account_healing_due` | `billing_lifecycle_projection.py:237` → `unwall_paid_accounts.py:391` |
| 6 | `app/services/collections/lifecycle.py:274` | `collections.lifecycle` | `collections_case` | `collections_next_action` | `collections.case_action_due` | **NONE** |
| 7 | `app/services/customer_experience_handoffs.py:394` | `customer.experience_handoff` | `cx_handoff` | `cx_acceptance_due` | `sales.cx_acceptance_due` | `sales_lifecycle_projection.py:199` → `:431` |
| 8 | `app/services/sla_assignment.py:198` | `support.ticket_lifecycle` | `sla_clock` | `sla_breach_due` | `support.ticket_sla_breach_due` | `support_lifecycle_projection.py:54,138` |
| 9 | `app/services/support.py:2293` | `support.ticket_lifecycle` | `support_ticket` | `resolution_confirmation_due` | `support.resolution_confirmation_due` | `support_lifecycle_projection.py:131` → `support.py:2610` |
| 10 | `app/services/team_inbox_commands.py:992` | `communications.team_inbox_commands` | `inbox_conversation` | `snooze_wake` | `team_inbox.snooze_wake` | `support_lifecycle_projection.py:149` → `team_inbox_commands.py:2830` |

`cancel_timer` has **four** callsites in **three** files:
`access_invitations.py:105`, `advance_renewal_invoicing.py:202`,
`collections/lifecycle.py:249` and `:359`. **Five of the eight owners schedule
timers they never cancel** — they rely entirely on replacement-by-supersede.
That is a legitimate pattern, but it means "cancel" is exercised by three owners,
not eight.

`fire_due_timers` has exactly **two** non-test callers:
`app/tasks/durable_timers.py:28` (the Celery driver) and
`scripts/billing/billing_target_shadow.py:829` (a shadow-evidence CLI).

**Direct model access outside the service.** Three production services import
`DurableTimer`/`TimerStatus` directly and query the table without going through
`runtime_durable_timers`: `advance_renewal_invoicing.py:17`,
`billing/contracts.py:44`, `billing/unwall_paid_accounts.py:40` (queries at
`:346-352` and `:427-437`). The owner does not own all reads of its own table.
In an installable module this becomes an application-to-module model import; the port must
publish a typed read API instead.

### 2.3 `dotmac_sub:tests/test_durable_timers.py` (267 lines)

Nine tests. What they cover, exactly:

1. `test_scheduling_outside_an_owner_command_fails_closed` — asserts the exact
   error code `runtime.durable_timers.timer_requires_owner_command`.
2. `test_rescheduling_supersedes_and_bumps_the_generation` — sequential.
3. `test_cancel_is_idempotent` — `True` then `False`.
4. `test_firing_emits_only_the_declared_trigger_and_marks_the_timer` — asserts
   the payload carries `trigger`, `generation`, `entity_id` and no decision.
5. `test_scheduling_after_fire_continues_the_generation`.
6. `test_scheduling_after_cancel_continues_the_generation`.
7. `test_a_timer_that_is_not_due_does_not_fire`.
8. `test_a_superseded_timer_never_fires`.
9. `test_the_due_scan_is_bounded` — `batch_limit=2` over three timers.

**What they run on.** The `db_session` fixture (`tests/conftest.py:372`) binds a
single `Session` to a single `Connection` inside one outer transaction. The
`engine` fixture (`:298-368`) uses PostgreSQL only when `TEST_DATABASE_URL` is
set, otherwise an in-memory SQLite engine with a UUID/JSONB/geometry
compatibility layer. Sub's CI runs `tests/test_durable_timers.py` in the
`unit-shards` job (`.github/workflows/ci.yml:241-274`, `make test-ci-shard`),
which sets **no** `TEST_DATABASE_URL`. The PostgreSQL job
(`ci.yml:487-511`, `make test-integration`) runs `tests/integration/` only, and
the sole durable-timer reference there is the table name in a migration
structural list (`tests/integration/test_migrations_423_to_head.py:34`).

Two consequences:

- Every test above runs on SQLite, where `with_for_update(skip_locked=True)`
  compiles to nothing. **The claim mechanism is not merely unproven; it is
  exercised only on a backend that cannot express it.** This is the identical
  failure mode the numbering revalidation found (`numbering-source-variance.md`
  S4), reproduced in a second capability.
- Even switching that suite to PostgreSQL would not produce a concurrency
  proof, because the fixture is one connection. Any two-actor test must build
  its own engines, as `tests/test_outbox_relay.py` already does in this
  repository.

Three architecture tests reference timers
(`tests/architecture/test_advance_renewal_invoicing_boundary.py:20`,
`test_walled_account_healing_ownership.py:45`,
`test_support_lifecycle_chain_boundary.py:40,48,56`). All are **source-substring
assertions** (`assert "ScheduleTimerCommand(" in owner`). They pin structure,
not behaviour.

### 2.4 `dotmac_kernel.messaging.relay` — the in-repo claiming source

This is not named by any dossier and is the most consequential omission.

- `packages/dotmac-kernel/src/dotmac_kernel/migrations/versions/20260731_0011_outbox_relay_leasing.py:74-94`
  defines `public.claim_outbox_batch(text, integer, integer)` — a
  `SECURITY DEFINER`, `SET search_path = ''` function that atomically claims
  ready rows **and stale-leased rows** with
  `ORDER BY available_at FOR UPDATE SKIP LOCKED LIMIT p_batch`.
- `:98-126` defines `settle_outbox_event(...)`, which only settles a row the
  caller still holds the lease on (`WHERE id = p_id AND leased_by = p_worker
  AND status = 'claimed'`) and returns whether exactly one row moved.
- `packages/dotmac-kernel/src/dotmac_kernel/messaging/relay.py:39-48` — a typed
  `RelayPolicy` (`batch_size`, `max_attempts=8`, capped exponential backoff,
  `stale_lease_seconds=300`), with the retry/dead-letter decision in Python and
  the SQL kept mechanical.
- `messaging/models.py:60-97` — `OutboxEvent` carries `status`, `attempts`,
  `available_at`, `leased_by`, `leased_at`, indexed by
  `(status, available_at)` and `(status, leased_at)`.
  `messaging/models.py:108-141` — `PlatformOutboxEvent` is the same engine on
  the platform plane, with no `tenant_id`, `REVOKE`d from `app_user`
  (`20260731_0012_platform_outbox.py:105-107`).
- The transaction contract is explicit (`relay.py:15-20`): *"every function
  RECEIVES a `Session` … and only executes — it never constructs a session or
  commits; the worker owns the transaction boundary."*

**And it is proved on real PostgreSQL.** `tests/test_outbox_relay.py` opens a
dedicated least-privilege `outbox_dispatcher` engine, skips when
`TEST_DATABASE_URL` is unset, and includes
`test_stale_lease_is_reclaimed` (asserts `leased_by == "w2"` — an exact value)
and `test_concurrent_workers_never_double_claim` (two independent sessions, A's
transaction left open, `assert a_claim.isdisjoint(b_claim)` plus
`assert (a_claim | b_claim) & ids == ids`).

`available_at` **is a due time**. Mechanically, a pending outbox row scheduled
into the future already is a timer. What durable timers add over it is
*identity* (`owner`/`entity_kind`/`entity_id`/`purpose`), *generation*
(supersede and cancel **by identity**, not by row id), and *at-most-one-current*.
That is the whole delta, and it is exactly what Sub supplies.

### 2.5 `dotmac_sub:app/models/subscription_lifecycle_schedule.py` — the second Sub ledger

Sub has **two** scheduler ledgers, and the one with a real lease is not
`durable_timers`. `SubscriptionLifecycleSchedule` (`:37-116`) carries
`effective_at`, `next_attempt_at`, `attempt_count`, `max_attempts`,
`claimed_at`, `claim_expires_at`, `claimed_by`, `last_error_code`,
`last_message`, `applied_at`, `canceled_at`, `canceled_by`, plus a
`command_fingerprint` and an `idempotency_key`.
`app/services/subscription_lifecycle_schedules.py:156-192` claims one row with
`FOR UPDATE SKIP LOCKED` over `(due AND pending) OR (processing AND
claim_expires_at <= now)` and stamps the lease.

It is a *reference* for the lease columns, not a mandatory source: see defects
D12 and D13.

### 2.6 Second-scheduler sweep — ERP, CRM, Vendor CP

- **`dotmac_vendor_control_plane`:** no matches for any timer, due-scan, lease
  or scheduled-job pattern under `src/`. Nothing to retire; nothing competing.
- **`dotmac_erp`:** `app/models/scheduler.py` `ScheduledTask` is a Celery-beat
  cadence table (`interval_seconds`, cron fields, `last_run_at`). It schedules
  *recurring tasks*, not per-entity one-shot deadlines. Different concern; not
  a parallel authority for this capability, and not a source.
- **`dotmac_crm`:** the same `ScheduledTask` cadence table
  (`app/models/scheduler.py`), **plus one genuine per-entity due ledger**:
  `ResponseObligation` (`app/models/crm/response_obligation.py:24-45`) with
  `next_escalation_at`, `response_due_at`, `escalation_level`, `state`, indexed
  `("state", "next_escalation_at")`, drained by
  `app/services/crm/inbox/response_obligations.py:383-471` with
  `FOR UPDATE SKIP LOCKED` and a re-check of `next_escalation_at` after the
  claim (`:422-423`). This is a domain-specific scheduler ledger of exactly the
  kind ADR-0030 §4 forbids re-inventing. It is a **future adopter and
  retirement target**, not a source: it fuses escalation policy, recipient
  resolution and notification into the drain loop.
- Inside `dotmac_sub` itself, `team_inbox_reply_reminders.py:55-72` sweeps
  active assignments with `FOR UPDATE SKIP LOCKED` and recomputes deadlines
  from message history on every pass — a rescan standing in for a timer, and
  another retirement target. `app/services/billing/obligations.py:705` names
  its own path as *"business-wide scan standing in for a durable timer"*.

---

## 3. Does the eight-owner claim hold?

**No — it is one too high, and "production-proven" is stronger than what Sub's
own registry declares.**

`collections-sources.md:1380-1395` states Sub's implementation is
*"production-proven by eight other owners"* and lists eight **files**. Two of
those files (`sla_assignment.py`, `support.py`) schedule under the **same
`owner` string**, `support.ticket_lifecycle`. Counting owners rather than files:

- **8** distinct `owner` strings schedule timers in total.
- **7** of those are owners other than `collections.lifecycle`.
- **9** distinct `(owner, purpose)` families; **10** `schedule_timer` callsites.

So: *seven* owners other than collections, across eight files, nine timer
families. The dossier counted files as owners.

Three further corrections to the same paragraph:

1. **Collections is a scheduler too.** `collections/lifecycle.py:274` calls
   `schedule_timer` and `:249`/`:359` call `cancel_timer`. The dossier's "the
   one owner that has *not* cut over" is right in substance but wrong in
   mechanism. What is actually true is sharper and worth recording:
   `collections.case_action_due` has **no consumer anywhere in the repository**
   (`git grep -n "case_action_due" origin/main` returns exactly one line — its
   own emission at `lifecycle.py:282`), and `CollectionsLifecycle` itself is
   referenced only by its own module, `scripts/billing/billing_target_shadow.py`
   and `tests/test_collections_target_lifecycle.py`. Collections schedules a
   timer that fires into nothing, from a class nothing in production calls.
2. **Sub declares the facility SHADOWING, not cut over.** Its own SOT registry
   entry (`app/services/sot_registry/domains/financial_access/durable_timers.py:130`)
   records `state=AuthorityMigrationState.SHADOWING`, `old_owner="dunning_runner,
   prepaid_balance_sweep, and other scheduled account scans"`, with a
   `cutover_gate` that is not met and a `fallback_retirement` that has not
   happened: `dunning_runner` (`scheduler_config.py:719`) and
   `prepaid_balance_sweep` (`:741`) are both still registered.
3. **The declared event type does not exist.** The same registry declares
   `event_types=("runtime.timer_due",)` (`:117`), a string that appears nowhere
   else in the repository. The code emits `EventType.custom` with the real name
   in the payload (`runtime_durable_timers.py:51`, `:283-296`).

What *is* fairly described as production-proven: the identity model, the
partial-unique current index, supersede-on-replace, and the decision-free fire
contract, exercised by seven other owners with eight wired consumers on a
permanently enabled 60-second dispatch. That is a real base. It is not the same
claim as "complete and tested".

---

## 4. The generation-safety property

This is the reason the capability is shared, so it deserves the most precise
statement in this document.

### 4.1 What Sub actually does

Two mechanisms, at two different layers:

**Layer 1 — supersede before fire (in the owner).** `schedule_timer:155-157`
sets a still-`scheduled` predecessor to `superseded`, and `_fire_due:271-273`
scans `status = scheduled` only. A replaced timer therefore cannot fire at all.
This closes the common case and is enforced by the database through
`uq_durable_timer_current`.

**Layer 2 — revalidate at consume (in the consumer).** The window Layer 1 does
not close is: the timer fires (`status = fired`), the entity then moves on and a
new generation is scheduled, and the *already fired* trigger is delivered late.
`status` is `fired`, not `superseded`, so nothing structural rejects it.

### 4.2 Is Layer 2 proven? No — it exists once, and it is untested

`git grep -n "max(DurableTimer.generation)" origin/main` returns **one** line:
`app/services/billing/unwall_paid_accounts.py:431`. Only
`financial.walled_account_healing` re-derives the current generation and returns
`"skipped_stale_generation"` (`:439`) when the fired generation is no longer the
maximum.

Three consumers (`advance_renewal_invoicing.py:280-287`,
`billing/contracts.py:1035-1045`, `unwall_paid_accounts.py:412-421`) validate
that the *payload matches its own timer row* — owner, entity, purpose,
`output_event_type`, `generation`, `status is fired`, `fired_event_id`. That is
evidence integrity, not staleness: a genuinely stale but self-consistent trigger
passes every one of those checks.

Five consumers do neither. `support_lifecycle_projection.py:91-143` and
`identity_lifecycle_projection.py:37-41` extract only `entity_id` from the
payload and **discard `generation` entirely**. They instead re-read their own
entity and refuse on state — e.g. `consume_snooze_wake`
(`team_inbox_commands.py:2844-2860`) returns `skipped_missing`, `skipped_state`
or `skipped_resnoozed`. That is a defensible alternative discipline, but it is a
*different* one, chosen per consumer, with no shared contract.

And `git grep -n "skipped_stale\|stale_generation\|invalid_timer_evidence"
origin/main -- tests/ scripts/` returns **nothing**. The one generation-safety
implementation in the fleet, and the fail-closed evidence check next to it, are
**both untested**.

`expected_source_version` (`durable_timer.py:89-91`) is documented as the field
"the consumer revalidates … so a stale fire cannot act on superseded state". It
is set to a real value by exactly one owner (`billing/contracts.py:739`, `:984`)
and read by exactly one consumer (`contracts.py:1039`). The other seven owners
leave the column at its `default=1`, so the stored value asserts "source version
1" when it means "not applicable" — a silent default dressed as a claim.

### 4.3 What a correct one needs

The module must make staleness a **module guarantee, not a consumer
convention**:

1. The fired trigger carries `(timer_id, identity, generation)` and the module
   exposes one function — call it a `claim`/`accept` step — that atomically
   re-checks *within the consumer's transaction* that `generation` is still the
   maximum for that identity, and returns a typed
   `Stale`/`Superseded` outcome rather than raising.
2. That check must be a single locked statement against the identity, not a
   read-then-decide across two statements.
3. It must be impossible to consume a fired trigger without passing through it —
   a consumer that only receives `entity_id` (five of Sub's eight today) must
   not be expressible.
4. `expected_source_version` must be `NULL`-able and optional, so "no source
   version" is distinguishable from "version 1", and must be returned to the
   consumer rather than merely stored.
5. A stale rejection is an **observable outcome with its own record**, not a
   silent no-op, so drift is measurable.

---

## 5. Claiming and cancellation

### 5.1 How a timer is leased today: it is not

`_fire_due` (`:261-314`) holds a `FOR UPDATE SKIP LOCKED` row lock for the whole
batch, inside one `execute_owner_command` transaction, and the driver commits at
the end (`app/tasks/durable_timers.py:27-38`). There is no lease column, no
`claimed_by`, no `claim_expires_at`, no `attempts`, no visibility timeout.

- **Worker dies holding timers:** the transaction aborts, the locks release, and
  every row reverts to `scheduled`. Crash recovery is free and correct. Credit
  where due — this is *better* than a lease that must be reaped, and it means
  there is no stale-lease reaper to get wrong.
- **But there is no failure isolation.** All 200 timers in a batch share one
  transaction. If `emit_event` raises for one timer — a malformed payload, a
  constraint violation, an event-store failure — the **entire batch rolls
  back**. Because the scan is `ORDER BY due_at`, the same poison row is picked
  first on every subsequent run. One permanently failing timer blocks every
  timer behind it, indefinitely, with no attempt counter to notice and no
  dead-letter to escape to. This is head-of-line blocking on the fleet's only
  wake-up path. The SOT registry states the retry policy as *"A failed fire
  batch rolls back whole; timers stay scheduled and the next run retries them"*
  (`durable_timers.py:94-97`) — that is an accurate description of the defect,
  recorded as if it were the design.
- `SKIP LOCKED` gives concurrent runners disjoint batches, so two dispatchers do
  not double-emit. But this is exercised only on SQLite, where it compiles away
  (§2.3). Nothing in Sub proves it.

### 5.2 Cancellation versus fire

Both orders are correct **on PostgreSQL under READ COMMITTED**, by accident of
predicate re-evaluation rather than by design:

- **Fire first.** `_fire_due` locks the row. `cancel_timer`'s `_current_timer`
  uses plain `.with_for_update()` (no `skip_locked`), so it blocks. When fire
  commits, the row is `fired`; Postgres re-evaluates the `status == scheduled`
  predicate against the updated tuple under EvalPlanQual, the row no longer
  qualifies, `_current_timer` returns `None`, and `cancel_timer` returns
  `False`. Correct — the fire wins and the cancel reports it did nothing.
- **Cancel first.** Fire's `SKIP LOCKED` skips the locked row for that batch;
  after the cancel commits, the row is `canceled` and never fires. Correct.

Two caveats to carry forward:

- This depends on **READ COMMITTED**. Under REPEATABLE READ or SERIALIZABLE the
  blocked `SELECT … FOR UPDATE` raises `40001` instead of silently returning
  `None`. Nothing in Sub declares or tests the required isolation level. The
  module must state it, and prove the behaviour at it.
- `cancel_timer` returning `False` is **ambiguous**: it means both "there was no
  timer" and "the timer just fired and you lost the race". A caller that cancels
  a deadline because the entity was settled cannot tell whether a consequence is
  already in flight. The module contract must distinguish
  `NothingScheduled` from `AlreadyFired`.

### 5.3 The reschedule race — a first-use defect of the ERP shape

`_latest_timer` (`:91-102`) is
`SELECT … ORDER BY generation DESC LIMIT 1 FOR UPDATE`. Two consequences on
PostgreSQL:

- **No existing row:** `FOR UPDATE` over a predicate matching nothing takes no
  lock. Two concurrent first schedules for the same identity both compute
  `generation = 1` (`:154`) and both `INSERT` (`:171`). One violates
  `uq_durable_timer_generation` or `uq_durable_timer_current`. Because the
  insert runs inside the *owning business transition's* transaction, the
  `UniqueViolation` aborts that whole transition. There is no `ON CONFLICT` and
  no savepoint on this path. This is defect 1 of the ERP numbering audit,
  reproduced exactly.
- **Existing scheduled row at generation N:** the second caller blocks on
  gen N. When it unblocks, EvalPlanQual re-checks *that tuple only* — the
  predicate has no `status` filter, so it still qualifies and is returned as
  `latest`. The gen N+1 row the first caller just inserted is **not visible to
  the LIMIT 1 lock**. The second caller computes `generation = N + 1` and
  collides.

Neither can be observed on SQLite. Both are mandatory port deltas: the
establishment step must be conflict-safe (`INSERT … ON CONFLICT DO NOTHING`
then a locked read, or an identity-row lock taken before the generation is
computed), and it must never abort the caller's business transaction to report
a lost race.

---

## 6. Placement decision — selectable module

**Resolved 2026-08-15.** Michael accepted the module side of the candidate
decision recorded in §6.4. `dotmac-durable-timers` is a selectable module under
ADR-0028, with tenant-only, platform-only and both-plane selections. It imports
the kernel contract and declares `outbox_relay.v1`; it does not live in the
kernel lineage and it never imports a sibling module or a product assembly.

Sections 6.1–6.4 retain the cost analysis that informed that ruling. Their
kernel-placement recommendation is historical evidence, not current direction.

### 6.1 What the floor is today

`dotmac_kernel` is at `0.1.0a65`. Its migration lineage has **25** revisions and
its models declare **24 tables**, and it is composed by `alembic.ini`
`version_locations` as a single unconditional lineage. `ModulePlane` /
`supported_plane_sets` / `ProductAssemblySpec.module_planes` (ADR-0028) let an
assembly select planes **for a stateful module**. There is no equivalent for the
kernel. Every composed database gets every kernel table.

ADR-0017's amendment already records the cost of this: the effective kernel
floor is `0.1.0a53` after ADR-0023, and *"neither cutover-1 product can compose
a module lineage at its present pin"* — Vendor pins `a45`, Sub pins `a50`.
Raising the floor again is not free bookkeeping; it is the thing already
blocking two cutovers.

### 6.2 What this specific migration adds

If durable timers lands in the kernel and follows the `idempotency_models`
precedent (`IdempotencyRecord`, tenant, `tenant_id NOT NULL` + FORCE RLS +
isolation policy; `PlatformIdempotencyRecord`, no tenant column, `GRANT`ed to
`platform_api`/`app_admin`, `REVOKE ALL … FROM app_user`; migration
`20260810_0018_idempotency_one_owner.py:112,171-178,221`), then revision `0026`
adds to **every composed database, unconditionally**:

- **two tables** — `durable_timers` and `platform_durable_timers` — taking the
  kernel from 24 to 26 (a ~8% increase in the unconditional floor);
- one RLS policy plus `ENABLE`/`FORCE ROW LEVEL SECURITY` on the tenant table;
- one full grant/revoke set on the platform table;
- two partial unique indexes and two due-scan indexes;
- a native enum type, if the status column is modelled the way Sub models it —
  **which it should not be**, see D6;
- possibly a **third database role**, if the timer dispatcher does not reuse
  `outbox_dispatcher` (migrations `0011` and `0012` each create one:
  `outbox_dispatcher`, `platform_outbox_dispatcher`).

For an adopter that will never schedule anything, that is two permanently empty
tables, two indexes each, an RLS policy, a grant set, and a version-floor bump
they must take to get anything else in the same release. It is not catastrophic
and the `idempotency_models` precedent means it is not unprecedented — but the
precedent cuts both ways: `idempotency_records` earns its place because
at-most-once execution is genuinely universal (ADR-0014). "Wake me up later" is
not.

### 6.3 A tenant/platform split IS required

Not optional, and not inferable. ADR-0023 demands both planes be **declared,
never inferred from a missing `tenant_id`**, no foreign key across planes, no
nullable `tenant_id`, no sentinel tenant, no polymorphic scope column.

Sub's table has no tenant column at all — which under ADR-0023's gate reads as a
platform table, and it is not one; it is a single-tenant deployment's tenant
table with the column omitted. Adding `tenant_id NOT NULL` to the ported model
and mirroring a platform table is therefore a **mandatory greenfield delta with
no product-first source**, exactly as the numbering audit found for its platform
plane. `dotmac_kernel.messaging` is the shape to copy: `OutboxEvent` +
`PlatformOutboxEvent`, one behaviour, two declared tables, two roles.

Note the identity consequence: on the tenant plane the current-timer uniqueness
key becomes `(tenant_id, owner, entity_kind, entity_id, purpose) WHERE status =
'scheduled'`; on the platform plane it stays as Sub has it. Getting this wrong
in either direction is a cross-tenant timer collision or a silently
tenant-global deadline.

### 6.4 The candidate decision this raised — resolved by ADR amendment

The original ADR-0030 §4 named the owner `dotmac_kernel.durable_timers`. This
audit could not itself overturn an accepted decision, so it surfaced the
following question for Michael:

> Should durable timers be kernel-resident (unconditional floor for every
> adopter), or a **selectable dual-plane module** under ADR-0028 with
> `supported_plane_sets` — the shape `dotmac-approvals` already uses to support
> tenant-only, platform-only and both?

Arguments originally recorded before adjudication. For the kernel: the claiming engine it must
reuse (`messaging.relay`, migrations `0011`/`0012`) is kernel-resident, and a
module cannot depend on kernel *migration internals* without a prerequisite
binding; the outbox is already there; timers and the outbox are one drain
concern. For a module: seven of eight would-be adopters in this fleet today
schedule nothing, the floor is already the binding constraint on two cutovers,
and ADR-0028 exists precisely to stop unconditional plane installation.

**Historical recommendation, superseded by the 2026-08-15 ADR amendments:**
keep it in the kernel as the original ADR-0030 directed, *and* make the
reuse binding explicit in the same change — durable timers must be layered on
`claim_outbox_batch`-shaped SQL and the `RelayPolicy` retry/dead-letter
discipline, not beside them. If instead the implementation writes a fresh claim
loop, the module premise ("subscriptions and collections must not each invent a
scheduler ledger") has been violated by the very thing enforcing it, and the
placement question should be reopened before that code lands.

---

## 7. Defects

Numbered against `origin/main` at the heads in the header. Each marked
still-present / newly-found relative to what the dossiers record.

### D1 — the fire batch has no failure isolation (**newly found**, highest severity)

`app/services/runtime_durable_timers.py:261-314`. All due timers in a batch
(default 200, `:236`) are marked fired and their events emitted inside one
transaction, flushed once at `:313`. One failing emission rolls back the whole
batch; `ORDER BY due_at` (`:275`) reselects the same poison timer first on the
next run, forever. No `attempts`, no `max_attempts`, no dead-letter, no
per-timer savepoint. **Do not port.** The kernel's `RelayPolicy`
(`messaging/relay.py:39-48`: `max_attempts=8`, capped exponential backoff,
retained `dead` status) is the shape.

### D2 — concurrent first schedule aborts the caller's business transaction (**newly found**)

`:91-102` + `:154` + `:171`. `SELECT … LIMIT 1 FOR UPDATE` over an empty
predicate takes no lock; both callers insert `generation = 1`; one raises on
`uq_durable_timer_generation`, inside the owning transition's transaction. See
§5.3. Identical to `numbering-source-variance.md` defect 1. **Mandatory port
delta:** conflict-safe establishment plus an identity lock taken before the
generation is computed.

### D3 — concurrent reschedule collides on the generation unique (**newly found**)

`:91-102`. The `LIMIT 1 FOR UPDATE` cannot see a generation inserted by the
transaction it just waited on. See §5.3. Same fix as D2.

### D4 — the whole suite runs on SQLite, where the lock is a no-op (**still present**, and the dossiers do not record it)

`tests/conftest.py:298-368` + `.github/workflows/ci.yml:241-274`. `with_for_update`
and `skip_locked` at `:277` and `:101`/`:119` are never exercised under a real
row lock. The `db_session` fixture is single-connection, so even the PostgreSQL
lane could not express a race. **This is the numbering S4 finding, in a second
capability.** Everything in §8 is consequently new evidence, not inherited.

### D5 — no tenancy, no RLS, no grants (**still present**, recorded in `collections-sources.md:1394` as "and no `tenant_id`")

`app/models/durable_timer.py` has no tenant column;
`alembic/versions/433_durable_timers_collections_cases.py:60-101` issues no
`ENABLE`/`FORCE ROW LEVEL SECURITY`, no policy, and no `GRANT`/`REVOKE`. Under
starter hard rule 11 and ADR-0023 this cannot ship as-is. Both planes must be
declared. No product-first source exists for the platform plane.

### D6 — `status` is a native PostgreSQL enum (**newly found**)

`app/models/durable_timer.py:94-98` binds `Enum(TimerStatus, name="timerstatus")`;
the migration creates the type at `:29-36,56`. A closed native enum makes adding
a state (`dead`, `stale_rejected`, `expired`) a type migration in every adopter
database. ADR-0008 rules a new vocabulary a declaration registry, never an enum.
Port as a constrained string, as `messaging/models.py` does with
`OutboxStatus` values persisted as text.

### D7 — the declared event vocabulary does not match the emitted one (**newly found**)

The SOT registry declares `event_types=("runtime.timer_due",)`
(`sot_registry/domains/financial_access/durable_timers.py:117`); that string
appears nowhere else. The code emits `EventType.custom`
(`runtime_durable_timers.py:51`, with an inline TODO) and puts the real name in
`payload["trigger"]`. Consequence: **every** `EventType.custom` handler must
string-match the payload to decide whether the event is theirs —
`support_lifecycle_projection.py:48-56`, `identity_lifecycle_projection.py:32-36`,
`sales_lifecycle_projection.py`, `billing_lifecycle_projection.py:77`. N handlers
each doing a payload compare is a fan-out anti-pattern and a routing bug waiting
to happen. **Do not port.** In the module contract, `output_event_type` must be a declared
code under manifest rule 12, and routing must be by declared type.

### D8 — `output_event_type` is an unvalidated free string (**newly found**)

`:141-145` validates only `.strip()` non-empty against a `String(100)` column.
A typo produces a timer that fires into nothing, silently — which is exactly
what `collections.case_action_due` is today (§3). Under manifest rule 12 the
declared output must be owned by a module and refused at boot if undeclared.

### D9 — `expected_source_version` is a silent default (**newly found**)

`durable_timer.py:89-91`, `default=1`, `nullable=False`. Seven of eight owners
never set it and one consumer reads it. Stored "1" cannot be distinguished from
"not applicable". Make it nullable and return it to the consumer.

### D10 — the module's own read API is bypassed (**newly found**)

`advance_renewal_invoicing.py:17`, `billing/contracts.py:44`,
`billing/unwall_paid_accounts.py:40` import `DurableTimer` and query it directly
(`unwall_paid_accounts.py:346-352`, `:427-437`). Meanwhile the module's own
public reader `current_timer` (`:197-215`) has **zero** production callers. Port
the typed read API; do not export the model.

### D11 — no retention, ever (**newly found**)

`fired`, `canceled` and `superseded` rows are never deleted. There is no
`expires_at`, no pruning task, and no reference to `durable_timers` in any
retention or purge path in Sub. Every reschedule of a frequently-changing
deadline appends a permanent row. Contrast `IdempotencyRecord`, which carries
`expires_at` with an index (`idempotency_models.py:89`) and leaves retention as
the product's policy per ADR-0014. The module history must do the same.

### D12 — the secondary source hardcodes its lease and reads the host (**newly found**, in `subscription_lifecycle_schedules.py`)

`_LEASE_DURATION = timedelta(minutes=15)` at `:28` is not configurable;
`worker_id or socket.gethostname()` at `:137` couples worker identity to the
host. Under the repository's "everything by config" rule and ADR-0024's
no-host-coupling posture, neither ports. The kernel's `RelayPolicy
.stale_lease_seconds` and explicit `worker_id` argument
(`relay.py:47`, `:77`) are the correct shape.

### D13 — the secondary source commits inside the service (**newly found**)

`subscription_lifecycle_schedules.py:191`, `:264`, `:305`, `:124` call
`db.commit()` inside service functions. Starter hard rule 8 makes
`dotmac_kernel/db.py` the one transaction authority and `relay.py:15-20` states
the contract: receive a Session, never commit. Do not port the transaction
handling.

### D14 — `cancel_timer(...) is False` is ambiguous (**newly found**)

`:190-191`. "Nothing scheduled" and "you lost the race to the fire" return the
same value. See §5.2.

### D15 — the isolation level the correctness argument depends on is undeclared (**newly found**)

§5.2 holds under READ COMMITTED and raises `40001` under REPEATABLE READ.
Nothing in the model, the service, the registry or the tests states this.

### D16 — architecture guards are substring assertions (**newly found**)

`tests/architecture/test_advance_renewal_invoicing_boundary.py:23-24`,
`test_walled_account_healing_ownership.py:53-54,69`,
`test_support_lifecycle_chain_boundary.py:62`. Each asserts a literal appears in
a source file. They pin the shape of a call, not the behaviour of a lock. Under
ADR-0018 they carry no sensitivity proof and cannot be treated as evidence.

---

## 8. Do not port

- **Sub's owner-command framework.** `_require_participant` /
  `owner_command_active` (`:77-83`) and `execute_owner_command`
  (`app/services/owner_commands.py:341-427`) are a Sub-specific transaction
  authority that `db.begin()`s, `commit()`s and `rollback()`s, and validates a
  command manifest. It contradicts starter hard rules 8 and 9 head-on. The
  module equivalent of the "you must be inside the owning transition" guarantee
  is structural, not runtime: the schedule/cancel functions receive a `Session`
  and never commit, so they *cannot* be durable without the caller's
  transaction. **Record the loss honestly** — the fail-closed error
  `runtime.durable_timers.timer_requires_owner_command`, the one behaviour Sub's
  suite asserts by exact code, has no module counterpart and its guarantee
  becomes an architecture test rather than a runtime check.
- **`emit_event` / `EventType.custom` / Sub's event store.** The kernel's
  outbox is the transport. See D7.
- **`consume_owner_output`** (Sub's inbox/receipt owner). The kernel's
  at-least-once identity is `dotmac_kernel.idempotency`
  (`execute_once`, `fingerprint_of`, `IdempotencyConflict`,
  `IdempotentOutcome.replayed`) per ADR-0014. Bind to that; do not port a second
  receipt ledger.
- **Product vocabulary.** Every literal in the caller table of §2.2 —
  `access_invitation`, `subscription`, `billing_contract`, `subscriber`,
  `collections_case`, `cx_handoff`, `sla_clock`, `support_ticket`,
  `inbox_conversation`, and every `*_due` trigger name. `owner`, `entity_kind`
  and `purpose` are open registered strings; the module must know none of these
  values.
- **The native `timerstatus` enum** (D6).
- **The dialect-branching and SQLite compatibility posture.** `sqlite_where` on
  the partial index (`durable_timer.py:70`, migration `:99`) exists to make the
  SQLite lane build the index. The module targets PostgreSQL; a SQLite
  accommodation inside a shared owner is the `numbering-source-variance.md` S5
  finding and ADR-0024's "no product/provider switches".
- **Hardcoded lease duration and `socket.gethostname()`** (D12).
- **`db.commit()` anywhere in the service** (D13).
- **Silent fallbacks.** `expected_source_version` defaulting to 1 (D9); a
  free-string `output_event_type` that can be a typo (D8); `cancel → False`
  meaning two different things (D14).
- **Auto-creation on the read path.** Sub does not have this defect; note it as
  a constraint anyway, because the numbering audit found it in the sibling
  capability: a fire against an unconfigured or unknown identity must fail
  closed, never invent a row.

---

## 9. The boundary — what the timer owns and what it does not

Sub's own docstrings state this well and the module should adopt the wording
verbatim: the runtime *"performs no customer, invoice, funding, or access
decision — those belong to the consumer that declared the timer"*
(`durable_timer.py:13-14`). Generalised:

**`dotmac-durable-timers` OWNS:**

- the durable record that *something* should happen for
  `(scope, owner, entity_kind, entity_id, purpose)` at `due_at`;
- the generation of that record, its supersede-on-replace, its cancellation, and
  the database-enforced invariant of at most one current timer per identity;
- scheduling and rescheduling by atomically enqueueing one kernel outbox event
  per generation with `available_at = due_at`; the module owns no due-row scan;
- cancellation and stale-trigger rejection at acceptance time, including a
  typed stale verdict that returns both observed and current generations;
- emission of the caller's **declared** output as an opaque token, carrying
  identity, generation and `expected_source_version` and nothing else;
- the atomic staleness verdict at consume time (§4.3);
- both persistence planes, declared;
- append-only timer history and a retention mechanism that never deletes a
  currently scheduled timer.

**`dotmac_kernel.messaging` OWNS and the module REUSES:**

- due-event claiming and exclusive leasing through the tenant/platform outbox
  claim functions;
- stale-lease recovery, retry, bounded backoff and retained dead-letter state;
- dispatcher privileges and per-event batch isolation.

The timer module calls the kernel's enqueue surface and sets the returned
outbox row's `available_at` to the required `due_at` in the same transaction as
the timer transition. It never reads, claims or scans due timer rows. A relay
delivery calls the module's acceptance surface before any consumer effect; an
old or canceled generation therefore remains harmless even if its immutable
outbox record is delivered.

**The CALLER owns, and the module must be structurally unable to touch:**

- **what the deadline means** — grace expiry, dunning step, SLA breach, renewal,
  snooze wake. The module never branches on `purpose`.
- **when the deadline is** — business-calendar arithmetic, timezones, working
  hours, proration, contract terms. `due_at` is a required, timezone-aware
  input; the behavior engine reads no ambient clock. Any comparison instant is
  a required timezone-aware input.
- **whether the work should still happen** — the entity's current state. A
  non-stale timer means "your deadline arrived", never "act".
- **the work itself**, and its transaction. The module emits; it does not
  invoke a business function.
- **the vocabulary** — `owner`, `entity_kind`, `purpose`, `output_event_type`
  are the caller's declared codes.
- **the source-version semantics** — the module carries
  `expected_source_version` opaquely and returns it; it never interprets it.
- **retention policy** — the module offers the mechanism (ADR-0014's precedent).

The one-line test for any proposed API: *if the kernel needs to know what the
string means, it does not belong in the kernel.*

---

## 10. Draft `EXTRACTION.toml`

**Historical draft, superseded by the checked-in package dossier.** The
authoritative contract is now `packages/dotmac-durable-timers/EXTRACTION.toml`.
The block remains below as decision provenance; where it differs, the package
dossier and the current-state amendment above win.

```toml
schema_version = 1
package = "dotmac-durable-timers"
classification = "optional-module"
status = "audit-complete"
source_mode = "product-first-with-mandatory-port-delta"
owner = "Timer identity, generation allocation, scheduling and rescheduling, supersession, cancellation, current-generation verification, stale-trigger rejection, append-only timer history and terminal-history retention"
contract = "Given an explicit tenant or platform scope, an opaque (owner, entity_kind, entity_id, purpose) identity, a required timezone-aware due_at, a declared output code and optional opaque source evidence, atomically allocate the next generation, supersede the prior current generation, append history and enqueue one kernel outbox event whose available_at equals due_at. Cancellation returns one of Canceled, AlreadyFired, NothingScheduled or Stale. Trigger acceptance locks and re-derives the current identity state and returns both observed_generation and current_generation when stale, before the caller may run its effect. Retention never deletes a scheduled timer. The module declares and reuses outbox_relay.v1 for all claiming, leasing, stale-lease recovery, retry, backoff, dead-letter handling and dispatcher privileges; it contains no due-row scanner, claim loop, retry engine, consumer decision or ambient clock read."
source_repositories = [
  "dotmac_sub",
  "dotmac_starter_mt",
]
source_revisions = [
  "dotmac_sub:4489ca1712f3c263d914f2af0ebfcf044aa70605",
  "dotmac_starter_mt:7e0543004864845f0035c9ec325e3f5064c281cc",
  "dotmac-kernel-v0.1.0a67:ed3ac864b350d4556808a69496f999f764682442",
]
source_paths = [
  # identity, generation, one-current-per-identity, decision-free fire
  "dotmac_sub:app/models/durable_timer.py",
  "dotmac_sub:app/services/runtime_durable_timers.py",
  "dotmac_sub:app/tasks/durable_timers.py",
  # lease columns on a per-entity schedule row (secondary reference; see D12/D13)
  "dotmac_sub:app/models/subscription_lifecycle_schedule.py",
  "dotmac_sub:app/services/subscription_lifecycle_schedules.py",
  # the claiming engine, REUSED not re-implemented (in-repo, already kernel-resident)
  "dotmac_starter_mt:packages/dotmac-kernel/src/dotmac_kernel/messaging/outbox.py",
  "dotmac_starter_mt:packages/dotmac-kernel/src/dotmac_kernel/messaging/relay.py",
  "dotmac_starter_mt:packages/dotmac-kernel/src/dotmac_kernel/messaging/platform_relay.py",
  "dotmac_starter_mt:packages/dotmac-kernel/src/dotmac_kernel/messaging/worker.py",
  "dotmac_starter_mt:packages/dotmac-kernel/src/dotmac_kernel/messaging/platform_worker.py",
  "dotmac_starter_mt:packages/dotmac-kernel/src/dotmac_kernel/messaging/models.py",
  "dotmac_starter_mt:packages/dotmac-kernel/src/dotmac_kernel/migrations/versions/20260731_0011_outbox_relay_leasing.py",
  "dotmac_starter_mt:packages/dotmac-kernel/src/dotmac_kernel/migrations/versions/20260731_0012_platform_outbox.py",
  # plane split precedent
  "dotmac_starter_mt:packages/dotmac-kernel/src/dotmac_kernel/idempotency_models.py",
  "dotmac_starter_mt:packages/dotmac-kernel/src/dotmac_kernel/migrations/versions/20260810_0018_idempotency_one_owner.py",
]
preserved_tests = [
  "dotmac_sub:tests/test_durable_timers.py",
  "dotmac_starter_mt:tests/test_outbox_relay.py",
  "dotmac_starter_mt:tests/test_outbox_dispatcher_grants.py",
  "dotmac_starter_mt:tests/test_platform_outbox_relay.py",
  "dotmac_starter_mt:tests/test_platform_outbox_dispatcher_grants.py",
]
contract_consumers = []
candidate_consumers = [
  "dotmac_sub",
  "dotmac_crm",
  "dotmac_vendor_control_plane",
]
composition_boundary = "Selectable dual-plane module under ADR-0028: the manifest declares disjoint tenant and platform tables plus tenant-only, platform-only and both-plane supported sets; every assembly makes one explicit ModulePlaneSelection. Tenant tables carry tenant_id UUID NOT NULL, composite identities, RLS ENABLE and FORCE, policy and exact grants in their creating migration. Platform tables carry no tenant column or RLS, are reachable by the online platform role and are revoked from app_user across all table and column privileges. No foreign key crosses planes. Status and caller vocabulary are constrained/open strings, never PostgreSQL enums. The module imports kernel contracts only, declares outbox_relay.v1 as a common prerequisite and enqueues through the kernel outbox with available_at = due_at. It imports no application or sibling module and implements no scanner, claim, lease, retry, backoff, dead-letter or dispatcher privilege. Schedule, cancel, accept and retention receive a Session and explicit timezone-aware instants where needed, never commit and never read an ambient clock."
inventory_evidence = [
  "docs/inventories/durable-timers-sources.md",
  "docs/inventories/collections-sources.md",
  "docs/inventories/subscriptions-sources.md",
  "docs/inventories/numbering-source-variance.md",
  "docs/adr/0017-adoption-is-the-scarce-resource.md",
  "docs/adr/0023-dual-plane-modules-declare-both-persistence-planes.md",
  "docs/adr/0030-cloud-commerce-is-composed-from-complete-domain-owners.md",
]
first_cutover = "dotmac_sub, tenant plane, ONE timer family at a time, ordered by consumer discipline rather than by volume. Slice 1 is financial.walled_account_healing (app/services/billing/unwall_paid_accounts.py:363 schedule, :391 consume): it is the only owner in the fleet that already re-derives the current generation (:431) and the only one that raises a typed evidence error (:412-421), so it is the one caller whose consumer semantics the module can adopt without inventing them — and cutting it over first converts that untested convention into a proved module guarantee. Slice 2 is billing.contracts (contracts.py:731, :976, consume :1014), the only owner that uses expected_source_version, which forces the opaque-carry contract to be exercised. Slice 3 is auth.access_invitations, the simplest full schedule/cancel/consume triangle. Only then the five owners whose consumers discard generation entirely (support.ticket_lifecycle x2, communications.team_inbox_commands, customer.experience_handoff, financial.advance_renewal_invoicing): each must first choose between the generation verdict and an explicit entity-state guard, and declare which. collections.lifecycle is NOT a cutover, it is a repair: it schedules collections.case_action_due with no consumer anywhere (lifecycle.py:282) from a class no production path calls, so it needs a consumer or a deletion before it means anything. dotmac_crm follows on the tenant plane, retiring crm_response_obligations' next_escalation_at drain (services/crm/inbox/response_obligations.py:383-471). dotmac_vendor_control_plane is the first platform-plane adopter; the platform plane has no product-first source and is written fresh against the same parameterised suite."
shadow_and_drift = "Neither the timer facility nor any of its consumers has a real-database test, so the shadow phase is the first genuine evidence and the ten PostgreSQL proofs in the audit's §11 land before any caller is touched. For each Sub timer family: schedule into BOTH the local durable_timers row and the module in the same transaction, let the local task and kernel relay deliver independently, and compare identity, generation, due instant, cancellation and declared output, recording divergence rather than failing. Divergence classes to expect and adjudicate before cutover: (a) the module succeeds under a concurrent reschedule where Sub's LIMIT-1 lock can raise UniqueViolation (runtime_durable_timers.py:91-102); (b) a poison emission rolls back Sub's whole 200-timer batch (:261-314), while the kernel relay dead-letters only the poison outbox event and delivers the remainder; (c) the module rejects a fired-then-superseded trigger that seven of Sub's eight consumers accept today; (d) tenant_id attribution, since Sub's rows carry none and the module requires one. Do not backfill fired/canceled/superseded history: it has no retention policy in Sub. Backfill only rows whose current status is scheduled, and give every row a decided tenant because the source has no tenant column."
local_copy_retirement = "dotmac_sub retires, in order: the eight owners' direct DurableTimer imports and queries (advance_renewal_invoicing.py:17, billing/contracts.py:44, billing/unwall_paid_accounts.py:40 including the queries at :346-352 and :427-437); the dead public reader current_timer (runtime_durable_timers.py:197-215, zero production callers); schedule_timer/cancel_timer/fire_due_timers and the durable_timers table; app/tasks/durable_timers.py and the durable_timer_dispatch_runner registration (scheduler_config.py:1947-1953); the collections.case_action_due emission (collections/lifecycle.py:274-285) or the collections owner itself; and the two rescan-instead-of-timer paths the facility exists to replace, dunning_runner (scheduler_config.py:719) and prepaid_balance_sweep (:741), whose retirement is the ADR 0007 Phase 5 cutover gate Sub's own SOT registry records as unmet (sot_registry/domains/financial_access/durable_timers.py:130-152). subscription_lifecycle_schedules is a SEPARATE decision: it is a command ledger with a lease, not only a timer, and only its claim/lease/retry columns are in scope. dotmac_crm retires crm_response_obligations' next_escalation_at drain loop but KEEPS the obligation row, which carries escalation policy the module must never learn. A two-directional import/caller ratchet reaches zero in each repository before its local owner is deleted. No permanent mirrored timer table and no second due scan is allowed in either product."
next_action = "The selectable-module decision and released outbox_relay.v1 gate are met. Wait for the active starter-billing changes to the namespace ledger, kernel metadata and root lockfile to settle; integrate their exact head before allocating the timer namespace or creating the package. Then write the ten PostgreSQL proofs before the first behavior implementation, run them only in an isolated exact-revision worktree on Observe, and shadow financial.walled_account_healing first. Do not touch a caller whose consumer discards generation until that consumer has declared its stale-generation discipline."
```

---

## 11. PostgreSQL test matrix

Ten proofs. **All of this is new.** Sub contributes no real-database timer test
and its suite runs where the lock does not exist; the kernel's outbox tests
prove the *claiming* half only. Everything must land before the first caller is
cut over.

All require a real migrated PostgreSQL (`make test-db-up`) and belong at the top
level of `tests/`, never under `tests/unit`, per this repository's testing
model. The existing `tests/test_outbox_relay.py` is the harness precedent to
follow.

### Common harness requirements

- **Independent connections.** Separate `Session` objects on separate DBAPI
  connections. Sub's `db_session` shape (one connection, one outer transaction)
  makes every race test pass vacuously and must not be copied.
- **Distinct roles.** `app_user` (tenant, RLS forced), a platform role, and the
  kernel's reused `outbox_dispatcher` / `platform_outbox_dispatcher`. Assert
  `current_user` inside the test — a proof run as the migration owner or a
  superuser silently bypasses RLS.
- **A two-thread rendezvous** (`threading.Barrier(2)`) so both actors are
  demonstrably inside their transactions before either proceeds, or the
  open-transaction shape used at `tests/test_outbox_relay.py:161-186`.
- **A declared isolation level** on every concurrency proof, asserted from
  `SHOW transaction_isolation` inside the test (D15).
- **A sensitivity proof for every guard** (ADR-0018): a companion that removes
  the guard — patch establishment to query-then-insert, skip `FOR UPDATE`, or
  drop the staleness re-derivation — and asserts the proof *fails*. A
  concurrency test that cannot be made to fail is not evidence.
- **Observables the guard alone produces.** Prefer a SQLSTATE, a named
  constraint, an exact status string or an exact integer over timing, liveness
  or set membership. Where a set assertion is unavoidable, pair it with a
  coverage assertion, as `test_concurrent_workers_never_double_claim` does.
- **No default `due_at` and no ambient clock** in any test.

---

### Proof 1 — two concurrent first schedules for one identity

**Setup.** No timer row for `(scope, owner, entity_kind, entity_id, purpose)`.

**Mechanism.** Two threads, independent connections, both open a transaction,
meet at `Barrier(2)`, both call schedule for the identity with different
`due_at` values, both commit.

**Pass.** Both succeed. Exactly two rows exist with `generation` **exactly**
`{1, 2}`. Exactly one row has `status = 'scheduled'` and it is generation 2;
generation 1 is `'superseded'`. Neither thread saw an exception.

**Failing run.** `psycopg.errors.UniqueViolation`, SQLSTATE **`23505`**, with
`diag.constraint_name` **`uq_durable_timer_generation`** or
`uq_durable_timer_current` — the D2 defect exactly. Or two rows both
`'scheduled'` (the partial unique index missing). Or one thread hangs to
statement timeout.

**Sensitivity.** Monkeypatch establishment to Sub's
`ORDER BY generation DESC LIMIT 1 FOR UPDATE` and assert this proof fails with
SQLSTATE `23505` on the named constraint.

**Extend.** Repeat at N=8 threads; assert generations are exactly `1..8` with no
gaps and exactly one `'scheduled'`.

---

### Proof 2 — concurrent reschedule over an existing generation

**Setup.** One `'scheduled'` timer at generation N.

**Mechanism.** As Proof 1, both threads reschedule the same identity.

**Pass.** Generations `{N, N+1, N+2}` exist, `N` and `N+1` are `'superseded'`,
`N+2` is the sole `'scheduled'`.

**Failing run.** `23505` on `uq_durable_timer_generation` — the D3 defect, where
the `LIMIT 1` lock cannot see the row inserted by the transaction it waited on.

**Sensitivity.** Same patch as Proof 1.

---

### Proof 3 — kernel-relay claim exclusivity across two connections

**Setup.** Schedule twelve due timers across distinct identities in one scope
and capture the twelve kernel outbox ids written by the module. The module has
no claim API and no due-row scan.

**Mechanism.** Two dispatcher sessions on independent connections. A claims a
batch of 5 inside an **open, uncommitted** transaction; B then claims a batch of
20; both commit. (This is `tests/test_outbox_relay.py:161-186`'s shape, which is
the one already proved in this repository.)

**Pass.** `a_claim` and `b_claim` are disjoint **and** their union is exactly the
twelve captured outbox ids — the coverage half is what stops two empty claims passing
vacuously. `len(a_claim) == 5`. Every claimed row's `leased_by` equals the exact
worker id that claimed it.

**Failing run.** Any id in both sets; or the union missing an id (B blocked
instead of skipping, meaning `SKIP LOCKED` was dropped); or B blocking to
statement timeout.

**Sensitivity.** Remove `skip_locked` and assert B blocks until statement
timeout (SQLSTATE **`57014`**).

---

### Proof 4 — kernel-relay crash recovery and one accepted timer effect

**Setup.** One due timer.

**Mechanism.** Worker `w1` claims and commits the lease, then its session is
closed without settling — the crash. Age `leased_at` by an hour with a direct
`UPDATE`. Worker `w2` claims with `stale_lease_seconds=60`.

**Pass.** `leased_by` is **exactly `'w2'`** (an exact value, not "some worker")
and `attempts` remains exactly 0 because reclaim is not a delivery failure.
`w2` delivers the existing trigger through the module acceptance surface and
settles it. Exactly one outbox row exists for that generation, the consumer
effect count is exactly one, and the timer's current state is exactly `fired`.
The at-least-once control re-delivers that same payload and proves acceptance
does not execute the effect twice.

**Failing run.** `leased_by` still `'w1'` (no reclaim); a second trigger row is
inserted; the consumer effect count becomes 2; or the outbox row remains
`claimed` forever.

**Sensitivity.** Set `stale_lease_seconds` to a value larger than the aged
interval and assert `w2` claims nothing — proving the reclaim is time-bounded
rather than unconditional.

---

### Proof 5 — cancellation versus fire, both orders, at a declared isolation level

**Setup.** One due `'scheduled'` timer. Assert
`SHOW transaction_isolation = 'read committed'` in both sessions.

**Mechanism, case A (fire wins).** Relay delivery enters the module acceptance
transaction, locks the timer identity and holds it open. A second session calls
cancel and blocks. Acceptance records `fired` and commits; cancel unblocks.

**Pass A.** Cancel returns the typed **`AlreadyFired`** outcome (not `False`,
not `NothingScheduled` — D14). The row's `status` is exactly `'fired'`. Exactly
one trigger emitted.

**Mechanism, case B (cancel wins).** Cancel locks the timer identity and holds
its transaction open. Relay delivery invokes acceptance on a separate
connection and blocks on that same identity.

**Pass B.** After cancel commits, acceptance rejects the already-enqueued
trigger as canceled and the consumer effect count remains exactly zero. The
timer's current state is exactly `canceled`; relay settlement succeeds, so the
immutable outbox event is not replayed forever.

**Mechanism, case C (nothing there).** Cancel an identity with no timer.

**Pass C.** Typed **`NothingScheduled`**, distinct from case A's value.

**Failing run.** A returning the same value as C; a consumer effect in B; or,
under REPEATABLE READ, an unhandled SQLSTATE **`40001`** — which is the D15
observable and should be asserted explicitly in a variant that sets
`SET TRANSACTION ISOLATION LEVEL REPEATABLE READ`, so the isolation requirement
is documented by a test rather than by a comment.

**Sensitivity.** Drop the `FOR UPDATE` from the cancel read and assert case A
produces a `'canceled'` row after a `'fired'` one — a lost update.

---

### Proof 6 — generation safety under concurrency (the load-bearing proof)

**Setup.** One timer at generation N whose due outbox trigger is claimed but
**not yet accepted**.

**Mechanism.** Thread 1 (the owning transition) reschedules the identity to
generation N+1 and commits. Thread 2 then delivers the generation-N trigger to
the module's acceptance step. Then the interleaved variant: both threads meet at
a barrier, thread 1 reschedules while thread 2 is inside its acceptance
transaction.

**Pass.** The acceptance step returns a typed **`Stale`** verdict naming
`observed_generation = N` and `current_generation = N+1` — exact integers, not a
boolean. The consumer's effect did **not** run: assert the consumer's own
observable (a counter, a written row) is unchanged. A stale-rejection record
exists with its identity and both generations. Then the positive control:
deliver the generation-N+1 trigger and assert the verdict is `Current` and the
effect ran exactly once.

**Failing run.** The acceptance step returning `Current` for generation N — the
stale fire acts on superseded state, which is the entire failure mode this
capability exists to prevent, and which seven of Sub's eight consumers are
exposed to today. Or the interleaved variant returning `Current` because the
re-derivation read outside a lock.

**Sensitivity.** Remove the re-derivation (leaving only the row-field
comparison that `unwall_paid_accounts.py:412-421` performs) and assert this
proof fails — demonstrating that field validation is not staleness detection.

---

### Proof 7 — one poison timer does not block the batch

**Setup.** Twenty due timers and their twenty module-created outbox rows. One
delivery transport raises deterministically for one outbox id.

**Mechanism.** Run the dispatcher once, then repeatedly to `max_attempts`.

**Pass.** After the first pass: exactly **19** timer effects are accepted and
their timers are `fired`; the poison timer remains `scheduled`, while its
kernel outbox row has `attempts = 1` and a future `available_at`. After
`max_attempts` passes, that outbox row is exactly `dead`, has `last_error`, and
is retained. The poison timer remains retained for explicit reconciliation;
the other 19 effects remain exactly once and are never replayed.

**Failing run.** Zero timer effects run on the first pass — Sub's D1 behaviour, where
one failure rolls back the whole batch. Or the poison timer retrying forever
with no `attempts` ceiling. Or the 19 good timers emitting twice because the
batch was retried wholesale.

**Sensitivity.** Wrap the whole loop in one transaction (Sub's shape) and assert
this proof fails with 0 fired.

---

### Proof 8 — tenant-plane RLS, isolation, and the composite key

**Setup.** The same `(owner, entity_kind, entity_id, purpose)` configured under
tenant A and tenant B, each with timers.

**Mechanism.** Through the `app_user` role with the tenant GUC set per
statement; no `BYPASSRLS`.

**Pass.** From A's context: only A's rows are visible; a direct
`SELECT`/`UPDATE`/`DELETE` naming B's row id affects **exactly zero** rows;
accepting A's timer leaves B's current state unchanged; both tenants hold a
`'scheduled'` timer for the identical identity simultaneously, proving the
partial unique is `(tenant_id, …)` and not `(…)`. Assert inside the test that
`current_user = 'app_user'` and that `pg_class.relforcerowsecurity` is true for
the tenant table.

**Failing run.** A non-zero row count for B's id from A's context; or the second
tenant's schedule raising SQLSTATE **`23505`** on `uq_durable_timer_current`,
which means `tenant_id` was left out of the key.

---

### Proof 9 — platform-plane revocation and reachability

**Setup.** A configured platform timer with one accepted outbox trigger.

**Mechanism.** As `app_user`, attempt `SELECT`, `INSERT`, `UPDATE`, `DELETE`
against every declared platform table, **and a column-level `SELECT` of each
column** — ADR-0023 requires revocation across every table *and column*
privilege, and a leftover column grant does not surface in a table-level check.
Enumerate the tables from the declaration, not a hand-written list, so a table
added later is covered automatically. Then, as the platform role, run a full
schedule → fire → accept cycle.

**Pass.** Every `app_user` statement raises `InsufficientPrivilege`, SQLSTATE
**`42501`**. The platform role completes the cycle.

**Failing run.** Any `app_user` statement returning rows or a row count. A false
pass to guard against: `app_user` failing with `UndefinedTable` (`42P01`)
because the table is missing rather than revoked — assert the SQLSTATE is
specifically `42501`, and separately assert as the platform role that the table
exists.

---

### Proof 10 — plane parity, no clock, append-only history

**Setup.** The same timer expressed on both planes.

**Mechanism.** Parameterise the entire behaviour suite over tenant and platform
scope from **one shared test body** — a fixture returning
`(engine, role, scope_factory)` — so a behaviour added later cannot reach only
one plane. Assert the parameterisation count is exactly twice the case count.

**Pass, parity.** Identical inputs produce identical generation sequences,
identical staleness verdicts and identical cancellation outcomes on both
planes; module-created events traverse the corresponding kernel relay with the
same claim/retry/dead-letter policy.

**Pass, no clock.** Drive the whole suite with `due_at`, `recorded_at` and
acceptance instants in a
period unrelated to today (e.g. 2029), and assert the outputs are byte-identical
to a run where they coincide with the real clock. Pair it with a static guard —
an architecture test asserting the capability's source contains no
`date.today`, `datetime.now`, `datetime.utcnow` or `time.time` — with its own
sensitivity proof, since a check over an empty set passes for the wrong reason.

**Pass, append-only.** As `app_user`, attempt to `UPDATE` a `'fired'` row's
`generation` or `fired_at` and to `DELETE` it; assert refusal by grant
(SQLSTATE `42501`), not by convention. Assert a retention entry point exists and
that deleting expired history leaves every `'scheduled'` row untouched (D11).

**Structural assertions.** The two planes' tables are distinct; no foreign key
crosses them; the tenant table has `tenant_id NOT NULL` with FORCE RLS; the
platform table has no tenant column and no nullable or sentinel substitute; and
`status` is a constrained string, with `pg_type` holding **no** enum type for
it (D6).

**Failing run.** A case executing on only one plane; the planes disagreeing;
`pg_type` containing a `timerstatus` enum; a nullable `tenant_id`; or a
cross-plane foreign key.

---

## 12. Adoption and retirement

1. **Placement and prerequisite gates are met.** ADR-0017/0030 select the
   module, P11 is met, and released kernel `0.1.0a67` publishes the structurally
   verified `outbox_relay.v1` prerequisite.
2. **The Billing allocation gate is resolved.** Billing and Durable Timers land
   through one integrated Starter PR at kernel `0.1.0a70`, with separate
   namespace rows and no cross-module imports. Future changes must preserve
   that one shared harness and release-metadata surface.
3. **Land the ten proofs first.** Neither source contributes a real-database
   timer test; Sub's suite runs where the lock does not exist. This matrix is the
   capability's entire correctness evidence base, not an addition to an
   inherited one.
4. **Reuse the claiming engine; do not rewrite it.** The module schedules by
   writing a kernel outbox row with `available_at = due_at`. Any module claim
   function, timer due scan or timer dispatcher role is a boundary violation.
5. **Sequence Sub's cutover by consumer discipline, not volume.** Slice 1 is
   `financial.walled_account_healing` — the only owner that already re-derives
   the current generation, and therefore the only one whose consumer semantics
   the module can adopt rather than invent. Slice 2 is `billing.contracts`, the
   only user of `expected_source_version`. The five owners whose consumers
   discard `generation` must each declare a staleness discipline before they are
   touched.
6. **Treat `collections.lifecycle` as a repair, not a cutover.** It emits
   `collections.case_action_due` with no consumer anywhere, from a class no
   production path calls. It needs a consumer or a deletion before "cutting it
   over" means anything.
7. **Sub's cutover is not complete until the sweeps are gone.**
   `dunning_runner` (`scheduler_config.py:719`) and `prepaid_balance_sweep`
   (`:741`) are the `fallback_retirement` Sub's own registry records as unmet.
   Until they are removed, the fleet is running a timer *and* a rescan for the
   same deadlines.
8. **`dotmac_crm` is the second adopter and a genuine retirement.**
   `crm_response_obligations`' `next_escalation_at` drain
   (`services/crm/inbox/response_obligations.py:383-471`) is a per-entity
   scheduler ledger. Retire the drain; keep the obligation row, which carries
   escalation policy the module must never learn.
9. **Backfill only `'scheduled'` rows.** Fired history has no retention policy in
   Sub (D11); copying it imports an unbounded table into every adopter database.
   And note the attribution problem: Sub's rows carry no `tenant_id`, so every
   backfilled row needs a decided tenant with no source column to derive it from.
10. **A green suite is not a cutover.** A local writer is deleted only after its
   two-directional import/caller ratchet reaches zero in that repository, the
   shadow divergence classes in §10's `shadow_and_drift` have each been
   adjudicated, and the legacy scan it replaces has been removed from the
   scheduler — not merely disabled.
