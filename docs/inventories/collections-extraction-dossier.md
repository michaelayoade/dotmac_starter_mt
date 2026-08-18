# `dotmac-collections` extraction dossier (content, not a package)

**As of:** 2026-08-18
**starter:** `8d4ddfd9` · **Sub:** `d1a1a913` · **ERP:** `0f4b1698` · **vendor CP source evidence:** `8984801`
**Evidence:** `docs/inventories/collections-sources.md`
**Contracts:** `docs/superpowers/specs/2026-08-14-collections-policy-consequence-and-timer-contracts.md`
**Canary surface:** `docs/superpowers/specs/2026-08-18-collections-canary-first-surface.md`
**Adoption sequence:** `docs/superpowers/plans/2026-08-14-collections-sub-vendor-cp-adoption.md`
**Decision boundary:** ADR-0020 § 4 + its 2026-08-14 amendment (A1, A2, A6),
as amended for Collections by ADR-0032

## Why this is a markdown file and not `packages/dotmac-collections/EXTRACTION.toml`

P11 is met by the production evidence recorded in ADR-0017's 2026-08-18
amendment, and ADR-0032 resolves the inbound/outbound contracts and tenant-first
plane. Package creation is nevertheless not safe in the current checkout:
`starter-billing` has active overlapping changes to the namespace ledger,
kernel/package metadata, root dependency metadata, lockfile and migration/test
harness. Those shared allocations must be integrated at one exact head, never
overwritten by a parallel stateful-package diff.

So this file holds the dossier **content**, in the exact field names and shapes of
`packages/dotmac-files/EXTRACTION.toml`, ready to be moved into a package root
once the shared-file overlap clears. `status` is
`audit-complete` and may not become `approved`, `adopted` or `reuse-proven` here —
those transitions are earned by the cutovers in the adoption plan, not by a
document.

Every `source_paths` and `preserved_tests` entry below was reconfirmed at Sub
`d1a1a913e287ffadaf21b7da7be448f2c28b5483`. From the previous clean pin
`4489ca1712f3c263d914f2af0ebfcf044aa70605`, only
`app/services/billing_enforcement_guards.py` changed in the Collections source
set (14 additions, 2 removals); the Collections owners and preserved tests did
not otherwise drift.

---

## Dossier content

