# dotmac-usage-rating

Owns effective meter rating rules and immutable pre-tax obligations derived
from opaque usage references. It does not own usage collection, invoices, tax,
payments, revenue recognition, or GL entries. The tenant-only `ur` lineage owns
`mod_usage_rate`.

## Integration checklist

- Pin `dotmac-usage-rating` in the consuming assembly and register `dotmac_usage_rating.module` there.
- Bind the prerequisites declared by the manifest: `TENANT_SCOPE_CATALOG_V1.name`, `MODULE_DATABASE_ROLES_V1.name`.
- Add `dotmac_usage_rating.versions_dir()` to the assembly’s Alembic version locations and apply the declared lineage on its configured plane before calling persistence APIs.
- Resolve the declared Starter package dependencies with the assembly pins: `dotmac-kernel`.

### Executable reference

[Example: effective rule creates a pre tax obligation](../../tests/unit/test_usage_rating_service.py#L47). This uses the repository’s fixtures and assertions.

```sh
poetry run pytest tests/unit/test_usage_rating_service.py::test_effective_rule_creates_a_pre_tax_obligation -q
```

This example is covered by the hosted unit and architecture suite (`make test-unit`).
