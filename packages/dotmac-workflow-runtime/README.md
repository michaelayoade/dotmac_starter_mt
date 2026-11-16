# dotmac-workflow-runtime

This module owns tenant user-authored workflow execution instances, ordered
checkpoints, finite leases, retries and explicit repair evidence. ERP's generic
execution behavior is the product-first source.

Definitions, subjects and outputs are opaque versioned references. Assemblies
execute domain/provider intents and return observations; this package performs
no domain mutation, transport, scheduling or arbitrary code execution.

## Integration checklist

- Pin `dotmac-workflow-runtime` in the consuming assembly and register `dotmac_workflow_runtime.module` there.
- Bind the prerequisites declared by the manifest: `TENANT_SCOPE_CATALOG_V1.name`, `MODULE_DATABASE_ROLES_V1.name`.
- Add `dotmac_workflow_runtime.versions_dir()` to the assembly’s Alembic version locations and apply the declared lineage on its configured plane before calling persistence APIs.
- Resolve the declared Starter package dependencies with the assembly pins: `dotmac-kernel`.

### Executable reference

[Example: checkpoints are claimed in order and complete the execution](../../tests/unit/test_workflow_runtime.py#L94). This uses the repository’s fixtures and assertions.

```sh
poetry run pytest tests/unit/test_workflow_runtime.py::test_checkpoints_are_claimed_in_order_and_complete_the_execution -q
```

This example is covered by the hosted unit and architecture suite (`make test-unit`).