```toml
schema_version = 1
package = "dotmac-collections"
classification = "optional-module"
status = "audit-complete"
source_mode = "product-first"
owner = "The collections decision on an identified receivable or coverage exposure: immutable versioned policy ladders, exact case membership, grace, payment arrangements and installments, typed notice and CollectionActionRequestedV1 requests, owner receipts, reconciliation, closure and reopening; tenant plane first, with any later platform plane demand-gated"
contract = """
Accept AssessCollectionExposureV1 carrying identity, explicit scope and trigger \
provenance but NEVER a money amount; reread an exact current per-currency \
receivable or coverage position through ReceivablesReader at every decision; \
resolve the applicable immutable policy version and pin it to a case; \
evaluate an arbitrary-length ordered step ladder against declared anchors; \
schedule, replace and cancel the exact next-action timer by identity through a \
Timer port; emit typed, idempotent notice requests and \
CollectionActionRequestedV1 to the service that owns the state; record the \
owner's typed receipt, refusal or durable \
failure as append-only evidence; and open, pause, resume, resolve and reopen \
the case from settlement, correction and cancellation facts.

NOT owned, and refused by the gate: invoice or receivable AMOUNTS; payments, \
settlements, allocations, deallocations, reversals, refunds, credit or prepaid \
funding; subscription, contract, offer or licence lifecycle; any mutation of \
service, access, RADIUS, entitlement, allocation or licence state; customer \
contact details, consent decisions, channel selection, templates, locale or \
delivery; provider/PSP clients, credentials, webhook verification, retries or \
checkpoints; durable-timer infrastructure (dotmac-durable-timers owns identity, \
generations and firing); \
approval workflow (ADR-0026); ERP's general ledger, journals, fiscal periods, \
tax returns or statutory collections reporting; and ANY SECOND BALANCE \
CALCULATION — the module holds no field a caller could read as "the balance", \
and derives no amount from invoices, payments, credits or provider state.
"""
source_repositories = [
  "dotmac_sub",
  "dotmac_erp",
  "dotmac_vendor_control_plane",
]
source_paths = [
  "dotmac_sub:app/models/collections.py",
  "dotmac_sub:app/models/collections_case.py",
  "dotmac_sub:app/models/payment_arrangement.py",
  "dotmac_sub:app/models/durable_timer.py",
  "dotmac_sub:app/services/collections/_core.py",
  "dotmac_sub:app/services/collections/lifecycle.py",
  "dotmac_sub:app/services/collections/postpaid_policy.py",
  "dotmac_sub:app/services/collections/prepaid_policy.py",
  "dotmac_sub:app/services/collections/mode_policies.py",
  "dotmac_sub:app/services/collections/grace_policy.py",
  "dotmac_sub:app/services/collections/prepaid_balance_sweep.py",
  "dotmac_sub:app/services/collections/scheduled.py",
  "dotmac_sub:app/services/prepaid_enforcement_planner.py",
  "dotmac_sub:app/services/prepaid_enforcement_state.py",
  "dotmac_sub:app/services/payment_arrangements.py",
  "dotmac_sub:app/services/payment_arrangement_staff_actions.py",
  "dotmac_sub:app/services/dunning_staff_actions.py",
  "dotmac_sub:app/services/billing_communication_policy.py",
  "dotmac_sub:app/services/enforcement_window.py",
  "dotmac_sub:app/services/billing_enforcement_guards.py",
  "dotmac_sub:app/services/runtime_durable_timers.py",
  "dotmac_sub:app/services/web_billing_dunning.py",
  "dotmac_sub:app/web/admin/billing_dunning.py",
  "dotmac_sub:app/services/account_lifecycle.py",
  "dotmac_sub:app/services/sot_registry/domains/financial_access/collections.py",
  "dotmac_sub:docs/adr/0007-end-to-end-billing-target-architecture.md",
  "dotmac_sub:docs/designs/DUNNING_STAFF_SAFE_ACTIONS.md",
  "dotmac_sub:docs/FINANCIAL_ACCESS_ENFORCEMENT.md",
  "dotmac_sub:tests/architecture/billing_scheduled_sweep_baseline.txt",
  "dotmac_erp:app/services/finance/reminder_service.py",
]
preserved_tests = [
  "dotmac_sub:tests/test_collections_target_lifecycle.py",
  "dotmac_sub:tests/test_collections_dunning_services.py",
  "dotmac_sub:tests/test_collections_services.py",
  "dotmac_sub:tests/test_payment_arrangements.py",
  "dotmac_sub:tests/test_payment_arrangement_safe_actions.py",
  "dotmac_sub:tests/test_dunning_staff_safe_actions.py",
  "dotmac_sub:tests/test_prepaid_enforcement_planner.py",
  "dotmac_sub:tests/test_prepaid_balance_sweep.py",
  "dotmac_sub:tests/test_prepaid_threshold_resolver.py",
  "dotmac_sub:tests/test_prepaid_sweep_budget.py",
  "dotmac_sub:tests/test_prepaid_notice_progression.py",
  "dotmac_sub:tests/test_prepaid_enforcement_state_owner.py",
  "dotmac_sub:tests/test_prepaid_flag_clear_on_restore.py",
  "dotmac_sub:tests/test_grace_policy_sot.py",
  "dotmac_sub:tests/test_financial_access_consequence_evidence.py",
  "dotmac_sub:tests/test_financial_access_restore.py",
  "dotmac_sub:tests/test_notification_queue_suppression.py",
  "dotmac_sub:tests/test_enforcement_window.py",
  "dotmac_sub:tests/test_enforcement_window_gate.py",
  "dotmac_sub:tests/architecture/test_financial_action_boundaries.py",
  "dotmac_sub:tests/architecture/test_financial_ownership.py",
  "dotmac_sub:tests/architecture/test_grace_policy_boundary.py",
  "dotmac_sub:tests/architecture/test_billing_target_architecture.py",
]
contract_consumers = []
candidate_consumers = ["dotmac_sub", "dotmac_vendor_control_plane"]
composition_boundary = "ADR-0032 and ADR-0024 § 2: dotmac-collections imports no sibling business module and no assembly. AssessCollectionExposureV1 carries identity/scope/trigger provenance and no amount. ReceivablesReader is the only current-money path and is called at every decision; the application assembly binds its authoritative producer. Every consequence leaves as CollectionActionRequestedV1, which the assembly maps to a locally owned command; the owner's typed receipt is the only proof of result. The timer surface is a port/fake/conformance suite bound by the assembly to released dotmac-durable-timers; Collections imports no timer sibling and contains no scanner, claim loop or retry engine. Each adopter installs its own lineage and owns its rows; product identity is reached only through a product-owned link helper, never a foreign key into a product table."
persistence_planes = "ADR-0032 applies hard rule 27 and ADR-0028: revision 1 is tenant-only because Sub is the only real Collections adopter. The manifest explicitly declares tenant tables and empty platform_tables; that one plane is atomic, so supported_plane_sets stays empty and Sub supplies no ModulePlaneSelection for a choice that does not exist. Every table carries tenant_id UUID NOT NULL, composite uniqueness/FKs including tenant_id, RLS ENABLE and FORCE, policy and exact grants in its creating migration, plus cross-tenant canaries. A later additive release may declare platform tables only after Vendor CP's demand gate has a real authoritative reader, overdue exposure and named request consumers; that release adds every genuinely supported subset and requires each assembly to select one. No foreign key crosses planes; nullable/sentinel tenants and polymorphic scope columns are always refused."
inventory_evidence = [
  "docs/inventories/collections-sources.md",
  "docs/inventories/billing-sources.md",
  "docs/adr/0020-billing-owns-operational-receivables.md",
  "docs/adr/0023-dual-plane-modules-declare-both-persistence-planes.md",
  "docs/adr/0024-apps-compose-by-synchronizing-data.md",
  "docs/adr/0032-collections-assesses-identity-and-requests-actions.md",
  "docs/superpowers/specs/2026-08-14-collections-policy-consequence-and-timer-contracts.md",
  "docs/superpowers/specs/2026-08-18-collections-canary-first-surface.md",
  "docs/superpowers/plans/2026-08-14-collections-sub-vendor-cp-adoption.md",
]
first_cutover = "dotmac_sub is cutover 1, and is also the qualifying product-first source — the opposite of dotmac-billing's ordering, and correct for the same reason billing's is. Sub has TWO live owners to retire (the dunning_runner postpaid workflow and the prepaid_balance_sweep) plus a complete tested-but-unwired ADR-0007 Phase 5 shadow implementation, so a Sub cutover is the only place the shared contract meets real cases, real policy sets, real grace, real arrangements and a real access-lifecycle owner. dotmac_vendor_control_plane is cutover 2, behind a demand gate: at 8984801 it has no invoice, overdue, dunning, arrangement or consequence code at all (verified by grep for dunning/overdue across src/ — zero files), so a Vendor-first cutover would install empty platform tables, declare guessed action codes with no consumer, and retire nothing. It would prove neither the policy engine (no policy would ever fire), nor the consequence boundary (no owning service would ever return a receipt), nor the retirement property the whole programme exists for. Installing empty tables is not adoption. Vendor CP starts only once it has an authoritative production receivables owner, at least one non-test receivable past its due time with a positive exact collectible amount, and a NAMED consequence owner whose action code can be declared together with its real consumer. dotmac_erp is exclusion evidence, not an adopter: app/services/finance/reminder_service.py sends AR reminders and owns no case, ladder, grace, arrangement or consequence state machine, and ADR-0020 A6 gives ERP none of the three commercial modules."
shadow_and_drift = "Follows the adoption plan's S0-S5 and adds nothing to it. S0 classifies every live row and every writer with a TOTAL classifier and no default bucket; ambiguous rows become entity-scoped work items with a one-cycle deadline and do not stop unaffected cases. S1 ports contracts and canaries before models or routes. S2-S4 land policy/cases/consequence-requests, then arrangements, then grace, in SHADOW ONLY — the module writes shadow cases and action previews and no notice or consequence may escape. S5 compares the live owner and the module shadow for the full candidate cohort by exact subject/reason/currency/source version, over one complete production ladder window per active policy, with deterministic replay over production-derived inputs where wall-clock length is unreasonable: case existence; pinned policy and current/next step; exact next-action time and timer generation; notice request and suppression reason; arrangement/grace shield scope and expiry; requested consequence and idempotency identity; close/reopen after settlement or correction; and product access outcome observed ONLY as the owner's receipt. Every mismatch is classified source defect, module defect, approved behaviour change, or unresolved — no unexplained bucket passes. Expected deliberate differences: one workflow for advance and arrears instead of two scanners; arbitrary versioned ladders instead of a fixed four-state chain and mutable steps; exact per-entity timers instead of account-wide periodic sweeps; case-scoped consequence REQUESTS instead of direct credential writes; exposure-scoped arrangements instead of blanket account shields; and strict per-currency exact money with no de-minimis epsilon. Two drift sources are specific to this module and must be watched by name: (a) Sub's postpaid run commits ONCE for the whole run and has no per-account error isolation, so a shadow run and a live run can disagree simply because the live run aborted — every parity comparison records whether the live run completed; (b) fleet-wide billing_health_reasons still enters the live preview fingerprint at _core.py:714 -> :741, so a fleet-scoped observation invalidates live fingerprints for reasons outside the entity, and shadow fingerprints deliberately will not move with it. During the later dotmac-billing authority switch, the ReceivablesReader returns Unavailable(retryable=True) for the whole coupled invoice/settlement/allocation window; no case advances, no consequence is emitted, and a timer that fires into it reschedules its own identity with a bumped generation."
local_copy_retirement = "Sub must delete or reduce to historical-only, each behind its own two-directional ratchet with a sensitivity proof, and lowering the baseline in the same change as the removal: dunning_runner (scheduler_config.py:713-723) and DunningWorkflow (_core.py:2522-2821); prepaid_balance_sweep (scheduler_config.py:733-745) and its 758-line executor plus the 725-line planner; PrepaidSweepCycleState (models/collections.py:267-293), whose own docstring already marks it TRANSITIONAL — a timer has no cycle, so the cursor has no successor; Subscriber.prepaid_low_balance_at / .prepaid_deactivation_at and prepaid_enforcement_state.py, replaced by one DurableTimer per (owner, entity, purpose) with a generation; the direct credential writes at _core.py:918-921 and :1475-1476, and the dead _throttle_account/_restore_throttle at :1637-1777, replaced by a typed request whose owner is account_lifecycle; the Invoices.mark_overdue_system call and apply_prepaid_overlap_hold inside the dunning SCAN (_core.py:2554, :2562-2567), which are collections writing a billing fact; local dunning_cases / dunning_action_logs / policy_dunning_steps writers and the collections interpretation of mutable PolicySet fields; the shadow collections_cases writers and the whole unwired ADR-0007 Phase 5 stack once its tests pass against the module; the hardcoded notice copy and the subject-string dedupe at _core.py:1780-1883; and dead policy fossils (enforcement_health_blocked at :1953, DunningWorkflow.resolve_cases_for_account at :2823-2857). Sub keeps product adapters only: local identity and authorization, policy assignment, the communication adapter, and the consequence executor (account_lifecycle remains the SOLE writer of Subscription.status/.access_state). At pinned source revision d1a1a913e287ffadaf21b7da7be448f2c28b5483, tests/architecture/billing_scheduled_sweep_baseline.txt ratchets dunning_runner and prepaid_balance_sweep in both directions but carries NO sensitivity proof. The uncommitted 2026-08-18 Sub adoption worktree adds a separate syntax-only AST scanner, exact JSON count baseline and planted-mutation sensitivity suite covering legacy classes/functions/tables, schedules, timer and credential assignments, invoice/overlap writers, notice delivery/subjects, ambient clocks, direct access-owner calls, module-alias access/receivable consumers, private imports and the flat shim. On Observe at that exact base, format and lint passed and the suite passed 4 tests; planted credential and aliased legacy-access sites failed the primary ratchet at credential_write:radius_profile_id 4 -> 5 and legacy_access_call:restore_account_services 9 -> 10 before clean reruns passed. This freezes the debt; it neither cuts authority nor proves adoption. Every retirement must lower the matching count in the same change. A product-local copy of the extracted pure engine is itself a ratchet entry. Retaining old tables for retention does not retain their authority."
next_action = "P11 and the contract-name/shape decisions are complete (ADR-0017 amendment and ADR-0032). Five executable canary files now start RED by design: the contract, pure-domain and timer-port suites fail only because dotmac_collections is absent; the alias-hardened boundary scanner's twelve planted/clean sensitivity cases pass; and the stateful scanner's complete synthetic package plus eleven planted defects pass while both live assertions refuse the missing package. The pure-domain suite pins deterministic immutable publication, one arbitrary-ladder evaluator, explicit missing-anchor evidence, exact exposure-scoped arrangement membership and schedules, explicit grace evaluation, and owner-receipt replay/conflict behavior. A separate product-first fixture/guard preserves 17 normalized scenarios from 22 exact pytest nodes at the Sub pin; its primary and five planted mutations pass, and an AST verifier confirmed every source node. After integrating the active starter-billing session's exact shared-file head, move this content to packages/dotmac-collections/EXTRACTION.toml, allocate the namespace/prefix/branch in the same package-creation diff, explicitly declare only tenant tables with empty platform_tables and an atomic plane contract, then turn the public-schema, ReceivablesReader, timer fake and architecture canaries green before behavior. dotmac-durable-timers remains a separate unreleased owner today: Collections may not implement timer infrastructure, and timer-backed behavior, shadow due-step parity and live cutover wait for its released contract and Sub adoption. Vendor platform persistence remains absent until its real-case demand gate."
```

