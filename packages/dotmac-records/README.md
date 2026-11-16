# dotmac-records

Tenant records-management authority after an explicit declaration of one exact
document version or domain artifact. It owns immutable file-plan and retention
definitions, trigger observations, legal holds, preservation/custody evidence
and disposition authorization.

The source domain keeps business meaning. `dotmac-files` keeps bytes and acts
only on a Records authorization. Approvals and Durable Timers remain separate
owners and are composed through typed observations/requests.

## Integration checklist

- Pin `dotmac-records` in the consuming assembly and register `dotmac_records.module` there.
- Bind the prerequisites declared by the manifest: `TENANT_SCOPE_CATALOG_V1.name`, `MODULE_DATABASE_ROLES_V1.name`.
- Add `dotmac_records.versions_dir()` to the assembly’s Alembic version locations and apply the declared lineage on its configured plane before calling persistence APIs.
- Resolve the declared Starter package dependencies with the assembly pins: `dotmac-kernel`.

### Executable reference

[Example: live catalog proves both independent owner contracts](../../tests/test_documents_records_isolation.py#L127). This uses the repository’s fixtures and assertions.

```sh
poetry run pytest tests/test_documents_records_isolation.py::test_live_catalog_proves_both_independent_owner_contracts -q
```

Run this example in the hosted Postgres integration environment (`make test-integration` supplies the database configuration).
