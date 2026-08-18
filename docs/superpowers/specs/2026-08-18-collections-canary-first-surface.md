# Collections canary-first public and persistence surface

- **Status:** implementation-ready, non-authoritative specification. ADR-0032
  owns the boundary; this file pins the first executable shapes and proofs.
- **Starter pin:** `8d4ddfd9e285da06ce1fdd29b59f1b483d6ea38c`
- **Sub pin:** `d1a1a913e287ffadaf21b7da7be448f2c28b5483`
- **Source dossier:** `docs/inventories/collections-extraction-dossier.md`
- **Contract decision:** ADR-0032
- **Current stop:** the active `starter-billing` worktree owns the namespace
  ledger, kernel/root package metadata, lockfile and migration-test surfaces.
  This specification creates none of them and does not reserve an allocation.

## 1. First-diff rule

The executable canaries may precede the package while the Billing overlap is
active. Once that overlap settles, the first Collections package diff turns
them green in this order:

1. strict frozen public values, ports and in-memory fakes;
2. their contract tests and architecture sensitivity proofs;
3. the exact tenant table declaration and creation migration;
4. migrated-PostgreSQL RLS, grants, composite-identity, immutability and
   concurrency proofs; then
5. behavior, one owner slice at a time.

No service behavior lands ahead of those tests. No timer table, scanner, claim
loop or retry engine lands in any slice. The separately released
`dotmac-durable-timers` contract is an assembly binding, never a sibling import.

Initial RED evidence (2026-08-18) is already checked into this working slice:

- `tests/unit/test_collections_contract_canaries.py` pins the assessment,
  position, reader-outcome, fake-reader, arbitrary-ladder, notice/action
  requests and both typed owner-receipt families. On
  `dotmac-observe`, in a disposable worktree at exact Starter base
  `8d4ddfd9e285da06ce1fdd29b59f1b483d6ea38c`, Ruff format and lint passed and
  pytest failed during collection only because `dotmac_collections` does not
  exist yet (`ModuleNotFoundError`).
- `tests/unit/test_collections_domain_behavior_canaries.py` pins deterministic
  immutable publication, one evaluator for one/four/seven-step ladders, typed
  missing-anchor evidence, exact exposure-scoped arrangement membership and
  schedules, explicit grace evaluation, and action-receipt replay/conflict
  behavior. On Observe at the same exact base, Ruff `0.6.9` format and lint
  passed; pytest was RED only on the absent `dotmac_collections` package.
- `tests/unit/test_collections_timer_port_canaries.py` pins explicit scoped
  identity, caller-supplied aware instants, generation supersession, exact
  purpose cancellation, the four typed cancellation outcomes, stale evidence
  with observed/current generations, and replay-aware current acceptance. Ruff
  passed on Observe at the same base and pytest was RED only on the absent
  `dotmac_collections.timers` module (`1 error in 0.14s`).
- `tests/architecture/test_collections_module_boundaries.py` rejects a missing
  or empty scan root, application/sibling/provider imports, ambient clocks,
  timing-specific owner symbols, scheduler loops/sleeps/sweeps, and hardcoded
  money/time thresholds. It canonicalizes import aliases so `datetime as
  clock`, `Decimal as Exact` or `sleep as wait` cannot bypass the detector. On
  the same host and base, all twelve sensitivity/clean
  fixtures passed; the full file was RED only on the exact missing-package
  sentinel (`1 failed, 12 passed`).
- `tests/architecture/test_collections_stateful_contract.py` statically binds
  the package's `EXTRACTION.toml`, distribution dependencies, atomic tenant
  manifest, logical prerequisites, exact 17-table declaration, independent
  lineage root, schema qualification, tenant-composite identity/FKs, RLS,
  grants and constrained-string posture. Its complete synthetic package plus
  eleven planted defects passed (`12 passed, 1 deselected`); the full file was
  RED only on `package-missing:packages/dotmac-collections` (`1 failed, 12
  passed`). The planted cases include a greenfield dossier, platform table or
  selector, missing outbox prerequisite, nullable tenant, id-only FK, missing
  FORCE RLS, native enum, `search_path`, foreign revision and unused
  `platform_api` grant.