---

## Plane evolution note (ADR-0032, ADR-0023, ADR-0028)

**Revision 1 is tenant-only.** Sub is the only real adopter, so the manifest
explicitly declares tenant `tables`, an empty `platform_tables`, and no
`supported_plane_sets`. That is an atomic one-plane contract, not a selectable
subset: Sub's assembly supplies no `ModulePlaneSelection`, and the current
kernel rejects one with `atomic plane contract`. The plane is never inferred
from a prerequisite binding or from a missing column.

The ladder evaluator, case state machine, grace calculator, arrangement
coverage derivation and action-request builder remain persistence-free. This is
what lets a later real platform adopter reuse one behavior without shipping
unused platform persistence today.

Every revision-1 table has `tenant_id UUID NOT NULL`, tenant-composite unique
keys and foreign keys, and RLS ENABLEd and FORCEd with its policy and exact
grants in the creating migration. `TenantScope(tenant_id)` is never nullable,
and `link_tenant_collection_subject()` is the only product relation seam.

**A future platform plane is additive and demand-gated.** Once Vendor CP has a
real authoritative receivables reader, overdue exposure and named notice/action
consumers, the same lineage may add separately declared `platform_tables`,
supported platform selections, `PlatformScope()`, platform link helper, exact
online-role reachability and complete `app_user` table/column revocation. No RLS
or tenant column exists on that plane. No foreign key crosses planes or points
from module tables into a Sub or Vendor domain table.

