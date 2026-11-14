# Changelog — dotmac-payroll

## 0.1.0a3 — declared successor

- Add read-only GL consequence facts: `build_run_accounting_consequence`
  (finalized run: Dr earning and employer-liability expense; Cr deduction and
  employer liability accounts; Cr the revision net-payable account, e.g. the
  tenant-configured payroll payable) and `build_liability_settlement_consequence`
  (recorded settlement or statutory remittance: Dr liability account / Cr
  caller-supplied bank GL ref).
- Facts are frozen, balanced, tenant-scoped, deterministic and carry an
  idempotency key and content sha256. Refusals: run not finalized, no
  calculations, missing account refs, line/snapshot/liability drift, bank ref
  equal to the liability account, wrong tenant.
- No migration, no GL write, no payment or provider call, no change to
  existing transitions or separation of duties. The adopting adapter posts.
- Publication and ERP adoption remain separate gates; `0.1.0a2` is unchanged.

## 0.1.0a2 — declared successor

- Extract independent gross/deduction/net sums and net-only payment-total parity
  from ERP PR #761 into frozen scalar contracts. Reject non-finite/unscaled
  amounts, currency/scale mismatch and duplicate calculation references without
  putting amounts or references in errors.
- Read tenant/run persisted calculation scalars through the same aggregate and
  consume its count during approval; retain existing calculation, lifecycle and
  liability authority. No stored totals, migrations or payment execution.
- Publication and ERP adoption remain separate gates; `0.1.0a1` is unchanged.

## 0.1.0a1 — 2026-08-21

Published, installed back from the private index, conformance-checked and
tagged from exact protected-main revision `20d24703` by release run
`32479474001`. Publication is supply-chain evidence only; it composes no product
and moves no authority.

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
