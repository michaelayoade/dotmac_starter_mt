# dotmac-media-observations

Tenant-scoped, provider-neutral immutable observations of external media entity
state, hierarchy and aggregate performance metrics.

The package preserves what an external system reported, its source time, receipt
time, opaque installation and receipt references, normalization version, replay
identity and correction chain. Current entity, hierarchy and period-metric rows
are rebuildable projections over append-only facts.

Period reads and emitted analytics facts retain the observation fingerprint,
restatement link and every opaque transport receipt together with its own receipt
time. Analytics facts use a kind-matched entity, hierarchy or metric payload, so
the normalized state is not discarded at the reporting boundary. Exact Decimal
configuration values are type-preserving; values destined for fixed database
numeric columns are refused when they cannot fit without rounding or overflow.

It does not perform provider I/O, store raw payloads, import audiences, identify
people, assign Leads or customers, decide campaign effectiveness, or turn
provider conversion claims into official attribution or revenue.

See the [extraction dossier](EXTRACTION.toml),
[source audit](../../docs/inventories/media-observations-sources.md), and
[ADR-0033](../../docs/adr/0033-media-observations-own-provider-reports-not-attribution.md).

## Persistence

- tenant plane only: schema `mod_mediaobs`, lineage prefix `mo`;
- all twelve tables carry `tenant_id NOT NULL`, tenant-composite identity and
  forced RLS;
- declarations, facts, receipts, metric periods and reconciliation evidence are
  append-only by grants and trigger;
- `current_entities`, `current_hierarchy` and `current_metrics` are disposable
  projections rebuilt by `reconcile_projections`.

## Connector handoff

Connector plugins construct the typed commands exported here. They retain
credentials, endpoints, wire mapping, verification, polling, retry/checkpoint
and raw transport evidence in Integrator. The package exposes
`run_normalized_conformance` so a plugin can prove its domain handoff with a
provider-free fixture, independently of Integration SPI conformance. A producer
declares `normalized_observation_spi_version`; V1 is exported as
`CURRENT_NORMALIZED_OBSERVATION_SPI_VERSION`. Conformance fails closed on a
missing, malformed or incompatible version and returns the immutable normalized
facts proving stable observation ids, fingerprints and receipt provenance. The
report also carries the exact versioned node/metric declarations and observation
kinds exercised by the fixture, so an authorized certifier can see coverage
rather than infer it. Missing or malformed case factories, declarations and
commands fail with typed observation rejections.

## Adoption status

No product has adopted the package. Michael paused Backoffice and Sub adoption
on 2026-08-18; `EXTRACTION.toml` therefore has no contract consumer and no
product composition, publication or writer retirement is part of this change.

The uncomposed candidate at
`b30fc32a56bbd0b90fa834b9290c13ba113f03f0` passed exact-revision Observer
static, unit, architecture, clean-wheel, PostgreSQL and pinned Governance gates,
plus all 15 jobs in
[CI run 32230562002](https://github.com/michaelayoade/dotmac_starter_mt/actions/runs/32230562002).
That evidence proves candidate quality, not first-adopter or release readiness:
the hosted PR-only Engineering Standards job, Backoffice shadow/cutover proof
and any Mkt writer retirement remain deliberately unperformed.
