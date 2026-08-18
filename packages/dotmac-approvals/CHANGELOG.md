# Changelog — dotmac-approvals

All notable changes to the `dotmac-approvals` distribution. This package follows
[Semantic Versioning](https://semver.org). The `.github/release-modules.json`
entry landed once the live Postgres migration and catalog gate passed;
`0.1.0a1` through `0.1.0a5` have since been published.

## 0.1.0a5 — 2026-08-16

Published and verified by release run `32062654126`; the registry-installed
manifest registered `mod_approvals` against kernel `0.1.0a68` before the
workflow created `dotmac-approvals-v0.1.0a5` on commit `8d4ddfd`.

**Declares `outbox_relay.v1`, the effect this module has always needed and
could not name.** `dotmac_approvals.outbox.emit_tenant_events` calls
`enqueue_event` and `emit_platform_events` calls `enqueue_platform_event`, so
this module writes `public.outbox_events` and `public.platform_outbox_events`
at REQUEST time. `ap_0001` creates neither — the approval tables live in
`mod_approvals`, and the relay is the kernel's.

Every release through `0.1.0a4` therefore shipped an undeclared runtime
dependency: an adopter running its own lineage installed the module, passed
every gate it has, migrated cleanly, and took an `UndefinedTable` on the first
approval decision that emitted an event — with the approval transaction rolling
back alongside it.

Kernel `0.1.0a67` published the name; this declares it. Found by building the
facility-to-prerequisite guard rather than by the sweep that produced the
`dotmac-integration` and `dotmac-entitlement-allocation` fixes: those came from
grepping the IDEMPOTENCY facility, and nobody had grepped this one.

### Added

- `ap_0002_outbox_relay`, a verification-only revision whose entire `upgrade()`
  body is `require_prerequisites`. A new head rather than an edit to `ap_0001`,
  whose bytes shipped in four published tags and have run in databases this
  repository does not own.
- `ModuleManifest.requires` gains `outbox_relay.v1`. COMMON, not plane-specific:
  both planes enqueue, so a PLATFORM-only install needs it exactly as much as a
  tenant one.
- The release allowlist now requires `ap_0002_outbox_relay.py` in the wheel.
  Omitting the file would publish a5's declaration without the migration-time
  verification that makes the dependency fail before a request.
- The released-migration guard now enrols approvals with an exact tag/digest
  census. It records — but does not excuse — the three `ap_0001` byte sets that
  shipped across a1–a5, retains a3/a4/a5 as the canonical current bytes, and has a
  sensitivity proof that a fourth byte set is refused.
- A six-case PostgreSQL upgrade matrix reconstructs each historical byte set
  from its release tag, builds every meaning that release supported, seeds
  durable rows, and upgrades to `ap_0002` without rerunning `ap_0001`, losing
  data, or changing the selected persistence planes.

### Changed

- Kernel floor `>=0.1.0a67`. a61 (`supported_plane_sets`) held until the relay
  prerequisite existed; the floor is always the highest capability actually
  consumed.

### Note on scope

The whole spec is required even though this module only ENQUEUES and never
claims. An event enqueued into a database with no relay is never delivered, so
a table-only dependency would be satisfied by a deployment in which approvals
silently stop reaching anyone.

## 0.1.0a4 — 2026-08-15

**Adds the public lineage locator `versions_dir()`.** The `ap` lineage has
shipped as package data since `0.1.0a1`, but nothing exposed WHERE it is. The
Starter could hard-code `packages/dotmac-approvals/...` because the package sits
in its checkout; a cross-repository consumer cannot, and the vendor control
plane hit exactly that composing the module and had to write a private shim
reaching into `__file__`. That shim made this package's filesystem layout part
of its contract, in the consumer's code, where this package cannot see it break.

`dotmac_approvals.versions_dir()` returns the installed directory holding the
revisions, for composition into a consuming assembly's Alembic
`version_locations`. Same signature and semantics as
`dotmac_release_catalog.versions_dir()`,
`dotmac_entitlement_allocation.versions_dir()` and
`dotmac_application_directory.versions_dir()` — a consumer composing several
modules should not meet four spellings of one idea. Re-exported from the
top-level namespace, which is the stable surface this package documents;
submodules are not.

No schema change, no migration, no model change, and no kernel-floor change:
still `>= 0.1.0a61` for ADR-0028 plane selection. `migrations/__init__.py` was
already a required wheel content, so the locator ships with the lineage it
locates.

A new architecture guard now requires every module shipping a lineage to expose
this locator, so the gap cannot reopen here or appear in the next module.

## 0.1.0a3 — 2026-08-14

Corrects a2's plane selector (ADR-0028). **a2 was published**, so an assembly
already on it must add an explicit selection — see the kernel a61 changelog's
"Migrating from a60" for the three-step change. The manifest declares all
three supported installations — tenant-only, platform-only and both — and an
assembly must select one explicitly. `ap_0001` builds exactly that selection;
truthfully binding a tenant catalogue no longer opts the tenant tables in.

Kernel floor is `>=0.1.0a61`. The live PostgreSQL platform-only canary runs the
kernel lineage first, proves `public.tenants` exists, then proves all three
tenant approval tables are absent while all three platform tables are usable.

## 0.1.0a2 — 2026-08-14

**Published, then superseded by a3.** This version incorrectly
used absence of a tenant prerequisite binding as installation intent. Vendor CP
has that provider through its composed kernel lineage, so the premise and the
selector were both wrong.

Makes the module installable in a platform-only assembly (ADR-0027). Found while
starting ADR-0026's cutover 1: the vendor control plane has no tenant catalogue
and never will, so the previous lineage — which demanded `tenant_scope_catalog.v1`
to create any table at all — could not install there.

### Changed

- The manifest declares `module_database_roles.v1` as `requires` and
  `tenant_scope_catalog.v1` as `tenant_requires`, so the tenant plane is
  conditional on the assembly binding a tenant catalogue.
- `ap_0001_approvals` builds the platform plane unconditionally and the tenant
  plane only where that binding exists, and grants schema USAGE to `app_user`
  only when there is something there for the tenant role to reach.
- Kernel floor is `>=0.1.0a60`, the release that added per-plane prerequisites.

No behaviour changes. A built tenant plane is identical to `0.1.0a1`'s.

## 0.1.0a1 — 2026-08-14

The first slice: the contract, both persistence planes, and the parity tests.
Implementation authority is ADR-0017's 2026-08-14 owner-directed exception; the
boundary is ADR-0026.

### Added

- `contracts` — the frozen vocabulary (`ApprovalState`, `DecisionAction`,
  `ApprovalLevel`, `PolicyRevision`, `Actor`, `Evaluation`, `ApprovalEvent`) and
  every typed refusal. No Money, no FX, no subject enum.
- `policy` — the rules, pure and shared by both planes: ordered levels,
  distinct-actor quorum, eligibility, separation of duties, self-approval
  exclusion, MFA, and ordered-level evaluation.
- `models` — six tables on two declared planes. Tenant tables carry
  `tenant_id NOT NULL`, composite identity and FORCEd RLS; platform tables carry
  no tenant column. `(policy_code, version)` is unique per scope, and
  `(request_id, level, actor_id)` is unique so a duplicate vote is impossible
  rather than merely refused.
- `service` — ten explicitly plane-named entry points. Services `add`/`flush`
  and never commit.
- `outbox` — optional adapter writing `approval.requested|approved|rejected|
  cancelled` onto the kernel's transactional outbox. Kept out of `service` so a
  consumer with its own delivery is not forced to install the kernel's.
- `manifest` — declares both plane tuples and the two logical prerequisites
  (`tenant_scope_catalog.v1`, `module_database_roles.v1`).
- The `ap` lineage: `ap_0001_approvals`, schema `mod_approvals`.

### Allocated elsewhere

- `mod_approvals` / prefix `ap` / branch label `approvals` in
  `dotmac_kernel.namespaces.MIGRATION_OWNER_LEDGER` (kernel `0.1.0a59`), landing
  in the same change as this manifest.

### Not included, deliberately

Threshold/FX routing (stays in the domain — ADR-0026 § 7a), any subject-type
vocabulary, any consuming-domain import, and any execution of an approved
transition.
