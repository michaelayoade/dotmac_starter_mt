# Changelog — dotmac-tax

## 0.1.0a3 — unreleased

### Added

- Published ORM-free `TaxDeterminationSetV1`, `TaxDeterminationComponentV1`
  and `TaxDeterminationLineV1` read contracts. They retain the determination
  fingerprint, ordered rule/classification evidence, progressive-band lines,
  exact kernel `Money`, and explicit zero-rated, exempt and out-of-scope
  components.
- `TaxDeterminationSetV1.reportable_zero_components`, so an all-zero legal
  determination remains a typed reporting outcome rather than an accounting
  error.
- Fail-closed read-contract validation for component membership, exact
  currencies, recovery and line/component totals, strict ordering, inclusive
  arithmetic, finite rates, aware determination timestamps, and explicit zero
  treatments.
- Persisted `rv1:<sha256>` result-content seals covering the complete
  normalized set, component and progressive-line evidence, including row and
  membership identities. Every projection, replay and statutory aggregation
  verifies the seal and every duplicated component field against its enclosing
  set; reports refuse legacy standalone evidence rather than summing around the
  boundary.
- Additive `tx_0003_result_fingerprint` migration. Immutable a2 determination
  sets remain unchanged with a `NULL` seal and are refused by a3 readers. New
  results have one transaction-local `building` phase: child insertion closes
  at the single content-preserving transition to `sealed`, a deferred trigger
  refuses commit before that transition, and the app role receives UPDATE only
  on the two seal columns. No historical fingerprint is invented.

### Compatibility

- `determine_tax_set` now returns `TaxDeterminationSetV1` instead of exposing a
  SQLAlchemy `TaxDeterminationSet`. Its `id` compatibility property preserves
  row identity, while monetary attributes intentionally change from bare
  `Decimal` values to exact `Money`. This is an alpha contract change.
- The legacy single-component `determine_tax` API continues to return its a1
  ORM result. ERP is the first read-contract consumer and deletes its temporary
  `ApplyTaxDeterminationSetV1` mirror in the same change that pins a3.
- Both determination entry points reject naïve input timestamps and conflicting
  simultaneous legacy/set owners. Persisted naïve timestamps are refused, not
  silently relabelled as UTC.

## 0.1.0a2 — 2026-08-24

Published, installed back from the private index, registered and tagged from
exact protected-main revision `bd8d2262c26f62041cc22a813916066b9af85c7f`
by release run `32734134160`. Publication moved no product authority.

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
