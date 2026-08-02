# Dotmac ERP — selective kernel adoption and financial delivery improvements

> **Status:** Proposed implementation plan, 2026-08-02. This is non-authoritative
> execution intent; `dotmac_erp/docs/SOT_RELATIONSHIP_MAP.md`, its executable
> registry, `docs/gl_source_of_truth.md`, checked-in cross-product contracts, and
> accepted Dotmac ADRs remain authoritative. The plan authorizes no code, schema,
> release, or production change by itself.
>
> **Target repository:** `dotmac_erp`.
>
> **Evidence basis:** `dotmac_erp` `origin/main` at
> `96928fa1774612ecd5cd28db1ab04b8e45425df4` plus released
> `dotmac-kernel==0.1.0a7`. Uncommitted or feature-branch worktree changes were
> deliberately excluded.
>
> **Relationship to earlier plans:** this is the ERP-specific execution plan under
> `2026-07-18-existing-product-adoption.md`. It refreshes the older Phase-0 ledger,
> which predates ERP's checked-in SOT map, current Sub integration contracts, and the
> released kernel.

## Outcome

Improve ERP by adopting product-neutral kernel contracts at safe seams while retaining
ERP authority for organization tenancy, GL/AP/AR, inventory, tax, payroll, HR, assets,
procurement, and accounting reconciliation.

The target flow for external financial and stock consequences is:

```text
validated versioned request
  -> ERP admission owner persists exact evidence + fingerprint
  -> canonical ERP finance/inventory owner commits consequence
  -> ERP outbox stages exact applied/rejected acknowledgement
  -> transport delivers after commit
  -> source system observes; ERP reconciliation remains authoritative
```

The first program outcome is not an app rewrite. It is a safer existing ERP outbox,
exact money-bearing contracts, and one digest-disciplined Sub collaboration.

## Decisions and non-negotiable boundaries

1. ERP remains authoritative for its accounting, inventory, procurement, workforce,
   tax, asset, and backoffice records. Kernel services do not post journals, move stock,
   approve payments, or decide employment outcomes.
2. `Organization` remains ERP's application tenancy key. It maps to platform tenancy
   through an explicit adapter; `organization_id` is not renamed across the schema.
3. ERP's dual tenancy enforcement—organization context plus PostgreSQL RLS—must not be
   weakened or partially initialized by kernel adoption.
4. Existing ERP identity, OIDC binding, sessions, and RBAC remain local during this
   program. Provider identity claims never become ERP permissions.
5. The existing finance outbox is improved in place. Kernel messaging tables must not be
   introduced beside it before the organization/tenant and migration ADR is complete.
6. Kernel `Money` is a boundary value, not a replacement for ERP's six-decimal posting,
   quantity, FX-rate, tax, or functional-currency internals.
7. ERP and Sub remain independent applications and databases. ERP admits Sub facts
   through versioned APIs/events and returns observations; neither imports the other's
   models or opens a cross-database transaction.
8. Commercial licence entitlement is separate from ERP RBAC and from accounting/data
   integrity. A missing licence cannot make a valid ledger invalid or bypass retention.

## Reuse/adaptation matrix

| Kernel capability | ERP decision | Timing |
| --- | --- | --- |
| `Money` / `Currency` / immutable `ExchangeRate` | Adopt at API/event boundaries; preserve ERP accounting precision internally | Early |
| `CapabilityCatalogue` + `FeatureManifest` | Declare ERP product modules and one owner per capability | Early |
| `DeploymentProfileSpec` / registry | Add deterministic composition/provider/currency/legal preflight | Early |
| `ProductAssemblySpec` | Declare metadata only; do not replace ERP app startup initially | Early |
| `dotmac_kernel.testing` | Use pure fakes/clock/licence kit and compatibility tests | Early |
| Kernel messaging behavior | Use as the target semantics for the existing ERP outbox | Early |
| Kernel messaging storage/relay | Defer until organization/tenant and migration compatibility is accepted | Deferred |
| Typed settings contract | Adapt behind ERP's canonical settings owner after direct writers are removed | Mid-program |
| Tenant entitlements | Add only after Organization -> Tenant mapping and module catalogue | Deferred |
| WS8 licence verification | Replace the current placeholder-key path through one explicit cutover | Deferred but required |
| Kernel Party/auth/RBAC | Do not replace ERP identity in this program | Out of scope |
| Reference assembly CRUD/web services | Do not adopt as ERP domain services | Out of scope |

