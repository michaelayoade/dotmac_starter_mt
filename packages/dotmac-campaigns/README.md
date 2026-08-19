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
ADR-0032 owns the boundary and the Sub-first/Backoffice-second sequence.

## Public shape

The top-level package exports commands, value contracts, lifecycle services,
read models, the manifest and lineage discovery. Product adapters should type
against the protocols in `dotmac_campaigns.contracts`; shipped fakes and
conformance checks live in `dotmac_campaigns.fakes`.

All mutators receive a caller-owned SQLAlchemy `Session`, mutate/flush and never
commit or roll back. The package is tenant-only in V1 and the independent `ca`
lineage creates every table in `mod_campaigns` with forced RLS.

## Release and adoption status

The implementation is part of Starter's integrated validation matrix but
remains deliberately unallowlisted and unreleased. The separate Durable Timers
release prerequisite is now satisfied by
`dotmac-durable-timers-v0.1.0a1`; Campaigns still imports only its typed port and
does not gain a scheduler, claim loop or retry engine.

Publication remains gated on Sub completing its kernel consent, idempotency and
outbox collision dispositions, composing the released timer, and rehearsing the
backfill, shadow comparison, sealed authority switch and local-writer
retirement. No Campaigns production authority changes in this Starter change.
