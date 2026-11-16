# dotmac-sites

`dotmac-sites` owns tenant site/page identity, immutable page revisions,
immutable composed site revisions, navigation, SEO, redirects and which
revision is ready for release.

It does not own stored bytes, form submissions, publication schedules or
outcomes, hosting, provider identity, credentials, domains, DNS, certificates,
analytics or Leads. File and form references are opaque. A local renderer
consumes the immutable `SiteReleaseV1`; a later assembly adapter may pass that
same value to `dotmac-publishing`, while Integrator alone performs remote I/O.

The package is tenant-only. Its independent `si` lineage owns five tables in
`mod_sites`, all with forced RLS. Revision evidence is protected by database
immutability triggers. Services require an explicit `TenantScope`, mutate and
flush within the caller's transaction, and never commit or roll back.

The greenfield proof and adoption gates are recorded in
[`EXTRACTION.toml`](EXTRACTION.toml) and the
[sites dossier](../../docs/inventories/sites-extraction-dossier.md).

## Integration checklist

- Pin `dotmac-sites` in the consuming assembly and register `dotmac_sites.module` there.
- Bind the prerequisites declared by the manifest: `TENANT_SCOPE_CATALOG_V1.name`, `MODULE_DATABASE_ROLES_V1.name`.
- Add `dotmac_sites.versions_dir()` to the assembly’s Alembic version locations and apply the declared lineage on its configured plane before calling persistence APIs.
- Resolve the declared Starter package dependencies with the assembly pins: `dotmac-kernel`.

### Executable reference

[Example: live catalog proves the complete forced rls plane](../../tests/test_sites_isolation.py#L196). This uses the repository’s fixtures and assertions.

```sh
poetry run pytest tests/test_sites_isolation.py::test_live_catalog_proves_the_complete_forced_rls_plane -q
```

Run this example in the hosted Postgres integration environment (`make test-integration` supplies the database configuration).