## Delivery sequence

Each numbered item is a separate, reviewable ERP change. Publication, merging, staging,
or production work remains separately authorized. Every behavior slice begins with a
failing canary and ends with a sensitivity-proven architecture guard where an ownership
boundary changes.

### E1 — Rebaseline ERP's platform-adoption ledger and collision inventory

**Purpose:** replace stale discovery facts with an exact current contract before adding
the dependency.

**Changes in `dotmac_erp`:**

- Refresh `docs/PLATFORM_ADOPTION_LEDGER.md` against the exact target main revision.
- Reconcile the ledger with `docs/SOT_RELATIONSHIP_MAP.md` and
  `app/services/sot_relationships.py`; the executable registry wins for current owners.
- Classify kernel modules as `consume-pure`, `adapt-existing`, `defer-db`, or
  `prohibited`.
- Inventory Python package, model, table, schema, Alembic revision, middleware, route,
  settings, audit, identity, outbox, and session collisions.
- Record the current organization/RLS initialization path and prove both layers are
  established together for HTTP, Celery, CLI, reconciliation, and migration contexts.
- Add an architecture test that allows only documented kernel public imports and proves
  failure on an internal import.

**Acceptance:** adding a package pin alone cannot mount routes, run kernel migrations,
create a second session factory, or change current ERP owner/transaction behavior.

### E2 — Pin the kernel and prove pure-contract compatibility

**Purpose:** create a supported release-consumption lane without changing runtime state.

**Changes:**

- Pin exactly `dotmac-kernel==0.1.0a7` from the approved private Forgejo index.
  Add `testing` and `licensing` only where required. No registry or signing secret enters
  Git, logs, fixtures, or configuration defaults.
- Verify the published wheel, type marker, version, and supported public surface in a
  clean environment.
- Import only DB-free contracts in this slice: assembly/manifests, capabilities,
  profiles, Money/FX values, licence value types/verifier, and test fakes.
- Do not import `dotmac_kernel.db`, mount `create_app`, or compose kernel migrations.
- Add a dependency-update gate that rejects an unreviewed version range or private API
  import.

**Canary:** import the approved surface without `DATABASE_URL`, then boot the unchanged
ERP app and compare `/api/v1` OpenAPI, route inventory, startup checks, and current
organization-context behavior.

### E3 — Harden the existing ERP outbox to applied-result semantics

**Purpose:** repair the highest-value reliability gap before using the outbox for more
cross-product consequences.

Current evidence at the plan pin shows three behaviors to remove:

- settlement methods on `OutboxPublisher` commit inside the service;
- an unregistered event is counted as skipped but marked `PUBLISHED`; and
- a ledger handler may swallow a per-line error, return normally, and let the relay mark
  the whole event published.

**Changes:**

- Name the worker/session adapter as transaction boundary. Outbox services mutate and
  `flush()` only; they never commit or roll back.
- Split claim, delivery, and settle. Claim a bounded batch using concurrency-safe leases
  or `FOR UPDATE SKIP LOCKED`; delivery occurs outside the claim transaction; settlement
  requires the claim token.
- Unknown event types fail closed into an explicit unsupported/dead-letter result. They
  are never marked published.
- A handler succeeds only when its complete declared consequence commits. Per-item
  outcomes may be persisted, but an unrecorded partial failure cannot become success.
