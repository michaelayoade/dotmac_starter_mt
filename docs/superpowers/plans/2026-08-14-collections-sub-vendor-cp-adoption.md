# `dotmac-collections` adoption: Sub first, Vendor CP on real overdue demand

- **Status:** execution plan; non-authoritative intent and not evidence that the
  module exists or either product has adopted it
- **Decision boundary:** ADR-0020 and its 2026-08-14 amendment
- **Parent plan:**
  `docs/superpowers/plans/2026-08-11-billing-subscriptions-collections.md`
- **Order:** Sub tenant-plane cutover first; Vendor Control Plane platform-plane
  cutover only after it has real production overdue cases
- **Current blocker:** ADR-0017 P11, followed by the demand-pulled P3 durable-
  timer slice

## Exit condition

This programme is complete only when:

1. Sub pins a released `dotmac-collections` version, composes its independently
   owned lineage, runs the tenant plane in production, and deletes the local
   collections decision paths it replaced;
2. the same module version or a compatible later exact release is installed by
   Vendor CP only after a production receivable actually becomes overdue;
3. both products use the same persistence-free policy/case/arrangement engine
   through their separate declared persistence planes, without sharing rows;
4. every policy step is immutable versioned data, every time transition has one
   current durable timer, and every external effect is an idempotent request to
   the service that owns the consequence; and
5. the dossier moves from `audit-complete` to `adopted` only after Sub's live
   cutover, and to `reuse-proven` only after Vendor CP completes a real overdue
   case lifecycle.

Installing empty platform tables in Vendor CP is not adoption. Running Sub and
the module as permanent parallel writers is not adoption either.

## Evidence snapshot

The focused audit for this plan read these exact revisions on 2026-08-14:

| Repository | Revision | Finding |
|---|---|---|
| Starter | `1b1d62b` | No collections package or namespace exists. ADR-0020 assigns the domain to a future independent dual-plane module. |
| Sub | `27c76aaee` | Qualifying source. The focused source set is about 8,200 service/model LOC plus 16 focused collection, timer, arrangement, enforcement, and architecture test files. |
| ERP | `0f4b1698` | `reminder_service.py` has AR reminder delivery, but no dunning case, arrangement, grace, or consequence state machine. ERP is not an adopter. |
| Vendor CP | `8984801` | No invoice, overdue, dunning, arrangement, or collections owner. It is a future platform-plane adopter, not an extraction source. |

Sub contains two generations at once:

- The **live owners** are `financial.dunning`,
  `financial.payment_arrangements`, `financial.grace_policy`, the prepaid
  enforcement paths, and their access-consequence coordination. Arrangements,
  grace, and safe staff actions are recorded as `COMPLETE`; the operational
  dunning and prepaid paths still act.
- ADR-0007 Phase 5's `collections.postpaid_policy`,
  `collections.prepaid_policy`, `collections.lifecycle`, and
  `runtime.durable_timers` are implemented and tested but remain
  `SHADOWING`. Migration 433 explicitly says the old runner and prepaid sweep
  continue unchanged until the Phase 5 parity gate passes.

The extraction source is therefore **one product with two evidence layers**:
the live implementation defines behavior that cannot silently regress, while
the Phase 5 implementation supplies the corrected owner boundaries, reason-
scoped cases, generation timers, and consequence-request tests. The shared
module may not call the shadow code authoritative, and it may not greenfield a
third interpretation.

Mandatory source paths for the eventual `EXTRACTION.toml` include:

- `dotmac_sub:app/models/collections.py`
- `dotmac_sub:app/models/collections_case.py`
- `dotmac_sub:app/models/payment_arrangement.py`
- `dotmac_sub:app/models/durable_timer.py`
- `dotmac_sub:app/services/collections/`
- `dotmac_sub:app/services/payment_arrangements.py`
- `dotmac_sub:app/services/dunning_staff_actions.py`
- `dotmac_sub:app/services/payment_arrangement_staff_actions.py`
- `dotmac_sub:app/services/billing_communication_policy.py`
- `dotmac_sub:app/services/runtime_durable_timers.py`
- `dotmac_sub:docs/adr/0007-end-to-end-billing-target-architecture.md`
- `dotmac_erp:app/services/finance/reminder_service.py` as exclusion evidence
- the focused tests named under "Verification" below

## Ownership boundary

`dotmac-collections` owns:

