# Changelog — dotmac-tax

## 0.1.0a2 — unreleased

### Added

- One immutable determination set per source fact, containing an ordered
  component for every applicable tax code. This permits VAT, levies,
  withholding and tenant-defined taxes to compose without product fields.
- Explicit calculation sequence and `source_amount` / `source_plus_prior_tax`
  bases for deterministic compound taxes.
- Tax-specific, effective-dated party, supply and place classifications with
  approval, evidence and source-version provenance; versions are contiguous
  append-only overrides and the selected row is snapshotted on each component.
- Distinct `standard_rated`, `zero_rated`, `exempt` and `out_of_scope`
  treatments; zero amount no longer erases the legal reason.
- Fail-closed category selection per configured tax code and fact signature;
  custom taxes require explicit exempt or out-of-scope rules instead of being
  silently omitted.
- Additive `tx_0002_multi_tax` migration. Released `tx_0001_tax` remains
  byte-identical to `dotmac-tax-v0.1.0a1`.
- Python `>=3.11,<3.14` compatibility, aligned with the kernel and the ERP
  first-adopter runtime; the package uses no Python-3.12-only surface.

### Compatibility

- `determine_tax` remains the single-component API and replays legacy a1
  determinations. Callers that can receive stacked taxes use
  `determine_tax_set`.

## 0.1.0a1 — 2026-08-21

Published, installed back from the private index, conformance-checked and
tagged from exact protected-main revision `20d24703` by release run
`32480725191`. Publication is supply-chain evidence only; it composes no product
and moves no authority.

### Added

- Configurable authorities, jurisdictions, codes, effective rules, flat/fixed/
  progressive calculations, and recoverability evidence.
- Source-versioned determinations and immutable determination lines.
- Configurable statutory report definitions/boxes, filing obligations and
  versioned report snapshots.
- Prepare, approve, file, accept/reject, and amend return lifecycle with an
  append-only event trail and separation of duties.
- Fourteen directly tenant-scoped tables with forced RLS and PostgreSQL
  isolation canaries.

### Deliberate exclusions

- No country authority enum, statutory rate, due-day constant, government
  portal client, invoice/order/payroll query, GL foreign key, or journal writer.
