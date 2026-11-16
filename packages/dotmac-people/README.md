# dotmac-people

`dotmac-people` owns a tenant's narrow employment directory: employee
relationships to kernel Party identities, organization catalogues, positions,
temporal assignments, and date-aware reporting resolution.

It does not own identity, credentials, RBAC, payroll, compensation, attendance,
leave, discipline, training, files, notifications, or external provisioning.
See `EXTRACTION.toml` and
`docs/inventories/people-directory-sources.md` for the product-first boundary.

The package is an installable tenant-plane module. Its `pe` migration lineage
owns `mod_people`; services take an explicit `TenantScope`, mutate and flush,
and never commit or roll back.

## Integration checklist

- Pin `dotmac-people` in the consuming assembly and register `dotmac_people.module` there.
- Bind the prerequisites declared by the manifest: `TENANT_SCOPE_CATALOG_V1.name`, `MODULE_DATABASE_ROLES_V1.name`, `PARTY_PERSON_CATALOG_V1.name`.
- Add `dotmac_people.versions_dir()` to the assembly’s Alembic version locations and apply the declared lineage on its configured plane before calling persistence APIs.
- Resolve the declared Starter package dependencies with the assembly pins: `dotmac-kernel`.

### Executable reference

[Example: every table is forced and has exactly the tenant policy](../../tests/test_people_isolation.py#L214). This uses the repository’s fixtures and assertions.

```sh
poetry run pytest tests/test_people_isolation.py::test_every_table_is_forced_and_has_exactly_the_tenant_policy -q
```

Run this example in the hosted Postgres integration environment (`make test-integration` supplies the database configuration).
