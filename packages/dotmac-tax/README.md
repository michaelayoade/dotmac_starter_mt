# dotmac-tax

`dotmac-tax` owns tenant tax policy data, effective-dated determinations,
statutory-report snapshots, filing obligations, and the tax-return lifecycle.

Authorities, jurisdictions, tax codes, rates, recognition bases, progressive
bands, report boxes, calendars, and due dates are operator data—not enums or
country constants. Products submit exact source facts; assemblies project
approved tax consequences through their accounting owner and perform any
authority transport outside this package.

The optional package owns the `tx` lineage and `mod_tax` schema. It imports no
sibling domain and never reads another application's database. See
`EXTRACTION.toml` and `docs/inventories/tax-sources.md`.