**Rejected, explicitly:**

- `platform=True` as a flag on a shared model — it makes isolation a runtime
  conditional on data and gives one code path two opposite security contracts.
- a **nullable `tenant_id`** — the column stops being an isolation key; the
  kernel already has one documented exception of this shape (`domain_settings`)
  whose cost is a split read/write policy pair, and ADR-0017's own amendment
  records a kernel defect where exactly this nullability let a row persist that
  the resolver could not reach.
- a **sentinel / fake vendor tenant** — every query and report then has to know
  which tenant id means "not a tenant"; that knowledge is unwritten, spreads by
  copy-paste, and is wrong the first time someone forgets it. ADR-0020 A6 says
  "**No fake tenant**" for Vendor CP in as many words.
- a **polymorphic scope column** (`scope_kind` + nullable `scope_id`) — a UUID
  PostgreSQL does not know means anything destroys referential integrity and
  turns the isolation predicate into a conditional on data.
- **a second module** for a later platform plane — it would duplicate the lifecycle,
  the reason registry, the transition guards and the ladder evaluator, which is
  the entire reason the module exists, to avoid duplicating four `CREATE TABLE`
  statements.

Empty Vendor tables are not adoption. The platform declaration arrives only
with its real consumer and security canaries.

---

## Parity-test inventory

