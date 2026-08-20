# Changelog — dotmac-payroll

## 0.1.0a1 — UNRELEASED

### Added

- Tenant-configured pay components and immutable, effective-dated structure
  revisions using typed input/fixed/percentage rules.
- Opaque employee assignments and source-evidenced payroll calculation lines,
  including proration and deduction-over-gross refusal.
- Creator/approver/finalizer separation and materialized employee/external
  liabilities with partial/full settlement evidence.
- Eleven directly tenant-scoped tables with forced RLS and PostgreSQL isolation
  canaries.

### Deliberate exclusions

- No employee/person table, statutory tax formula, component-code catalogue,
  expression evaluator, attendance query, bank/payment client, GL foreign key,
  journal writer, or authority-specific export.
