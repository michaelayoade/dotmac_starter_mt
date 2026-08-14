# ADR-0027: A plane declares its own prerequisites

**Status:** Accepted
**Date:** 2026-08-14
**Decision owner:** Michael
**Scope:** FLEET-WIDE for dual-plane modules.
**Relates to:** ADR-0023 (dual-plane modules declare both persistence planes),
ADR-0006 D1 amendment (logical prerequisites), ADR-0026 (`dotmac-approvals`,
whose Vendor CP cutover this unblocks), ADR-0017 (adoption is the scarce
resource — this removes a structural bar to one), hard rule 27.

## Context

ADR-0023 established that a capability operating in two security contexts
declares **both** persistence planes: `tables` (tenant, `tenant_id NOT NULL`,
FORCEd RLS) and `platform_tables` (control plane, no tenant column, REVOKEd from
the tenant role). It did not say what happens when an adopting assembly can
operate only one of them.

The answer, until now, was: nothing installs.

A dual-plane module ships **one** lineage, and that lineage declared one flat
`requires` list containing `tenant_scope_catalog.v1`. So its `upgrade()`
unconditionally created tenant tables with foreign keys to `public.tenants` and
RLS policies calling `public.app_current_tenant_id()`. An assembly with no
tenant catalogue could not run it at all — not the tenant half, not the platform
half, nothing.

**The vendor control plane is exactly that assembly**, permanently and by
design. Its own description is "NOT a product data plane"; it owns vendor-side
accounts, contracts and deployment lifecycle, and has no `public.tenants` and no
`app_current_tenant_id()` anywhere in its seven migrations. This was discovered
while starting ADR-0026's cutover 1, which names Vendor CP as the first adopter
of `dotmac-approvals` precisely because its plane has no `tenant_id`
prerequisite.

It is not one module's problem. `tk_0001_tickets` has the identical shape — same
two prerequisites, same unconditional tenant tables — so `dotmac-ticketing`'s own
vendor-control-plane adoption is blocked in the same way. Two of the fleet's
three dual-plane modules could not be adopted by the assembly they were dual for.

ADR-0017 already refused the obvious escape when ticketing hit the neighbouring
version of this: "inventing a tenant row to satisfy the constraint would be an
installation rather than an adoption". Giving a control plane a vestigial
`tenants` table so that dead tenant-scoped tables can be created beside it is
that, exactly.

## Decision

### 1. Prerequisites are declared per plane

`ModuleManifest` gains `tenant_requires` beside `requires`:

- **`requires`** — effects the module needs to create *anything*. Mandatory.
  Unbound is a composition error and fails closed, unchanged.
- **`tenant_requires`** — effects only the *tenant plane* needs. An assembly
  that binds them gets both planes; one that does not gets the platform plane
  alone.

`dotmac-approvals` therefore declares `module_database_roles.v1` as required and
`tenant_scope_catalog.v1` as tenant-only. Nothing else about the module changes.

This is not a general "optional requirement" mechanism, and the kernel refuses
to let it become one: a name in both lists is an error, because it reads as
mandatory and behaves as optional. `tenant_requires` on a module with no tenant
tables is also an error — there is no plane to condition on.

### 2. The binding is the switch, and absence is the safe answer

A lineage asks `all_bound(TENANT_REQUIRES)` and builds the tenant plane only if
the answer is yes. There is no separate flag, environment variable or install
option, because a second switch could disagree with the first: an assembly that
binds a tenant catalogue but sets `tenant_plane=false` (or the reverse) would be
in a state nobody can reason about.

Absence is safe in the direction that matters. An assembly that never bound a
tenant scope cannot have tenant data, so skipping tenant tables loses nothing.
The failure mode in the other direction — an assembly that *should* have the
tenant plane but forgot the binding — is visible as a missing plane in the live
catalog and, before that, as a composition without a binding a reviewer can see
in `app/migration_bindings.py`.

`is_bound` never raises for an unbound prerequisite; that is the answer, not an
error. It still raises for an *unregistered* name, because returning False for a
typo would silently skip a plane.

