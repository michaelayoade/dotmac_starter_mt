# ADR-0028: An assembly declares each installed module plane

**Status:** Accepted
**Date:** 2026-08-14
**Decision owner:** Michael
**Scope:** FLEET-WIDE for independently installable module planes.
**Supersedes:** ADR-0027's binding-as-selector mechanism.
**Relates to:** ADR-0006 D1, ADR-0017, ADR-0023, ADR-0026.

## Context

ADR-0027 correctly separated common and tenant-plane prerequisites, but used
the absence of a tenant binding to select a platform-only installation. Its
Vendor CP premise was wrong. Vendor CP is semantically platform-only, yet its
database physically composes kernel 0001 and therefore truthfully contains the
tenant catalogue and roles.

Two facts had been collapsed:

1. **Provider fact:** which revision supplies a database effect?
2. **Installation intent:** which planes of this module should this product own?

The first cannot safely infer the second. Omitting an optional binding looked
identical whether deliberate or accidental, and the live gate then adjusted its
expectation to match that omission. A forgotten binding could silently erase a
declared tenant plane from both migration DDL and its monitor.

The same implementation was asymmetric: it could build platform-only or both,
but always built the platform plane and could not express tenant-only.

## Decision

### 1. The module declares what its one lineage can build

`ModuleManifest` keeps three disjoint prerequisite lists:

- `requires`: effects every installation needs;
- `tenant_requires`: effects only the tenant plane needs;
- `platform_requires`: effects only the platform plane needs.

It may declare `supported_plane_sets`. Empty preserves the historical atomic
contract: every declared plane is installed together. A selectable dual-plane
module lists every supported non-empty combination explicitly. A combination
cannot name a plane for which the manifest owns no tables, and the full declared
combination remains supported.

`dotmac-approvals` supports tenant-only, platform-only, and both. Existing
`dotmac-ticketing` remains atomic until its separately released lineage adopts
this contract; this ADR does not silently change a published module's DDL.

### 2. The assembly selects; omission fails

`ProductAssemblySpec.module_planes` carries one typed `ModulePlaneSelection`
per selectable module. A selectable module with no selection is an invalid
assembly. An unknown module, duplicate selection, empty selection, undeclared
plane, or unsupported combination is refused before startup and by the composed
migration gate.

This is not a second switch competing with bindings. It owns a different fact:

- the plane selection says **what this product installs**;
- prerequisite bindings say **where the selected installation's effects come
  from**.

The gate joins them and refuses disagreement. Selecting TENANT makes every
`tenant_requires` entry mandatory; selecting PLATFORM does the same for
`platform_requires`. A provider may exist without its plane being selected.

### 3. Migrations and the live gate consume the same selection

A selectable lineage calls
`resolve_depends_on(common, module=..., tenant=..., platform=...)`. Only the
selected planes contribute physical Alembic edges, but every selected edge is
strict. `upgrade()` reads `selected_module_planes(module)` and creates exactly
those tables.

`NamespaceRegistry.expected_tables` reads the same typed assembly selection.
`declared_tables` remains the immutable full ownership claim; expected tables
are the selected subset. Provider bindings never alter either classification.

Graph commands that do not run `env.py` retain the existing inspection
tolerance. An assembly that exports `DOTMAC_MIGRATION_BINDINGS` for a faithful
graph also exports `DOTMAC_MODULE_PLANE_SELECTIONS` as the same kind of
`module.path:ATTRIBUTE` pointer to its typed selection sequence. Upgrade
entrypoints install both bindings and plane selections before the revision map
is built; the static gate checks the checked-in composition and refuses a
selectable lineage whose reachable `upgrade()` path never consumes
`selected_module_planes(module)`.

### 4. PostgreSQL proves the distinguishing case

The platform-only approvals canary first runs the kernel lineage and proves
`public.tenants` exists. It then runs `ap_0001` with an explicit PLATFORM-only
selection and proves:

- all three platform approval tables exist and remain reachable by
  `platform_api`;
- all three tenant approval tables are absent;
- the truthful tenant provider did not opt the tenant plane in.

