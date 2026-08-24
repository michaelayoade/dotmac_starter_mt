# ADR-0046: Payroll owns calculations and liabilities

- Status: Accepted. Amended 2026-08-24 (A1) — see "Amendment — 2026-08-24" at
  the end of this record. A1 makes the "downstream adapter" seam concrete: an
  authorized net-pay obligation produces a Treasury `PaymentInstruction`, and
  Treasury never receives a salary component. No earlier text is rewritten.
- Date: 2026-08-19
- Deciders: Michael
- Supersedes: none
- Related: ADR-0006 (product-first extraction), ADR-0024 (application
  independence), ADR-0031 (sealed cutovers),
  `docs/inventories/payroll-sources.md`; and, from 2026-08-24, ADR-0063
  (Treasury owns the payment instruction) + its Amendment A2, ADR-0042 § 3,
  ADR-0047 Amendment A1 (the six-owner disbursement split), ADR-0026

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

> **Made concrete 2026-08-24 — see Amendment A1.** "A downstream adapter over
> finalized liabilities" names a shape, not a seam. A1 names it: one authorized
> net-pay obligation produces one Treasury `PaymentInstruction`, a run may
> group them into a `PaymentRun`, exporting the bank file marks nothing paid,
> settlement returns as a typed observation — and Treasury receives net amount,
> currency, payee/destination reference and obligation reference ONLY, never a
> salary component.

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


## Amendment — 2026-08-24 (accepted correction A1)

One correction. It rewrites nothing above; the superseded spot in "Decision"
carries a pointer to it.

### A1. An authorized net-pay obligation produces a Treasury `PaymentInstruction`

The Decision says *"payment execution and GL posting are downstream adapters
over finalized liabilities, not package logic"* and *"settlement observations
advance coverage but do not execute payment"*. Both are right and both describe
a shape rather than a seam. ADR-0063 now names the owner on the other side of
that seam, so the seam itself can be stated.

**Payroll owns calculation, approval and the net-pay obligation. Treasury owns
disbursement.** Concretely:

- **One authorized net-pay obligation produces ONE `PaymentInstruction`** — the
  obligation finalization materializes is the unit that gets paid, fails and is
  reconciled. Not one per pay component and not one per run.
- **A payroll run MAY group those instructions into a `PaymentRun`**
  (ADR-0063 § 3). Grouping is evidence and convenience; it confers no
  authorization, so approving a run never authorizes an instruction that was
  not itself authorized.
- **Treasury's manual rail produces the bank-upload artifact.** The bank file
  format, its digest, who exported it and who lodged it with which bank are
  Treasury's evidence (ADR-0063 § 2). Payroll stops owning a file format —
  today ERP's payroll run drives the shared generator itself
  (`app/services/people/payroll/web/run_web.py:1102` over
  `app/services/finance/banking/bank_upload.py:58`).
- **Exporting the file does NOT mark payroll paid.** ERP's payroll export
  writes no status at all today; that is the correct behaviour and it must
  survive the port. `submitted` needs operator submission evidence; `settled`
  needs Banking's settlement observation.
- **Settlement evidence returns to Payroll through a TYPED OBSERVATION**, which
  Payroll's own owner consumes to advance coverage. Treasury never writes a
  Payroll status, and an importer never assigns an authoritative lifecycle
  field (ADR-0024).

**The privacy boundary — it is a privacy boundary as much as an ownership
one.** Treasury receives exactly four things: the **net amount**, the
**currency**, the **payee / destination reference**, and the **payroll
obligation reference**.

**Never salary components** — not gross, basic, allowances, overtime, bonuses,
deductions, tax, pension, loan repayments, garnishments, grade or band, and
nothing from which any of them can be derived. Including in the instruction's
narration or memo field: the obligation reference is the pointer, and resolving
it is Payroll's authorization decision. This matters most on the bank-file rail,
because there the artifact LEAVES — a payments operator, a bank portal and an
email attachment are a payments-operations audience, not an HR one, and a
column headed "pension deduction" in an exported file is a payroll disclosure
to everyone who handles that file. ADR-0063 § 2 additionally requires Treasury
to retain that export immutably, so a component that reaches Treasury is a
component that is digested and archived beyond HR's reach to correct or redact.

**A payee who cannot be paid is a STATE, not an omission.** ERP drops an
employee with no account number from the export with a log line
(`app/services/people/payroll/web/run_web.py:1066`) — an unpaid employee in no
queue. Under ADR-0063 § 3 and its Amendment A1 the instruction stays visibly
blocked instead.

*Corrects: nothing. It makes the Decision's "downstream adapters" clause
concrete and adds a boundary the original record did not state. Related:
ADR-0063 § 2, § 3 and Amendment A2 (the same seam recorded on the Treasury
side), ADR-0042 § 3, ADR-0047 Amendment A1, ADR-0044, ADR-0026.*
