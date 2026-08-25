# Changelog — dotmac-banking

## 0.1.0a1 — 2026-08-19

### Added

- Configurable bank-institution and account masters with opaque accounting
  references; no provider or bank-name catalogue is compiled into the package.
- Immutable statement and cash-observation imports with source-version
  deduplication and statement balance validation.
- Data-driven weighted matching, multi-observation allocations, and
  preparer/approver-separated reconciliation snapshots.
- Nine directly tenant-scoped tables with composite identity, forced RLS, and
  real PostgreSQL isolation canaries.

### Deliberate exclusions

- No provider client, credential, polling job, statement format, GL foreign
  key, journal writer, payment decision, or product-specific collection-account
  directory.
