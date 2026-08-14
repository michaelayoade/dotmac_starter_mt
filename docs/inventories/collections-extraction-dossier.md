# `dotmac-collections` extraction dossier (content, not a package)

**As of:** 2026-08-14
**starter:** `5417e51` · **Sub:** `27c76aaee` · **ERP:** `0f4b1698` · **vendor CP:** `8984801`
**Evidence:** `docs/inventories/collections-sources.md`
**Contracts:** `docs/superpowers/specs/2026-08-14-collections-policy-consequence-and-timer-contracts.md`
**Adoption sequence:** `docs/superpowers/plans/2026-08-14-collections-sub-vendor-cp-adoption.md`
**Decision boundary:** ADR-0020 § 4 + its 2026-08-14 amendment (A1, A2, A6)

## Why this is a markdown file and not `packages/dotmac-collections/EXTRACTION.toml`

ADR-0017's P11 lineage gate is closed, and ADR-0020 § 6 is explicit that the
decision "authorizes a boundary, not an implementation start." The adoption
plan's G3 places `EXTRACTION.toml` creation *after* G1 (P11 cleared) and names it
as part of the package-creation diff, alongside the `MIGRATION_OWNER_LEDGER`
allocation.

So this file holds the dossier **content**, in the exact field names and shapes of
`packages/dotmac-files/EXTRACTION.toml`, ready to be moved verbatim into a package
root at Stage E. Placing a real `EXTRACTION.toml` at a package root today would
create the package, which is the thing the gate forbids. `status` is
`audit-complete` and may not become `approved`, `adopted` or `reuse-proven` here —
those transitions are earned by the cutovers in the adoption plan, not by a
document.

Every `source_paths` and `preserved_tests` entry below was confirmed to exist at
the stated revision.

---

## Dossier content

