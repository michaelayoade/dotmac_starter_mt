# dotmac-operational-escalations

Owns **whether an operational escalation should exist, under which terms, and
who answered it** — across tickets, outages and staffed inboxes alike.

A policy is a stable identity plus **immutable versions**; an escalation binds
the exact version it was raised under, so it stays auditable after the policy
moves on. Cooldown is enforced as a refusal to raise, because whether an
escalation should exist is this module's decision.

It does not deliver anything. `dotmac-durable-timers` provides the scheduling
the intervals imply, and Messaging/Integrator performs delivery and owns its
outcomes — the source kept deliveries beside the policy, which made the
escalation owner the delivery owner too.

The tenant-only `oe` lineage owns `mod_escalations`.

## Integration checklist

- Pin `dotmac-operational-escalations` in the consuming assembly and register `dotmac_operational_escalations.module` there.
- Bind the prerequisites declared by the manifest: `TENANT_SCOPE_CATALOG_V1.name`, `MODULE_DATABASE_ROLES_V1.name`.
- Add `dotmac_operational_escalations.versions_dir()` to the assembly’s Alembic version locations and apply the declared lineage on its configured plane before calling persistence APIs.
- Resolve the declared Starter package dependencies with the assembly pins: `dotmac-kernel`.

### Executable reference

[Example: activating a version retires the one it replaces](../../tests/unit/test_operational_escalations_service.py#L91). This uses the repository’s fixtures and assertions.

```sh
poetry run pytest tests/unit/test_operational_escalations_service.py::test_activating_a_version_retires_the_one_it_replaces -q
```

This example is covered by the hosted unit and architecture suite (`make test-unit`).
