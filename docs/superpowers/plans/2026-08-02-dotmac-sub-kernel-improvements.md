# Dotmac Sub — selective kernel adoption and reliability improvements

> **Status:** Proposed implementation plan, 2026-08-02. This is non-authoritative
> execution intent; `dotmac_sub/docs/ARCHITECTURE.md`,
> `dotmac_sub/docs/SOT_RELATIONSHIP_MAP.md`, its executable SOT registry, and
> accepted Dotmac ADRs remain authoritative. The plan authorizes no code, schema,
> release, or production change by itself.
>
> **Target repository:** `dotmac_sub`.
>
> **Evidence basis:** `dotmac_sub` `origin/dev` at
> `0d045baae05c91fb9307772d7aaad181b928715f` plus released
> `dotmac-kernel==0.1.0a7`. Uncommitted worktree changes were deliberately excluded.
>
> **Relationship to earlier plans:** this replaces the executable detail in
> `2026-07-30-sub-adoption-preparation.md`, which was written before the kernel
> existed and before Sub's owner-command and integration-platform cutovers. It
> preserves ADR-0003's thin-assembly direction and the one-operator-tenant rule.

## Outcome

Improve Sub by consuming only the kernel contracts that remove duplicated platform
work or strengthen external-consequence correctness, while preserving Sub's current
domain owners and mature integration control plane.

The target is not a rebuilt application. It is this bounded shape:

```text
Sub authoritative intent
  -> existing owner command and transaction
  -> existing event/integration delivery
  -> kernel-compatible provider/value/capability contract
  -> exact observed result or acknowledgement
  -> existing Sub reconciler and repair owner
```

The first useful result must be achievable without mounting the kernel app factory,
running kernel migrations, replacing Sub identity, or creating a second outbox.

## Decisions and non-negotiable boundaries

1. Sub remains authoritative for subscriber, subscription, billing, collections,
   service readiness, network intent, RADIUS/OLT/ONT/ACS, IPAM, topology, support,
   vendor-work, and official timeline state.
2. The existing `execute_owner_command` boundary remains the transaction authority.
   Kernel adapters call registered Sub owners; they do not become owners or commit.
3. Sub's integration platform remains the delivery/inbox/outbox authority. Kernel
   messaging semantics may be used as conformance criteria, but no parallel kernel
   messaging tables are introduced during the pure-contract phases.
4. The ISP operator deployment eventually maps to one platform `Tenant`.
   Subscribers, resellers, customer organizations, and staff are not platform tenants.
5. Kernel entitlement answers commercial product/module availability only. It never
   replaces subscriber financial-access, service-readiness, or RBAC decisions.
6. Sub and ERP remain separate databases. Cross-product work uses typed versioned
   contracts, explicit external references, immutable evidence, and reconciliation.
7. Existing portals, APIs, Celery jobs, migrations, and deployment mechanics remain
   operational. `ProductAssemblySpec` is composition metadata before it is ever an app
   factory cutover.

## Reuse/adaptation matrix

| Kernel capability | Sub decision | Timing |
| --- | --- | --- |
| `ProvisioningProvider` + provider contract suite | Adopt through an adapter over an existing Sub projection owner | Early |
| `Money` / `Currency` / immutable `ExchangeRate` | Use at typed integration boundaries; do not rewrite billing persistence | Early |
| `CapabilityCatalogue` + `FeatureManifest` | Declare coarse product capabilities with one owning domain | Early |
| `DeploymentProfileSpec` / registry | Add deterministic dedicated-ISP composition preflight | Early |
| `ProductAssemblySpec` | Declare metadata only; do not replace `app.main` initially | Early |
| `dotmac_kernel.testing` | Use provider/fake/clock contracts; retain PostgreSQL isolation canaries | Early |
| Kernel messaging tables/relay | Do not add beside `events.store` and `integration.*`; evaluate only after tenant/migration ADR | Deferred |
| Tenant entitlements | Add only after the operator-tenant bridge and capability catalogue are proven | Deferred |
| WS8 licence receiver | Implement a Sub-owned receiver using the kernel verifier after entitlements exist | Deferred |
| Kernel settings/audit | Adapt behind current canonical owners after parity; do not create another writer | Deferred |
| Kernel Party/auth/RBAC | Do not replace Sub identity during this program | Out of scope |
| Kernel CRUD/web reference features | Do not adopt as domain services | Out of scope |

