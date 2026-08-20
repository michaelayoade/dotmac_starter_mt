# Changelog — dotmac-tax

## 0.1.0a1 — UNRELEASED

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
