# Changelog — dotmac-files

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