- immutable collection-policy versions and ordered step ladders;
- the effective policy snapshot pinned to a case;
- one active case per local collection subject, service, reason, and currency,
  with exact versioned exposure membership;
- case progression, pause/resume, resolution/reopen evidence, and action trail;
- payment-arrangement proposal, approval/activation, exact exposure membership,
  installment schedule, fulfillment, default, cancellation, and its narrowly
  scoped collection shield;
- policy and explicitly granted collection grace, including provenance and
  expiry;
- notice and consequence **requests**, their idempotency identity, and the
  receipt state returned by the actual owner; and
- the decision about which exact next timer to schedule, replace, or cancel.

It does not own:

- invoices, collectible amount, cash, settlement, allocation, account credit,
  prepaid funding, write-off, or refund — those are billing facts;
- subscription, licence, deployment, RADIUS, service, account, or entitlement
  mutation — the consuming product owns those transitions;
- customer contact details, consent, channel selection, templates, or delivery;
- approvals as a generic workflow — a product that requires maker-checker
  approval composes the approval decision and then asks collections to recheck
  and perform its own arrangement transition (ADR-0026);
- PSP/provider transport, credentials, webhooks, retries, or checkpoints;
- ERP's GL, journals, periods, tax returns, or statutory collections reporting;
  or
- durable-timer infrastructure. P3 owns timer generations and firing;
  collections only requests a timer within its transaction and consumes the
  resulting trigger.

The boundary in one flow:

```text
local billing/financial owner
  -- exact versioned collection exposure --> assembly adapter
  -- AssessCollectionExposure -----------> dotmac-collections
  -- notice/action request ---------------> assembly adapter
  -- owner-specific command --------------> Sub or Vendor CP owning service
  -- consequence receipt -----------------> dotmac-collections

P3 timer owner -- generation trigger -----> dotmac-collections
settlement/correction fact ---------------> dotmac-collections closes/replans
```

No arrow is a sibling-module import. Until Sub adopts `dotmac-billing`, its
assembly maps the existing authoritative Sub financial facts into the same
collections input contract. A later billing cutover changes the producer behind
that adapter, not the collections owner. `dotmac-billing` adoption is therefore
not a prerequisite for the first collections cutover.

## Public contract

### Inbound exposure

The module accepts one versioned `AssessCollectionExposure` command. Its typed
payload carries, at minimum:

- command, correlation, source event, and business idempotency identities;
- source owner, source exposure identity, and monotonically increasing source
  version;
- local subject and service/contract opaque references;
- collection timing (`advance` or `arrears`) and a typed reason input;
- currency and exact amount — never float and never a generic account balance;
- arrears due time or advance coverage start, whichever is meaningful;
- exact resolved/corrected/cancelled state;
- the selected policy code/version or the declared setting scope from which the
  module must resolve it; and
- the observation time and source-state fingerprint.

Arrears supplies an exact collectible receivable. Advance supplies an exact
uncovered obligation and typed funding result; it does not invent an invoice so
collections can act. Stale source versions are ignored, same-version changed
fingerprints are conflicts, and a later correction replans only the affected
case.

The module never queries a billing sibling's tables and never reconstructs a
receivable from invoices, payments, credit, or provider state.

### Outbound action request

Every external action is an immutable `CollectionActionRequested` output with:

- request, case, policy-version, policy-step, subject, and exposure identities;
- action code and schema version;
- reason, currency, exact amount snapshot, and causal source version;
- requested time, expected product-state fingerprint, and idempotency key; and
- no provider credential, product model, or transport instruction.

The consuming assembly maps that request to a locally owned command. The owner
locks and revalidates its own state, applies or refuses its consequence, and
returns a typed receipt. A delivery acknowledgement is not proof that a service
was suspended; only the owning service's receipt is.

Action codes are an ADR-0008 declaration registry, not an enum or JSON free-for-
all. The initial kernel/module slice adds the registry and its declaration,
reference, consumer, uniqueness, and orphan tests in the same change as the
first real Sub action. Vendor codes are not predeclared; they arrive only with a
real Vendor consumer.

### Notice request

A notice is another owner request, not a send call. Collections supplies the
case, policy/step, stable message purpose, and evidence; the product's
communication adapter resolves contactability, consent, channel, template,
locale, and delivery.

Policy data declares whether a grace clock anchors on exposure time, request
time, or accepted notice receipt. "No contact route" and "do not enforce" are
separate outcomes. A delivery failure cannot silently become extra financial
grace, and a notice suppression cannot silently authorize a consequence.