```toml
schema_version = 1
package = "dotmac-collections"
classification = "optional-module"
status = "audit-complete"
source_mode = "product-first"
owner = "The collections decision on a receivable or coverage exposure: immutable versioned policy ladders, one case per subject/service/reason/currency, grace, payment arrangements, and typed consequence and notice REQUESTS with their idempotency identity and owner receipts, on explicit tenant and platform planes"
contract = """
Read an exact per-currency receivable or coverage position through a published \
port; resolve the applicable immutable policy version and pin it to a case; \
evaluate an arbitrary-length ordered step ladder against declared anchors; \
schedule, replace and cancel the exact next-action timer by identity through a \
Timer port; emit typed, idempotent notice and consequence REQUESTS to the \
service that owns the state; record the owner's receipt, refusal or durable \
failure as append-only evidence; and open, pause, resume, resolve and reopen \
the case from settlement, correction and cancellation facts.

NOT owned, and refused by the gate: invoice or receivable AMOUNTS; payments, \
settlements, allocations, deallocations, reversals, refunds, credit or prepaid \
funding; subscription, contract, offer or licence lifecycle; any mutation of \
service, access, RADIUS, entitlement, allocation or licence state; customer \
contact details, consent decisions, channel selection, templates, locale or \
delivery; provider/PSP clients, credentials, webhook verification, retries or \
checkpoints; durable-timer infrastructure (P3 owns generations and firing); \
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
composition_boundary = "ADR-0024 § 2 and ADR-0020 A1: dotmac-collections imports no sibling business module and no assembly. It never imports dotmac-billing and never queries a billing table; its only read path is the ReceivablesReader port, and the consuming assembly wires Billing's published output to it. Every consequence leaves as a typed request the assembly maps to a locally owned command, and the owning service's typed receipt is the only proof the consequence was applied. Each adopter installs its own lineage and owns its own rows; applications share the package contract, never a schema, a case row or a subject identity. Product identity is reached only through a product-owned link helper, never a foreign key into a product table."
persistence_planes = "ADR-0023 / ADR-0020 A2: one persistence-free behaviour engine plus two DECLARED planes. tables (tenant): tenant_id UUID NOT NULL, composite uniqueness including tenant_id, composite FKs, RLS ENABLEd AND FORCEd in the creating migration, cross-tenant canaries, TenantScope never nullable, link_tenant_collection_subject(). platform_tables (control plane): no tenant column at all, no RLS, control-plane-wide uniqueness, REVOKE ALL from the tenant app role across every table and column privilege, schema USAGE plus at least one of SELECT/INSERT/UPDATE/DELETE for the online platform role, PlatformScope(), link_platform_collection_subject(). Separate models and repositories per plane; a table appears in exactly one plane; NO FOREIGN KEY CROSSES THE PLANES in either direction. Refused by the gate and by review: a platform=True flag, a nullable tenant_id, a sentinel or fake vendor tenant, and a polymorphic scope_kind + nullable scope_id column."
inventory_evidence = [
  "docs/inventories/collections-sources.md",
  "docs/inventories/billing-sources.md",
  "docs/adr/0020-billing-owns-operational-receivables.md",
  "docs/adr/0023-dual-plane-modules-declare-both-persistence-planes.md",
  "docs/adr/0024-apps-compose-by-synchronizing-data.md",
  "docs/superpowers/specs/2026-08-14-collections-policy-consequence-and-timer-contracts.md",
  "docs/superpowers/plans/2026-08-14-collections-sub-vendor-cp-adoption.md",
]
first_cutover = "dotmac_sub is cutover 1, and is also the qualifying product-first source — the opposite of dotmac-billing's ordering, and correct for the same reason billing's is. Sub has TWO live owners to retire (the dunning_runner postpaid workflow and the prepaid_balance_sweep) plus a complete tested-but-unwired ADR-0007 Phase 5 shadow implementation, so a Sub cutover is the only place the shared contract meets real cases, real policy sets, real grace, real arrangements and a real access-lifecycle owner. dotmac_vendor_control_plane is cutover 2, behind a demand gate: at 8984801 it has no invoice, overdue, dunning, arrangement or consequence code at all (verified by grep for dunning/overdue across src/ — zero files), so a Vendor-first cutover would install empty platform tables, declare guessed action codes with no consumer, and retire nothing. It would prove neither the policy engine (no policy would ever fire), nor the consequence boundary (no owning service would ever return a receipt), nor the retirement property the whole programme exists for. Installing empty tables is not adoption. Vendor CP starts only once it has an authoritative production receivables owner, at least one non-test receivable past its due time with a positive exact collectible amount, and a NAMED consequence owner whose action code can be declared together with its real consumer. dotmac_erp is exclusion evidence, not an adopter: app/services/finance/reminder_service.py sends AR reminders and owns no case, ladder, grace, arrangement or consequence state machine, and ADR-0020 A6 gives ERP none of the three commercial modules."
shadow_and_drift = "Follows the adoption plan's S0-S5 and adds nothing to it. S0 classifies every live row and every writer with a TOTAL classifier and no default bucket; ambiguous rows become entity-scoped work items with a one-cycle deadline and do not stop unaffected cases. S1 ports contracts and canaries before models or routes. S2-S4 land policy/cases/consequence-requests, then arrangements, then grace, in SHADOW ONLY — the module writes shadow cases and action previews and no notice or consequence may escape. S5 compares the live owner and the module shadow for the full candidate cohort by exact subject/reason/currency/source version, over one complete production ladder window per active policy, with deterministic replay over production-derived inputs where wall-clock length is unreasonable: case existence; pinned policy and current/next step; exact next-action time and timer generation; notice request and suppression reason; arrangement/grace shield scope and expiry; requested consequence and idempotency identity; close/reopen after settlement or correction; and product access outcome observed ONLY as the owner's receipt. Every mismatch is classified source defect, module defect, approved behaviour change, or unresolved — no unexplained bucket passes. Expected deliberate differences: one workflow for advance and arrears instead of two scanners; arbitrary versioned ladders instead of a fixed four-state chain and mutable steps; exact per-entity timers instead of account-wide periodic sweeps; case-scoped consequence REQUESTS instead of direct credential writes; exposure-scoped arrangements instead of blanket account shields; and strict per-currency exact money with no de-minimis epsilon. Two drift sources are specific to this module and must be watched by name: (a) Sub's postpaid run commits ONCE for the whole run and has no per-account error isolation, so a shadow run and a live run can disagree simply because the live run aborted — every parity comparison records whether the live run completed; (b) fleet-wide billing_health_reasons still enters the live preview fingerprint at _core.py:714 -> :741, so a fleet-scoped observation invalidates live fingerprints for reasons outside the entity, and shadow fingerprints deliberately will not move with it. During the later dotmac-billing authority switch, the ReceivablesReader returns Unavailable(retryable=True) for the whole coupled invoice/settlement/allocation window; no case advances, no consequence is emitted, and a timer that fires into it reschedules its own identity with a bumped generation."
local_copy_retirement = "Sub must delete or reduce to historical-only, each behind its own two-directional ratchet with a sensitivity proof, and lowering the baseline in the same change as the removal: dunning_runner (scheduler_config.py:713-723) and DunningWorkflow (_core.py:2522-2821); prepaid_balance_sweep (scheduler_config.py:733-745) and its 758-line executor plus the 725-line planner; PrepaidSweepCycleState (models/collections.py:267-293), whose own docstring already marks it TRANSITIONAL — a timer has no cycle, so the cursor has no successor; Subscriber.prepaid_low_balance_at / .prepaid_deactivation_at and prepaid_enforcement_state.py, replaced by one DurableTimer per (owner, entity, purpose) with a generation; the direct credential writes at _core.py:918-921 and :1475-1476, and the dead _throttle_account/_restore_throttle at :1637-1777, replaced by a typed request whose owner is account_lifecycle; the Invoices.mark_overdue_system call and apply_prepaid_overlap_hold inside the dunning SCAN (_core.py:2554, :2562-2567), which are collections writing a billing fact; local dunning_cases / dunning_action_logs / policy_dunning_steps writers and the collections interpretation of mutable PolicySet fields; the shadow collections_cases writers and the whole unwired ADR-0007 Phase 5 stack once its tests pass against the module; the hardcoded notice copy and the subject-string dedupe at _core.py:1780-1883; and dead policy fossils (enforcement_health_blocked at :1953, DunningWorkflow.resolve_cases_for_account at :2823-2857). Sub keeps product adapters only: local identity and authorization, policy assignment, the communication adapter, and the consequence executor (account_lifecycle remains the SOLE writer of Subscription.status/.access_state). tests/architecture/billing_scheduled_sweep_baseline.txt already ratchets dunning_runner and prepaid_balance_sweep in both directions but carries NO sensitivity proof, so the detector cannot be distinguished from a detector that stopped looking; the ported ratchets must add one, and every new ratchet (timer columns, direct credential writes, collections writes to Invoice.*, local model imports, notification subject literals) ships with its own. A product-local copy of the extracted pure engine is itself a ratchet entry. Retaining old tables for retention does not retain their authority."
next_action = "None in this repository beyond documentation. The gates, in order: G0 land ADR-0020's 2026-08-14 amendment and this evidence; G1 clear ADR-0017 P11 — the kernel migration lineage composed and RUNNING in Sub's production database, recorded in Sub's PLATFORM_ADOPTION_LEDGER.md (a prepared branch, a copied migration or a stamped revision does not satisfy it); G2 extract P3 durable timers as their own adopted kernel facility, demand-pulled only when the Sub collections cutover is actually blocked on scheduling, ported from dotmac_sub:app/services/runtime_durable_timers.py + app/models/durable_timer.py + tests/test_durable_timers.py, released separately and consumed by Sub — NOT placed inside a collections schema and NOT bundled with numbering or rendering; G3 create the package, allocate one namespace, migration prefix and branch label in MIGRATION_OWNER_LEDGER in the package-creation diff, and move this dossier content to packages/dotmac-collections/EXTRACTION.toml BEFORE behaviour code, declaring both persistence planes from revision 1. Two open questions must be answered before the first contract slice: the inbound seam shape (a pushed command carrying money vs a command carrying identity plus a ReceivablesReader supplying the amount) and the outbound contract name. One must be answered before any Vendor CP work: the platform plane has no consent ledger and no delivery receipt loop, because dotmac_kernel.consent and dotmac_kernel.delivery are tenant-plane only. Do not reserve a namespace, create an empty package, or predeclare a guessed Vendor action code ahead of these gates."
```

