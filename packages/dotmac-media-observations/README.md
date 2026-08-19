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
provider-free fixture, independently of Integration SPI conformance.

## Adoption status

No product has adopted the package. Michael paused Backoffice and Sub adoption
on 2026-08-18; `EXTRACTION.toml` therefore has no contract consumer and no
product composition, publication or writer retirement is part of this change.

The uncomposed candidate passed exact-revision Observer static, unit,
architecture, clean-wheel and PostgreSQL gates and all jobs in
[CI run 32148037659](https://github.com/michaelayoade/dotmac_starter_mt/actions/runs/32148037659).
That evidence proves candidate quality, not first-adopter or release readiness:
the PR-only Engineering Standards gate, Backoffice shadow/cutover proof and any
Mkt writer retirement remain deliberately unperformed.
