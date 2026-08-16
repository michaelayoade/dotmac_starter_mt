# Changelog — dotmac-entitlement-allocation

## Release state — read this before pinning

**Four versions have been released:** `0.1.0a1`, `0.1.0a2`, `0.1.0a3` and
`0.1.0a4`, tagged `dotmac-entitlement-allocation-v0.1.0a1` … `-v0.1.0a4` from
`847ce0b`, `5ded880`, `c371b0f` and `67bdfb8`. **Pin `0.1.0a4`.**

`0.1.0a5` is declared in `pyproject.toml`, `manifest.py` and `__init__.py` and
is **not released**: no tag, nothing on the index. It is recorded in
`docs/inventories/declared-publication-baseline.json` as declared-unpublished,
and — unlike the ordinary declared-then-released cycle — that row does **not**
expire at the next release run. Michael's standing condition of 2026-08-16: the
next published version of this module must declare BOTH the idempotency ledger
and the platform audit dependency. Version `0.1.0a5` now does; publication
still waits for kernel `0.1.0a68` to be released and verified.

Nothing in this file is a publication claim except this section.

## 0.1.0a5 — UNRELEASED (on `main`; no tag, not on the index)

### Both runtime persistence effects are declared and verified at deploy

Declares the kernel prerequisite this module has consumed since `0.1.0a1`.

`stage_allocation` delegates at-most-once execution to
`dotmac_kernel.idempotency.execute_once_platform` (hard rule 21, ADR-0014 — the
ledger has exactly one owner and it is not this module), so
`public.platform_idempotency_records` is written at REQUEST time. `ea_0001` —
the module's only other revision — creates nothing of the sort, so the
dependency existed only inside a function body: an adopter running its own
lineage that never ran the kernel's (ERP hosts `public.tenants` itself and
structurally cannot run kernel `0001`) installs this module, passes every gate
it has, migrates cleanly, and raises `UndefinedTable` on the first staged
activation. No test could have caught it before kernel `0.1.0a66`, because
there was no name to declare. Same defect, same week, as `dotmac-numbering`
`0.1.0a1` and `dotmac-integration` `0.1.0a1`..`0.1.0a3`.

The blast radius here is wider than replay protection:
`write_platform_audit_event` is called from INSIDE the idempotent operation, so
a missing ledger takes the audit trail down with the allocation.

#### Changed

- `ModuleManifest.requires` gains `idempotency_ledger.v1`. COMMON rather than
  `platform_requires`: this module owns one plane, installed atomically, so
  there is no selection under which the requirement lapses — and a plane list
  is unresolvable for an atomic module anyway, since `resolve_depends_on` reads
  one only via `module=`, which needs a `ModulePlaneSelection` such a module
  may not have.
- Kernel floor raised to `>=0.1.0a66`, the release that published the name.
  a56..a65 HAVE the tables — kernel `0018` created them — but do not know the
  name, so `validate_prerequisites` refuses the manifest at import. **This is a
  visible break for a consumer pinned to a released a1..a4 on an older
  kernel**, and deliberately so: every one of those installs against a kernel
  whose ledger it silently requires and cannot state.
- All three migrations are required wheel contents in
  `.github/release-modules.json`. `ea_0002` and `ea_0003` create nothing, so a
  wheel that dropped either would ship a declared-but-never-verified
  prerequisite.

#### Added

- **`ea_0002_idempotency_ledger`** — a DDL-free revision whose whole body is
  `require_prerequisites`. Deploy is the last moment at which a missing ledger
  is a failed migration rather than a failed staging call.
- **`ea_0003_platform_audit_log`** — a second DDL-free revision for the audit
  effect. It is separate because its provider revision (`0026`) descends the
  ledger provider (`0018`); naming both on one module revision gives Alembic
  two heads from one provider lineage and fails during head maintenance.
- A NEW revision rather than an edit to `ea_0001`, which shipped in four
  published tags and whose bytes are therefore history.
  `tests/architecture/test_released_migrations.py` — extended to this
  distribution in the same change — records the SHA-256 of every migration file
  in every released tag, cross-checks each digest against the blob git holds at
  that tag, and fails if one changes or disappears.

#### Platform audit dependency closed

- `write_platform_audit_event` runs inside the same idempotent operation and
  appends to `public.platform_audit_events`. `ModuleManifest.requires` and
  `ea_0003` now also require `platform_audit_log.v1`.
- Kernel floor raised again to `>=0.1.0a68`, the first kernel that names and
  verifies that append-only platform effect. The already-unpublished a5 keeps
  its version because no consumer could have installed the earlier declaration.

## 0.1.0a4 — 2026-08-15 — RELEASED (tag `dotmac-entitlement-allocation-v0.1.0a4`)

**The persistence plane is now declared correctly.** This module always built
control-plane tables — no `tenant_id`, no RLS, grants to `platform_api` and
`app_admin`, `REVOKE ALL` from `app_user` — but the manifest declared them under
`tables=`, the TENANT slot. No DDL changed; the declaration did.

The mismatch mattered: ADR-0023 § 2 makes the plane declared and never inferred,
and the live-catalog gate holds each plane to its own contract, so these tables
were being audited against the tenant contract they can never satisfy.