The existing both-plane canaries continue to prove forced RLS, tenant isolation,
platform-role reachability, tenant-role revocation and no cross-plane FK.

### 5. A checked-in declaration outranks the ambient environment

`DOTMAC_MIGRATION_BINDINGS` and `DOTMAC_MODULE_PLANE_SELECTIONS` exist so that
Alembic's graph commands — which build the revision map WITHOUT running
`env.py` — inspect the same graph an upgrade applies. They are a transport for
one assembly's declarations, never a way to configure that assembly from
outside.

**So an assembly that has checked-in declarations ASSIGNS both variables; it
does not `setdefault` them.** A value already exported into the process must
lose, not win.

The failure this prevents is quiet and specific. `setdefault` has it exactly
backwards: a stale or foreign value left over from another assembly, a test
harness, or an operator's shell survives into the process, and the assembly
then *inspects* a different graph from the one it *applies*. That is precisely
the divergence these variables exist to close, reintroduced by the mechanism
meant to close it. Nothing fails loudly — the graph is well-formed, just not
this product's.

The rule is therefore about authority, not convenience: **the assembly is
authoritative for what it composes, and ambient environment is authoritative
for nothing it declares.** A deployment's own runtime values that the assembly
does NOT own — a database DSN, for instance — are the opposite case and must
not be overwritten; supply a fallback and leave the operator's value standing.

The distinguishing question, when adding a variable to either group: *does this
name a fact the assembly declares in checked-in code?* If yes, assign it. If it
names a fact the deployment owns, defer to it.

**Implementation stays assembly-local for now.** Vendor CP implements this in
`vendor_cp.migrations.make_alembic_config`; the kernel ships the rule, not a
helper. Extract a shared mechanism only when a SECOND real assembly needs it,
and migrate both consumers together in that change — one shared rule now, one
shared mechanism only when reuse is proven. Writing the helper first would be
building a generalisation from a single instance, which ADR-0006's product-first
extraction rule already forbids.

## Consequences

- Kernel a60 and `dotmac-approvals` a2 are superseded. The corrected public
  surfaces are kernel a61 and approvals a3.

  > **Amendment, 2026-08-15 — they were published, then superseded.** This line
  > originally read "withdrawn, never published", and that is not what happened.
  > `dotmac-kernel-v0.1.0a60` and `dotmac-approvals-v0.1.0a2` both exist as tags
  > on origin, and the release workflow writes a tag only AFTER `verify-registry`
  > succeeds — so each tag is evidence of a completed publish, not an intention.
  > `packages/dotmac-kernel/CHANGELOG.md` says so directly for a60: *"Published,
  > then superseded by a61."*
  >
  > The distinction is not pedantic. A published artifact cannot be
  > un-published: it stays resolvable on the index and installable by anything
  > that pins it. So any reasoning about **what a consumer could already be
  > running** — a floor, a compatibility claim, an incident timeline, a
  > "corrected public surface" statement — must treat a60 and approvals a2 as
  > REACHABLE. "Never published" invites precisely the opposite conclusion, and
  > it was the durable document carrying the wrong version of events while the
  > changelog carried the right one.
  >
  > "Withdrawn" was accurate as INTENT — both surfaces were superseded quickly,
  > and nobody should adopt them. The original wording recorded the intent as
  > though it were the outcome.
- Vendor CP can adopt only the approvals platform plane while continuing to run
  the kernel lineage it already composes.
- A future module may support only a subset of the three combinations; the
  assembly cannot select a combination the module did not promise.
- `dotmac-files` remains unadopted in Vendor CP. A generic installation mechanism
  does not create a stored-byte consumer or lift ADR-0017's demand gate.

## Alternatives rejected

**Keep absence of a binding as the selector.** It cannot distinguish intent from
omission and is factually wrong for a platform-only product that composes a
tenant provider.

**Infer from the live catalogue.** That turns drift into configuration: the
monitor would bless whatever happened to be present.

**Use one assembly-wide plane flag.** Different modules may legitimately install
different plane combinations. Intent belongs to each module installation.

**Split the distribution or lineage.** ADR-0023's one behaviour/one lineage rule
still holds. Plane selection controls one lineage; it does not create a second
owner or release train.
