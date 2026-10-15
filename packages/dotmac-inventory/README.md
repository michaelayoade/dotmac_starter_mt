# dotmac-inventory

Tenant-local inventory ownership for Dotmac products: SKU and warehouse
identity, immutable movements, rebuildable stock/value balances, reservations,
lot/serial traceability, and valuation facts.

The package is built in Starter but not composed into the reference assembly.
ERP is the qualifying source and first candidate adopter. See ADR-0036 and
`EXTRACTION.toml` for the authority and cutover boundary.

The unreleased 0.2.0a1 candidate requires Rust 1.90.0, a C compiler and make
when built from source. Its private extension statically bundles checksum-pinned
libmpdec 4.0.1 for sequential ledger reductions with Python Decimal context
semantics. Database queries, tenancy, reservations and transactions remain in
the Python service. Hosted artifact and parity qualification gates release.
