# dotmac-payables

`dotmac-payables` owns tenant supplier invoices, credit notes, recognized
liabilities, due-date payment obligations, credit applications and immutable
settlement/accounting observations.

Version `0.1.0a2` is declared and unpublished. It allows historical or
imported supplier invoices and credit notes whose supplier-issued document
number was never captured to persist that absence as NULL. New documents
created through the service contracts still require the supplier document
number, and internal document numbers remain required in all cases.

It does not own supplier identity, procurement, approval, tax policy,
inventory, Accounting journals, bank details or payment execution. An assembly
translates its provider-neutral accounting consequence and supplies settlement
facts from the payment owner. The package owns `mod_payables` through the
independent `pa` lineage and never commits or rolls back the caller session.

See `EXTRACTION.toml`, ADR-0042 and
`docs/inventories/accounting-payables-sources.md` for the product-first
boundary.
