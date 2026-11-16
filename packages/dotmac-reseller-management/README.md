# dotmac-reseller-management

Tenant-only reseller account identity, hierarchy, delegated commercial
authority, collaborator bindings and lifecycle. The module is product-first
from Sub's reseller account/user boundary.

The owner stores opaque Party-role, member and customer-account references. It
does not own Party or Customer rows, authentication, commissions, payouts,
invoices, catalog decisions, commercial agreements, or entitlements. Lifecycle
facts leave through the kernel outbox; delivery remains another owner's job.

The stateful lineage is `rm`, the schema is `mod_reseller`, and all four tables
carry non-null tenant identity, composite tenant foreign keys and forced RLS.

## Integration checklist

- Pin `dotmac-reseller-management` in the consuming assembly and register `dotmac_reseller_management.module` there.
- Bind the prerequisites declared by the manifest: `TENANT_SCOPE_CATALOG_V1.name`, `MODULE_DATABASE_ROLES_V1.name`, `OUTBOX_RELAY_V1.name`.
- Add `dotmac_reseller_management.versions_dir()` to the assembly’s Alembic version locations and apply the declared lineage on its configured plane before calling persistence APIs.
- Resolve the declared Starter package dependencies with the assembly pins: `dotmac-kernel`.

### Executable reference

[Example: child authority is an immutable subset of parent authority](../../tests/unit/test_reseller_management.py#L86). This uses the repository’s fixtures and assertions.

```sh
poetry run pytest tests/unit/test_reseller_management.py::test_child_authority_is_an_immutable_subset_of_parent_authority -q
```

This example is covered by the hosted unit and architecture suite (`make test-unit`).
