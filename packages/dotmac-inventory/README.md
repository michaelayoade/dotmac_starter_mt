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

## Integration checklist

- Pin `dotmac-inventory` in the consuming assembly and register `dotmac_inventory.module` there.
- Bind the prerequisites declared by the manifest: `TENANT_SCOPE_CATALOG_V1.name`, `MODULE_DATABASE_ROLES_V1.name`.
- Add `dotmac_inventory.versions_dir()` to the assembly’s Alembic version locations and apply the declared lineage on its configured plane before calling persistence APIs.
- Resolve the declared Starter package dependencies with the assembly pins: `dotmac-kernel`.

### Executable reference

[Example: manifest matches the immutable namespace allocation](../../tests/architecture/test_inventory_module.py#L20). This uses the repository’s fixtures and assertions.

```sh
poetry run pytest tests/architecture/test_inventory_module.py::test_manifest_matches_the_immutable_namespace_allocation -q
```

This example is covered by the hosted unit and architecture suite (`make test-unit`).