Every path was confirmed present at Sub `d1a1a913`. "Proves" is what the test
body asserts, not what its name suggests.

### Must port and keep passing

| Test (Sub) | Lines | Behaviour it proves |
|---|---|---|
| `tests/test_collections_target_lifecycle.py` | 324 | exact overdue and underfunded proposals in `Decimal`; partial settlement arithmetic; a prepaid planner never touches a receivable; **fail-closed on an incomplete opening source, with a work-item fingerprint** (the authority-transition refusal the `ReceivablesReader` generalises); each step replaces the *exact* next-action timer, leaving one scheduled at generation 2 and one superseded; close cancels timers and stages restore evidence; terminal-state replay returns the same consequence id |
| `tests/test_collections_dunning_services.py` | 1,471 | the live postpaid ladder: notice-runway gates; day-0 policies using real overdue age rather than the step day; arrangement, payment-proof and prepaid-credit shields; reconciliation holds excluded; non-collectible residuals never opening a case; paused cases never escalating; payment resolving open but not paused cases; unallocated settled credit applied before dunning decides, in any currency; the configured dedupe window honoured |
| `tests/test_prepaid_enforcement_planner.py` | 407 | the prepaid ladder as a **pure plan**: stale-timer and mode-change lock repair; terminal-service stale lock resolved without reactivation; drift reported without mutation; future anchor without coverage blocking adverse action; always using the materialized funding owner; zero grace suspending even when the notice is fault-suppressed; non-zero grace not starting until the warning is queued |
| `tests/test_prepaid_balance_sweep.py` | 969 | 30 behaviours including: an existing configured grace timer is not reset on rerun; zero grace suspends immediately; a positive-but-insufficient wallet suspends when the current period is unfunded; a funded period does not warn; funded recovery clears timers and restores; rerun does not re-warn or double-suspend; weekend is an ordinary enforcement day; arrangement and payment-proof shields; billing health observed but not blocking; postpaid accounts untouched; a stale timer repaired after the account leaves the cohort; a blocked suspend leaves the timer unarmed and retries |
| `tests/test_financial_access_consequence_evidence.py` | 412 | preview→confirm creates exactly one lock, one evidence row and one audit event and links the action log; **replay with the same idempotency key returns the same consequence with no second lock**; a stale preview is a hard reject with zero side effects; an *ineligible* consequence is still durably recorded; restore emits exactly `{lock_resolved, credential_restored, dunning_case_resolved}` and restores the exact pre-throttle profile; restore never guesses a missing pre-throttle profile |
| `tests/test_dunning_staff_safe_actions.py` | 271 | exact eligible/skipped membership with a reason per row; explicit confirmation required; transition + action log + audit commit together; **an audit failure rolls back every selected transition**; a changed case rejects a stale preview; close gated on canonical per-currency receivables as exact `Decimal` |
| `tests/test_payment_arrangements.py` | 980 | arrangement proposal, approval, installment schedule, fulfilment, default and cancellation |
| `tests/test_payment_arrangement_safe_actions.py` | 187 | arrangement preview, fingerprint and explicit-confirmation shape |
| `tests/test_grace_policy_sot.py` | 183 | precedence account override → policy set → billing-mode default with a distinguishable source; offsets beginning **after** grace ends (23:59 in grace, 00:01 actionable); explicit zero grace preserved and immediately actionable; invalid policy-set id and invalid grace days each a stable typed fail-closed error; naive instants normalised to UTC |
| `tests/test_prepaid_threshold_resolver.py` | 467 | threshold provenance and minimum exposure; account override beating the default; paid entitlement meaning no renewal requirement; a paid invoice without an entitlement blocking enforcement until reconciled; **price currency must match the configured enforcement currency**; missing price failing closed; discounts and unit-price overrides; terminal and postpaid subscriptions excluded; batch cost independent of account count |
| `tests/test_prepaid_notice_progression.py` | 165 | a missing contact route arms the timer **and** opens an SLA'd work item with a stable fingerprint; a phone-only customer is warned over non-email channels; the work item resolves when the account leaves the cohort; a notice shield expires after max hours so a forgotten ticket cannot park an account forever |
| `tests/test_notification_queue_suppression.py` | 127 | the marketing/transactional scope rule at the transport: a marketing send to an unsubscribed address never reaches the transport, **the same address still gets its invoice**, and a hard bounce (`all` scope) stops even the invoice |
| `tests/test_financial_access_restore.py` | 191 | an underfunded payment does not restore prepaid or clear timers; a funded top-up does; overdue debt prevents a payment restore at the owner; the suspend owner refuses once debt clears or the prepaid account is funded |
| `tests/test_collections_services.py` | 136 | imported Splynx deposits excluded from enforcement balance; native ledger credits still projecting |
| `tests/test_prepaid_enforcement_state_owner.py` | 72 | first observation wins and is not reset; each transition emits exactly one typed event; a second clear is a no-op; invalid and missing account identities fail closed |
| `tests/test_prepaid_flag_clear_on_restore.py` | 34 | restore clears both prepaid timer fields inside the caller's transaction, so a just-paid customer is not deactivated on a pending timer |
| `tests/test_prepaid_sweep_budget.py` | 158 | an exhausted budget defers accounts and still completes the cycle; the keyset cursor resumes and completes; planning is batched, not per account — **evidence for why a sweep needs a cursor at all, i.e. § "a sweep is not a timer"** |
| `tests/test_enforcement_window.py` / `test_enforcement_window_gate.py` | 89 / 69 | bounded windows with an exclusive end; midnight wrapping; configured timezone including `Africa/Lagos`; a bad timezone falling back; weekends treated identically; outside the window yielding a typed defer |
| `tests/architecture/test_financial_action_boundaries.py`, `test_financial_ownership.py`, `test_grace_policy_boundary.py` | — | who may construct `FinancialAccessConsequence` and its evidence, and where grace may be decided — the ownership guards whose module equivalents are the no-second-writer tests |
| `tests/architecture/test_billing_target_architecture.py` | 417 | the shrink-only baselines, including `test_no_new_scheduled_financial_sweep`, which fails in **both** directions |

