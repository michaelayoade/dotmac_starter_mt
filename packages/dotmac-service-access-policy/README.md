# dotmac-service-access-policy

Owns per-service access-policy inputs and the desired allow/restrict/deny
decision. FUP, prepaid, collections and administrators supply observations;
Network Access enforces the decision and remains authoritative for device
state. The tenant-only `sap` lineage owns `mod_serviceaccess`.

## Integration checklist

- Pin `dotmac-service-access-policy` in the consuming assembly and register `dotmac_service_access_policy.module` there.
- Bind the prerequisites declared by the manifest: `TENANT_SCOPE_CATALOG_V1.name`, `MODULE_DATABASE_ROLES_V1.name`.
- Add `dotmac_service_access_policy.versions_dir()` to the assembly’s Alembic version locations and apply the declared lineage on its configured plane before calling persistence APIs.
- Resolve the declared Starter package dependencies with the assembly pins: `dotmac-kernel`.

### Executable reference

[Example: out of order and replay against postgresql](../../tests/test_service_access_policy_versioning.py#L159). This uses the repository’s fixtures and assertions.

```sh
poetry run pytest tests/test_service_access_policy_versioning.py::test_out_of_order_and_replay_against_postgresql -q
```

Run this example in the hosted Postgres integration environment (`make test-integration` supplies the database configuration).
