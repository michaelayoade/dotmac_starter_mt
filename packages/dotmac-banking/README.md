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

## Integration checklist

- Pin `dotmac-banking` in the consuming assembly and register `dotmac_banking.module` there.
- Bind the prerequisites declared by the manifest: `TENANT_SCOPE_CATALOG_V1.name`, `MODULE_DATABASE_ROLES_V1.name`.
- Add `dotmac_banking.versions_dir()` to the assembly’s Alembic version locations and apply the declared lineage on its configured plane before calling persistence APIs.
- Resolve the declared Starter package dependencies with the assembly pins: `dotmac-kernel`.

### Executable reference

[Example: manifest allocates one tenant only banking lineage](../../tests/architecture/test_banking_module.py#L24). This uses the repository’s fixtures and assertions.

```sh
poetry run pytest tests/architecture/test_banking_module.py::test_manifest_allocates_one_tenant_only_banking_lineage -q
```

This example is covered by the hosted unit and architecture suite (`make test-unit`).