- `tests/fixtures/collections_preserved_sub_scenarios.json` and
  `tests/architecture/test_collections_preserved_sub_scenarios.py` preserve 17
  product-neutral scenarios from 22 exact pytest nodes at the pinned Sub
  revision. They keep authoritative partial positions, incomplete-source
  failure, case replay/close/reopen, timer replacement, exact arrangement
  schedules/membership, grace boundaries, stale previews, typed action evidence
  and notice suppression executable without importing the absent package. The
  guard rejects source-pin or scenario drift, floats, product-model keys,
  implicit grace anchors and inexact arrangement totals. On Observe at the
  exact Starter base, Ruff passed, the primary plus five planted mutations
  passed (`6 passed`), and an AST verifier confirmed all 22 pytest nodes exist
  in the exact Sub source files.

None of these lanes used PostgreSQL. The fifth lane used disposable checkout
`/opt/dotmac-agent-tests/collections-domain.S2AZJI/wt` and a temporary venv
containing only the lock-pinned Ruff `0.6.9` and pytest `8.4.2`; both were
removed after the results were recorded. All earlier disposable worktrees and
virtual environments were likewise removed. The preservation proof used
disposable checkout `/opt/dotmac-agent-tests/collections-preserved.BsvMmD/wt`
and its isolated venv; both were removed after validation.

## 2. Package layout to create after the overlap clears

```text
packages/dotmac-collections/
  EXTRACTION.toml
  pyproject.toml
  src/dotmac_collections/
    __init__.py
    manifest.py
    contracts.py
    receivables.py
    timers.py
    policies.py
    cases.py
    arrangements.py
    grace.py
    actions.py
    reconciliation.py
    models.py
    migrations/
      __init__.py
      versions/<allocated-prefix>_0001_collections.py
```

`dotmac_collections` imports only published `dotmac_kernel` surfaces and the
Python standard library. It imports no assembly, Billing, Subscriptions,
application model, notification provider or timer module.

## 3. Exact inbound read seam

All values below are frozen, slotted dataclasses. Every datetime validator
rejects a naive instant. Every opaque reference is a non-empty string and is
never parsed as a product model identifier. `Scope` is the published kernel
`TenantScope | PlatformScope` type; revision-one persistence accepts only
`TenantScope`, while the pure contract remains plane-neutral.

### 3.1 Assessment command

```python
@dataclass(frozen=True, slots=True)
class TriggerProvenanceV1:
    kind: str                 # source_event | timer | reconciliation | manual
    trigger_id: str
    triggered_at: datetime   # aware; supplied, never read from ambient clock


@dataclass(frozen=True, slots=True)
class AssessCollectionExposureV1:
    command_id: UUID
    idempotency_key: str
    correlation_id: UUID
    causal_event_id: str | None
    scope: Scope
    source_owner: str
    exposure_ref: str
    subject_ref: str
    service_ref: str | None
    collection_timing: str   # advance | arrears
    reason_code: str
    trigger: TriggerProvenanceV1
```

The command has no amount, currency, balance, credit, funding, due/coverage
anchor, position version, position fingerprint, resolved state or policy
choice. A canary asserts that none of those field names exists. Constructors
reject unknown keyword arguments, so a caller cannot smuggle a money snapshot
through a compatibility bag.

### 3.2 Current position

```python
@dataclass(frozen=True, slots=True)
class ReceivablePositionV1:
    scope: Scope
    source_owner: str
    exposure_ref: str
    source_version: int
    state_fingerprint: str
    subject_ref: str
    service_ref: str | None
    collection_timing: str
    reason_code: str
    collectible_receivable: Money
    available_credit: Money
    funding_available: Money
    due_at: datetime | None
    coverage_start_at: datetime | None
    resolution: str          # open | partially_resolved | resolved | cancelled | reversed
    authority: str           # authoritative | shadow
    completeness: str        # complete | opening_source_incomplete
    observed_at: datetime
```

