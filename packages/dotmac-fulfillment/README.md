# dotmac-fulfillment

`dotmac-fulfillment` owns the tenant-local saga ledger for making an accepted
commercial intent true: immutable ordered step definitions, append-only
participant attempts, idempotent asynchronous outcome receipts, derived partial
progress, explicit compensation requests and receipts, and reconciliation
wake-up requests. Every initial dispatch schedules a durable re-observation, so
a provider success followed by a lost callback converges from observation
rather than being marked failed by elapsed wall-clock time.

The package does not know what a participant does. Participant codes are an
open vocabulary declared by installed module manifests; the engine contains no
domain, hosting, ISP, registrar, panel, or provider branch. Product assemblies
adapt `ParticipantCommand` and `CompensationCommand` values to local domain
owners and feed typed outcomes back into this owner.

The module is tenant-only. Its six tables live in `mod_fulfillment`, carry a
non-null `tenant_id`, use composite tenant foreign keys, have enabled and forced
PostgreSQL row-level security, and are structurally append-only. Run progress is
derived from the latest attempt receipts rather than maintained in a mutable
status column.

Uncertain outcomes require a caller-supplied durable-timer adapter. The manifest
depends on `dotmac-durable-timers`, but the package imports no sibling module;
the assembly maps `ReobservationSchedule` to the released timer contract. It
likewise accepts a transactional publisher callback rather than owning provider
I/O, connector delivery attempts, leases, backoff, or dead-letter state.

Operator repair is an explicit contract rather than an unrestricted service
call. Redrive, compensation, and reviewed-terminal settlement each require a
canonical non-system actor and an assembly-supplied authorizer, then append a
manifest-declared kernel audit event in the same transaction. The repair queue
is derived from immutable attempts and receipts; repair never updates evidence.

The package is `audit-complete`, not adopted. Dotmac Cloud is the first candidate
consumer; Sub follows by retiring its synchronous provisioning loop and reaper,
not by adopting its orphaned ISP-bound saga tables.

## Composition

An adopter imports `dotmac_fulfillment.module`, installs `durable_timers`, adds
`dotmac_fulfillment.versions_dir()` to Alembic `version_locations`, binds the
declared kernel prerequisites, and runs the `fu` lineage in its own database.
Every mutation receives a caller-owned `Session`, flushes, and leaves commit or
rollback to the assembly transaction boundary.
