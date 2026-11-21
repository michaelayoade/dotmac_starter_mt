# Changelog — dotmac-payroll

## 0.1.0a7 — declared successor

D-9 (Michael, 2026-11-17/18: two-person approval is never compulsory; separation of
duties is an optional per-tenant policy, default off).

- `separation_of_duties: bool = True` (keyword-only) on `approve_payroll_run`,
  `finalize_payroll_run` and `reverse_liability_settlement`. When true, the a6
  checks apply unchanged (run creator may not approve, approver may not finalize,
  recorder may not reverse their own settlement). When false the check is skipped
  and one operator can run payroll end to end. The default keeps a6 behaviour for any
  caller that does not name it; an assembly passes its tenant policy explicitly.
- No migration and no column: payroll has no database separation CHECK and nothing is
  stamped on the row (the caller supplies the policy at each step). Payroll emits no
  lifecycle event or idempotency payload, so there is nothing to carry the flag.
- `separation_of_duties` must be a real `bool`: None, 0, "" and other values raise
  `TypeError` before any state is read or written, so a falsy non-bool never relaxes
  a check.
- The per-row database relaxation of `ck_supplier_payments_sod` is in payables 0.1.0a7
  (#1129); payroll has no database separation check and nothing is stamped.
- WIDENING, stated plainly: the owner's separation-of-duties checks in the three
  commands above are relaxed when the caller passes `separation_of_duties=False`.
  Synthetic distinct actor identifiers are not a substitute and must not be used.

## 0.1.0a6 — declared successor

- `adjust_liability`: an audited, signed adjustment of one liability (a write-down
  such as unclaimed salary, or an increase). Takes an adjustment reference,
  reason, evidence reference, an offset account ref and a timezone-aware
  `adjusted_at` that is not before the liability's `created_at`. The liability is
  locked FOR UPDATE. Refused: internal recoveries
  (`internal_recovery_not_adjustable`), a different currency, an amount off the
  currency scale or zero, a write-down larger than effective amount minus
  settled amount (`adjustment_exceeds_outstanding`), an offset equal to the
  liability account (`offset_is_liability_account`) and blank references. An
  identical replay returns the existing row; anything else under the same
  (tenant, liability, adjustment reference) is a `PayrollConflict`. Corrections
  are made forward with an opposite adjustment. No second person is required.
- `payroll_liabilities.amount` is NEVER changed (the run consequence compares it
  with the calculation). The signed sum lives in the new
  `payroll_liabilities.amount_adjusted` column and in the append-only
  `payroll_liability_adjustments` rows. `effective_liability_amount(liability)`
  is `amount + amount_adjusted`; `record_liability_settlement` and
  `reverse_liability_settlement` compare against it, and the status becomes
  `settled` when settled equals effective, `outstanding` when nothing is
  settled, else `partially_settled`. A fully written-down liability is
  `settled` and cannot be paid. The caller (ERP pin) must pay
  `effective_liability_amount`, not `amount`.
- `build_liability_adjustment_consequence`: read-only fact, kind
  `payroll_liability_adjustment`, keyed on the adjustment id. A write-down is
  Dr liability account / Cr offset; an increase is Dr offset / Cr liability
  account. The offset is the one frozen on the adjustment row; the posting date
  is caller-supplied and fingerprinted.
- Migration `py_0004_liability_adjustments` is additive: `amount_adjusted`
  (default 0), the amounts CHECK widened to the effective amount, and the new
  table (evidence trigger, forced RLS, SELECT/INSERT grants, `amount <> 0`,
  unique (tenant, liability, adjustment reference)). Downgrade refuses once an
  adjustment exists.
- Existing a3-a5 transitions, amounts and consequence fingerprints are
  byte-identical (regression tests pin the settlement and run facts).
- Publication and ERP adoption remain separate gates; `0.1.0a5` is unchanged.

## 0.1.0a5 — declared successor

- `reverse_liability_settlement`: governed reversal of one whole recorded
  settlement whose bank credit was returned (the money never reached the
  payee). Takes a normalized `return_ref` and a mandatory reason; the reverser
  must differ from the settlement's recorder; refuses `internal_recovery`
  liabilities and any liability the settlement does not fully settle. An
  identical replay (same return reference, reason and reverser) returns the
  existing reversal; anything else is a `PayrollConflict`; a settlement can be
  reversed once. `reversed_at` must be timezone-aware and not before the
  settlement's `recorded_at`. Re-paying under a reversed settlement reference is
  a conflict (use a new reference). The caller (ERP pin) must pass the cash GL
  of the settlement's original posted journal to the reversal fact.
- One transaction (flush-only): an append-only `payroll_settlement_reversals`
  row is inserted, the liability's settled amount is reduced by exactly the
  settlement amount and its status returns to `outstanding`, so it can be
  settled again under a new settlement reference. The settlement row is never
  updated.
- `build_settlement_reversal_consequence`: read-only fact Dr bank cash GL /
  Cr liability account (for example 2040), keyed on the reversal id with a
  stable fingerprint; the bank value date is caller-supplied and fingerprinted.
- Migration `py_0003_settlement_reversal` is additive (new table, append-only
  trigger, forced RLS, SELECT/INSERT grants). Downgrade refuses once a reversal
  exists.
- Existing a3/a4 transitions, amounts and consequence fingerprints are
  byte-identical (regression test pins the settlement fact formula).
- Publication and ERP adoption remain separate gates; `0.1.0a4` is unchanged.

## 0.1.0a4 — declared successor

- Deduction `settlement_mode` on `PayRuleInput` (default `external`, exactly the
  a3 behaviour) and on the stored rule, calculation line and liability:
  `internal_recovery` marks a deduction recovered against the employee's own
  balance (for example a staff loan asset such as 1410). Finalize books it as
  already recovered (settled, employee-side, against the asset account ref) and
  never creates an outstanding external payable. `record_liability_settlement`
  refuses internal recoveries. The run accounting consequence still credits the
  asset ref; it now also refuses internal-recovery drift. Internal recovery is
  valid only on deduction components.
- `cancel_payroll_run` (owner command): draft or approved -> cancelled with the
  actor and a mandatory reason, stored on the run (`cancelled_by_id`,
  `cancelled_at`, `cancel_reason`) as the audit record; refused once finalized;
  idempotent for the same actor and reason, conflicting otherwise.
- Mid-month leavers: when no assignment is active at the period end, the lookup
  uses min(period_end, assignment end date) of the assignment that ends inside
  the period; revision effectiveness is checked at that date. Stayers are
  unchanged.
- Migration `py_0002_mode_and_run_cancel` is additive (defaulted columns
  and CHECKs; existing rows backfill to `external`). Downgrade refuses once a
  cancelled run or an internal-recovery row exists.
- No change to existing transitions, separation of duties, amounts or the
  consequence fingerprint for external deductions.
- Publication and ERP adoption remain separate gates; `0.1.0a3` is unchanged.

## 0.1.0a3 — declared successor

- Add read-only GL consequence facts: `build_run_accounting_consequence`
  (finalized run: Dr earning and employer-liability expense; Cr deduction and
  employer liability accounts; Cr the revision net-payable account, e.g. the
  tenant-configured payroll payable) and `build_liability_settlement_consequence`
  (recorded settlement or statutory remittance: Dr liability account / Cr
  caller-supplied bank GL ref).
- Facts mirror the payables `AccountingConsequence` shape (source, posting_date,
  description, currency_code, entries, fingerprint; no exchange rate, so
  adopters refuse a non-functional currency). Settlement facts take an explicit
  bank-value `posting_date` covered by the fingerprint; run facts use the
  period end.
- Facts are frozen, balanced, tenant-scoped, deterministic and carry an
  idempotency key and content sha256. Refusals: run not finalized, no
  calculations, missing account refs, line/snapshot/liability drift (including component liabilities), bank ref
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
