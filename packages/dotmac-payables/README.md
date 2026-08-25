# dotmac-payables

`dotmac-payables` owns tenant supplier invoices, credit notes, recognized
liabilities, due-date payment obligations, credit applications and immutable
settlement/accounting observations.

It does not own supplier identity, procurement, approval, tax policy,
inventory, Accounting journals, bank details or payment execution. An assembly
translates its provider-neutral accounting consequence and supplies settlement
facts from the payment owner. The package owns `mod_payables` through the
independent `pa` lineage and never commits or rolls back the caller session.

See `EXTRACTION.toml`, ADR-0042 and
`docs/inventories/accounting-payables-sources.md` for the product-first
boundary.

