# dotmac-network-control

Tenant-only owner of provider-neutral network command intent, approval,
dispatch state, execution evidence, reconciliation, and recovery. Kernel owns
shared execution mechanics; Integrator connector plugins perform provider I/O.
The package contains no SDK, endpoint, credential, or provider switch. Status:
audit-complete `0.1.0a1` candidate; no adopter yet.

## Integration checklist

- Pin `dotmac-network-control` in the consuming assembly and register `dotmac_network_control.module` there.
- Bind the prerequisites declared by the manifest: `TENANT_SCOPE_CATALOG_V1.name`, `MODULE_DATABASE_ROLES_V1.name`.
- Add `dotmac_network_control.versions_dir()` to the assembly’s Alembic version locations and apply the declared lineage on its configured plane before calling persistence APIs.
- Resolve the declared Starter package dependencies with the assembly pins: `dotmac-kernel`.

### Executable reference

[Example: control command has one approved dispatch and terminal evidence](../../tests/unit/test_network_suite_services.py#L321). This uses the repository’s fixtures and assertions.

```sh
poetry run pytest tests/unit/test_network_suite_services.py::test_control_command_has_one_approved_dispatch_and_terminal_evidence -q
```

This example is covered by the hosted unit and architecture suite (`make test-unit`).
