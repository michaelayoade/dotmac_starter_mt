# dotmac-referrals

`dotmac-referrals` is the tenant-plane owner of referral programmes, issued
codes, attribution, conversion evidence and provider-neutral reward requests.
It was extracted product-first from Sub's native referral path.

Party, Customers, Sales/Leads and Billing keep their own rows and decisions.
Every collaborator is an opaque reference; the module never resolves contact
data, creates a customer/lead, or applies a credit. Services receive a caller
`Session` and explicit `TenantScope`, mutate and flush, and never commit or roll
back.

## Service location

Referral capture V2 (`capture_referral_v2`) may carry an opaque
`service_location_ref`: the place the referred prospect would be served, as the
composing product names it. The module stores the token and nothing it resolves
to: no coordinates, no address text. V1 capture is unchanged.

## Integration checklist

- Pin `dotmac-referrals` in the consuming assembly and register `dotmac_referrals.module` there.
- Bind the prerequisites declared by the manifest: `TENANT_SCOPE_CATALOG_V1.name`, `MODULE_DATABASE_ROLES_V1.name`, `OUTBOX_RELAY_V1.name`.
- Add `dotmac_referrals.versions_dir()` to the assembly’s Alembic version locations and apply the declared lineage on its configured plane before calling persistence APIs.
- Resolve the declared Starter package dependencies with the assembly pins: `dotmac-kernel`.

### Executable reference

[Example: manifest matches the immutable namespace allocation](../../tests/architecture/test_referrals_module.py#L24). This uses the repository’s fixtures and assertions.

```sh
poetry run pytest tests/architecture/test_referrals_module.py::test_manifest_matches_the_immutable_namespace_allocation -q
```

This example is covered by the hosted unit and architecture suite (`make test-unit`).
