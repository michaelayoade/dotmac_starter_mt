# Changelog — dotmac-customers

## 0.1.0a2 — 2026-11-20

### Added

- `rename_account(db, scope, account_id, display_name)`: the single governed
  writer of `display_name` after creation. Required, at most 200 characters,
  idempotent for an unchanged name, tenant-scoped, refused (`Conflict`) on a
  CLOSED account. No migration; account id and number are never changed.

## 0.1.0a1 — 2026-08-20

### Added

- Product-first extraction of Sub's customer account/profile boundary.
- Typed, opaque Party references without duplicating Party identity or contact
  reachability.
- Three tenant-scoped tables with composite internal identity and forced RLS.