## Domain model

The first package dossier must lock exact table names. The required logical
records are below; tenant tables are unprefixed and platform twins use the
established `platform_` naming pattern.

### Policy

- **CollectionPolicy** — stable code and lifecycle identity.
- **CollectionPolicyVersion** — immutable after publication; effective interval,
  actor/reason, applicability inputs, grace rule, arrangement/default rule, and
  version fingerprint.
- **CollectionPolicyStep** — stable step code, ordinal, offset anchor/duration,
  request kind, declared action code, required receipt, retry rule, and optional
  approval requirement.

An open case pins one policy version. Publishing a new version never rewrites an
old case. Moving existing cases is a separate previewed command naming exact
case IDs, old/new fingerprints, actor, reason, timer replacements, and expected
action changes.

The case engine is not the shadow implementation's hardcoded
`open -> warned -> escalated -> consequence_requested` chain. An arbitrary
versioned ladder cannot be represented by four fixed states. The shared shape
uses a small closed case lifecycle (`active`, `paused`, `resolved`, `cancelled`)
plus the pinned current step and append-only step-attempt records. The policy
rows determine how many warnings, reviews, and consequences exist.

### Case and action evidence

- **CollectionCase** — subject/service, reason, currency, pinned policy version,
  lifecycle, current step, next-action time, command and correlation evidence,
  and optimistic state version.
- **CollectionCaseExposure** — exact source exposure identity/version, amount,
  due/coverage anchor, active/resolved state, and admission evidence. A case may
  collect several same-currency exposures for one service without losing their
  individual causal identities.
- **CollectionCaseAction** — append-only request/receipt/refusal/retry evidence
  for one policy step. A unique action identity makes replay harmless.
- **CollectionGraceGrant** — case-scoped policy or explicit grace, starts/ends,
  actor/reason/provenance, supersession/revocation, and timer generation.

Grace changes collections progression only. It does not change an invoice due
date, reduce debt, add credit, extend a commercial contract, or directly alter
access. Sub's outage/service-extension owner remains product-owned and can ask
collections for a scoped deferral through the assembly.

### Arrangement

- **PaymentArrangement** — proposal and lifecycle, subject, currency, exact
  arranged total, actor/reason, approval evidence reference, and state version.
- **PaymentArrangementExposure** — exact billing exposure identity/version and
  amount admitted to the arrangement.
- **PaymentArrangementInstallment** — ordinal, amount, due time, and derived
  settlement coverage.
- **PaymentArrangementSettlementReceipt** — a deduplicated billing-owned
  settlement/allocation observation applied to exact installments.

An active arrangement shields only the exposures and amounts it contains. It
never shields a whole account merely because any arrangement exists. A new
unarranged receivable remains collectible.

Collections never manufactures a payment. Staff cannot mark an installment
financially paid; the billing owner first accepts the settlement/allocation and
then sends the typed observation. Arrangement totals, installment coverage, and
remaining amount are derived from immutable membership and receipts — no
mutable `installments_paid` or generic `balance` authority.

The arrangement stores an explicit ordered installment schedule with aware due
instants. It does not ship a `weekly|biweekly|monthly` enum or infer dates by
adding 7/14/30 days. A product may propose a schedule, but the collections owner
validates exact totals, ordering, currency, and bounds before persisting it.

### Persistence planes

The module ships one pure behavior engine and two repository families:

| Tenant plane — Sub | Platform plane — Vendor CP |
|---|---|
| Every table has `tenant_id UUID NOT NULL` | No table has `tenant_id` |
| Composite unique keys and composite FKs | Control-plane-wide unique keys |
| RLS ENABLEd and FORCEd in the creating migration | No RLS at all |
| Tenant scope is a `TenantScope`, never nullable | Scope is `PlatformScope()` |
| Product links use `link_tenant_collection_subject()` | Product links use `link_platform_collection_subject()` |
| Cross-tenant canaries | `app_user` REVOKEd across all table and column privileges; online platform role has schema `USAGE` plus row DML |

No foreign key crosses planes. No module table points at a Sub or Vendor table.
The product-owned link helper creates the local relation on the correct plane
and refuses an unusable configuration. Nullable tenant, sentinel tenant, and a
polymorphic plane column are forbidden.

## Invariants and conformance gates

The package does not release until sensitivity-proven tests enforce all of the
following:

1. **One writer.** Only the module service changes policy, case, grace, or
   arrangement rows; services mutate and flush, while the kernel transaction
   boundary commits or rolls back.
2. **No sibling knowledge.** The package imports only published kernel surface,
   never billing, subscriptions, an assembly, or product code.
3. **Versioned policy.** Published policy versions and steps are immutable;
   cases pin the exact version and re-policy is an explicit audited command.
4. **Data-driven time.** No numeric overdue/grace threshold or hardcoded notice
   ladder occurs in service control flow. P3 owns exact timer generations;
   stale fires cannot advance a case.
5. **Exact money.** No float and no fuzzy tolerance. A de-minimis exception, if
   ever approved, is a capped audited waiver fact rather than an epsilon.
6. **Separate quantities.** Receivable, credit, prepaid funding, arranged amount,
   and installment settlement coverage never collapse into one balance.
7. **Scoped arrangements.** An arrangement shields only its admitted exposure
   rows and stops shielding them at default/cancellation/fulfillment as policy
   declares.
8. **Grace is not finance.** Grace changes only the next collection action and
   retains provenance; it cannot rewrite billing or service state.
9. **Requests, not consequences.** No notification send, service/access write,
   licence mutation, or provider call exists in the module. Every action has a
   declared code, exact cause, idempotency key, and owner receipt.
10. **Entity-scoped failure.** Bad or ambiguous input blocks the exact case and
    creates owned correction evidence. It never pauses the fleet. First durable
    detection starts the non-resettable one-cycle correction deadline.
11. **Two real planes.** The manifest declares every tenant and platform table;
    live-catalog tests prove both security contracts and refuse crossing FKs.
12. **No secrets or environment resolution.** Settings read rows/defaults only;
    actions contain references or names, never a secret value.

## Prerequisites and release order

### G0 — Accept the documentation boundary

Land ADR-0020's 2026-08-14 amendment and its billing inventory/parent-plan
updates. This focused plan adds no authority by itself.

### G1 — Clear P11

The kernel lineage must be composed and running in Sub's production database,
recorded in Sub's `PLATFORM_ADOPTION_LEDGER.md`. A prepared branch, copied
migration, or stamped revision does not satisfy the gate.

### G2 — Extract P3 as its own adopted facility

Once the Sub collections cutover is actively blocked on scheduling, port
`runtime.durable_timers` and its tests from Sub into the appropriate kernel
facility. Its contract is owner/entity/purpose/generation/due-at/expected-source-
version/output-event; it scans only due timer rows and makes no collections
decision.

P3 lands as a separate reviewable release and Sub consumes it. Do not put
`durable_timers` inside the collections schema, and do not bundle numbering,
rendering, or unrelated scheduling use cases into this slice.

### G3 — Create the module and dossier

Only after G1, with G2 available for the live slice:

- allocate one namespace, migration prefix, and branch label in
  `MIGRATION_OWNER_LEDGER` in the package-creation diff;
- create `packages/dotmac-collections/EXTRACTION.toml` before behavior code,
  status `audit-complete`, with Sub as source and first cutover, ERP as exclusion
  evidence, Vendor CP as demand-gated candidate, and no contract consumers;
- declare both persistence planes from revision 1;
- add the package to release metadata only when its wheel, migrations, and
  kernel floor are verifiable; and
- add the owner row and public surface to `docs/ARCHITECTURE.md` in the same
  change.

Do not reserve a namespace or create an empty package ahead of this gate.

## Cutover 1 — Sub tenant plane

### S0 — Characterize production before mapping

Use read-only evidence on the explicitly named Sub target when this stage is
authorized. Record, per tenant/account/reason/currency:

- live dunning cases and statuses, mutable policy sets/steps, current step and
  action history;
- shadow `collections_cases` and timer generations;
- prepaid and postpaid candidate cohorts and every typed skip/shield reason;
- grace source/provenance and deadlines;
- arrangements, exact intended exposures, installment states, default rules,
  and settlement evidence;
- outstanding receivables, prepaid funding, account credit, and access holds as
  separate quantities;
- current notice requests/receipts and access-consequence evidence; and
- every direct call or scheduled task that can change dunning, arrangement,
  grace, notification, or financial-access state.

The classifier must be total. Ambiguous source rows become explicit entity-
scoped work items with a one-cycle deadline; they do not stop unaffected cases.

### S1 — Port the contract and canaries first