## Delivery sequence

Each numbered slice is a separate, focused Sub change. Later slices may begin only
when their stated gate is green. Work starts from `dev` on a feature branch and follows
Sub's required `feature -> dev -> staging -> main` promotion path when Michael later
authorizes publication and deployment.

### S1 — Rebaseline the adoption ledger and add collision guards

**Purpose:** replace the 2026-07-19 discovery assumptions with a current, executable
boundary before importing the package.

**Changes in `dotmac_sub`:**

- Refresh `docs/PLATFORM_ADOPTION_LEDGER.md` against the exact target `origin/dev`.
- Classify every kernel public module as `consume-pure`, `adapt`, `defer-db`, or
  `prohibited` for Sub.
- Inventory package, model, table, Alembic revision, middleware, route, settings,
  audit, identity, and session-name collisions.
- Record the current owners from `app/services/sot_relationships.py`; do not create
  owner rows for adapters.
- Add an architecture test that permits only documented public kernel imports and
  rejects imports from kernel internals.
- Add a negative-control test proving the guard fails for an internal import.

**Acceptance:** the ledger names every intended import and proves that adding the
dependency alone runs no kernel migrations, mounts no routes, constructs no engine,
and changes no Sub transaction or owner.

**Rollback:** remove the dependency-preparation guard and ledger amendment; runtime is
unchanged.

### S2 — Pin the released kernel and prove pure-contract compatibility

**Purpose:** establish a supported, versioned dependency lane without runtime cutover.

**Changes:**

- Pin exactly `dotmac-kernel==0.1.0a7` from the approved private Forgejo index.
  Add `testing` only to the development/test dependency group. No registry credential
  or secret value enters Git.
- Add a clean-environment install/import test for the wheel and its `py.typed` marker.
- Import only DB-free public surfaces in this slice:
  `ProductAssemblySpec`, `FeatureManifest`, `CapabilityCatalogue`,
  `DeploymentProfileSpec`, `DeploymentProfileRegistry`, `Money`, `Currency`, and the
  provisioning protocol/result types.
- Pin the kernel compatibility version in CI and reject an unreviewed range upgrade.
- Do not call `create_app`, import `dotmac_kernel.db`, or compose kernel migrations.

**Canary first:** a test imports the intended surface with `DATABASE_URL` absent and
boots the unchanged Sub application through its existing factory.

**Acceptance:** Sub's existing OpenAPI, route inventory, migrations, startup, and
critical lifecycle characterizations are byte-/behavior-identical.

### S3 — Declare Sub composition and capabilities without remounting the app

**Purpose:** give releases and licences stable, declared product vocabulary.

**Changes:**

- Add one Sub-owned composition module containing a frozen `ProductAssemblySpec`.
  It is metadata consumed by validation; `app.main` remains the runtime owner.
- Define coarse `FeatureManifest`s around existing SOT domains, not one manifest per
  service. Every capability code names exactly one existing domain owner.
- Build a `CapabilityCatalogue` from those manifests and fail on duplicate ownership.
- Add a versioned dedicated-ISP `DeploymentProfileSpec` requiring the minimum product
  modules and declared provider seams. Keep locale, currency, legal/tax, residency,
  and provider axes independent.
- Add no-orphan tests: every declared capability has a real consumer or is removed.
- Do not use profile names in business logic and do not treat a declared capability as
  an entitlement or permission.

**Initial scope:** declare only the domains needed by S4-S6: network projection,
backoffice collaboration, billing export, and licensing reception. Expand in later
domain slices rather than generating a catalogue for the entire repository at once.

**Acceptance:** deterministic catalogue/profile reports; duplicate, missing-provider,
forbidden-module, and orphan-capability negative tests; zero route or permission change.

### S4 — Provisioning-provider adapter pilot

**Purpose:** standardize `plan -> apply -> observe -> cancel`, partial progress,
retryability, and idempotency around one existing network projection without moving its
decision authority.

**Recommended pilot:** adapt the existing RADIUS projection/reconciliation boundary,
because it already has one writer, desired-state planning, multiple targets, explicit
partial failure, and readback. `access.radius_projection` remains the owner.

