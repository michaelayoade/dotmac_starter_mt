# Changelog — dotmac-numbering

All notable changes to the `dotmac-numbering` distribution.

## 0.1.0a2 — 2026-08-15

Declares the kernel prerequisite this module was already consuming.
`0.1.0a1` was never published; nothing is in the field on the undeclared shape.

`allocate` delegates at-most-once to `dotmac_kernel.idempotency` (hard rule 23)
and therefore writes `public.idempotency_records` /
`public.platform_idempotency_records` at request time. Nothing in `nu_0001`
creates those tables, so the dependency existed only in the call: against an
adopter running its own tenant lineage that had never run the kernel's, this
module passed every gate, migrated cleanly, and would raise `UndefinedTable` on
the first allocation. Found by the ERP adoption dossier
(`docs/inventories/numbering-erp-adoption-slice.md`).

### Changed

- `ModuleManifest.requires` and `nu_0001`'s `COMMON_REQUIRES` gain
  `idempotency_ledger.v1` — COMMON, not plane-specific: both planes call one of
  the pair. Verified at migration time, which is the last moment a missing
  ledger is a failed deploy rather than a failed allocation in production.
- Kernel floor raised to `>=0.1.0a66`, the release that publishes the name. A
  kernel below it HAS the tables but does not know the name, so
  `validate_prerequisites` refuses the manifest at import.

## 0.1.0a1 — 2026-08-15

First release. Allocation and formatting of configured document series on
declared tenant and platform planes, with an immutable receipt per allocation.

- `allocate` reserves the next value under `SELECT ... FOR UPDATE`, joins the
  caller's transaction and never commits.
- Replay by `(scope, series_code, idempotency_key)` returns the original
  formatted number; a changed request fingerprint conflicts.
- `reference_date` is a required business input. The module reads no clock.
- Reset boundaries compare periods by **ordering**, so a backdated allocation
  continues the current period instead of restarting it.
- `advance_to_at_least` repairs a counter forward to proven evidence and has no
  path that rewinds one or removes a receipt.
- One formatter, shared by allocation and preview.
- An unconfigured `series_code` fails closed; nothing is auto-created.

Requires kernel `>=0.1.0a65`, the release that allocated `mod_numbering`.
(Superseded by 0.1.0a2's floor.)
