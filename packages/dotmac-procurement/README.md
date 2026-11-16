# dotmac-procurement

`dotmac-procurement` owns one tenant's buyer-side decisions from an authorized
purchase requisition through a published sourcing event, immutable supplier
offers, evaluated/approved award and approved purchase-order commitment.

The package receives budget and approval results as digest-bound facts.  It
stores supplier, actor, item, project and receipt identities only as opaque
references.  Receipt observations update a rebuildable line-quantity projection
without writing Inventory.  All mutations use the caller's transaction and
flush without commit or rollback.

The authorization chain is exact: a sourced requisition's lines are preserved
through the sourcing snapshot, selected bid and purchase commitment, and each
requisition/award can produce only one purchase order.  Receipt batches validate
every line before changing any projected quantity.

V1 deliberately excludes annual budgets, statutory threshold policy, supplier
identity/prequalification, long-form contract administration, stock, assets,
supplier invoices, three-way matching, payments, journals, product work,
numbering, rendering, notifications and provider transport.

The full product-first evidence and cutover gate are in
[`EXTRACTION.toml`](EXTRACTION.toml),
[`docs/inventories/procurement-sources.md`](../../docs/inventories/procurement-sources.md)
and ADR-0050.

The independent `pc` lineage creates twelve forced-RLS tables in
`mod_procurement`.  Product assemblies pin the release, compose that lineage in
their own database and translate their local facts through this typed API.

## Integration checklist

- Pin `dotmac-procurement` in the consuming assembly and register `dotmac_procurement.module` there.
- Bind the prerequisites declared by the manifest: `TENANT_SCOPE_CATALOG_V1.name`, `MODULE_DATABASE_ROLES_V1.name`.
- Add `dotmac_procurement.versions_dir()` to the assembly’s Alembic version locations and apply the declared lineage on its configured plane before calling persistence APIs.
- Resolve the declared Starter package dependencies with the assembly pins: `dotmac-kernel`.

### Executable reference

[Example: live catalog proves the declared forced rls contract](../../tests/test_procurement_isolation.py#L138). This uses the repository’s fixtures and assertions.

```sh
poetry run pytest tests/test_procurement_isolation.py::test_live_catalog_proves_the_declared_forced_rls_contract -q
```

Run this example in the hosted Postgres integration environment (`make test-integration` supplies the database configuration).