---

## Dual-plane design note (ADR-0023, ADR-0020 A2)

Restated outside the TOML because it is the part most likely to be got wrong at
revision 1, and because `dotmac-ticketing 0.1.0a1` already proved that
retrofitting a platform plane after a tenant-only release costs a rename.

**One behaviour, two planes.** The ladder evaluator, the case state machine, the
grace calculator, the arrangement coverage derivation and the consequence-request
builder are **persistence-free**. They take value objects and return value
objects. If the engine imports persistence, the "one behaviour" claim is false and
the platform plane cannot reuse the guards (ADR-0023 § 1).

| | tenant plane (Sub) | platform plane (Vendor CP) |
|---|---|---|
| `tenant_id` | `UUID NOT NULL` | **absent** |
| Isolation | RLS ENABLEd **and** FORCEd in the creating migration | no RLS; `REVOKE ALL` from the tenant app role across every table **and column** privilege — the revoke *is* the isolation and is checked as strictly as a policy |
| Reachability | tenant policy | online platform role holds schema `USAGE` **plus** at least one of `SELECT`/`INSERT`/`UPDATE`/`DELETE` per table; declared-and-unreachable is a contract violation |
| Uniqueness | composite, includes `tenant_id` | control-plane-wide |
| Scope object | `TenantScope(tenant_id)`, never nullable | `PlatformScope()` |
| Product link | `link_tenant_collection_subject()`, composite FK | `link_platform_collection_subject()`, single-column FK |
| Canary | cross-tenant isolation canary | `app_user` privilege canary + platform-role reachability canary |

