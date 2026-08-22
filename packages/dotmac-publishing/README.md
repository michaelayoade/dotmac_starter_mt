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
