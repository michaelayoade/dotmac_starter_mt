# dotmac-finance

`dotmac-finance` owns one tenant's fixed-asset accounting subledger: asset
books, prospective depreciation, impairment and reversal, revaluation,
derecognition, and the immutable balanced accounting consequences of those
changes.

It does not own physical asset identity, condition, custody, maintenance or
disposal workflow. Those remain in `dotmac-assets`; an asset book keeps only an
opaque asset identifier and source evidence. It also does not own a chart of
accounts, fiscal periods, journal entries, tax books, payments or cash. Account
and period identifiers are opaque references supplied by an assembly.

The package starts from ERP's production fixed-assets implementation and parity
tests, with source defects removed. In particular, accounting state never
changes physical asset state, all child rows are directly tenant-scoped, service
functions flush but never commit or roll back, and every consequence is balanced
and append-only at the database boundary.

## Public shape

The top-level package exports typed commands, pure Decimal calculations,
flush-only lifecycle services, the module manifest and version. V1 supports
straight-line, declining-balance and double-declining depreciation. A method
without a complete typed measurement contract is refused rather than silently
treated as straight-line.

The independent `fn` lineage creates six tenant tables in `mod_finance`, all
with forced row-level security. The reference Starter assembly builds and proves
the package but does not install its lineage. ERP is the first candidate cutover.

See [`EXTRACTION.toml`](EXTRACTION.toml), the
[source inventory](../../docs/inventories/finance-asset-accounting-sources.md),
and [ADR-0048](../../docs/adr/0048-finance-owns-asset-books-and-accounting-consequences.md).