Port behavior and tests before models or routes. At minimum preserve:

- `test_collections_target_lifecycle.py`
- `test_collections_dunning_services.py`
- `test_collections_services.py`
- `test_payment_arrangements.py`
- `test_payment_arrangement_safe_actions.py`
- `test_dunning_staff_safe_actions.py`
- `test_durable_timers.py` in P3, not duplicated here
- `test_prepaid_enforcement_planner.py`
- `test_financial_access_consequence_evidence.py`
- the relevant settlement/restoration and account-lifecycle tests

Add property/concurrency tests for arbitrary ladder length, policy replay,
same-version exposure conflicts, stale source/timer versions, concurrent case
open, concurrent arrangement settlement, consequence receipt replay, and exact
minor-unit allocation.

Each governance test includes a sensitivity proof against a temporary
violation.

### S2 — Land policy, cases, and consequence requests in shadow

First coherent module slice:

- pure ladder evaluator;
- tenant/platform policy, case, and case-action models;
- published inbound/outbound contracts and fakes;
- declared action-code registry;
- case/timer transaction integration; and
- no router and no product-specific consequence implementation.

Sub composes a tenant adapter from its current authoritative financial facts.
The module writes shadow cases and action previews only. No notice or
consequence may escape from the shadow path.

### S3 — Land arrangements in shadow

Port the arrangement lifecycle and safe preview/fingerprint behavior, correcting
the source boundary:

- bind exact exposure identities and amounts;
- obtain payment truth only from the billing owner;
- derive installment coverage and progress;
- keep approval decision separate from the collections transition; and
- replace account-wide shielding with exposure-scoped shielding.

Import current arrangements with a total row disposition: exact exposure
membership, reviewed account-level allocation, terminal history only, or
entity-scoped remediation. Do not silently treat every account balance as the
arrangement's subject.

### S4 — Land grace in shadow

Port Sub's typed precedence/provenance behavior into versioned policy and
case-scoped grace records. Preserve invalid-setting fail-closed behavior and the
distinction between notice suppression, arrangement shield, outage/service
extension, and collections grace.

The module schedules the precise post-grace action through P3. It does not poll
accounts to discover that grace ended.

### S5 — Full-cohort parity

For the full candidate cohort, compare the live owner and module shadow by exact
subject/reason/currency/source version. The evidence must cover one complete
production dunning-ladder window for each active policy; where wall-clock length
would be unreasonable, deterministic replay over production-derived inputs
covers the remaining steps and calendar boundaries:

- whether a case exists;
- pinned policy and current/next step;
- exact next-action time and timer generation;
- notice request and suppression reason;
- arrangement/grace shield scope and expiry;
- requested consequence and idempotency identity;
- close/reopen result after settlement/correction; and
- product access outcome, observed only as the owner's receipt.

Every mismatch is classified as source defect, module defect, approved behavior
change, or unresolved. An approved change gets an explicit fixture and operator
impact report. No unexplained bucket passes.

The expected deliberate differences are:

- one workflow for advance/arrears inputs instead of two scanners;
- arbitrary versioned ladders instead of fixed states or mutable steps;
- exact timers instead of account-wide periodic sweeps;
- case-scoped consequence requests instead of direct access writes;
- exposure-scoped arrangements instead of blanket account shields; and
- strict per-currency exact money with no de-minimis epsilon.

### S6 — Cut over one bounded cohort

Choose one explicit policy/version cohort with complete parity and an on-call
steward. In one deployment:

1. stop the legacy writer for that cohort;
2. enable module case, arrangement, grace, and timer-triggered outputs;
3. keep legacy reads as a temporary projection only;
4. verify one action request is applied at most once and only by the product
   owner; and
5. prove settlement/correction closes the exact case and removes only the
   matching financial restriction.

Rollback disables the new cohort writer and returns authority to the still-
intact old owner. It never deletes module evidence or runs both writers.

### S7 — Expand, then retire the local owners

Expand only after each prior cohort's operational evidence is green. When the
full candidate cohort is cut over, delete or make historical-only:

- `DunningWorkflow` / `dunning_runner` and the billing-enforcement account scan;
- `prepaid_balance_sweep` as a business decision path;
- local `dunning_cases`, `dunning_action_logs`, and shadow
  `collections_cases` writers;
- local `policy_dunning_steps` and collections interpretation of mutable
  `PolicySet` fields;
