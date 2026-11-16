# dotmac-money-flows

## Integration checklist

- Pin `dotmac-money-flows` in the consuming assembly and register `dotmac_money_flows.module` there.
- Bind the prerequisites declared by the manifest: `MODULE_DATABASE_ROLES_V1.name`, `OUTBOX_RELAY_V1.name`.
- Add `dotmac_money_flows.versions_dir()` to the assembly’s Alembic version locations and apply the declared lineage on its configured plane before calling persistence APIs.
- Resolve the declared Starter package dependencies with the assembly pins: `dotmac-kernel`.

### Executable reference

[Example: manifest declares a tenant only money flows lineage](../../tests/architecture/test_money_flows_module.py#L536). This uses the repository’s fixtures and assertions.

```sh
poetry run pytest tests/architecture/test_money_flows_module.py::test_manifest_declares_a_tenant_only_money_flows_lineage -q
```

This example is covered by the hosted unit and architecture suite (`make test-unit`).

Tenant-plane composition of money flows over the published owners. The package
holds instance state only: a state, the policy and definition version it was
decided under, an optional deadline, a reason code and opaque references to the
owners' records. It stores no amount, currency or balance, owns no timer, lease,
retry or scanner, and never commits or rolls back.

This release skeleton declares the manifest, the instance-table allowlist, the
`mf` migration lineage (the Settle instance table) and the event-type constants.
Flow behaviour arrives separately.

Only an assembly imports this package. The Starter reference app does not
compose it; the intended consumer is the Sub thin-client deployment.
