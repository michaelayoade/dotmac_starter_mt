# dotmac-campaigns

`dotmac-campaigns` owns provider-neutral outbound campaign progression for one
tenant: immutable revisions, audience/recipient snapshots, ordered one-time and
nurture steps, consent receipts, delivery intents, normalized observations,
unsubscribe/response evidence, rebuildable counters and repair.

It does not discover an audience. A product submits typed candidate facts and
keeps Party, customer, financial and cohort policy. It does not render a
template, select provider credentials, run a scheduler, deliver a message,
create a conversation or create a Lead. Assemblies bind Template Studio,
Durable Timers and provider-neutral sender ports; kernel consent,
idempotency and outbox remain the corresponding universal owners.

The complete source and retirement evidence is in
[`EXTRACTION.toml`](EXTRACTION.toml) and
[`docs/inventories/campaigns-sources.md`](../../docs/inventories/campaigns-sources.md).
ADR-0056 owns the boundary and the Sub-first/Backoffice-second sequence.

## Public shape

The top-level package exports commands, value contracts, lifecycle services,
read models, the manifest and lineage discovery. Product adapters should type
against the protocols in `dotmac_campaigns.contracts`; shipped fakes and
conformance checks live in `dotmac_campaigns.fakes`.

All mutators receive a caller-owned SQLAlchemy `Session`, mutate/flush and never
commit or roll back. The package is tenant-only in V1 and the independent `ca`
lineage creates every table in `mod_campaigns` with forced RLS.

## Integration checklist

- Pin `dotmac-campaigns` in the consuming assembly and register `dotmac_campaigns.module` there.
- Bind the prerequisites declared by the manifest: `TENANT_SCOPE_CATALOG_V1.name`, `MODULE_DATABASE_ROLES_V1.name`, `IDEMPOTENCY_LEDGER_V1.name`, `OUTBOX_RELAY_V1.name`.
- Add `dotmac_campaigns.versions_dir()` to the assembly’s Alembic version locations and apply the declared lineage on its configured plane before calling persistence APIs.
- Resolve the declared Starter package dependencies with the assembly pins: `dotmac-kernel`.

### Executable reference

[Example: live catalog proves forced rls and composite tenant contract](../../tests/test_campaigns_isolation.py#L207). This uses the repository’s fixtures and assertions.

```sh
poetry run pytest tests/test_campaigns_isolation.py::test_live_catalog_proves_forced_rls_and_composite_tenant_contract -q
```

Run this example in the hosted Postgres integration environment (`make test-integration` supplies the database configuration).