- Retain event version, correlation, causation, idempotency, attempts, error class, next
  retry, and terminal reason. Keep payloads bounded and secret-free.
- Add deterministic retry/backoff and authorized replay with full audit evidence.
- Add a reconciler comparing outbox success with the authoritative consequence, including
  the GL balance-cache projection built from posted ledger lines.
- Add metrics for pending age, lease age, retries, unsupported events, dead letters,
  partial outcomes, replay, and reconciliation drift.

**Canaries:** two workers cannot deliver one claim concurrently; worker death after remote
acceptance is repairable; unknown handler never publishes; one failed ledger line prevents
whole-event success unless a durable per-line recovery record exists; commit failure emits
no success acknowledgement.

**Kernel relationship:** match WS3 semantics and typed outcomes, but retain ERP tables in
this slice. Do not copy kernel implementation or introduce parallel tables.

### E4 — Exact Money/FX boundary adapter

**Purpose:** gain shared exact-money types without changing ERP accounting precision.

**Changes:**

- Add one ERP adapter between kernel `Money`/`Currency` values and ERP's existing
  `Decimal + currency_code` contracts.
- Use it first in Sub-facing material-support, vendor-advance/payables, invoice,
  credit-note, receipt, and WHT command/observation schemas.
- Preserve ERP `Numeric(20,6)`, FX-rate precision, tax ratios, posted-line snapshots,
  functional-currency behavior, and account mappings.
- Use currency minor units only at the legal/document/API boundary. Quantities, rates,
  unit prices requiring more scale, and ledger internals remain explicitly typed ERP
  decimals.
- Eliminate remaining Python `float` annotations/usages on monetary fields in the touched
  slice and centralize its rounding decision.
- Reject missing currency, floats, currency mismatch, excess boundary precision, and live
  provider lookup during posting.
- Convert only from an immutable ERP-owned FX observation carrying pair, rate type,
  effective time, source, and snapshot identity.

**Acceptance:** golden journals, tax, WHT, allocation, reversal/repost, and functional-
currency results remain identical. Boundary serialization round-trips exactly and no
minor unit is lost or invented.

### E5 — Digest-disciplined Sub request admission and acknowledgement

**Purpose:** make ERP consequences provably correspond to the exact Sub decision that
requested them.

**First vertical slice:** material release through the existing
`inventory.material_support` owner. Vendor advance/payables follows after the first slice
is green and reconciled.

**ERP-owned changes:**

- Define a typed, immutable admission command containing request ID, request version,
  evidence digest, source system, target organization, actor/provenance, and normalized
  domain values. Do not use a generic `dict[str, Any]` owner interface.
- Persist normalized admission evidence and its contract-versioned fingerprint before
  requesting the ERP consequence. Raw provider payload is diagnostic evidence only.
- Same request ID/version/digest is an idempotent replay. Same identity with different
  digest, organization, trust class, or normalized evidence is a hard conflict.
- The admission owner delegates to the canonical stock, fiscal-period, serial, AP, or GL
  owners. It does not reproduce their decisions.
- Stage an `applied` acknowledgement in the hardened ERP outbox only in the same
  transaction that commits the complete consequence. The acknowledgement repeats the
  exact request identity and ERP result reference.
- Stable `rejected` acknowledgements name the owning policy/error reason and commit only
  durable rejection evidence; they do not pretend a consequence ran.
- Unknown or malformed requests are quarantined with bounded evidence and alerting.
- Add an ERP reconciler that rebuilds acknowledgement/delivery state from authoritative
  admissions and consequences without repeating a committed stock or money movement.

**Cross-repo pairing:** Sub slice S6 owns request staging, transport, observation, and its
local projection. Neither product marks the other product's state directly.

**Critical canaries:** request replay; same-ID/different-digest conflict; wrong
organization/source; out-of-order version; ERP commit failure; acknowledgement transport
loss; replay after remote timeout; stock/AP consequence exists but acknowledgement is
missing; acknowledgement exists only when exact consequence evidence exists.

