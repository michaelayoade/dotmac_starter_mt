# dotmac-payroll

`dotmac-payroll` owns tenant pay-component configuration, immutable structure
revisions, opaque employee assignments, payroll calculation evidence, and
employee/external liabilities with settlement observations.

Component codes and amounts are data. Calculations use typed fixed, input, and
percentage rules—never expression evaluation. Employee identity, attendance,
tax policy, bank transport, GL posting, and payment execution remain separate
owners connected by the adopting application.

Version `0.1.0a7` makes separation of duties optional (D-9). `approve_payroll_run`,
`finalize_payroll_run` and `reverse_liability_settlement` take a keyword-only
`separation_of_duties` (default `False`: separation is OFF by default, per Michael's
no-compulsory-two-person ruling; a caller that wants two-person approval passes
`True`); an assembly passes its tenant policy explicitly. There is no migration:
payroll has no database separation check and nothing is stamped on the row.

The optional package owns the `py` lineage and `mod_payroll` schema. See
`EXTRACTION.toml` and `docs/inventories/payroll-sources.md` for the boundary.


The declared `0.1.0a2` successor adds scalar run totals and payment parity.
`PayrollAmountSnapshot` carries an opaque calculation reference, `Currency`, and
independent `Decimal` gross/deduction/net amounts. `aggregate_payroll_run_totals`
returns frozen `PayrollRunTotals`; it sums rather than rederives net, rejects
non-finite/unscaled amounts, duplicate calculation references and mixed
currencies, and never rounds. Empty input yields zero totals in the supplied
currency. Signed amounts and exact extra trailing zeroes remain valid.

`validate_payment_total(run_totals=..., currency=..., payment_amounts=...)`
compares the exact sum of net-only payment item amounts with run net. Its input
contains no employee, bank or salary-component details. Payment selection,
export format, authorization and execution remain with the adopting owner.

The pure implementation is `dotmac_payroll.totals`, with public names also
exported at package root. Importing any package submodule still runs the existing
root initializer, which imports the manifest, ORM models and service: this is
not an ORM-free import promise. No database session or engine is constructed by
the totals API. Stateful `read_payroll_run_totals` flushes and reads tenant/run
scalars, then calls that same aggregate; approval consumes its count. No stored
run-total fields, migrations, shadow runs or new authority are introduced.

ERP adoption must pin a published successor and supply persisted scalar facts
after flush. This declaration is not publication or ERP adoption evidence.

The declared `0.1.0a3` successor adds read-only GL consequence facts
(`dotmac_payroll.consequences`). `PayrollAccountingConsequence` mirrors the
payables `AccountingConsequence` field names: `source`
(`PayrollConsequenceSource`: owner `payroll`, document_kind `payroll_run` or
`payroll_liability_settlement`, document_id, version `1`, fingerprint),
`posting_date`, `description`, `currency_code`, `entries`
(`PayrollAccountingEntry`: account_ref, debit, credit, description,
dimension_refs) and `fingerprint` (sha256 of the content). Extras:
`minor_units` and `idempotency_key`.

`build_run_accounting_consequence` turns a finalized run into one balanced fact
(Dr earning and employer-liability expense; Cr deduction/employer liability
accounts; Cr the configured net payable account) dated at the run period end,
and refuses if component liability rows drift from the calculation lines.
`build_liability_settlement_consequence` turns a recorded settlement or
statutory remittance into Dr liability account / Cr a bank GL ref, dated by an
explicit caller-supplied `posting_date` (the bank value date, included in the
fingerprint). Both call `db.flush()` and take no lock: finalized runs are
immutable.

Payroll stores no exchange rate, so the facts have no `exchange_rate` and
adopters must refuse a non-functional currency. Payroll writes no journals,
moves no money and calls no provider; there is no migration. This declaration
is not publication evidence.

The declared `0.1.0a4` successor adds a per-rule deduction `settlement_mode`
(`external`, the default, or `internal_recovery`), the `cancel_payroll_run`
owner command and mid-month-leaver assignment lookup at
min(period end, assignment end). An internal recovery is recorded at finalize as
already recovered against the employee reference and the asset account ref (for
example a staff loan); no outstanding external payable is created,
`record_liability_settlement` refuses it, and the run consequence still credits
the asset ref. Cancelling a draft or approved run stores actor, instant and a
mandatory reason; a finalized run cannot be cancelled. The additive migration
is `py_0002_mode_and_run_cancel`. This declaration is not publication
evidence.

The declared `0.1.0a5` successor adds `reverse_liability_settlement`, the
governed correction for a bank-returned salary credit: whole settlement only,
mandatory bank return reference and reason, a reverser different from the
recorder, refused for internal recoveries and for liabilities the settlement
does not fully settle. It inserts an append-only reversal row and reopens the
liability (`outstanding`, settled amount reduced) so the payee can be paid again
under a new settlement reference. `build_settlement_reversal_consequence`
returns the balanced fact Dr bank cash GL / Cr liability account for the
assembly to post. The additive migration is `py_0003_settlement_reversal`; its
downgrade refuses once a reversal exists. This declaration is not publication
evidence.

The declared `0.1.0a6` successor adds `adjust_liability`, an audited signed
adjustment of one liability (for example writing down unclaimed salary). The
adjustment lives in the append-only `payroll_liability_adjustments` table and
the `amount_adjusted` column; `payroll_liabilities.amount` is never changed, so
the run consequence still matches its calculation. `effective_liability_amount`
(`amount + amount_adjusted`) is what settlement compares against and what a
payout adapter must pay. `build_liability_adjustment_consequence` returns the
balanced fact (write-down: Dr liability account / Cr offset; increase: the
reverse). No second person is required; corrections are made forward with an
opposite adjustment. The additive migration is `py_0004_liability_adjustments`;
its downgrade refuses once an adjustment exists. This declaration is not
publication evidence.