### 3. A bound optional prerequisite is ordered exactly like a required one

`resolve_depends_on(required, optional=…)` contributes a real Alembic edge for
every optional prerequisite that IS bound. Where the tenant plane is built it
must still run after whatever supplies the catalogue; where it is not, the edge
is simply absent. Optionality is about whether the plane exists, never about
whether ordering matters.

### 4. The gates learn "declared, but not installed here"

Two changes, and the split between them is the point:

- The **composed static gate** requires bindings for `requires` and tolerates
  their absence for `tenant_requires` — but checks any tenant binding that IS
  present exactly as strictly as before. A half-truthful binding is worse than
  an absent one.
- The **live-catalog gate** audits `expected_tables(schema)` rather than
  `declared_tables(schema)`. They differ only for a dual-plane schema whose
  tenant prerequisites are unbound, where the tenant tables were deliberately
  never created and reporting them missing would fail a correct install.

`declared_tables` remains the full ownership claim, unchanged: who owns a table
name does not vary per assembly, only what got built does. Keeping them as two
readers rather than redefining one is what stops "expected" quietly eroding into
"whatever we found".

### 5. What this does not weaken

A tenant table that IS created is held to every rule it was before —
`tenant_id NOT NULL`, composite identity, FORCEd RLS, the isolation canary.
A platform table likewise stays REVOKEd from the tenant role. This ADR changes
*whether* a plane is built in a given assembly, never *what* a built plane must
satisfy.

Nor does it make planes optional per deployment as a matter of taste. The
condition is a factual one — does this assembly supply a tenant catalogue — and
an assembly that does supply one always gets both planes.

## Consequences

- `dotmac-approvals` becomes installable in the vendor control plane, which
  unblocks ADR-0026's cutover 1. That cutover still requires Vendor CP to adopt
  a kernel from `0.1.0a46` to `0.1.0a60` and to install prerequisite bindings in
  its `alembic/env.py`; this ADR removes the structural bar, not the adoption
  work.
- **`dotmac-ticketing` needs the same split**, and is deliberately NOT changed
  here: it is a separately released module with its own adopters, and bundling
  its version bump into this change would couple two release trains. It is the
  named next change, and until it lands its vendor-control-plane adoption stays
  blocked for the reason this ADR describes.
- The kernel gains a small public surface — `is_bound`, `all_bound`, the
  `optional=` parameter, `ModuleManifest.tenant_requires`,
  `NamespaceRegistry.expected_tables` / `tenant_plane_requires` /
  `tenant_plane_installed` — released as `0.1.0a60`.
- A module that declares `tenant_requires` and is installed platform-only has a
  schema whose contents depend on the assembly. That is new, and it is why the
  live gate now distinguishes expected from declared rather than treating a
  short schema as drift.

## Alternatives rejected

**Give the vendor control plane a tenant catalogue.** Refused for ticketing by
ADR-0017 in almost these words, and refused again here. A control plane with a
`tenants` table it never populates, so that tenant-scoped tables it never reads
can exist, is an installation dressed as an adoption — and it puts a tenant
estate into the one assembly whose whole identity is not being one.

**Ship two lineages, one per plane.** Hard rule 14 gives each module exactly one
registered migration lineage, and a second branch label per module would double
every allocation, every `alembic_version` attribution and every gate's notion of
ownership — to express a condition the prerequisite mechanism already models.

**Make the tenant plane a separate module.** `dotmac-approvals-tenant` and
`dotmac-approvals-platform` would be two distributions of one capability, with
one behaviour, two versions and two release trains to keep in step. ADR-0023
declared both planes as one module for exactly this reason; splitting the
package to solve an installation problem would undo that decision to avoid a
manifest field.

**Reorder the cutovers and make ERP first.** ERP has a real tenant estate, so
the module installs there — but ERP is gated on its E8 Organization-to-Tenant
decision, and ADR-0026 chose Vendor CP first precisely because it does not wait
on E8. Reordering would park adoption behind a decision this ADR does not need.