### E6 — Consolidate settings and audit before kernel adapters

**Purpose:** prevent the kernel from becoming an additional writer over already
fragmented ERP control-plane state.

**Settings changes:**

- Name one canonical `DomainSetting` writer and route every direct constructor/update in
  the touched domains through it.
- Ensure write validation, encryption/pointer rules, history, cache invalidation, actor,
  and source are atomic.
- Build a kernel settings read/declare adapter behind that owner. The ERP database remains
  runtime-authoritative; environment variables are bootstrap/migration inputs only.
- Add no-orphan setting specs and fail-closed tests for missing/invalid security policy.

**Audit changes:**

- Inventory the current manual, HTTP, ORM-listener, field-change, and settings-history
  writers against the executable SOT map.
- Name one business-audit admission interface and classify automatic field history and
  settings history as distinct observation projections where appropriate.
- Financial/access/identity transitions stage audit evidence in the owning transaction.
  Asynchronous audit delivery cannot be the only evidence of a committed decision.
- Adapt kernel tenant/platform audit vocabulary only after the writer consolidation; no
  new kernel audit table is added beside unresolved writers.

**Acceptance:** direct-writer baselines shrink to zero for migrated domains, sensitivity-
proven architecture tests prevent recurrence, and every audit row has owner, actor/source,
correlation, entity, action, time, and transaction semantics.

### E7 — ERP composition, capabilities, and deployment profile

**Purpose:** declare what an ERP release supports without conflating module presence,
tenant entitlement, RBAC, or feature rollout.

**Changes:**

- Add a frozen ERP `ProductAssemblySpec` used as metadata and release validation. Keep the
  current FastAPI factory/routes/templates intact.
- Define coarse `FeatureManifest`s for ERP domains, starting with finance, inventory,
  procurement, workforce, settings, integration, and licensing.
- Declare stable capability codes with exactly one domain owner and a real consumer.
- Build a `CapabilityCatalogue`; duplicate and orphan codes fail CI.
- Add a versioned ERP deployment profile validating required/forbidden modules, provider
  seams, currency/locale, legal/tax, residency, and web/API surfaces.
- Keep user permissions in ERP RBAC, tenant commercial grants in entitlements, feature
  rollout in settings/flags, and module composition in manifests/profiles.
- Never branch a finance or HR decision on profile or edition strings.

**Acceptance:** deterministic profile validation and negative tests for duplicate code,
missing provider, unknown module, forbidden composition, and orphan capability; no route,
permission, posting, or HR behavior change.

### E8 — Organization-to-Tenant and migration compatibility ADR

**Purpose:** unlock kernel-persisted entitlements/licensing without weakening ERP's
organization isolation.

The ADR and canaries land before any kernel migration executes. It must decide:

- the stable one-to-one Organization/Tenant identity mapping and lifecycle;
- whether exact-ID reuse is safe or a unique mapping table is required;
- preservation of `organization_id` across existing models and APIs;
- how ORM filtering and RLS GUCs are primed with kernel sessions/middleware;
- platform/global versus organization-scoped table classification;
- schema/table/revision collisions and Alembic version locations;
- database roles, grants, FORCE RLS, worker/reconciler/bypass handling;
- expand/backfill/verify/cutover/contract and rollback behavior; and
- explicit non-adoption of kernel Person/credential/session/RBAC storage.

**Required canaries:** fresh install, every supported ERP migration head, production-shape
upgrade, rollback/re-upgrade, cross-organization reads/writes through HTTP/Celery/CLI,
worker context loss, cache/object/export scope, and proof both tenancy layers are always
primed together.

**Gate:** no kernel DB/session/messaging/entitlement migration before the accepted ADR and
green red-sensitive canaries.

### E9 — Replace legacy licensing with WS8 + local entitlements

**Purpose:** remove the current placeholder-key implementation and make product-module
licensing compatible with the vendor control plane.