The three `Money` values are required, exact and in one currency. A postpaid
position represents unused funding as exact zero; a prepaid position represents
no receivable as exact zero. They never collapse into a generic `balance`.
Negative amounts, mixed currencies, source versions below one, naive instants,
and a timing/anchor mismatch fail construction.

The accepted reader outcomes are typed and exhaustive:

```python
@dataclass(frozen=True, slots=True)
class PositionReadOk:
    position: ReceivablePositionV1

@dataclass(frozen=True, slots=True)
class PositionUnavailable:
    reason_code: str
    retry_after: datetime | None

@dataclass(frozen=True, slots=True)
class PositionUnknown:
    source_owner: str
    exposure_ref: str

@dataclass(frozen=True, slots=True)
class PositionAuthorityMismatch:
    expected_owner: str
    observed_owner: str

ReceivablesReadResultV1 = (
    PositionReadOk
    | PositionUnavailable
    | PositionUnknown
    | PositionAuthorityMismatch
)

class ReceivablesReader(Protocol):
    def read(
        self,
        *,
        scope: Scope,
        source_owner: str,
        exposure_ref: str,
        as_of: datetime,
    ) -> ReceivablesReadResultV1: ...
```

`PositionUnavailable` is retryable by definition and never means zero.
`PositionUnknown` and `PositionAuthorityMismatch` are terminal correction
evidence for that exposure. The reader is called for initial assessment, every
timer delivery, step advance, arrangement/grace decision, request emission,
closure and reopening.

### 3.3 Reader fake

`FakeReceivablesReader` is part of the public testing surface. It:

- is configured by exact `(scope, source_owner, exposure_ref)` identity;
- requires an explicit typed result for every identity and raises
  `AssertionError` when unconfigured rather than inventing zero or `Unknown`;
- records every read including the caller-supplied aware `as_of`;
- supports replacing a result between calls, so a test proves delayed behavior
  rereads rather than caches; and
- reads no ambient clock.

Its callable surface is pinned so assembly adapters can run the same contract
suite without a compatibility wrapper:

```python
@dataclass(frozen=True, slots=True)
class ReceivablesReadCallV1:
    scope: Scope
    source_owner: str
    exposure_ref: str
    as_of: datetime

class FakeReceivablesReader:
    @property
    def calls(self) -> tuple[ReceivablesReadCallV1, ...]: ...

    def set_result(
        self,
        *,
        scope: Scope,
        source_owner: str,
        exposure_ref: str,
        result: ReceivablesReadResultV1,
    ) -> None: ...

    def read(
        self,
        *,
        scope: Scope,
        source_owner: str,
        exposure_ref: str,
        as_of: datetime,
    ) -> ReceivablesReadResultV1: ...
```

The contract suite runs unchanged against the fake and each Sub assembly
adapter.

## 4. Policy publication and arbitrary ladders

```python
@dataclass(frozen=True, slots=True)
class GraceRuleV1:
    duration: timedelta
    anchor: str  # exposure_at | request_at | accepted_notice_receipt_at

@dataclass(frozen=True, slots=True)
class PolicyStepDraftV1:
    code: str
    ordinal: int
    offset: timedelta
    offset_anchor: str
    request_kind: str         # notice | action | review
    action_code: str | None
    receipt_required: bool

@dataclass(frozen=True, slots=True)
class PolicyVersionDraftV1:
    policy_code: str
    reason_code: str
    collection_timing: str
    grace: GraceRuleV1 | None
    steps: tuple[PolicyStepDraftV1, ...]
```

A ladder has one or more uniquely coded steps with contiguous, strictly
increasing ordinals and non-decreasing offsets. It is not the Sub shadow
`open -> warned -> escalated -> consequence_requested` enum. Publication
computes a stable fingerprint and makes the version and every step immutable.
Changing a published ladder creates a new version. Moving an existing case is a
separate previewed command naming the exact old/new fingerprints and case IDs.

