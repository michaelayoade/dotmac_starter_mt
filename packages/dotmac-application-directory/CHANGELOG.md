# Changelog — dotmac-application-directory

All notable changes to the `dotmac-application-directory` distribution. This
package follows [Semantic Versioning](https://semver.org). Pre-1.0 (`0.x`, incl.
this alpha) the surface is still settling — a `0.MINOR` bump may carry breaking
changes, each called out here.

## 0.1.0a2 — 2026-08-13

Declares the database EFFECTS this lineage needs instead of naming a foreign
revision (ADR-0006 D1 amendment).

### Changed

- `ad_0001_application_bindings` previously read
  `depends_on = ("0001_initial_tenant_schema",)`. That edge is true only in an
  assembly that runs the kernel lineage: ERP hosts `public.tenants` in its own
  lineage and can never run kernel `0001`, so the module was un-installable
  there for want of a foreign-key target. The manifest now declares
  `requires=("tenant_scope_catalog.v1", "module_database_roles.v1")`, the root
  resolves its `depends_on` from the assembly's bindings, and `upgrade()` proves
  both effects against the live catalog before any DDL.
- Kernel floor raised to `>=0.1.0a56`, the release that added the prerequisite
  contract. A kernel below it cannot import this manifest.


## 0.1.0a1 — 2026-08-12

First release. The tenant's connected-application portfolio, and the permanent
home of the `ApplicationDescriptor` contract (ADR-0021 §4).

### Added

- **`ApplicationDescriptor`** — what a target application publishes about one
  instance, with one deterministic, domain-separated content digest. The field
  list is written out explicitly so that adding a field is a decision about
  identity rather than an accident of declaration order.
- **Binding identity is immutable.** `(application_code, instance_ref,
  local_tenant_ref)` is refused-on-mismatch before any mutation. Without the
  `local_tenant_ref` member a live binding could adopt a newer descriptor naming
  a different local tenant and stay launchable, silently re-pointing a tile at
  another tenant's instance; version and digest checks cannot catch it, because
  a genuine version bump carrying a changed local tenant passes both.
  Fails closed at construction: a bad URL, a version below 1, a duplicate role
  code or an empty required field raises `DescriptorError` rather than reaching
  a binding whose digest would then attest to nonsense.
- **`ApplicationBinding`** in `mod_appdir`, with `tenant_id NOT NULL`, both
  composite uniques, RLS ENABLEd and FORCEd (`ad_0001_application_bindings`).
- **Three closed vocabularies** — `BindingState` (five states, `detached`
  terminal, every non-terminal state able to reach it directly so disconnecting
  never requires first repairing a broken binding), `BindingSource`, and
  `ReconciliationStatus`. Closed in Python, stored as text, per ADR-0008.
- **`service`** — the one writer of a binding row. Never commits, never rolls
  back; uses `conflict_savepoint` with the mutation inside the block.
- **`activate_binding`** is the ONLY route to `ACTIVE`, and requires a
  descriptor read from the application. It refuses if reconciling that
  descriptor would not adopt it, or if the application names a different local
  tenant than the binding was created for. `attach_application` takes no `state`
  argument and `transition` refuses `ACTIVE` outright, so a binding that is
  launchable and has never been verified is not a state this module can produce.
- **Every mutation takes `(tenant_id, binding_id)` and locks the row**
  (`SELECT ... FOR UPDATE`) rather than accepting a caller-supplied object.
  Reconciliation is not commutative: two reconcilers reading v1 concurrently,
  one observing v2 and the other v3, would both pass their version checks
  against the stale copy they hold, and the last to commit would win. The same
  race let a suspend land after a detach and resurrect a disconnected binding.
  Both were invisible at the call site, which is why the object-taking
  signatures were removed rather than supplemented. Proven by PostgreSQL
  concurrency canaries — SQLite omits `FOR UPDATE` silently.
- **`reconcile_descriptor`** — four outcomes, only two of which adopt. A version
  regression is `stale` and keeps the stored copy; the same version carrying
  different content is `failed` and is never adopted, because a version is a
  promise that content did not change beneath it and the case is
  indistinguishable from tampering. A failed read never moves
  `descriptor_refreshed_at`, so an unreachable application cannot look freshly
  checked.

### Deliberately absent

- **The role catalogue.** `ApplicationRole`, a `delegable` flag,
  `delegable_role_codes` and a separate `role_catalogue_digest` were drafted and
  cut, because nothing consumes them: the access module that would is deferred
  (ADR-0021 §5) and the launcher never reads a role. Shipping them would publish
  a contract — and a database column — designed against zero consumers, which is
  the failure ADR-0008 records against declarations with no reader and ADR-0017
  against facilities with no adopter. They return with the access slice.

- **Any authorization column.** No person, member, group, role, grant or
  permission column exists, and `test_the_directory_holds_no_authorization_column`
  fails the build on one. Directory visibility is not authorization (ADR-0021
  §3).
- **Routers, capabilities, permissions, audit actions.** Every such declaration
  exists to gate or annotate a route, and this release ships none — the
  launcher is the `dotmac_workspace` assembly's UI facet, not a domain module.
  Declaring codes with no consumer is the dead-vocabulary failure ADR-0008's
  registries exist to prevent, and CI enforces it.
- **Access requests, approvals, delegation policy, grant sets.** Those are
  `dotmac-application-access`, deferred by ADR-0021 §5 until the kernel has a
  generic signed-document mechanism.

### Requires

`dotmac-kernel >= 0.1.0a46`, the release that allocates `mod_appdir` in
`MIGRATION_OWNER_LEDGER`. A hard floor, not a preference: an earlier kernel
refuses the composition at boot with `UnallocatedNamespaceError`.
