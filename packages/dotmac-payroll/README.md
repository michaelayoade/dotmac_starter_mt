# dotmac-payroll

`dotmac-payroll` owns tenant pay-component configuration, immutable structure
revisions, opaque employee assignments, payroll calculation evidence, and
employee/external liabilities with settlement observations.

Component codes and amounts are data. Calculations use typed fixed, input, and
percentage rules—never expression evaluation. Employee identity, attendance,
tax policy, bank transport, GL posting, and payment execution remain separate
owners connected by the adopting application.

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
