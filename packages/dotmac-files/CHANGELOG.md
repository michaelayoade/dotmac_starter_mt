# Changelog — dotmac-files

## 0.1.0a2 — 2026-08-13

Declares the database EFFECTS this lineage needs instead of naming a foreign
revision (ADR-0006 D1 amendment).

### Changed

- `fi_0001_stored_files` previously read
  `depends_on = ("0001_initial_tenant_schema",)`. That edge is true only in an
  assembly that runs the kernel lineage: ERP hosts `public.tenants` in its own
  lineage and can never run kernel `0001`, so the module was un-installable
  there for want of a foreign-key target. The manifest now declares
  `requires=("tenant_scope_catalog.v1", "module_database_roles.v1")`, the root
  resolves its `depends_on` from the assembly's bindings, and `upgrade()` proves
  both effects against the live catalog before any DDL.
- Kernel floor raised to `>=0.1.0a56`, the release that added the prerequisite
  contract. A kernel below it cannot import this manifest.


## 0.1.0a1 — 2026-08-13

- Adds streamed admission with size, extension, declared-type, and detected
  content checks for PDF, CSV, `.xls`, `.xlsx`, PNG, JPEG, GIF, and WebP.
- Adds the provider-neutral immutable-object contract and typed failures.
- Adds explicit tenant `mod_files.stored_files` and platform
  `mod_files.platform_stored_files` planes in one independent `fi` lineage.
  Tenant rows use forced RLS; platform rows have no tenant column or RLS and
  are revoked from `app_user` (ADR-0023).
- Shares one persistence-free physical engine across both planes, selected by
  required `TenantScope` or `PlatformScope` values with non-overlapping object
  prefixes.
- Excludes domain attachment relations and import/document parsing by design
  (ADR-0022).
