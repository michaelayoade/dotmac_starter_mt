# Changelog — dotmac-people

## 0.1.0a2 — 2026-08-28

### Added

- Adds the first cutover-sized public surface: immutable Employment Type
  records, tenant-scoped search and exact-code query/page reads, typed
  create/revise/lifecycle commands, active-reference validation, and
  source-ID-preserving reconcile.
- Namespaces the complete normalized Employment Type decision-state
  fingerprint (code, name, description and active state) as `et1`, with
  length-prefixed typed fields, tenant and record identity, and an explicit
  absence encoding. Creation/update provenance is preserved and compared
  separately rather than folded into the semantic decision digest.
- Carries the owning tenant UUID on every immutable record so an assembly
  projector can refuse a record from another scope before writing a local
  compatibility table.
- Preserves timezone-aware source creation and update instants during
  reconciliation; a legacy null update instant deterministically adopts the
  source creation instant because the owned table requires both timestamps.

### Compatibility

- Keeps a1's `create_employment_type(CreateCatalogEntry)` ORM-returning entry
  point as a compatibility adapter over the same internal create decision.
- Adds no table or migration: a2 changes the typed public contract over the
  existing `mod_people.employment_types` owner.
- Uses the kernel's public engine-free transaction surface and therefore
  requires `dotmac-kernel>=0.1.0a98`; consuming applications retain ownership
  of engines, sessions, and the outer transaction.

## 0.1.0a1 — 2026-08-18

### Added

- Extracts ERP's employment lifecycle, organization catalogues, position tree,
  temporal assignments, and date-aware vacancy-routing behavior behind a
  kernel Party identity reference.
- Creates six directly tenant-scoped tables with composite identity, composite
  internal foreign keys, forced row-level security, and tenant-role grants in
  the root module migration.
- Enforces primary assignment interval exclusion in PostgreSQL rather than
  inheriting ERP's open-ended-only partial indexes.

### Deliberate exclusions

- No duplicate person/contact record, manager cache, department-head employee
  pointer, persisted vacancy cache, payroll/compensation field, delivery side
  effect, credential, or product-specific integration.
