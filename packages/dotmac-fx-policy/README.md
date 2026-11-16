# dotmac-fx-policy

Tenant-scoped ownership of effective FX-rate observations, their source
provenance, source-selection policy, and immutable determination evidence.

The module accepts observations from product-owned importers through typed
commands. It performs no provider network access and stores no provider
credentials. Kernel owns `Money`, `Currency`, and immutable exchange-rate value
types; Billing owns applied invoice snapshots; Accounting owns revaluation and
GL consequences; Tax owns tax decisions.

The package is audit-complete but uncomposed and unpublished. ERP is the first
candidate adopter through an explicit backfill, shadow comparison, one-writer
cutover, and retirement of its local `core_fx` decision paths.

## Integration checklist

- Pin `dotmac-fx-policy` in the consuming assembly and register `dotmac_fx_policy.module` there.
- Bind the prerequisites declared by the manifest: `TENANT_SCOPE_CATALOG_V1.name`, `MODULE_DATABASE_ROLES_V1.name`.
- Add `dotmac_fx_policy.versions_dir()` to the assembly’s Alembic version locations and apply the declared lineage on its configured plane before calling persistence APIs.
- Resolve the declared Starter package dependencies with the assembly pins: `dotmac-kernel`.

### Executable reference

[Example: latest effective direct observation is selected and evidenced](../../tests/unit/test_fx_policy_service.py#L69). This uses the repository’s fixtures and assertions.

```sh
poetry run pytest tests/unit/test_fx_policy_service.py::test_latest_effective_direct_observation_is_selected_and_evidenced -q
```

This example is covered by the hosted unit and architecture suite (`make test-unit`).