### Belongs to P3, not here

`tests/test_durable_timers.py` (267 L) proves scheduling outside an owner command
fails closed; reschedule supersedes and bumps the generation; cancel is
idempotent; firing emits only the declared trigger and makes no business
decision; **a superseded timer never fires**; the due scan is bounded. This is the
`Timer` contract suite in full. It ports with the P3 facility and is **not**
duplicated into collections — collections ports only the port, the fake, and the
parametrized suite that binds them.

### No adequate source test — must be written new

Each of these is a required module behaviour with nothing to port. Flagged so
that "we ported the tests" is never mistaken for "these are covered."

| Behaviour | Why there is nothing to port |
|---|---|
| A case pinned to the policy version that opened it | No versioning **mechanism** exists. `PolicySet` has no version column; `DunningCase.policy_set_id` pins a mutable pointer; `_core.py:2647` re-reads steps live; the shadow `CollectionsCase` pins nothing about policy at all |
| An arbitrary-length ladder | The shadow ladder is a fixed four-state enum with per-state timestamp columns; the live ladder is `day_offset` rows but with no version |
| Cancelling/replacing a pending timer on settlement | Proven only in the **unwired** shadow stack; the live paths have no timer identity to cancel |
| A failed consequence being durable and retryable | The live code swallows restore failures (`_core.py:2805-2812`, `:2947-2957`) and a test **asserts the swallow is correct**. `FinancialAccessConsequence` has no attempt count, no `next_retry_at`, no failed status |
| Consent honoured at the collections **decision** | Proven at the queue runner only. No test drives a dunning or prepaid notice through a consent-suppressed contact — and the sweep would classify it as `delivery_unavailable`, indistinguishable from a broken transport |
| No account-wide consequence for a scoped debt | The live postpaid code **is** account-wide (`_core.py:650-674`); no test in either stack asserts that debt on service A leaves service B online |
| Exact money as a rule | Held in practice but never asserted: no test forbids `float` in a money path, no rounding/repr test, and currency mixing is rejected only in the threshold resolver |
| The same scenario under `advance` and `arrears` | **No parity test exists.** The two planners are tested for mutual *exclusion*, which is the opposite property |
| A consequence request reaching a real owner from the corrected stack | Structurally impossible today: `collections.consequence_requested` and `collections.case_action_due` have producers and no consumer |
| Platform-plane behaviour of any kind | Vendor CP has no collections code, so every platform-plane test is new |

---

## Consistency with the parallel adoption plan

`first_cutover`, `shadow_and_drift` and `local_copy_retirement` above are written
to match
`docs/superpowers/plans/2026-08-14-collections-sub-vendor-cp-adoption.md` and do
not introduce an independent sequence. ADR-0032 resolves that plan's original
contract-shape disagreements: identity-only `AssessCollectionExposureV1` plus
`ReceivablesReader`, `CollectionActionRequestedV1` plus the typed owner receipt,
and tenant-only revision-one persistence. The plan's 2026-08-18 amendment now
records the same decisions.

## Index entry owed

`docs/inventories/README.md` already indexes `collections-sources.md`, but its
description predates the 2026-08-18 revalidation and it has no row for this
dossier. The file is currently modified in the active `starter-academy-adoption`
and `starter-omni-inbox` worktrees, so it was deliberately not touched here.
After those owners settle, update the existing source row and add the dossier
row in the integration change.
