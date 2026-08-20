# Changelog

## 0.1.0a1 — 2026-08-20

- Extracted Sub's reseller account, user-binding, hierarchy and lifecycle
  behavior behind opaque collaborator references.
- Added immutable least-privilege authority revisions, cycle-safe hierarchy
  changes, idempotent member/customer bindings and provider-neutral lifecycle
  outbox facts.
- Added the tenant-only `rm` lineage with forced RLS in `mod_reseller`.
