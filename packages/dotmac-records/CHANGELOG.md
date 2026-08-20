# Changelog — dotmac-records

## 0.1.0a1 — UNRELEASED

- Greenfield-after-inventory managed-record declaration, schedule, legal-hold,
  preservation, custody and disposition authority.
- Independent `re` lineage in `mod_records`, with tenant-composite identity and
  forced RLS on every table.
- Typed opaque seams to source owners, Files, Approvals and Durable Timers; the
  package does not own source-domain meaning, bytes or timer delivery.
