# Changelog — dotmac-surveys

## 0.1.0a1 — 2026-08-21

Published, installed back from the private index, conformance-checked and
tagged from exact protected-main revision `20d24703` by release run
`32480579585`. Publication is supply-chain evidence only; it composes no product
and moves no authority.

### Added

- Product-first tenant Surveys owner sourced from Sub's typed survey lifecycle.
- Typed four-question contract, guarded lifecycle, opaque-source invitations,
  tracked and public responses, exact rating/NPS validation, and rebuildable
  aggregates.
- Independent `sv` migration lineage in `mod_surveys`, with tenant-composite
  foreign keys and ENABLEd plus FORCEd row-level security on all three tables.

### Changed from the source

- Ticket/work-order trigger enums, subscriber/person/product foreign keys,
  delivery state and direct transaction completion stay outside the module.
- Activated definitions are immutable; a changed questionnaire is a new Survey,
  preventing one aggregate from silently mixing response schemas.