There is no grace default. A policy that uses grace but supplies no
`GraceRuleV1.anchor` cannot publish. A missing subject anchor produces typed
non-actionable evidence; it never silently selects exposure, request or notice
time.

## 5. Requests and typed owner receipts

### 5.1 Notice

`CollectionNoticeRequestedV1` carries request/case/policy/step/exposure
identities, a declared purpose code, decision evidence, requested time and
idempotency identity. It carries no address, rendered body, provider, template
implementation, channel selection or consent decision.

The exact request shape is the action request minus the product consequence
vocabulary. `purpose_code` is a stable communication intent, not a template or
channel:

```python
@dataclass(frozen=True, slots=True)
class CollectionNoticeRequestedV1:
    request_id: UUID
    idempotency_key: str
    case_id: UUID
    policy_version_id: UUID
    policy_step_code: str
    step_attempt_ordinal: int
    source_owner: str
    exposure_ref: str
    source_version: int
    position_fingerprint: str
    subject_ref: str
    service_ref: str | None
    purpose_code: str
    decision_evidence: ReceivablePositionV1
    requested_at: datetime
```

The communication owner returns one typed outcome:

- `NoticeAccepted` with its owner receipt identity and accepted time;
- `NoticeSuppressed` with a stable suppression reason;
- `NoticeUnavailable` with an optional retry time; or
- `NoticeFailed` with a stable reason and retryable flag.

Every notice outcome carries `request_id`, `owner_code` and a unique
`owner_receipt_id`. `NoticeAccepted` adds `accepted_at`; suppression adds
`reason_code` and `observed_at`; unavailability adds those fields plus optional
`retry_at`; failure adds them plus `retryable`. All instants are supplied and
timezone-aware. A transport acknowledgement is acceptance by the communication
owner, not evidence of customer delivery and never product-action evidence.

Suppression, unavailability and delivery failure are distinct and none grants
implicit grace or authorizes the next consequence.

### 5.2 Product action

```python
@dataclass(frozen=True, slots=True)
class CollectionActionRequestedV1:
    request_id: UUID
    idempotency_key: str
    case_id: UUID
    policy_version_id: UUID
    policy_step_code: str
    step_attempt_ordinal: int
    source_owner: str
    exposure_ref: str
    source_version: int
    position_fingerprint: str
    subject_ref: str
    service_ref: str | None
    action_code: str
    effect_scope: str
    decision_evidence: ReceivablePositionV1
    requested_at: datetime
```

The position is evidence, not an instruction and not a current-balance API.
Before building this request, Collections rereads `ReceivablesReader`. The
effect scope cannot be broader than the narrowest scope shared by the admitted
case exposures.

The owner returns exactly one immutable receipt variant:

- `ActionApplied(action_ref, applied_at, owner_state_fingerprint)`;
- `ActionRefused(reason_code, observed_at, owner_state_fingerprint)`;
- `ActionDeferred(reason_code, observed_at, retry_at)`; or
- `ActionFailed(reason_code, observed_at, retryable)`.

Every variant carries `request_id`, `owner_code` and a unique
`owner_receipt_id`. Same request/same receipt is a replay. Same request with a
different receipt fingerprint is a conflict. A transport acknowledgement is
never converted into `ActionApplied`.

The exact receipt value shapes are:

```python
@dataclass(frozen=True, slots=True)
class ActionApplied:
    request_id: UUID
    owner_code: str
    owner_receipt_id: str
    action_ref: str
    applied_at: datetime
    owner_state_fingerprint: str

@dataclass(frozen=True, slots=True)
class ActionRefused:
    request_id: UUID
    owner_code: str
    owner_receipt_id: str
    reason_code: str
    observed_at: datetime
    owner_state_fingerprint: str

@dataclass(frozen=True, slots=True)
class ActionDeferred:
    request_id: UUID
    owner_code: str
    owner_receipt_id: str
    reason_code: str
    observed_at: datetime
    retry_at: datetime

@dataclass(frozen=True, slots=True)
class ActionFailed:
    request_id: UUID
    owner_code: str
    owner_receipt_id: str
    reason_code: str
    observed_at: datetime
    retryable: bool

CollectionActionReceiptV1 = (
    ActionApplied | ActionRefused | ActionDeferred | ActionFailed
)
```

