# dotmac-network-inventory

Tenant-only owner of managed sites, nodes, interfaces, ports, VLANs,
capabilities, network admission, archive, and configuration-snapshot metadata.
It correlates Assets opaquely and never owns stock, durable physical lifecycle,
IP allocation, live observations, topology, incidents, commands, RADIUS, or
PON state. Status: audit-complete `0.1.0a1` candidate; no adopter yet.

## Integration checklist

- Pin `dotmac-network-inventory` in the consuming assembly and register `dotmac_network_inventory.module` there.
- Bind the prerequisites declared by the manifest: `TENANT_SCOPE_CATALOG_V1.name`, `MODULE_DATABASE_ROLES_V1.name`.
- Add `dotmac_network_inventory.versions_dir()` to the assembly’s Alembic version locations and apply the declared lineage on its configured plane before calling persistence APIs.
- Resolve the declared Starter package dependencies with the assembly pins: `dotmac-kernel`.

### Executable reference

[Example: network inventory admission is idempotent and archival is terminal](../../tests/unit/test_network_suite_services.py#L44). This uses the repository’s fixtures and assertions.

```sh
poetry run pytest tests/unit/test_network_suite_services.py::test_network_inventory_admission_is_idempotent_and_archival_is_terminal -q
```

This example is covered by the hosted unit and architecture suite (`make test-unit`).