**Changes:**

- Introduce a thin `ProvisioningProvider` adapter that accepts an opaque, typed Sub
  desired-state reference and calls the existing registered owner.
- Derive a stable `plan_hash` from normalized desired-state evidence. Persist the exact
  intent/plan identity with the existing `NetworkOperation` evidence; do not place
  credentials or full customer payloads in it.
- Reuse the same `operation_id` for retry/resume. A reused ID with different intent or
  plan hash fails closed.
- Map per-target results to explicit step outcomes. Partial result is not success.
- Make `observe` read the authoritative operation and projection/readback evidence.
  It must not infer success from task completion alone.
- Keep cooperative cancellation at the operation owner; cancellation cannot roll back
  already-applied device state and must leave reconciliation evidence.
- Run `check_provisioning_provider_contract` against the real adapter factory plus
  Sub-specific PostgreSQL concurrency/readback tests.

**Shadow gate:** compare adapter plan/outcome with the existing path for a bounded cohort
without changing the writer. Every mismatch is classified and repaired before one caller
is cut over.

**Cutover gate:** one adapter caller changes at a time; old direct dispatch is removed in
the same coherent slice and an architecture guard prevents its return.

### S5 — Exact Money at Sub/ERP boundaries

**Purpose:** standardize currency-bearing contracts without rewriting Sub's mature NGN
billing models.

**Changes:**

- Use kernel `Currency`/`Money` inside typed backoffice commands and outcomes for vendor
  advances, material-support valuation, purchase/payables collaboration, and ERP billing
  exports.
- Convert existing `Decimal + currency_code` into `Money` at the local port and serialize
  it explicitly at the connector boundary.
- Keep Sub database columns, ledger, invoice arithmetic, tax rules, and display formatting
  unchanged in this slice.
- Reject floats, currency mismatch, excess precision, missing currency, and ambiguous
  defaults before staging a delivery.
- Defer live FX conversion; if later needed, it consumes an immutable, sourced
  `ExchangeRate` observation and never calls a rate provider inside a decision.

**Acceptance:** golden tests prove identical NGN totals, allocations, WHT/tax evidence,
JSON payloads where compatibility is required, and exact round trips for boundary values.

### S6 — Digest-disciplined Sub-to-ERP delivery and acknowledgement

**Purpose:** apply the WS8 delivery method to backoffice collaboration while preserving
the existing Sub integration platform.

**First vertical slice:** material release. Vendor advance follows only after the first
slice is green and reconciled.

**Sub-owned changes:**

- The operational owner emits an immutable, versioned request containing the approved
  normalized decision evidence. The contract defines one stable evidence fingerprint.
- Stage request state and the existing domain event in the owner transaction.
- Use `integration.delivery` for transport and `integration.inbox` for ERP
  acknowledgements; do not create a second delivery table.
- Require acknowledgement identity `(request_id, request_version, request_digest,
  provider/source, target organization)`.
- Record every verified acknowledgement as an observation. Only an exact `applied`
  acknowledgement may project the Sub collaboration to applied/fulfilled.
- Exact `rejected` acknowledgement records its stable reason and leaves the request
  repairable. Unknown digest/version/source acknowledgements are retained, quarantined,
  and alerted; they never advance state.
- Duplicate acknowledgements are idempotent. A late older acknowledgement cannot regress
  a newer request version.
- Add a reconciler that compares Sub's request evidence with ERP's authoritative outcome
  and repairs lost delivery/acknowledgement projections.

**Cross-repo pairing:** ERP slice E5 in the companion plan implements admission,
consequence, and acknowledgement. Neither side is complete alone.

**Critical canary:** force ERP consequence commit failure and prove Sub never observes an
`applied` acknowledgement. Then drop the acknowledgement transport after a successful ERP
commit and prove reconciliation repairs Sub without repeating the ERP consequence.

### S7 — Operator-tenant and migration compatibility ADR

**Purpose:** unlock kernel-persisted entitlements/licensing without confusing Sub's
single-operator product model or weakening its current security.

This is a design-and-canary slice before any kernel migration runs.

**Required decision record:**