## 6. Timer port and fake only

The Collections package declares the narrow port it consumes:

```python
@dataclass(frozen=True, slots=True)
class TimerIdentityV1:
    scope: Scope
    owner: str
    entity_kind: str
    entity_id: str
    purpose: str

@dataclass(frozen=True, slots=True)
class TimerRequestV1:
    identity: TimerIdentityV1
    due_at: datetime
    recorded_at: datetime
    output_event_type: str

@dataclass(frozen=True, slots=True)
class TimerHandleV1:
    timer_id: UUID
    identity: TimerIdentityV1
    generation: int
    due_at: datetime
    output_event_type: str

@dataclass(frozen=True, slots=True)
class CancelTimerV1:
    identity: TimerIdentityV1
    observed_generation: int
    recorded_at: datetime

@dataclass(frozen=True, slots=True)
class TimerTriggerV1:
    timer_id: UUID
    identity: TimerIdentityV1
    generation: int
    due_at: datetime
    output_event_type: str

@dataclass(frozen=True, slots=True)
class Canceled:
    observed_generation: int
    current_generation: int

@dataclass(frozen=True, slots=True)
class AlreadyFired:
    observed_generation: int
    current_generation: int

@dataclass(frozen=True, slots=True)
class NothingScheduled:
    observed_generation: int
    current_generation: None = None

@dataclass(frozen=True, slots=True)
class Stale:
    observed_generation: int
    current_generation: int

CancellationOutcomeV1 = Canceled | AlreadyFired | NothingScheduled | Stale

@dataclass(frozen=True, slots=True)
class Current:
    observed_generation: int
    current_generation: int
    replayed: bool = False

TriggerAcceptanceV1 = Current | Stale

class CollectionsTimer(Protocol):
    def schedule(self, request: TimerRequestV1) -> TimerHandleV1: ...
    def cancel(self, request: CancelTimerV1) -> CancellationOutcomeV1: ...
    def accept_trigger(
        self,
        trigger: TimerTriggerV1,
        *,
        accepted_at: datetime,
    ) -> TriggerAcceptanceV1: ...
```

Identity is `(owner, entity_kind, entity_id, purpose)`. Handles and triggers
carry a generation, while `scope` remains a distinct typed part of the durable
identity. A stale acceptance or stale cancellation returns both observed and
current generation. `NothingScheduled` keeps the attempted generation and an
explicitly absent current generation, so it cannot be conflated with
`AlreadyFired`. Every supplied instant is timezone-aware; the port reads no
clock.

The fake preserves generation, supersession, exact-identity cancellation,
typed outcomes and replay-aware stale rejection, but it has no thread, scanner,
sleep, lease, retry or clock. It exposes `current(identity)` for test inspection
only; Collections behavior never uses that method to replace its own recorded
handle. The real adapter belongs to Sub's assembly and targets the released
`dotmac-durable-timers` contract.

Candidate audit on 2026-08-18: the separate durable-timers worktree is still
uncommitted, unreleased, `status = "audit-complete"`, and declares
`contract_consumers = []`. Its current `CancelOutcome` has
`ALREADY_CANCELED` rather than the approved typed `Stale` carrying observed and
current generations, and its cancel command accepts no observed generation.
Collections therefore does not import, adapt or claim that candidate yet. The
timer owner must resolve the contract and release/adoption gates first.

## 6A. Pure policy, arrangement, grace and receipt helpers

The first behavior slice keeps its calculations independent of persistence so
the same engine is used by both timing modes and by any later real plane. The
public pure surface is intentionally narrow:

