# dotmac-documents

Tenant-controlled document identity, immutable content versions, exact-version
renditions, document-scoped access/collaboration, acknowledgements and a
Documents-owned lifecycle.

Bytes stay in `dotmac-files`; approval verdicts stay in `dotmac-approvals`;
scheduled wake-ups stay in `dotmac-durable-timers`; record retention, holds and
disposition stay in `dotmac-records`. This package imports none of them. A
product assembly passes opaque ids, checksums and typed observations across the
boundaries.

## Integration checklist

- Pin `dotmac-documents` in the consuming assembly and register `dotmac_documents.module` there.
- Bind the prerequisites declared by the manifest: `TENANT_SCOPE_CATALOG_V1.name`, `MODULE_DATABASE_ROLES_V1.name`.
- Add `dotmac_documents.versions_dir()` to the assembly’s Alembic version locations and apply the declared lineage on its configured plane before calling persistence APIs.
- Resolve the declared Starter package dependencies with the assembly pins: `dotmac-kernel`.

### Executable reference

[Example: live catalog proves both independent owner contracts](../../tests/test_documents_records_isolation.py#L127). This uses the repository’s fixtures and assertions.

```sh
poetry run pytest tests/test_documents_records_isolation.py::test_live_catalog_proves_both_independent_owner_contracts -q
```

Run this example in the hosted Postgres integration environment (`make test-integration` supplies the database configuration).
