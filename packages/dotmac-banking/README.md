# dotmac-banking

`dotmac-banking` is the tenant banking owner for configured institutions and
accounts, immutable bank-statement and cash-account observations, configurable
match policies, accepted allocations, and reconciliation snapshots.

It does not fetch a bank, know a provider, own cash-ledger postings, or decide
what an upstream receipt/payment means. Integrator or a product adapter records
typed observations; the adopting assembly maps each configured bank account to
an opaque cash-account reference and projects approved consequences through its
accounting owner.

The optional package owns the `bk` lineage and `mod_banking` schema. Services
use the caller's transaction and flush only. See `EXTRACTION.toml` and
`docs/inventories/banking-sources.md` for the product-first boundary.
