# ADR-0046: Payroll owns calculations and liabilities

- Status: Accepted
- Date: 2026-08-19
- Deciders: Michael
- Supersedes: none
- Related: ADR-0006 (product-first extraction), ADR-0024 (application
  independence), ADR-0031 (sealed cutovers),
  `docs/inventories/payroll-sources.md`

## Context

ERP has the qualifying payroll implementation, but its aggregate reaches into
HR, attendance, tax, GL, bank/payment and authority-specific export concerns.
It also contains formula-string patterns and branches around familiar component
codes. Porting that shape would make the shared package a second People, Tax,
Banking and Accounting owner.

## Decision

`dotmac-payroll` is the tenant-only owner of pay-component configuration,
immutable effective-dated structure revisions, opaque employee assignments,
calculation evidence, payroll-run decisions and employee/external liabilities.

Component identities and values are tenant data. The first calculation contract
supports typed input, fixed and percentage rules with explicitly ordered basis
components; it never executes a stored expression. Published revisions snapshot
component identity, kind, account references and destination references so a
later master edit cannot restate an old calculation.

The module receives an opaque employee reference plus source-evidenced inputs.
People/employment, compensation, attendance and tax remain their own owners.
An application may obtain a tax determination and submit it as an evidenced
deduction input; Payroll never imports Tax. Likewise, payment execution and GL
posting are downstream adapters over finalized liabilities, not package logic.

A run is created, calculated, approved by someone other than its creator, and
finalized by someone other than its approver. Finalization materializes employee
net-pay and external liabilities. Settlement observations advance coverage but
do not execute payment.

## Consequences

- `mod_payroll` is a separately composed tenant schema with forced RLS.
- New pay components, liability destinations and policy structures require data
  changes, not code branches.
- No shared module owns an employee twice or creates a sibling import cycle.
- ERP cuts over first only after accounting and employee-reference contracts
  exist and a sealed switch retires its calculation/run/liability writers.

## Alternatives rejected

**Port the ERP payroll aggregate.** This would preserve cross-domain foreign
keys and multiple decision owners.

**Use a general expression evaluator.** It expands the security and audit
surface and makes deterministic parity harder. Typed rule kinds can grow under
versioned contracts when a real source behavior requires them.

**Put payroll inside People.** Employment identity and monetary calculation/
liability lifecycles have different authority, release and access boundaries.
