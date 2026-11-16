# dotmac-publishing

`dotmac-publishing` owns tenant publication intent after an assembly has frozen
an immutable snapshot and selected one or more opaque targets. It persists the
requested delivery time, one delivery per target, monotonic attempts,
deduplicated normalized observations and the aggregate publication state
derived from those deliveries.

The package is provider-neutral. It contains no provider enum, SDK, credential,
endpoint, OAuth flow, webhook verifier, connector retry/checkpoint or target
registry. Integrator and its connector plugins own those concerns. Publishing
stores opaque target, receipt and remote references and emits typed commands
through the kernel transactional outbox.

Scheduling is a typed port implemented by an assembly; the package does not
import the Durable Timers module or construct a worker session. Services require
an explicit `TenantScope`, mutate and flush the caller's session, and never
commit or roll back.

The a1 candidate is tenant-only and owns four tables in `mod_publishing`:

- `publication_releases`
- `publication_deliveries`
- `publication_attempts`
- `publication_observations`

All four tables have `tenant_id NOT NULL`, composite same-module foreign keys,
forced PostgreSQL RLS and exact tenant-role grants. The independent Alembic
lineage begins at `pb_0001_publishing`.

This package is audit-complete but unpublished, unallowlisted, uncomposed and
unadopted. Backoffice is the first candidate adopter. See
`docs/inventories/publishing-extraction-dossier.md` and
`packages/dotmac-publishing/EXTRACTION.toml`.

## Integration checklist

- Pin `dotmac-publishing` in the consuming assembly and register `dotmac_publishing.module` there.
- Bind the prerequisites declared by the manifest: `TENANT_SCOPE_CATALOG_V1.name`, `MODULE_DATABASE_ROLES_V1.name`, `IDEMPOTENCY_LEDGER_V1.name`, `OUTBOX_RELAY_V1.name`.
- Add `dotmac_publishing.versions_dir()` to the assembly’s Alembic version locations and apply the declared lineage on its configured plane before calling persistence APIs.
- Resolve the declared Starter package dependencies with the assembly pins: `dotmac-kernel`.

### Executable reference

[Example: live catalog proves the complete forced rls plane](../../tests/test_publishing_isolation.py#L183). This uses the repository’s fixtures and assertions.

```sh
poetry run pytest tests/test_publishing_isolation.py::test_live_catalog_proves_the_complete_forced_rls_plane -q
```

Run this example in the hosted Postgres integration environment (`make test-integration` supplies the database configuration).
