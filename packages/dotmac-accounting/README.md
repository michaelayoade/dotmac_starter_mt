# dotmac-accounting

`dotmac-accounting` owns a tenant's chart of accounts, fiscal years and
periods, open accounting dimensions, balanced journal posting, reversal and
immutable ledger/period evidence.

It is not a receivables, payables, tax, asset, inventory, banking, payment,
numbering or approval owner. Those domains submit typed facts through the
adopting assembly. The package is tenant-only, owns `mod_accounting` through
the independent `ac` lineage, and joins the caller's transaction without
commit or rollback.

See `EXTRACTION.toml`, ADR-0041 and
`docs/inventories/accounting-payables-sources.md` for the product-first
boundary.