Current main still contains `REPLACE_WITH_REAL_PUBLIC_KEY_BASE64`; no production profile
may rely on that path.

**Changes after E8:**

- Keep licensing disabled/fail-loud until a configured public keyring and deployment
  binding are present. No private signing key enters ERP or its database.
- Characterize existing module/user/org/grace behavior and map only legitimate commercial
  module grants into declared ERP capabilities.
- Implement one ERP-owned receiver around `verify_licence`; do not copy the starter
  feature or keep two enforcement owners.
- Shadow-compare old and kernel verification where the old path can safely verify; record
  every difference. Then cut over one owner and delete/gate the old validator in the same
  coherent slice.
- Persist receiver-owned version/digest replay state and revocation-list high-water mark.
- Project into local organization entitlement grants only after all capability codes
  validate. Return `applied` only after commit.
- Apply licence revocations immediately to existing grants and prevent a higher list from
  silently removing a prior revoked ID without an explicit approved reinstatement model.
- Architecture-guard the separation of licence entitlement, ERP RBAC, and financial/data
  integrity.

**Acceptance:** vendor issue -> ERP verify -> local module grant -> explainable decision ->
exact acknowledgement, plus bad-signature, unknown/revoked key, stale/conflict version,
deployment mismatch, expiry/grace, revocation, and cross-organization canaries.

### E10 — Release propagation, monitoring, and repair

- Add automated exact-version kernel update PRs with clean-wheel, public-surface,
  OpenAPI, profile, Money, outbox, organization-isolation, licensing, and migration gates.
- Record accepted/deferred releases with owner, reason, risk, and expiry.
- Alert on outbox pending/lease age, dead letters, unsupported events, partial outcomes,
  acknowledgement mismatch, admission conflicts, Sub/ERP reconciliation lag, licence
  expiry/revocation, and kernel-version drift.
- Add repair runbooks for lost acknowledgements, stranded outbox rows, admission conflicts,
  organization mapping drift, and licence/keyring updates.
- Roll back by disabling the new adapter/projection and reconciling from authoritative ERP
  inputs; never restore a retired parallel writer.

## Required tests and validation

Each slice uses the target repo's authoritative commands. At minimum before publication:

```bash
poetry run ruff check app tests alembic scripts
poetry run ruff format --check app tests alembic scripts
poetry run mypy app
poetry run pytest tests/ --ignore=tests/e2e/
python scripts/bump_version.py --check
```

Also run the repository's security, pre-commit, tenant-context, migration, relevant
PostgreSQL integration, OpenAPI, finance golden, and E2E/browser gates. CSS checks apply
only when a changed surface requires them. Report every skipped or failed check.

## Program completion criteria

This ERP plan is complete only when:

- ERP consumes an exact kernel release only through supported public APIs;
- the existing ERP outbox has claim/deliver/settle semantics, no internal commits, no
  silent unknown-event publication, and repairable partial failure;
- Sub-facing money contracts use exact typed values without changing GL/tax precision;
- material release and vendor advance are admitted and acknowledged by exact
  version/digest with reconciliation;
- settings and audit have one named writer per concern before kernel adaptation;
- ERP composition and capabilities are declared without replacing RBAC or feature flags;
- Organization/Tenant integration preserves dual-layer isolation under every adapter;
- the placeholder licensing path is retired in favor of WS8 and local entitlements; and
- checked-in ERP SOT maps, registry, finance contracts, architecture guards, and runbooks
  match the shipped owners and repair paths.

## Explicit exclusions

- No rename of `organization_id` or mass Person/Party migration.
- No GL/AP/AR, tax, payroll, HR, procurement, or inventory owner in the kernel.
- No second outbox, settings writer, audit writer, licence enforcement path, or session
  factory.
- No shared ERP/Sub database or ORM model.
- No business branching on deployment profile, edition, or licence strings.
- No production, SSH, release, merge, or deployment action without separate authorization.
