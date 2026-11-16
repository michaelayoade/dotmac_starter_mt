# dotmac-compliance-reporting

Product-first from Sub's NCC reporting pack, this package owns tenant
obligations, immutable classification revisions, exact evidence-pack snapshots,
filing submissions and regulator acknowledgements. Missing source facts remain
explicitly missing; they are never fabricated.

Source domains retain their facts, Files retains bytes, rendering retains
documents, Durable Timers schedules and Integrator transports.

## Integration checklist

- Pin `dotmac-compliance-reporting` in the consuming assembly and register `dotmac_compliance_reporting.module` there.
- Bind the prerequisites declared by the manifest: `TENANT_SCOPE_CATALOG_V1.name`, `MODULE_DATABASE_ROLES_V1.name`.
- Add `dotmac_compliance_reporting.versions_dir()` to the assembly’s Alembic version locations and apply the declared lineage on its configured plane before calling persistence APIs.
- Resolve the declared Starter package dependencies with the assembly pins: `dotmac-kernel`.

### Executable reference

[Example: pack preserves explicit missing sections and exact digests](../../tests/unit/test_compliance_reporting.py#L50). This uses the repository’s fixtures and assertions.

```sh
poetry run pytest tests/unit/test_compliance_reporting.py::test_pack_preserves_explicit_missing_sections_and_exact_digests -q
```

This example is covered by the hosted unit and architecture suite (`make test-unit`).
