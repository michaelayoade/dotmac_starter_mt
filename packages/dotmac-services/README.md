# dotmac-services

Owns the tenant service instance and its lifecycle transitions. Customer,
catalogue and qualification identities are opaque references; fulfillment,
network enforcement, subscriptions, rating and billing remain separate owners.
The tenant-only `se` lineage owns `mod_services`.

## Integration checklist

- Pin `dotmac-services` in the consuming assembly and register `dotmac_services.module` there.
- Bind the prerequisites declared by the manifest: `TENANT_SCOPE_CATALOG_V1.name`, `MODULE_DATABASE_ROLES_V1.name`.
- Add `dotmac_services.versions_dir()` to the assembly’s Alembic version locations and apply the declared lineage on its configured plane before calling persistence APIs.
- Resolve the declared Starter package dependencies with the assembly pins: `dotmac-kernel`.

### Executable reference

[Example: service status has one owned transition path](../../tests/unit/test_services_service.py#L51). This uses the repository’s fixtures and assertions.

```sh
poetry run pytest tests/unit/test_services_service.py::test_service_status_has_one_owned_transition_path -q
```

This example is covered by the hosted unit and architecture suite (`make test-unit`).
