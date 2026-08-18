# Collections: policy, consequence and timer contracts

- **Status:** non-authoritative intent (`docs/superpowers/specs/` — see
  `CLAUDE.md`'s documentation hierarchy). The module does not yet exist; P11 is
  met and ADR-0032 now authoritatively fixes the contract and initial-plane
  decisions this draft originally left open.
- **Decision boundary:** ADR-0020 and its 2026-08-14 amendment (A1 composition,
  A2 planes, A6 the per-application matrix); ADR-0023, ADR-0024, ADR-0014,
  ADR-0008, ADR-0018.
- **Conformance gates it implements:** C2 (one collection-timing field, not two
  subsystems) and C4 (dunning policy is versioned data, not control flow) from
  `docs/superpowers/plans/2026-08-11-billing-subscriptions-collections.md`.
- **Adoption sequence:** owned by
  `docs/superpowers/plans/2026-08-14-collections-sub-vendor-cp-adoption.md`.
  That plan governs gates, cutovers, cohorts and retirement; this spec does not
  restate it and must not contradict it. Where it does, § 12 says so explicitly.
- **Evidence:** `docs/inventories/collections-sources.md`;
  dossier `docs/inventories/collections-extraction-dossier.md`.
- **Revalidated at:** starter `8d4ddfd9`, Sub `d1a1a913`, ERP `0f4b1698`,
  vendor CP source evidence `8984801`; P11 production evidence is recorded in
  ADR-0017's 2026-08-18 amendment.

## Amendment 2026-08-18 — ADR-0032 resolves the draft conflicts

Michael approved the following exact boundary:

- inbound is `AssessCollectionExposureV1`, carrying identity, explicit scope
  and trigger provenance and **never a money amount**;
- `ReceivablesReader` supplies the current authoritative exact position at
  every decision point;
- outbound is `CollectionActionRequestedV1`; Collections records the owning
  product service's typed receipt as evidence;
- grace always declares an anchor; there is no implicit default;
- the timer port is assembly-bound to the separate
  `dotmac-durable-timers` owner and Collections builds no scheduler; and
- revision 1 explicitly declares an atomic tenant-only plane for the real Sub
  adopter, so ADR-0028 requires no assembly selector. Platform persistence
  remains absent until Vendor's real-case demand gate is met.

Sections 9 and 11 retain the original contradictions as historical review
evidence, but their open decisions are closed by ADR-0032.

This document specifies three things and nothing else: the
`AssessCollectionExposureV1`/`ReceivablesReader` inbound seam,
`CollectionActionRequestedV1` plus typed owner receipt, and the timer port the
module needs and is not allowed to build for itself.

---

## 1. Why these three, and not a design document

The adoption plan already fixes the ownership boundary, the domain model, the
plane split and the cutover sequence. What it deliberately leaves at the level
of "at minimum carries" are the two wire contracts and the scheduling seam.
Those are exactly the three places where a collections module becomes a second
writer if the detail is wrong:

1. **The read seam** is where a collections module starts querying billing
   tables and quietly becomes a second balance calculation.
2. **The write seam** is where a consequence stops being a request and becomes a
   direct write to service state.
3. **The scheduling seam** is where the separately released timer module is
   bound through a port. If that boundary is missing, a local sweep becomes a
   second time owner and ordering becomes business state.

Sub has already made two of those three mistakes and has already designed the
correction (its ADR-0007 Phase 5). This spec ports the correction, not the
mistake.

---

## 2. The read seam: `ReceivablesReader` and `ReceivablePositionV1`

### 2.1 The rule

`dotmac-collections` **never imports `dotmac-billing` and never queries a
billing table.** ADR-0024 § 2 forbids one installable module importing another
business module; ADR-0020 A1 makes the consuming assembly the composition root.
The module's only read path is a port it declares:

```python
class ReceivablesReader(Protocol):
    """The module's ONLY path to a receivable amount. One method."""

    def read(
        self,
        *,
        scope: Scope,                  # TenantScope | PlatformScope
        exposure_ref: ExposureRef,     # opaque source identity, never an invoice id
        as_of: datetime,               # aware
    ) -> ReceivablePositionV1 | Unavailable: ...
```

Three artefacts ship together in the same revision, per C5's shape and
`dotmac-files`' precedent: the **protocol**, an **in-memory fake**, and **one
parametrized contract suite** every implementation must pass. The suite is the
definition of the contract; the protocol is only its type.

The assembly wires Billing's published output to this port. Billing's team
specifies the producer side; this spec constrains only what the module is
allowed to see.

### 2.2 Why a port and not a queued payload

The adoption plan's original inbound `AssessCollectionExposure` command carried the
amount inside the message. A queued command carrying money goes stale between
enqueue and handling, and a policy step that fires three days later would act
on the amount as it was three days ago. ADR-0032 resolves the reconciliation:

> **The command carries identity; the port supplies the amount.**
> `AssessCollectionExposureV1` names *which* exposure to evaluate, in which
> scope, and why it was triggered. `ReceivablesReader.read` is called at
> decision time — including at every timer fire — to obtain the exact current
> position. The
> module holds no cached amount it can act on.

This keeps both halves of the plan: the assembly still delivers a versioned
command, and the module still never imports billing.

### 2.3 `ReceivablePositionV1` — the eight required elements

| # | Element | Specification |
|---|---|---|
| 1 | **Stable identity and version** | `(source_owner, exposure_ref, source_version)`. `source_owner` is a declared string naming the producing authority (e.g. `sub.financial.obligations`, later `dotmac-billing`). `exposure_ref` is opaque to collections — never parsed, never assumed to be an invoice id. `source_version` is monotonic **per `(source_owner, exposure_ref)`** and only there; see § 2.6. `contract_version = 1`. |
| 2 | **Idempotency key and fingerprint** | The read is a query and takes no idempotency key. It returns a `state_fingerprint`: a stable digest over every field the module is allowed to decide on (§ 2.4's amounts, anchors, resolved state, timing). The fingerprint is what a case's next step is validated against, and it is computed by `dotmac_kernel.idempotency.fingerprint_of`, not by a hand-rolled hash. |
| 3 | **Tenant or platform scope** | A required `Scope` — `TenantScope(tenant_id)` or `PlatformScope()`, never nullable, never a sentinel tenant, never a `scope_kind` column (ADR-0023 § "why the obvious workarounds are rejected"). The reader is constructed per plane; a tenant reader cannot answer a platform question and raises rather than degrading. |
| 4 | **Currency and exact amount** | `dotmac_kernel.money.Money` only. Separate, never-collapsed fields: `collectible_receivable`, `available_credit`, `funding_available` (C9). One currency per position; a multi-currency subject yields several positions. `float` is unrepresentable in the contract type, so C7 is a type error rather than a test. |
| 5 | **Source authority and provenance** | `source_owner`, `authority` (`authoritative` \| `shadow` — Sub already types this as `BillingRecordAuthority`), `observed_at`, and `completeness`. `completeness` is the field Sub learned it needed: `app/services/collections/prepaid_policy.py:69-93` refuses to evaluate an account whose complete history-derived opening source is not materialised, rather than manufacture an adverse decision from post-cutover facts alone. That refusal is part of the contract, not a Sub quirk. |
| 6 | **Correction / supersession / reversal** | A higher `source_version` for the same `(source_owner, exposure_ref)` supersedes. Same version with a different `state_fingerprint` is a **conflict**, never a silent update — the case is blocked with entity-scoped correction evidence. A reversal is a new version with `resolved_state = reversed` and its own causal reference; it never rewrites the prior position. A lower version is ignored, not applied. |
| 7 | **Accepted errors and retry classification** | Four typed outcomes, and only four: `Ok(position)`; `Unavailable(retryable=True)` — the producer is transiently unreachable or mid-cutover (§ 2.5); `Unknown(exposure)` — terminal for this exposure, opens correction evidence; `AuthorityMismatch` — the scope or `source_owner` is not one this reader may answer for, terminal and never retried. There is no fifth "assume zero" outcome: an unreadable position is never treated as a settled one. |
| 8 | **Compatibility** | Additive fields only within `V1`. A consumer ignores unknown fields; a producer may not repurpose one. Removing a field, changing a unit, or changing `source_version` monotonicity semantics is `V2` and the module supports both readers during a migration. Money fields never change currency representation. |

### 2.4 What the position deliberately does not carry

No invoice, no line, no payment, no allocation, no plan name, no customer
contact detail, no provider reference, and **no single `balance`**. C9 is
enforced structurally: there is no field a caller could read as "the balance",
so a second balance calculation has nowhere to land. This is the exact defect
recorded in `docs/inventories/billing-sources.md` § 4.4 (Sub's
`web_subscriber_details.py:385` computing `balance_due + available_credit`).

### 2.5 Behaviour during the coupled billing authority switch

`docs/superpowers/plans/2026-08-14-billing-vendor-cp-sub-cutover.md` § "S3 — one
production authority switch" moves invoices, settlements and allocations as a
**single deployment-wide switch** inside a bounded money-write maintenance
window, while individual calculations may shadow independently. During that
window there is a period in which no producer is authoritative.

The reader's required behaviour, in order of precedence:

1. **Producers are pinned, not inferred.** The assembly binds exactly one
   authoritative `source_owner` per scope. Two bound authoritative producers is
   a boot failure, matching ADR-0020 § 3's "the assembly refuses to boot with
   two authorities".
2. **In the window, the reader returns `Unavailable(retryable=True)`.** It does
   not fall through to the legacy producer, does not read the module's last
   known position, and does not synthesise zero.
3. **A timer that fires into `Unavailable` does not advance the case.** It
   records a typed `source_unavailable` step attempt and **reschedules the same
   timer identity with a bumped generation** (§ 4.3). It does not consume the
   step, and it does not silently become extra grace — the case's own grace
   clock is policy data and is unaffected by a producer outage.
4. **No consequence request may be emitted while the position is
   `Unavailable`.** This is the single most important line in this section: the
   switch window is precisely when a wrong suspension is most likely and least
   explicable.
5. **After the switch, `source_owner` has changed**, and version comparison
   across owners is meaningless. A changed `source_owner` for a known
   `exposure_ref` is an explicit **rebind**: a previewed, audited command that
   names the old owner, the new owner, the old and new `exposure_ref`, and the
   old and new fingerprints — never an implicit version comparison. An open case
   whose exposure has not been rebound is blocked, not advanced.

Precedent exists in the source: Sub's prepaid planner already refuses to decide
across its own authority cutover (`prepaid_policy.py:69`, code
`collections.prepaid_policy.opening_source_incomplete`). This section generalises
that refusal from one product cutover to the contract.

### 2.6 Monotonicity is per owner, not global

`source_version` is comparable only within one `(source_owner, exposure_ref)`
pair. Any check that compares versions across owners is a defect; the contract
suite includes a case that proves the module refuses such a comparison rather
than picking the larger number.

---

## 3. The write seam: `CollectionActionRequestedV1`

### 3.1 The rule

A consequence is a **typed request to the service that owns the state**, never a
write. The module contains no service, access, entitlement, licence, RADIUS,
subscription or notification writer. ADR-0020 § 4 states it; C4 states it; the
adoption plan's invariant 9 states it. What this section adds is the exact
shape and the failure semantics.

```text
collections  --CollectionActionRequestedV1-->  assembly adapter
             <--CollectionActionReceiptV1---  the owning service (Sub / Vendor CP)
```

The adapter maps the request onto a locally owned command. The owner locks and
revalidates its own state, applies or refuses, and returns a typed receipt.
**A delivery acknowledgement is not a receipt.** Only the owning service's
receipt closes the loop; a queue accepting the message proves nothing about
whether a service was suspended.

### 3.2 The eight required elements

| # | Element | Specification |
|---|---|---|
| 1 | **Stable identity and version** | `request_id` (module-generated UUID, stable across retries), plus the causal chain `(case_id, policy_version_id, policy_step_code, step_attempt_ordinal, exposure_ref, source_version)`. `contract_version = 1` and `action_schema_version` per action code — the envelope and the action payload version independently. |
| 2 | **Idempotency key and fingerprint** | `idempotency_key` is **derived, never random**: a digest of `(scope, case_id, policy_version_id, policy_step_code, step_attempt_ordinal, action_code, exposure_ref, source_version)`. `request_fingerprint` is a separate field over the decision inputs, and it is a **separate column**, never overloaded onto the key (ADR-0014's second property; `dotmac_kernel/idempotency.py:26-31` records the Sub defect this avoids). A replay with the same key and a different fingerprint is an `IdempotencyConflict`, not a duplicate. Sub's live shape is close but not conformant: `app/services/collections/lifecycle.py:216-218` builds `f"collections:{case.id}:{uuid4()}"` — a random component, so the key is not derivable and cannot dedupe a re-created case. That is corrected at the boundary, per ADR-0020 § 5. |
| 3 | **Tenant or platform scope** | The same required `Scope`. The request carries a **subject reference created by the product-owned link helper** (`link_tenant_collection_subject()` / `link_platform_collection_subject()`), never a foreign key into a product table. Sub's current `FinancialAccessConsequence.subscriber_id → subscribers.id` (`app/models/collections.py:146-151`) is exactly the FK a module may not have. |
| 4 | **Currency and exact amount** | The exact `Money` snapshot that justified the action, plus its currency, plus the `source_version` it came from. This is evidence, not an instruction: the owning service does not compute anything from it. No total, no rounding, no tolerance — ADR-0016's tolerance is a `SettingSpec` and is billing's, not collections'. |
| 5 | **Source authority and provenance** | `policy_version_id` + `policy_version_fingerprint` (the version pinned when the case opened — § 4.1), `decided_at`, `decided_by` (`policy` or an actor id for an operator-initiated step), `source_owner`, and the `state_fingerprint` the position returned. Every consequence is explainable from stored evidence alone, with no re-derivation. |
| 6 | **Correction / supersession / reversal** | A consequence is never edited. Settlement, correction or cancellation emits a **new** request — a release/restore action code — carrying the original `request_id` as `supersedes`. The owning service releases only the exact holds whose own current gates pass; collections does not assert that a restore is safe. This is Sub's existing rule, stated in `docs/designs/DUNNING_STAFF_SAFE_ACTIONS.md:46-49`, and it is preserved verbatim in intent. |
| 7 | **Accepted errors and retry classification** | The receipt is one of: `applied`, `refused(reason_code)`, `deferred(retry_after)`, or `failed(retryable: bool)`. **A failed consequence is durable and retryable, and is never recorded as success.** It is persisted as a `CollectionCaseAction` attempt with its typed outcome, and the retry ladder is policy data (§ 4.2), not a hardcoded backoff. `refused` is terminal for that attempt and advances nothing; a refusal is an outcome the ladder may branch on, not an error to swallow. Exception swallowing and ambiguous partial success are named retired paths in Sub's own design doc (`DUNNING_STAFF_SAFE_ACTIONS.md:67`) and must not return. |
| 8 | **Compatibility** | Additive within `V1`. An unknown `action_code` is **refused by the consumer**, never best-effort mapped — the code registry (§ 6) makes an unknown code a boot-time failure on the declaring side and a typed refusal on the consuming side. Removing an action code is a deprecation with a named consumer retirement, not a delete. |

### 3.3 The scope guard: no account-wide consequence for a scoped debt

**Guard.** A `CollectionActionRequestedV1` whose action's declared `effect_scope` is
broader than the narrowest scope shared by every exposure admitted to the case
is rejected at construction, in the pure engine, before persistence.

Concretely: each declared `action_code` carries a declared `effect_scope`
(`obligation` \| `service` \| `contract` \| `subject`). Each
`CollectionCaseExposure` carries the scope of the debt it represents. If every
active exposure on the case belongs to one service, the engine may emit an
action whose `effect_scope` is `obligation` or `service`, and **may not** emit
one scoped `subject`. Broadening requires that the case's exposures actually
span that breadth — it is a property of the admitted rows, not an operator
choice or a policy default.

The corresponding negative test asserts that a case holding one service's
overdue obligation cannot produce a subject-wide suspension request, and its
sensitivity proof widens one exposure's scope and shows the request becomes
permitted. This is the arrangement rule (adoption plan invariant 7) generalised
from arrangements to every consequence: an active arrangement shields only its
admitted exposures, and by the same logic a consequence reaches only its
admitted exposures.

---

## 4. One lifecycle, and policy as data

### 4.1 One collections lifecycle for advance and arrears (C2)

**Rule.** `collection_timing` is a field on the exposure, not a subsystem
selector. There is one case engine, one step evaluator, one timer purpose
vocabulary, one notice path, one consequence path and one error path. Policy
**data** differs between advance and arrears; **code** does not.

The evidence that this is achievable, rather than aspirational, is that Sub's
own corrected shadow planners already differ by only four predicates
(`docs/inventories/collections-sources.md` § 3 measures this): an accounting
treatment, a time anchor field, a coverage test, and a reason code. All four are
expressible as policy data — which is precisely C4's `applies_to` declared
receivable/coverage predicate. What Sub has not done is delete the second
module: `app/services/collections/postpaid_policy.py` and
`app/services/collections/prepaid_policy.py` are two modules named for exactly
one timing mode each, and C2's architecture test rejects both names.

**Check 1 — the architecture test.**
`test_no_module_symbol_is_named_for_one_timing_mode`: walk every module,
class, function, dataclass field, enum member, table name and column name in
the package; fail on any identifier matching a timing-mode vocabulary
(`prepaid`, `postpaid`, `advance_`, `arrears_`, `_advance`, `_arrears`) except
where the identifier is the declared value of the `collection_timing` field
itself, or a policy `applies_to` predicate literal in test data. The allowlist
is exactly two names and is asserted to have exactly two entries, so growing it
is a reviewable diff (ADR-0018 § 3's ratchet shape).

*Sensitivity proof:* the test is run against a fixture module defining
`def plan_prepaid_consequence(...)` and must fail. Without that proof a clean
run is indistinguishable from the walker not reaching the package
(ADR-0018 § 5).

**Check 2 — the behavioural test.**
`test_advance_and_arrears_traverse_the_same_owner_functions`: run one scenario —
open, warn, grace, escalate, request consequence, settle, close — twice, once
with `collection_timing=advance` and once with `arrears`, differing only in the
exposure's timing field and the policy row's anchor. Record the ordered sequence
of owner functions entered (via a call recorder installed on the engine, not by
mocking) and assert the two sequences are **equal**. Assert also that the two
runs produce the same number of persisted step attempts and the same action-code
sequence.

*Sensitivity proof:* introduce a temporary `if timing is advance:` branch that
skips one owner function, and show the test fails. A test that only asserts
"both produce a case" would pass with two engines and is not acceptable.

### 4.2 Policies and ladders are immutable versioned data (C4)

Policy is rows, resolved through `dotmac_kernel.settings_resolver` for the
*selection* of which policy applies, and stored as immutable versioned rows for
the *content*. The shape, restating C4's fields with the additions the adoption
plan's domain model requires:

```text
CollectionPolicy            stable code + lifecycle identity
CollectionPolicyVersion
  applies_to        # a DECLARED receivable/coverage predicate — never a plan name
  grace             # duration + declared anchor (exposure / request / accepted receipt)
  retry_ladder      # ordered offsets for a failed consequence attempt
  floor_minimum     # exact Money below which no action is requested
  suppression_windows
  version, effective_from, actor, reason, version_fingerprint
CollectionPolicyStep[]
  offset_from       # declared anchor code
  offset_days       # or an interval value object; the number lives in the ROW
  action_code       # an ADR-0008 declared code
  channel_preference
  template_id
  condition         # a declared predicate over position + case state
  requires_approval
```

**Immutability.** A published `CollectionPolicyVersion` and its steps are never
updated. Publishing a new version never rewrites an open case. Moving open cases
onto a new version is a separate previewed, audited command naming exact case
ids, old and new fingerprints, actor, reason, the timer replacements it will
perform, and the action changes it expects — the same exact-scope + fingerprint
+ explicit-confirmation shape Sub already proved in
`docs/designs/DUNNING_STAFF_SAFE_ACTIONS.md:24-38`.

**Every case pins the policy version that opened it.** `CollectionCase` stores
`policy_version_id` **and** `policy_version_fingerprint`. Every step evaluation,
every timer, and every consequence request reads the pinned version — never "the
current policy". A pinned version whose fingerprint no longer matches its stored
row is a corruption error, not a silent re-read.

**Forbidden, and what catches it:**

| Forbidden | Example | Caught by |
|---|---|---|
| A numeric day threshold in control flow | `if days_overdue > 30:` | AST test, § 4.2.1 |
| A hardcoded notice sequence | a fixed `open → warned → escalated → consequence_requested` chain | the case-lifecycle shape test; see § 4.2.3 |
| A money literal | `Decimal("0.01")`, `Decimal("5000")` | AST test, § 4.2.1 |
| A currency name | `NGN` as identifier or default | C5's grep test, already specified |
| A plan name as `applies_to` | `applies_to = "premium"` | declared-predicate validation at policy write |

#### 4.2.1 The AST test

`test_no_numeric_day_threshold_or_money_literal_in_the_module`: parse every
`.py` file under the package with `ast`, and fail on:

- a `Compare` node whose left operand's attribute/name path contains a
  time-quantity token (`days`, `age`, `overdue`, `elapsed`, `offset`, `hours`)
  and whose comparator is a numeric `Constant` other than `0`;
- any `Call` to `Decimal` or `Money` with a `Constant` argument, and any numeric
  `Constant` assigned to a name containing `amount`, `minimum`, `floor`,
  `threshold`, `tolerance` or `fee`;
- any `timedelta(...)` constructed from a literal outside a declared value-object
  constructor.

`0` is permitted as a comparator because `outstanding <= 0` is a sign test, not
a threshold, and forbidding it would push products into writing
`Decimal("0.00")` instead. Test data is out of scan scope; the scan roots are
asserted by the test itself so narrowing coverage fails the build
(ADR-0018 § "Enforcement").

*Sensitivity proof:* a fixture file containing `if days_overdue > 30:` and one
containing `Decimal("0.01")` must each make the test fail.

#### 4.2.2 The policy-replay test

`test_changing_only_the_policy_version_changes_only_the_outcome`: hold the
exposure stream, clock and subject fixed; run the engine under policy version A
and version B; assert that the **inputs** consumed are byte-identical (same
reader calls with the same arguments, same fingerprints) and that only the
step sequence, timer due times and action codes differ. Then run the same
scenario twice under version A and assert full determinism, including timer due
instants.

*Sensitivity proof:* make one engine function read the wall clock instead of the
injected instant, or read the current policy instead of the pinned one, and show
the test fails.

#### 4.2.3 The case-lifecycle shape

The case lifecycle is a small closed set — `active`, `paused`, `resolved`,
`cancelled` — plus a pinned current step and append-only step attempts. The
adoption plan states this; the reason is measurable in the source. Sub's shadow
`CollectionsCase` encodes the ladder **in the state enum and in per-state
timestamp columns** (`app/models/collections_case.py:41-47`, `:99-103`:
`open/warned/escalated/consequence_requested` with `warned_at`,
`escalated_at`, `consequence_requested_at`), and its advance function walks a
literal `_NEXT_STATE` dict (`app/services/collections/lifecycle.py:63-67`). A
five-step ladder is unrepresentable in that shape; a two-step ladder is
unrepresentable; a ladder whose third step is a second notice rather than an
escalation is unrepresentable. The test that keeps this from returning:
`test_a_ladder_of_arbitrary_length_is_representable`, parametrized over ladders
of one, two, three and seven steps, asserting the persisted step attempts match
the policy rows exactly and that no state name appears in the case lifecycle
enum that is also a step's `action_code`.

---

## 5. Steps, timers and at-most-once

### 5.1 Every step has an exact due time, generation and idempotency identity

A step is not "check later". When a case opens or advances, the owning
transition computes the next step's exact due instant from the pinned policy
version's `offset_from` anchor and `offset_days`, and schedules **one** timer
inside the same transaction. A transition that requires a future action cannot
commit without its timer — this is Sub's proven shape: `schedule_timer` is a
required flush-only participant that raises `timer_requires_owner_command` if
called outside an owner command (`app/services/runtime_durable_timers.py:1-15`,
`:77-84`).

### 5.2 Settlement, correction or cancellation replaces or cancels the exact timer

Not a sweep, not a broad requery. Closing a case cancels the case's exact
`(owner, entity_kind, entity_id, purpose)` timer in the same transaction that
closes it (`app/services/collections/lifecycle.py:359-365` already does this).
A policy change that affects open cases replaces each affected case's timer by
identity. A corrected exposure replans only the cases it touches.

### 5.3 The `Timer` port

`dotmac-durable-timers` is the accepted separate owner but is not yet released.
Collections therefore declares a **port**, ships a **fake**, and ships a
**parametrized contract suite**. The consuming application later binds that
port to the released timer module through its assembly; Collections imports no
sibling and builds no timer facility inside its schema.

```python
@dataclass(frozen=True)
class TimerRequest:
    owner: str            # the declaring owner, e.g. "collections.case"
    entity_kind: str      # "collection_case"
    entity_id: UUID
    purpose: str          # declared: "next_action" | "grace_expiry" | ...
    due_at: datetime      # aware; naive is an error, never coerced
    output_event_type: str
    expected_source_version: int

class Timer(Protocol):
    def schedule(self, req: TimerRequest) -> TimerHandle: ...   # create OR replace
    def cancel(self, *, owner, entity_kind, entity_id, purpose, observed_generation) -> CancellationOutcome: ...
    def current(self, *, owner, entity_kind, entity_id, purpose) -> TimerHandle | None: ...
    def accept_trigger(self, trigger: TimerTrigger) -> Current | Stale: ...
```

`CancellationOutcome` distinguishes `Canceled`, `AlreadyFired`,
`NothingScheduled` and `Stale`. `Stale` and stale trigger acceptance return both
`observed_generation` and `current_generation`; a boolean is insufficient
evidence for a delayed financial/access consequence.

The contract suite — the definition, which the fake and any real implementation
must both pass:

| Property | Assertion |
|---|---|
| Wake an owner/entity at a time | a scheduled timer fires at or after `due_at` and not before |
| Exactly one current timer | at most one `scheduled` row per `(owner, entity_kind, entity_id, purpose)`; a second `schedule` supersedes rather than adding |
| Generation | replacement bumps `generation`; a delivery carrying a superseded generation is rejected by the consumer and is a no-op |
| Cancel by exact identity | `cancel` on the four-tuple affects exactly that timer, never a sibling purpose, and returns one typed cancellation outcome |
| Stale delivery evidence | acceptance returns both observed and current generations and the consumer effect remains untouched |
| Transactional staging | `schedule`/`cancel` participate in the caller's transaction and flush only; they never commit (kernel rule 8) |
| Aware instants only | a naive `due_at` raises; it is never assumed UTC |
| No decisions | firing emits the declared trigger and records delivery, and reads no customer, invoice, funding or access state |
| At-most-once effect | § 5.4 |

The port preserves Sub's proven identity/generation fields and the accepted
typed-outcome corrections, so the released timer owner is an assembly binding
rather than a second Collections implementation.

### 5.4 At-most-once delegates to the kernel, and reserves nothing first

Firing a timer is a **delivery**, not an effect. The effect — a notice request,
a consequence request, a step attempt — runs under
`dotmac_kernel.idempotency.execute_once` (or `execute_once_platform` on the
platform plane) with `scope` naming the effect family and `key` the derived
step identity from § 3.2. ADR-0014's first property is load-bearing here:
**nothing is written before the effect**; the handler and the ledger row commit
together, so a crash mid-effect leaves no row and the retry re-drives cleanly.
The module must not build a reservation, lease, or "in progress" placeholder on
top of this — that is exactly the ERP defect the kernel docstring records
(`packages/dotmac-kernel/src/dotmac_kernel/idempotency.py:22-27`).

The fingerprint goes in the fingerprint parameter, not into the key. A timer
that fires twice replays the recorded outcome; a timer that fires with different
decision inputs is an `IdempotencyConflict` and blocks the case.

### 5.5 An in-process cron loop or business-wide sweep is forbidden as a substitute

**Rule.** The module ships no scheduler, no `while True` loop, no interval
thread, no cron registration, and no periodic scan over subjects, accounts,
exposures or cases. If no `Timer` implementation is bound, the module operates
in request/command-driven mode and says so; it does not degrade to polling.

**Why a sweep is not a timer** — three independent reasons, each of which is on
its own sufficient:

1. **A sweep rescans; a timer is scheduled.** A sweep reconstructs, every cycle,
   which transition *should have been* scheduled — which means the decision to
   act is re-derived from current state by a component that did not make the
   original decision. Sub's ADR-0007 rejects exactly this: "Business-wide
   dunning/prepaid sweeps: they repeatedly reconstruct work that should have
   been scheduled by the owning transition"
   (`dotmac_sub:docs/adr/0007-end-to-end-billing-target-architecture.md:671-672`).
2. **A sweep cannot be cancelled by identity.** Settlement must remove *this
   case's next action*. Against a timer that is one `cancel` on a four-tuple,
   inside the settling transaction. Against a sweep there is nothing to cancel —
   the next cycle will still visit the subject, and the only defence is a
   re-check that must reproduce the whole eligibility decision correctly, in a
   second place. Every "why did a paid customer get suspended" incident lives in
   the gap between those two implementations.
3. **A sweep turns ordering into business state.** To promise that every subject
   is visited once per cycle, a bounded sweep needs a durable cursor. Sub has
   one: `PrepaidSweepCycleState` — "keyset cursor for the bounded prepaid
   sweep's coverage cycle… checkpoints the last processed key, so every account
   is visited exactly once per cycle"
   (`dotmac_sub:app/models/collections.py:267-293`). That table is scheduling
   bookkeeping that has become persistent business state: whether a customer's
   dunning step happens today depends on where the cursor is. Its own docstring
   marks it `TRANSITIONAL: retired with prepaid_balance_sweep at the ADR 0007
   durable timer cutover`. A timer has no cursor because it has no cycle.

**Check.** `test_the_module_registers_no_periodic_scan`: an AST + import scan
over the package for `while True`, `threading.Timer`, `sched`, `asyncio.sleep`
in a loop, any APScheduler/Celery-beat registration decorator, and any function
whose body selects over the module's own case or exposure tables without a
bounding identity from its arguments.
*Sensitivity proof:* a fixture defining a `def run_due_cases_sweep(db):` that
selects all active cases must make it fail.

---

## 6. Notices, action codes, and money

### 6.1 Notices honour consent and channel policy — using what exists

A notice is a **request**, resolved by the product's communication adapter. But
the eligibility and channel decisions are already owned, and the module must not
restate them:

| Decision | Owner to use | Not this |
|---|---|---|
| May we contact this address on this channel for this category? | `dotmac_kernel.consent.may_send(db, tenant_id, channel=…, address=…, category=…)` | a local suppression list, a `do_not_contact` boolean on a case |
| Which channels does this class of message go out on? | `dotmac_kernel.channel_policy.resolve_channels(db, spec, tenant_id=…, event=…, category=…)` | a `channel` column on a policy step used as the only answer |
| What did the provider say, and does it suppress? | `dotmac_kernel.delivery.record_receipt` | a local delivery-status enum |
| Queue, retry, backoff, worker lease | `dotmac_kernel.messaging` outbox/relay | a notification queue in the collections schema |

`channel_preference` on a policy step is a **preference**, resolved against
`resolve_channels`; where they disagree, channel policy wins and the disagreement
is recorded on the step attempt. Consent is not a preference and never loses:
a suppressed address yields a typed `no_contact_route` outcome.

**Two outcomes that must stay distinct** (the adoption plan states this; the
mechanism is here): `no_contact_route` and `do_not_enforce` are different step
outcomes with different consequences. A delivery failure or a suppression
**never** becomes extra financial grace, and it **never** silently authorises
the next consequence. Which of the two a policy chooses is policy data — a
declared `on_no_contact` field on the version — not an implicit default.

`test_a_suppressed_address_does_not_grant_grace_and_does_not_authorise_a_consequence`
proves both directions, with a sensitivity proof that removes the branch and
shows the case advancing.

**Plane gap.** `dotmac_kernel.consent` and `dotmac_kernel.delivery` are
tenant-plane only: their models carry `tenant_id NOT NULL`
(`consent_models.py:117`, `delivery_models.py:133`) and there is no
`may_send_platform` or platform receipt table. `channel_policy.resolve_channels`
accepts `tenant_id: UUID | None` and so degrades to its fallback on the platform
plane. A platform-plane collections notice therefore has **no consent ledger and
no receipt loop** today. See § 12.3 — this is an open question, not a thing to
work around.

### 6.2 Every `action_code` is a declared code

ADR-0008 and hard rule 12: a vocabulary is a declaration registry, never an
enum. `action_codes` is declared on the owning module's manifest; a step
referencing an undeclared code fails the boot, and a declared code with no
consumer fails the orphan check. Sub's live shape is an enum —
`FinancialAccessAction(suspend|reject|throttle|restore)` at
`app/models/collections.py:32-37` — and that is a named non-conformance to
correct at the boundary, not to port.

Vendor codes are not predeclared. The registry gains a code in the same change
as its first real consumer.

### 6.3 Exact money only

`dotmac_kernel.money.Money` everywhere. No `float`, no implicit currency, no
cross-currency comparison, no epsilon. Receivable, available credit, funding,
arranged amount and installment coverage never collapse (C9). A de-minimis
threshold, if a product ever needs one, is a `SettingSpec` with a capped audited
waiver fact — not a tolerance constant, and ADR-0016 § 4 already owns that
decision on billing's side.

---

## 7. Contract test inventory

Every check below ships in the same revision as the code it governs, and every
one carries a sensitivity proof (ADR-0018 § 5).

| Test | Proves | Sensitivity proof |
|---|---|---|
| `test_no_module_symbol_is_named_for_one_timing_mode` | C2 structurally | fixture `plan_prepaid_consequence` fails it |
| `test_advance_and_arrears_traverse_the_same_owner_functions` | C2 behaviourally | a `if timing is advance:` skip fails it |
| `test_no_numeric_day_threshold_or_money_literal_in_the_module` | C4 statically | `if days_overdue > 30:` and `Decimal("0.01")` each fail it |
| `test_changing_only_the_policy_version_changes_only_the_outcome` | C4 behaviourally | reading the current policy instead of the pinned one fails it |
| `test_a_ladder_of_arbitrary_length_is_representable` | the ladder is data | a fixed four-state chain fails it |
| `test_a_case_pins_its_policy_version_and_fingerprint` | replay safety | re-reading current policy fails it |
| `test_the_module_registers_no_periodic_scan` | § 5.5 | a `run_due_cases_sweep` fixture fails it |
| `test_timer_contract_suite` (parametrized over fake + real) | § 5.3 | an implementation that appends instead of superseding fails it |
| `test_a_failed_consequence_is_durable_and_retryable` | § 3.2 element 7 | swallowing the exception and recording `applied` fails it |
| `test_no_account_wide_consequence_for_a_scoped_debt` | § 3.3 | widening one exposure's scope flips it to permitted |
| `test_a_suppressed_address_does_not_grant_grace_and_does_not_authorise_a_consequence` | § 6.1 | removing the branch fails it |
| `test_the_reader_is_the_only_billing_read_path` (import-linter + AST) | ADR-0024 § 2 | adding `import dotmac_billing` fails it |
| `test_reader_unavailable_blocks_the_case_and_emits_no_consequence` | § 2.5 | returning a zero position instead of `Unavailable` fails it |
| `test_source_version_is_not_compared_across_owners` | § 2.6 | a naive `>` comparison fails it |
| `test_idempotency_key_is_derived_not_random` | § 3.2 element 2 | a `uuid4()` component fails it |

---

## 8. What this spec does not do

It does not create a package, a namespace, a lineage, a model, a migration or an
`EXTRACTION.toml`. P11 is now met and ADR-0030/0032 authorize the named module;
this historical spec still does not itself claim implementation or adoption. It does not restate the adoption plan's
gates, cutovers or retirement sequence. It does not specify billing's producer
side, arrangements, or grace beyond the fields the three contracts touch.

---

## 9. Conflicts with the parallel adoption plan

`docs/superpowers/plans/2026-08-14-collections-sub-vendor-cp-adoption.md` was
authored concurrently and is not edited by this document. Contradictions found,
with evidence:

### 9.1 Inbound seam: pushed command vs read port

- **Plan:** "The module accepts one versioned `AssessCollectionExposure`
  command. Its typed payload carries… currency and exact amount" (plan
  § "Inbound exposure").
- **This spec:** the amount is read at decision time through
  `ReceivablesReader`; the command carries identity, explicit scope and trigger
  provenance only.
- **Why it matters:** a queued command carrying money goes stale between enqueue
  and a step that fires days later, and a timer fire has no message to re-read.
- **Resolved by ADR-0032:** `AssessCollectionExposureV1` carries identity,
  explicit scope and trigger provenance, never an amount; the reader supplies
  current money at every decision point.

### 9.2 Outbound contract naming

- **Plan:** `CollectionActionRequested`.
- **This spec's original brief:** `ConsequenceRequestV1`.
- These are the same record. The fields the plan lists are a subset of § 3.2.
  ADR-0032 chooses `CollectionActionRequestedV1`; `ConsequenceRequestV1` does
  not ship as an alias.

### 9.3 Timer ownership: facility vs port

- **Plan G2 originally:** extract Sub's `runtime.durable_timers` into "the
  appropriate kernel facility" as a separate release, before the live Sub slice.
- **This spec § 5.3:** the module declares a `Timer` **port** with a fake and a
  contract suite.
- **Resolved:** the port is what Collections declares,
  `dotmac-durable-timers` is what the assembly binds to it, and the fake makes
  the module developable and testable before timer-backed behavior. Recorded here because
  "extract the facility" and "declare a port" have been conflated before, and
  because a port with no adopter must not land in the kernel (P6's lesson,
  `billing-sources.md` P6).

### 9.4 Case lifecycle vocabulary

The plan's closed set is `active | paused | resolved | cancelled`. Sub's live
`DunningCaseStatus` is `open | paused | resolved | closed`
(`app/models/collections.py:25-29`) and its shadow `CollectionsCaseState` is
`open | warned | escalated | consequence_requested`
(`app/models/collections_case.py:41-47`). Three vocabularies for one concept.
The plan's set is correct and this spec uses it; the mapping of each live and
shadow value onto it is a **total classifier** owed by cutover stage S0, and no
default bucket is allowed.

### 9.5 No conflict found on planes, ownership, or the demand gate

§ "Persistence planes", § "Ownership boundary" and Vendor CP's demand gate are
consistent with ADR-0020 A2/A6, ADR-0023 and this spec. The dossier's
`first_cutover`, `shadow_and_drift` and `local_copy_retirement` are written to
match the plan and are not an independent sequence.

---

## 10. Relationship to the billing cutover plan

`docs/superpowers/plans/2026-08-14-billing-vendor-cp-sub-cutover.md` is a
constraint on this module, not a prerequisite for it. The adoption plan is right
that "`dotmac-billing` adoption is therefore not a prerequisite for the first
collections cutover" — before Sub adopts billing, Sub's assembly binds the
`ReceivablesReader` to Sub's existing authoritative financial facts, and the
later billing cutover changes the producer behind the port, not the collections
owner.

What the billing cutover does impose is § 2.5's window behaviour. Because
invoices, settlements and allocations switch production authority **together**,
there is a bounded period with no authoritative producer, and a dunning case
must not trust a receivable position during it. If the collections cutover
completes first — which the sequencing makes likely — then a live collections
module will be running when that window opens, and § 2.5 is the difference
between a quiet pause and a batch of wrong suspensions.

---

## 11. Original open questions and disposition

ADR-0032 disposes the contract and initial-plane questions:

1. **§ 9.1 — resolved:** `AssessCollectionExposureV1` plus reader; no command
   money.
2. **§ 9.2 — resolved:** `CollectionActionRequestedV1`.
3. **§ 6.1 plane gap — demand-gated:** the platform plane has no consent ledger and no
   delivery receipt loop. Options: (a) Vendor CP collections notices are
   product-owned and the module never requests one on the platform plane;
   (b) `dotmac_kernel.consent`/`delivery` gain platform variants under ADR-0023,
   which is kernel work with a named adopter and a retirement, i.e. its own
   dossier; (c) the demand gate defers it until a real Vendor overdue case
   exists. **Recommendation: (c) then (a)** — the demand gate already defers
   Vendor work, and inventing a platform consent ledger before a real Vendor
   notice exists is supply-pushed persistence of exactly the kind ADR-0017
   measured.
4. **Grace anchor default — resolved:** the policy declares whether grace anchors on
   exposure time, request time or accepted notice receipt. There is no safe
   default; a missing anchor is a policy validation failure.
5. **`effect_scope` vocabulary — still extensible:** `obligation | service | contract |
   subject` is proposed from Sub's shape. Vendor CP's consequence scope is
   unknown until it has a real case, and the vocabulary is a declaration
   registry precisely so it can grow. Confirm the four initial values.
