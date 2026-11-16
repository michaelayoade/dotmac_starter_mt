# dotmac-content

`dotmac-content` owns tenant editorial plans, canonical content items,
provider-neutral authored variants, calendar placement, and ordered opaque
creative-file references.

It does not own audiences, recipients, publication requests or outcomes,
provider transport, credentials, stored bytes, actor authorization, people, or
generic tasks. Product assemblies authorize actors and resolve files before
calling this owner. Publishing consumes the immutable `ContentSnapshotV1`
value; it never receives an ORM object.

The package is a tenant-only installable module. Its independent `ct` lineage
owns five tables in `mod_content`, all with forced RLS. Services require an
explicit `TenantScope`, mutate and flush within the caller's transaction, and
never commit or roll back.

The product-first boundary and writer-retirement gates are recorded in
[`EXTRACTION.toml`](EXTRACTION.toml) and the
[content dossier](../../docs/inventories/content-extraction-dossier.md).

## Integration checklist

- Pin `dotmac-content` in the consuming assembly and register `dotmac_content.module` there.
- Bind the prerequisites declared by the manifest: `TENANT_SCOPE_CATALOG_V1.name`, `MODULE_DATABASE_ROLES_V1.name`.
- Add `dotmac_content.versions_dir()` to the assembly’s Alembic version locations and apply the declared lineage on its configured plane before calling persistence APIs.
- Resolve the declared Starter package dependencies with the assembly pins: `dotmac-kernel`.

### Executable reference

[Example: live catalog proves the complete forced rls plane](../../tests/test_content_isolation.py#L179). This uses the repository’s fixtures and assertions.

```sh
poetry run pytest tests/test_content_isolation.py::test_live_catalog_proves_the_complete_forced_rls_plane -q
```

Run this example in the hosted Postgres integration environment (`make test-integration` supplies the database configuration).
