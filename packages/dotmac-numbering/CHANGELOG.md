# Changelog — dotmac-numbering

All notable changes to the `dotmac-numbering` distribution.

## UNRELEASED — 0.1.0a4

- Extend the existing numbering owner with strict immutable creation and
  explicit enrollment of existing series, immutable collision domains and
  exact-string claims across replacement codes, caller-transaction historical
  and external reservations, and immutable configuration/period read DTOs.
- Add `nu_0002_replacement_domains` for selected tenant/platform planes;
  preserve released `nu_0001_numbering` bytes and unenrolled caller behavior.
- Keep kernel idempotency as the replay owner and preserve legacy allocation
  fingerprints. Enrollment/replacement does not authorize an ERP or Sub switch.
- New canaries are authored for hosted CI. This candidate is not published or
  accepted by consumers; PostgreSQL proof and release/consumer gates remain due.

## 0.1.0a3

Retained release tag `dotmac-numbering-v0.1.0a3` carries
`ModuleReleaseTagEvidence.v1`, peeled source
`62ecb6b0e198ba08bef9a096037194a9dfca4da0`, verification run `39010380319`.
This corrects the earlier proposal-only description; the local evidence does
not replace a fresh registry check for a new adopter.

- Typed service queries and explicit tenant/platform series construction;
  an unknown scope is refused before creating a series.
- Strict validation of idempotency allocation results rather than coercing
  malformed result values. Kernel floor remains `>=0.1.0a66`.
- No migration or allocation algorithm change relative to a2.

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
