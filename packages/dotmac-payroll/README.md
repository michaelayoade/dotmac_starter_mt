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
