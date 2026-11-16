# dotmac-usage

Owns immutable normalized meter observations, append-only corrections and
rebuildable aggregates. Raw AAA/RADIUS records, service lifecycle, rating,
invoicing and accounting remain outside. The tenant-only `us` lineage owns
`mod_usage`.

## Integration checklist

- Pin `dotmac-usage` in the consuming assembly and register `dotmac_usage.module` there.
- Bind the prerequisites declared by the manifest: `TENANT_SCOPE_CATALOG_V1.name`, `MODULE_DATABASE_ROLES_V1.name`.
- Add `dotmac_usage.versions_dir()` to the assembly’s Alembic version locations and apply the declared lineage on its configured plane before calling persistence APIs.
- Resolve the declared Starter package dependencies with the assembly pins: `dotmac-kernel`.

### Executable reference

[Example: observation correction and rebuildable projection](../../tests/unit/test_usage_service.py#L56). This uses the repository’s fixtures and assertions.

```sh
poetry run pytest tests/unit/test_usage_service.py::test_observation_correction_and_rebuildable_projection -q
```

This example is covered by the hosted unit and architecture suite (`make test-unit`).
