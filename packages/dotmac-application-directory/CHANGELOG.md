# Changelog — dotmac-application-directory

All notable changes to the `dotmac-application-directory` distribution. This
package follows [Semantic Versioning](https://semver.org). Pre-1.0 (`0.x`, incl.
this alpha) the surface is still settling — a `0.MINOR` bump may carry breaking
changes, each called out here.

## 0.1.0a1 — 2026-08-12

First release. The tenant's connected-application portfolio, and the permanent
home of the `ApplicationDescriptor` contract (ADR-0021 §4).

### Added

- **`ApplicationDescriptor` + `ApplicationRole`** — what a target application
  publishes about one instance. Two deterministic digests, deliberately
  separate: `role_catalogue_digest` (what a future grant set binds itself to)
  and `digest` (the whole descriptor, which is what tells the Workspace that
  anything at all moved). Both are domain-separated and order-independent, and
  the descriptor's field list is written out explicitly so that adding a field
  is a decision about identity rather than an accident of declaration order.
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
