# dotmac-network-access

Tenant-only owner of provider-neutral access-policy projections,
authentication/accounting observations, active sessions, reconciliation, and
drift evidence. Products retain commercial eligibility and IPAM retains address
assignment; Integrator owns RADIUS/router transport. Status: audit-complete
`0.1.0a1` candidate; no adopter yet.

## Integration checklist

- Pin `dotmac-network-access` in the consuming assembly and register `dotmac_network_access.module` there.
- Bind the prerequisites declared by the manifest: `TENANT_SCOPE_CATALOG_V1.name`, `MODULE_DATABASE_ROLES_V1.name`.
- Add `dotmac_network_access.versions_dir()` to the assembly’s Alembic version locations and apply the declared lineage on its configured plane before calling persistence APIs.
- Resolve the declared Starter package dependencies with the assembly pins: `dotmac-kernel`.

### Executable reference

[Example: access projection and accounting keep session evidence](../../tests/unit/test_network_suite_services.py#L504). This uses the repository’s fixtures and assertions.

```sh
poetry run pytest tests/unit/test_network_suite_services.py::test_access_projection_and_accounting_keep_session_evidence -q
```

This example is covered by the hosted unit and architecture suite (`make test-unit`).