The module is ATOMIC and says so by saying nothing — `supported_plane_sets` is
omitted rather than written as an explicit `()`. That keeps the kernel floor at
`0.1.0a56`, the earliest published kernel with `platform_tables`; writing the
keyword would raise the floor to `a61` where that constructor field first
appears, for a value the default already supplies.

**Kernel floor raised to `>=0.1.0a56`**, which is the honest minimum for a
module that declares `platform_tables` at all.

## 0.1.0a3 — 2026-08-13

Allow the independent module's ORM models to coexist with same-named models in
its extraction source during a shadow phase. Relationship targets and ordering
now use class-bound callables instead of SQLAlchemy's global string registry,
so a consuming control plane can load both `Allocation` / `AllocationEntry`
pairs on the shared kernel `Base` without ambiguous mapper resolution. The
schema and domain contract are unchanged.

## 0.1.0a2 — 2026-08-13

Expose the installed Alembic lineage through the stable top-level
`versions_dir()` contract. A separate consuming repository can now compose the
module without deriving a private path from `__file__` or assuming a source
checkout layout. Domain behavior and schema are unchanged.

## 0.1.0a1 — 2026-08-12

First release. An immutable projection of what an activated contract version
entitles, extracted product-first from the vendor control plane.

**The module validates; the caller supplies the authority.** `stage_allocation`
takes a `CapabilityCatalogueReader` and performs the check itself — the kernel's
`grant_entitlement` pattern — so the invariant holds across every adapter rather
than once per adapter. There is no `validated=True`.

**Three couplings cut** relative to the source: the cross-module FK to
`contracts`, the direct `session.get(Contract, …)`, and the hard-coded
`"contract.activated"` literal. All three invert into one typed
`ContractSnapshot`, which is what makes the extraction independent of where
commercial contracts eventually live.

**`product_code` is persisted** — new relative to the source, and a correctness
fix: it closes the path where an allocation validated against one product is
issued as a licence for another. `allocation_product()` is how licence issuance
reads it.

**Replay does not re-validate.** An already-staged allocation is immutable
history whose legality was decided when it was staged; re-checking it against a
live catalogue would make a delivered entitlement unreplayable the day a
capability is retired.

Tables `allocations` + `allocation_entries` in `mod_ealloc`, lineage root
`ea_0001_allocations`. Platform catalog: no `tenant_id`, no RLS, `app_user`
REVOKEd. `platform_api` holds SELECT and INSERT, plus a COLUMN-LEVEL
`UPDATE (sealed, updated_at)` on `allocations` — the seal is the one decision
the online role may write, and every business column stays unwritable.

**Immutability includes append.** `platform_api` needs INSERT on
`allocation_entries` to stage at all, so the grant that makes the parent
immutable leaves the child appendable — raw SQL could add an unvalidated
capability to a staged allocation. A `refuse_late_entry` trigger rejects any
entry once the parent is SEALED,
and `quantity > 0` is a database CHECK rather than only a service rule.

The seal is EXPLICIT, not inferred from transaction identity. An earlier
revision tested `age(xmin) = 0` and rejected legitimate staging: the kernel's
at-most-once owner runs inside `conflict_savepoint`, and a SAVEPOINT is a
SUBTRANSACTION whose writes carry a subtransaction xid that never equals the
top-level one. `seal_is_one_way` makes the flip irreversible.

**Two identities, two protections.** `source_event_id` (a DELIVERY) goes through
the kernel's `execute_once_platform` — ADR-0014's one at-most-once owner — which
also means the staging audit event is written exactly once however many times an
at-least-once transport delivers. `(contract_ref, content_hash)` (an ACTIVATION)
carries a stored `snapshot_fingerprint`: the same pair arriving with a different
product, customer or entry set CONFLICTS rather than replaying. The fingerprint
lives on the allocation because idempotency records have a retention policy and
allocations do not.

**Duplicates are refused, not aggregated.** Deciding whether two lines of ten
seats mean twenty is a commercial rule owned by whoever owns contracts.

**Adapters translate.** The service catches nothing broad, so an adapter that
leaks its backing store's exception surfaces as a defect rather than being
relabelled an undeclared capability.

**Every delivery is recorded.** Every call enters the at-most-once owner before
anything else, including an activation replay. Otherwise a delivery key could be
spent without being recorded — stage claim A under event-a and claim B under
event-b, then replay claim A under event-b, and the call would succeed without
discovering that event-b belonged to a different request. An activation race
retries THROUGH the kernel, so the losing delivery key also receives its ledger
row.

**An unsealed allocation is not history.** Staging writes the parent, the
entries and the seal in one transaction, so this service cannot leave an
unsealed row behind by crashing; a committed one means raw SQL, an offline
repair, or a writer that split the sequence across transactions. Both
paths that consume an allocation — replay resolution and `allocation_product` —
raise `IncompleteAllocationError` rather than treating it as a fact, because the
row cannot say whether its missing entries were rejected or merely never
written.

**Fully typed contracts.** `AllocationStatus` and `AllocatedCapability` replace
a bare string and a positional pair; every public value object is frozen and
slotted with no mutable collection fields; the package type-checks strict.

Requires `dotmac-kernel >= 0.1.0a45`.
