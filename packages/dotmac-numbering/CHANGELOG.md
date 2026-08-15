# Changelog — dotmac-numbering

All notable changes to the `dotmac-numbering` distribution.

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