- `publish_policy_version(draft, publication)` returns a frozen published
  version and frozen steps with a stable `version_fingerprint`. The publication
  command supplies `policy_version_id`, positive `version`, aware
  `effective_from`/`published_at`, actor and reason. Publishing a successor
  cannot mutate the prior value.
- `evaluate_policy_version(version, anchors, completed_step_codes, as_of)`
  returns `StepWaiting`, `StepDue`, `AnchorUnavailable`, or `LadderComplete`.
  `PolicyAnchorSetV1` has three explicit optional values:
  `exposure_at`, `request_at`, and `accepted_notice_receipt_at`. A missing
  declared anchor is typed non-actionable evidence. Completed steps must be an
  exact prefix of the pinned version.
- `PaymentArrangementDraftV1` contains an explicit scope, subject, proposal
  instant, exact `ArrangementExposureV1` membership and explicit ordered
  `InstallmentDraftV1` values. Exposure membership records source owner/ref,
  source version/fingerprint, subject/service and exact admitted `Money`.
  Installments contain only ordinal, exact `Money`, and aware `due_at`; there
  is no frequency or mutable paid counter. Construction rejects duplicate
  exposure identities, mixed currency, non-contiguous installments, naive or
  unordered due times, and a schedule total unequal to admitted exposure
  totals. `arrangement_protects_exposure` compares source owner plus exposure
  identity and never subject/account identity.
- `GraceGrantV1` requires scope, case, explicit `anchor_kind` and `anchor_at`,
  non-negative duration, actor/reason and supplied grant instant.
  `evaluate_grace(grant, as_of=...)` returns `GraceActive` or `GraceExpired`;
  zero duration is immediately expired/actionable. There is no default anchor
  and no ambient clock fallback.
- `FakeActionReceiptRecorder` implements the receipt evidence contract without
  transport or persistence. Recording the same canonical receipt returns a
  replay marker; a different receipt fingerprint for one `request_id` raises
  `ActionReceiptConflict`. Its `receipts` property contains one value per
  request, never duplicate replay rows.

These helpers make no lifecycle writes. The named services in section 8 remain
the sole persistent writers and wrap the helpers inside the caller's kernel
transaction.

## 7. Exact revision-one tables

The first manifest declares these tenant tables and no platform tables:

```python
TENANT_TABLES = (
    "collection_policies",
    "collection_policy_versions",
    "collection_policy_steps",
    "collection_cases",
    "collection_case_exposures",
    "collection_case_transitions",
    "collection_step_attempts",
    "payment_arrangements",
    "payment_arrangement_exposures",
    "payment_arrangement_installments",
    "payment_arrangement_settlement_receipts",
    "collection_grace_grants",
    "collection_notice_requests",
    "collection_notice_receipts",
    "collection_action_requests",
    "collection_action_receipts",
    "collection_reconciliations",
)
PLATFORM_TABLES = ()
```

This is an explicit, atomic tenant-plane declaration. `supported_plane_sets`
is empty and Sub supplies no `ModulePlaneSelection`, because ADR-0028 rejects a
selector when there is no choice. A future real platform adopter adds distinct
platform tables and every genuinely supported subset in one additive release.

Every table has:

- `tenant_id UUID NOT NULL`;
- `UNIQUE (tenant_id, id)`;
- tenant-composite unique keys;
- child foreign keys beginning with `tenant_id` and referencing the parent's
  `(tenant_id, id)`;
- no foreign key to a Sub, Billing, Subscription, invoice, payment, service or
  access table;
- RLS ENABLE and FORCE, its tenant policy, and exact grants in the creation
  migration; and
- constrained string columns rather than PostgreSQL enums.

Only `tenant_id -> public.tenants.id` crosses the schema, through the declared
`tenant_scope_catalog.v1` prerequisite. The module also requires
`module_database_roles.v1` and the outbox prerequisite used by the exact current
kernel release; the package-creation diff verifies the released effect name
rather than copying it from this specification.

### 7.1 Structural ownership

- `collection_policy_versions` and `collection_policy_steps` become
  database-immutable after publication. Direct UPDATE/DELETE is refused, not
  merely hidden behind service convention.
