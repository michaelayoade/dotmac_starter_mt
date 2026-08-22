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