- one operator deployment -> one kernel `Tenant` and stable mapping;
- subscribers/resellers/customer organizations remain Sub records;
- exact schema/table/model/revision collision treatment;
- kernel and Sub session/GUC/transaction interaction;
- RLS and database-role behavior for new kernel tables;
- Alembic version-location and upgrade/downgrade strategy;
- backup, rollback, uninstall, and partial-migration recovery;
- explicit non-adoption of kernel Party/auth/RBAC in this program.

**Canaries before migration:** fresh database, current-production-shape upgrade,
upgrade from every supported Sub migration head, rollback/re-upgrade, cross-tenant denial
on kernel tables, and proof existing Sub tables/routes remain unchanged.

**Gate:** do not import DB-constructing kernel modules or compose kernel migrations until
the ADR is accepted and every migration canary is red-sensitive and green.

### S8 — Local commercial entitlements and WS8 receiver

**Purpose:** let a vendor licence control product/module availability locally without
touching subscriber service state.

**Changes after S7:**

- Create Sub-owned application services that consume kernel entitlement and licensing
  public APIs. Do not copy the starter reference feature.
- Persist grants only against the operator tenant and declared Sub capability codes.
- Verify signed licences offline, enforce deployment binding where contracted, maintain
  receiver-owned version/digest replay state, and apply revocation lists monotonically.
- Project a new licence atomically into local grants; reject undeclared capability codes
  before the first write.
- Remove capability grants absent from a newer licence version while preserving provenance.
- Return the shared version/digest acknowledgement only after the local projection commits.
- Reconcile revoked licences immediately against already-active grants; future verification
  alone is insufficient.
- Keep RBAC and subscriber access decisions separate and architecture-guarded.

**Acceptance:** vendor-issued envelope -> Sub verify -> local grant -> explainable module
decision -> exact acknowledgement, plus stale/conflict/revocation/cross-tenant canaries.

### S9 — Release propagation and operational evidence

**Purpose:** make reuse maintainable rather than a one-time dependency import.

- Add an automated exact-version kernel update PR lane. It runs Sub's full prescribed
  suite plus package compatibility, profile, provider-contract, licence, and migration
  rehearsals.
- Record accepted/deferred kernel updates with owner, reason, risk, and expiry.
- Export metrics for provider partial/failure outcomes, unknown acknowledgements,
  delivery age, reconciliation lag, licence expiry/revocation, and kernel-version drift.
- Add a runbook for disabling a new adapter or capability projection without restoring an
  old parallel writer.
- Roll out through Sub's required dev/staging/main promotion sequence; production remains
  separately authorized and explicitly targeted.

## Required tests and validation

Every slice writes its canary first and includes a sensitivity proof for new architecture
guards. Before publication, run the repository-prescribed suite:

```bash
poetry run ruff check app tests scripts alembic
poetry run ruff format --check app tests scripts alembic
poetry run mypy app --ignore-missing-imports --no-incremental
poetry run lint-imports
poetry run bandit -r app -c pyproject.toml -q
make test-architecture
make test
make test-integration
```

Also run the exact affected migration, OpenAPI/generated-client, mobile, browser, and
provider tests. A skipped gate is reported; it is not silently treated as green.

## Program completion criteria

This Sub plan is complete only when:

- Sub consumes an exact released kernel through documented public APIs only;
- the provider adapter passes the shared contract suite and has one real cut-over caller;
- currency-bearing Sub/ERP contracts are exact and float-free;
- material release and vendor advance use immutable version/digest acknowledgement with
  reconciliation and no second transport owner;
- operator tenancy, kernel migrations, entitlements, and licensing are proven by
  PostgreSQL isolation and migration rehearsals before runtime use;
- kernel capability/entitlement decisions remain separate from Sub RBAC and subscriber
  financial-access/service-readiness decisions;
- old callers removed by a cutover cannot return because architecture guards fail; and
- all checked-in Sub SOT registries, maps, architecture docs, and runbooks match reality.

## Explicit exclusions

- No big-bang `create_app` migration.
- No bulk `tenant_id` addition to ISP tables.
- No Subscriber/Person/UserCredential rewrite.
- No second outbox, inbox, settings writer, audit writer, or network decision owner.
- No shared Sub/ERP database or ORM dependency.
- No product feature branching on deployment-profile or licence-edition strings.
- No production, SSH, release, merge, or deployment action without separate authorization.
