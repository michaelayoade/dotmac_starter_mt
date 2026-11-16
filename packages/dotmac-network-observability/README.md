# dotmac-network-observability

Tenant-only owner of collector-neutral network observations, measurements,
availability facts, alert evidence, and rebuildable health projections. It is
not the Prometheus/Mimir/Loki/Tempo/Grafana control plane and contains no SNMP,
UISP, router, OLT, ACS, or other provider client. Status: audit-complete
`0.1.0a1` candidate; no adopter yet.

## Integration checklist

- Pin `dotmac-network-observability` in the consuming assembly and register `dotmac_network_observability.module` there.
- Bind the prerequisites declared by the manifest: `TENANT_SCOPE_CATALOG_V1.name`, `MODULE_DATABASE_ROLES_V1.name`.
- Add `dotmac_network_observability.versions_dir()` to the assembly’s Alembic version locations and apply the declared lineage on its configured plane before calling persistence APIs.
- Resolve the declared Starter package dependencies with the assembly pins: `dotmac-kernel`.

### Executable reference

[Example: observations deduplicate and health and alerts are evidence driven](../../tests/unit/test_network_suite_services.py#L109). This uses the repository’s fixtures and assertions.

```sh
poetry run pytest tests/unit/test_network_suite_services.py::test_observations_deduplicate_and_health_and_alerts_are_evidence_driven -q
```

This example is covered by the hosted unit and architecture suite (`make test-unit`).