**Declared, never inferred.** `ModuleManifest.platform_tables` alongside `tables`.
Inferring the plane from a missing `tenant_id` is rejected because a tenant table
that merely *forgot* its column would silently reclassify itself as platform and
lose its isolation. A table appears in exactly one plane; both is refused at
manifest construction and again in the registry.

**No foreign key crosses the planes**, in either direction, and no module table
points at a Sub or Vendor table — Sub's `FinancialAccessConsequence.subscriber_id
→ subscribers.id` (`app/models/collections.py:146-151`) is exactly the FK a
module may not have.

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
- **a second module** for the platform plane — it would duplicate the lifecycle,
  the reason registry, the transition guards and the ladder evaluator, which is
  the entire reason the module exists, to avoid duplicating four `CREATE TABLE`
  statements.

This is not speculative second-plane work under ADR-0006 § 5: both named
assemblies exist today, Sub needs the tenant plane, and the vendor control plane
is platform-only and has no `tenant_id` to give.

---

## Parity-test inventory

Every path was confirmed present at Sub `27c76aaee`. "Proves" is what the test
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
not introduce an independent sequence. Two contract-shape disagreements with that
plan — the inbound seam (pushed command carrying money vs command-plus-reader)
and the outbound contract name — are recorded in the contracts spec § 9 rather
than resolved here, because neither changes the ownership boundary, the cutover
order or the retirement set. The dossier's `next_action` lists both as blocking
the first contract slice.

## Index entry owed

`docs/inventories/README.md` has no row for this file or for
`collections-sources.md`. It is being edited concurrently by another session and
was deliberately not touched; both rows must be added in the same change that
lands these documents.
