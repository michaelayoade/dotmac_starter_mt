# dotmac-customers

`dotmac-customers` owns tenant customer account identity, the narrow customer
profile, and typed references to Party identities owned elsewhere.

It does not own authentication, contact reachability, addresses, qualification,
offers, prices, subscriptions, service lifecycle, billing, or network state.
The `cu` lineage owns `mod_customers`; every service requires `TenantScope` and
flushes inside the caller's transaction.

## Integration checklist

- Pin `dotmac-customers` in the consuming assembly and register `dotmac_customers.module` there.
- Bind the prerequisites declared by the manifest: `TENANT_SCOPE_CATALOG_V1.name`, `MODULE_DATABASE_ROLES_V1.name`.
- Add `dotmac_customers.versions_dir()` to the assembly’s Alembic version locations and apply the declared lineage on its configured plane before calling persistence APIs.
- Resolve the declared Starter package dependencies with the assembly pins: `dotmac-kernel`.

### Executable reference

[Example: account number is normalized and unique per tenant](../../tests/unit/test_customers_service.py#L58). This uses the repository’s fixtures and assertions.

```sh
poetry run pytest tests/unit/test_customers_service.py::test_account_number_is_normalized_and_unique_per_tenant -q
```

This example is covered by the hosted unit and architecture suite (`make test-unit`).
