# Changelog — dotmac-people

## 0.1.0a1 — UNRELEASED

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
