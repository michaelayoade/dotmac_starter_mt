# dotmac-workforce

Owns workforce teams, skills, memberships, worker skill evidence, shifts,
availability, capacity, and dispatch decisions. People owns employment and
identity; payroll/leave/attendance consequences, inbox presence, work-order
lifecycle, and route execution remain outside. The tenant-only `wf` lineage
owns `mod_workforce`.

## Integration checklist

- Pin `dotmac-workforce` in the consuming assembly and register `dotmac_workforce.module` there.
- Bind the prerequisites declared by the manifest: `TENANT_SCOPE_CATALOG_V1.name`, `MODULE_DATABASE_ROLES_V1.name`.
- Add `dotmac_workforce.versions_dir()` to the assembly’s Alembic version locations and apply the declared lineage on its configured plane before calling persistence APIs.
- Resolve the declared Starter package dependencies with the assembly pins: `dotmac-kernel`.

### Executable reference

[Example: skill and availability gate dispatch](../../tests/unit/test_workforce_service.py#L55). This uses the repository’s fixtures and assertions.

```sh
poetry run pytest tests/unit/test_workforce_service.py::test_skill_and_availability_gate_dispatch -q
```

This example is covered by the hosted unit and architecture suite (`make test-unit`).
