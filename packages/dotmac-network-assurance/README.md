# dotmac-network-assurance

Tenant-only owner of network incidents/outages, maintenance windows, impact
classification, notification evidence, SLA evidence, and escalation
recommendations. It never decides or closes product tickets/work orders and
does not own observations, topology, or delivery transport. Status:
audit-complete `0.1.0a1` candidate; no adopter yet.

## Integration checklist

- Pin `dotmac-network-assurance` in the consuming assembly and register `dotmac_network_assurance.module` there.
- Bind the prerequisites declared by the manifest: `TENANT_SCOPE_CATALOG_V1.name`, `MODULE_DATABASE_ROLES_V1.name`.
- Add `dotmac_network_assurance.versions_dir()` to the assembly’s Alembic version locations and apply the declared lineage on its configured plane before calling persistence APIs.
- Resolve the declared Starter package dependencies with the assembly pins: `dotmac-kernel`.

### Executable reference

[Example: assurance owns incident transitions and resolution evidence](../../tests/unit/test_network_suite_services.py#L254). This uses the repository’s fixtures and assertions.

```sh
poetry run pytest tests/unit/test_network_suite_services.py::test_assurance_owns_incident_transitions_and_resolution_evidence -q
```

This example is covered by the hosted unit and architecture suite (`make test-unit`).