- `collection_cases` holds the small lifecycle
  `active | paused | resolved | cancelled`, the pinned policy version and
  current step. It stores no four-step ladder state.
- `collection_case_exposures` is the exact durable membership. Amount columns
  are named `decision_*_amount` and are evidence at an admitted source version,
  never `balance` or `current_amount`.
- `collection_case_transitions`, `collection_step_attempts`, request rows,
  receipts and reconciliation evidence are append-only.
- `payment_arrangement_exposures` is mandatory. An arrangement cannot shield an
  account or exposure it does not contain.
- installment rows store explicit aware `due_at` instants and exact amounts.
  There is no frequency enum and no mutable `installments_paid` authority.
- settlement receipts are deduplicated billing observations. Staff cannot mark
  an installment financially paid.
- `collection_grace_grants.anchor_kind` and `anchor_at` are both non-null.
- action requests are uniquely keyed by the derived idempotency identity;
  retries add attempt evidence without creating a second product effect.

## 8. One named writer per transition

| State or decision | Sole writer |
|---|---|
| Policy draft, publish and successor version | `CollectionPolicyService` |
| Case open, step advance, pause, resume, close and reopen | `CollectionCaseService` |
| Exposure admission/removal from a case | `CollectionCaseService` |
| Arrangement proposal, activation, default, cancellation and fulfillment | `PaymentArrangementService` |
| Settlement receipt application to installments | `PaymentArrangementService` |
| Grace grant, supersession and revocation | `CollectionGraceService` |
| Notice request and receipt evidence | `CollectionNoticeService` |
| Product-action request and owner receipt evidence | `CollectionActionService` |
| Drift repair and reconciliation evidence | `CollectionReconciliationService` |

Routes, jobs, timer handlers, inbox handlers and Sub adapters validate,
authorize and delegate to these owners. None maintains a parallel state path.

## 9. Canary matrix before behavior

### 9.1 Pure contract and fake proofs

1. The assessment command has identity/scope/trigger provenance and no money or
   position-state fields; planting `amount` makes the architecture test fail.
2. Every money value rejects `float`, mixed currencies and epsilon comparisons.
3. Every command, position, policy, request, receipt and timer instant rejects a
   naive datetime.
4. The reader fake has no default result, records calls and returns a changed
   version on the delayed reread.
5. `Unavailable` advances nothing, emits no request and remains retryable;
   mutating the branch to zero fails the test.
6. An arbitrary one-, four- and seven-step ladder evaluates from the same
   engine; reintroducing the fixed Sub shadow transition map fails.
7. Published policy values and steps are frozen; a new version leaves an open
   case pinned to the old fingerprint.
8. A grace-using policy without an anchor fails publication; zero duration with
   an explicit anchor remains immediately actionable.
9. Arrangement coverage protects only exact admitted exposures; adding a new
   same-subject exposure remains collectible.
10. Receipt replay is idempotent, while a different fingerprint for one request
    is a conflict.
11. A stale timer acceptance exposes observed/current generations and executes
    no case or product effect.
12. AST/import tests reject sibling/application imports, product/provider
    branches, ambient clocks, schedulers and unbounded sweeps, each with a
    planted sensitivity violation.

### 9.2 Migrated PostgreSQL proofs

Every concurrency proof uses independent PostgreSQL connections, an explicit
isolation level and a deterministic rendezvous; sleeps and probabilistic races
are not evidence.

1. **Catalog contract:** all 17 declared tables exist and no undeclared table
   does; every table has non-null `tenant_id`, RLS ENABLE and FORCE, exactly one
   tenant policy and the intended role grants.
2. **Tenant isolation:** tenant A cannot select, count, join, insert, update or
   delete tenant B's policy, case, exposure, arrangement, grace, request,
   receipt or reconciliation evidence.
3. **Composite identity:** every tenant-relative unique and foreign key includes
   `tenant_id`; a planted id-only FK fails the live-catalog proof.
