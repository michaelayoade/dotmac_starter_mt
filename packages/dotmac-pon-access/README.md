# dotmac-pon-access

Tenant-only owner of OLT/ONT/PON inventory, admission, assignment,
commissioning, desired service configuration, observations, backup evidence,
and reconciliation. Subscriber/commercial decisions, fiber plant, generic
inventory, and every vendor client remain outside. Status: audit-complete
`0.1.0a1` candidate; no adopter yet.

## Integration checklist

- Pin `dotmac-pon-access` in the consuming assembly and register `dotmac_pon_access.module` there.
- Bind the prerequisites declared by the manifest: `TENANT_SCOPE_CATALOG_V1.name`, `MODULE_DATABASE_ROLES_V1.name`.
- Add `dotmac_pon_access.versions_dir()` to the assembly’s Alembic version locations and apply the declared lineage on its configured plane before calling persistence APIs.
- Resolve the declared Starter package dependencies with the assembly pins: `dotmac-kernel`.

### Executable reference

[Example: pon capacity commissioning and desired state are one lifecycle](../../tests/unit/test_network_suite_services.py#L603). This uses the repository’s fixtures and assertions.

```sh
poetry run pytest tests/unit/test_network_suite_services.py::test_pon_capacity_commissioning_and_desired_state_are_one_lifecycle -q
```

This example is covered by the hosted unit and architecture suite (`make test-unit`).