- local payment-arrangement lifecycle and account-wide shield queries;
- local grace decision copies and duplicate timer/notice fields;
- direct financial-access, RADIUS, notification, and service writes from
  collections code; and
- product-local copies of the extracted pure engine.

Keep product adapters for local identity, authorization, policy assignment,
communication, and consequence execution. Add two-directional ratchets with
sensitivity proofs for old imports/writers/sweeps, lowering the baseline in the
same change as every retirement.

Sub acceptance evidence is: fresh and upgraded composed migrations, cross-
tenant RLS, full-cycle parity, bounded cutover results, exact timer coverage or
typed no-timer reasons, consequence receipt replay, settlement/restoration,
arrangement default, grace expiry, correction-forward deadlines, and a zero old-
writer ratchet. Only then does the dossier become `adopted`.

## Cutover 2 — Vendor Control Plane platform plane

### Demand gate

Vendor work does not start merely because the platform tables exist. All of the
following must be true:

1. Vendor CP has an authoritative production billing/receivables owner;
2. at least one non-test receivable has crossed its due time with a positive
   exact collectible amount;
3. the local subject, policy owner, operations steward, and intended consequence
   owner are named; and
4. the consequence code and its real consumer can be declared together.

Until then Vendor CP does not pin the module, compose its lineage, create empty
collections rows, or predeclare guessed licence/contract actions.

### V0 — Re-audit at the moment of demand

Re-run the Vendor source inventory at an exact commit. The 2026-08-14 finding
of "no collections owner" is not assumed forever. If Vendor has since built a
local workflow, classify every writer and retire it through shadow/cutover. If
it remains manual, record the real overdue cohort and human procedure as the
behavior baseline without inventing a parallel local engine.

### V1 — Compose the platform plane

Pin the same released contract Sub proved, compose the module lineage through
Vendor's existing composed-migration path, and use `PlatformScope()`. Prove no
collections table has tenant/RLS, `app_user` has no table or column privilege,
the online platform role is reachable, and no FK reaches a tenant plane or
another application's database.

### V2 — Bind only real Vendor semantics

Create the platform subject link for the actual overdue commercial record.
Declare only the action code the named Vendor owner consumes. Contract,
licensing, deployment, account, approval, and allocation services remain
Vendor-owned; collections requests their transition and records the receipt.

### V3 — Observe, then act on the real case

Run the real overdue exposure through observe-only policy evaluation first.
Confirm the selected policy/version, next timer, notice route, action preview,
and operator impact. Then enable the platform writer and allow the case to reach
one real terminal path: paid/corrected and closed, or consequence requested,
applied/refused with receipt, and later resolved.

The dossier becomes `reuse-proven` only after that real case exercises the
shared contract and the product-specific consequence remains outside the
module. An empty greenfield install or a synthetic overdue fixture is test
evidence, not reuse evidence.

## Verification

### Starter/package

- `make check`
- `make test-unit`
- `make migration-gate`
- `make test-db-up && make test-integration && make test-db-down`
- clean-wheel install against the declared exact kernel floor
- module/public-surface and no-sibling-import tests
- manifest vocabulary declaration/reference/consumer/orphan tests
- tenant/platform catalog, privilege, reachability, and no-cross-plane-FK tests

### Sub cutover

- focused source parity suites listed in S1
- full-cycle shadow report with no unexplained mismatch
- cross-tenant isolation and transaction rollback canaries
- stale event/timer, concurrent case, and at-most-once consequence tests
- exact-money, multi-currency, partial settlement, reversal, arrangement,
  default, grace, pause/resume, no-contact, and restoration matrix
- production cutover evidence plus a zero old-writer/sweep ratchet

### Vendor cutover

- platform session and privilege canaries
- real overdue case evidence, not a seed fixture
- owner-specific consequence contract and receipt replay
- no tenant fabrication, provider client, or cross-application persistence

## Explicitly out of scope

- starting the package before P11;
- reserving a namespace in a plan;
- making billing or subscriptions a Python dependency;
- migrating ERP reminders into this module;
- putting service extensions, outage compensation, account lifecycle, RADIUS,
  licence enforcement, notification delivery, or approval state inside
  collections;
- a generic workflow/rules engine;
- a hardcoded Nigerian currency, grace duration, overdue threshold, or notice
  ladder;
- Vendor CP installation before real overdue demand; and
- metering, invoice issuance, payment transport, GL, or document rendering.