4. **Concurrent first case:** two transactions opening the same live case yield
   one case and one membership set; no whole-transaction rollback loses the
   winner's tenant context.
5. **Concurrent step action:** two transactions advancing the same due step
   yield one step attempt, one action request and one outbox effect.
6. **Published immutability:** direct SQL and service attempts to update/delete
   a published version or step are refused; drafts remain editable only through
   the policy owner.
7. **Exact arrangement membership:** concurrent proposal/activation cannot
   admit more than the exact source version and amount the reader returned.
8. **Settlement replay/race:** duplicate or concurrent settlement receipts
   allocate once, never overpay an installment, and close only cases whose exact
   exposures are satisfied.
9. **Grace/action race:** revoking or superseding grace while a step fires
   produces one serial owner outcome, never both an active shield and an action
   request.
10. **Receipt replay/conflict:** the same owner receipt replays; a different
    fingerprint for one request fails without regressing the recorded result.
11. **Transactional rollback:** a failed owner transaction leaves no partial
    transition, membership, request, receipt, timer staging or outbox row.
12. **Append-only evidence:** UPDATE/DELETE on transitions, attempts, requests,
    receipts and reconciliation evidence is refused to online roles.
13. **No unused plane:** the schema contains no platform collection table, no
    nullable/sentinel tenant and no cross-plane foreign key.

## 10. Preserved Sub behavior and deliberate corrections

The first behavior fixtures are ported from the exact tests named in the
extraction dossier. They preserve:

- exact partial-settlement arithmetic;
- postpaid-only receivable evaluation and prepaid-only funding evaluation;
- fail-closed incomplete prepaid opening sources;
- one current timer generation per exact case purpose;
- idempotent close and fresh reopen;
- grace precedence, explicit zero, and invalid-setting failure;
- arrangement proposal/activation/default/cancellation and exact schedule
  totals; and
- stale preview refusal plus applied/restored consequence evidence.

The extraction deliberately corrects rather than copies:

- mutable policy sets and a fixed four-state ladder;
- account-wide case/arrangement shielding;
- frequency-derived installments and mutable `installments_paid`;
- direct credential, RADIUS, Subscription or invoice writes;
- swallowed restoration/action failures;
- periodic account sweeps as time ownership; and
- PostgreSQL enums, missing tenant identity and missing RLS.

## 11. Sub shadow and retirement evidence owed later

After module release and timer adoption, Sub's assembly shadows both owners by
exact `(tenant, subject, effect scope, reason, currency, exposure version)` and
compares case existence, pinned policy, step, timer identity/generation/due
time, notices, arrangement/grace scope, action request/receipt, settlement close
and reopen. No request escapes the shadow path.

A bounded cohort cutover is an irreversible production-state change and needs
Michael's explicit authorization. Expansion follows only after clean
reconciliation. Retirement then lowers two-directional ratchets, with planted
sensitivity failures, to zero for:

- `dunning_runner`;
- `prepaid_balance_sweep`;
- direct invoice, credential, `Subscription` and access writers; and
- displaced Collections models, services, jobs, scheduler registrations and
  tables.

The uncommitted 2026-08-18 Sub adoption worktree has installed the first such
guardrail without retiring anything: a syntax-only `app/` + `scripts/` AST
scanner, exact JSON count baseline, and sensitivity suite covering the R1-R12
surface, ambient clock reads, direct access/notice calls and module-alias
consumers of legacy access and receivable APIs. On Observe at exact Sub base
`d1a1a913e287ffadaf21b7da7be448f2c28b5483`, format and lint passed, the suite
passed (`4 passed`). Planted `radius_profile_id` and aliased
`restore_account_services` sites produced the expected `4 -> 5` and `9 -> 10`
ratchet failures before their clean reruns passed. The baseline is therefore a
measured debt inventory, not an exemption or adoption proof.

Git-hosted CI at the exact proposed head, release evidence, a real Sub pin and
the production shadow/cutover records remain mandatory; this specification is
not evidence for any of them.
