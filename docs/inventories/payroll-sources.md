# Payroll extraction audit — calculations and liabilities

- **As of:** 2026-08-19
- **Starter:** `f7d69f7d3db6`
- **ERP:** `0f4b1698ddbf`
- **Sub:** `91c1ec477b3a`
- **CRM:** `c64b5aa0f790`
- **Backoffice:** `fcdd8270262d`
- **Vendor control plane:** `e6b2bbee815c`

This is the product-first source audit for `dotmac-payroll`. It names a narrow
payroll owner and keeps employee identity, tax policy, payment and accounting
outside the package.

## Finding

ERP is the only qualifying implementation. Its People/payroll models, services,
migrations and tests contain components, structures, employee assignments,
runs, slips, proration, approval behavior, amounts paid/coverage, and employee/
external liabilities.

No other audited product has a competing mature payroll engine. Sub and CRM are
possible source-fact/projection consumers, not owners. Backoffice is an intended
clean assembly. The vendor control plane has no tenant payroll data plane.

## Target owner

`dotmac-payroll` owns:

- tenant-configured component masters;
- immutable, effective-dated structure revisions with typed input, fixed and
  percentage rules plus explicit basis components;
- effective assignments to opaque employee references;
- run and employee calculation evidence, including proration and input source
  evidence;
- creator/approver/finalizer separation; and
- materialized employee-net and external liabilities plus settlement
  observations and remaining coverage.

It does not own person/employee identity, employment lifecycle, compensation
source authority, attendance/leave, statutory tax policy, bank account/payment
execution, GL accounts, journal entries, fiscal periods, government/provider
exports, or remittance transport.

## Qualifying ERP surface

- `app/models/people/payroll/*`
- `app/services/people/payroll/*`
- payroll migrations and focused `tests/people/payroll/*`

The package preserves versioned configuration, effective assignment, line-level
calculation evidence, proration, separation of duties, liabilities, partial
settlement and outstanding coverage. It snapshots component identity and
account/destination references in the published revision so later master edits
cannot rewrite an old payroll run.

## Couplings and defects not ported

1. No fixed component codes such as basic pay, a named tax, pension or housing
   fund. Components are tenant data.
2. No string formula or `eval`/`exec`. The first contract exposes only typed
   input, fixed and percentage rules with ordered bases.
3. No direct HR/People model or foreign key. `employee_ref` is an opaque
   application-level identity whose existence/authorization the adopter owns.
4. No direct tax-module import. The application asks its tax owner and supplies
   a source-evidenced deduction input; this keeps both packages independently
   releasable.
5. No bank/payment provider, state/authority export, GL account foreign key, or
   journal writer. Liabilities name opaque destinations/account references for
   application adapters.
6. A deduction greater than gross fails closed. Finalization is distinct from
   approval and creates the liability evidence once.

## Composition and cutover

The shared accounting owner must precede payroll adoption, and the employee
reference contract must be stable. ERP remains the sole payroll writer through
backfill and read-only shadow. The comparison covers every input/component line,
proration, totals, run transition, liability/destination, settlement and
coverage. One separately authorized sealed switch makes the module-backed
application sole writer and reduces ERP calculation/run/liability writers to
zero. Backoffice follows on the exact release; merely composing the package
moves no authority.

The implementation branch's kernel allocation is provisional because concurrent
alpha-train branches also target the next version. Integration must rebase and
renumber before any release claim.
