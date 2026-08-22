# ADR-0043: Analytics owns declared projections, never source-domain facts

- **Status:** Accepted for the first module slice
- **Date:** 2026-08-18
- **Decision owner:** Dotmac Starter architecture
- **Source evidence:** [`analytics-sources.md`](../inventories/analytics-sources.md)

## Context

Dotmac products contain many things called analytics: ERP metric snapshots and
finance reports, Sub KPI tables and operational reports, CRM dashboards,
Academy learner analytics, Mkt provider aggregates, and the copied
cross-domain `dotmac-insights` application. Treating their names as proof of
one owner would create a package allowed to query every business table and to
re-decide every domain's facts.

ERP has one narrower, qualifying, production-used implementation:
`OrgMetricSnapshot` plus `MetricStore`, scheduled product-side computers and
real dashboard/coach readers. It proves the need for durable scalar projections
and latest/history/comparison reads. It does not prove that a shared module
should own the calculations, domain tables, dashboards, files or delivery.

## Decision

Create tenant-only `dotmac-analytics` as the owner of **declared aggregate
metric evidence and rebuildable projections**.

A metric code/version has one named source-domain owner. The owner calculates
the metric from its own authoritative rows and publishes a typed aggregate
fact. Analytics validates the declaration, records immutable source identity
and exact numeric evidence, updates a rebuildable point projection, and offers
bounded reads. It never queries the source tables and never interprets a metric
as permission to mutate a domain.

### One metric owner

Metric codes are owner-namespaced (`billing.*`, `sales.*`, `ticketing.*`). An
immutable declaration names:

- owner code and metric code/version;
- display label, numeric value kind and unit;
- supported time granularities; and
- a bounded set of enum, boolean or opaque-reference dimensions.

An installed registry rejects duplicate identities. A source cannot publish a
metric declared by another owner. A new meaning or shape is a new schema
version; an activated declaration never changes in place.

### Evidence before projection

One source event has stable `(tenant, source_owner, source_event_id)` identity.
Replay, canonical fingerprint comparison and the concurrent-key race belong to
`dotmac_kernel.idempotency` (hard rule 23). Exact replay returns the original
analytics receipt id from that ledger; reuse with changed content raises a
typed conflict. The analytics receipt is immutable domain evidence for the
accepted batch, never a second replay mechanism.

Accepted points are append-only. `metric_points` is the only mutable analytical
projection. Corrections are new source events; a deterministic
`(observed_at, received_at, observation_id)` rank selects the winner for one
metric/version/period/granularity/dimension coordinate. Rebuild derives the
same winners from retained observations and records before/after digests.
Currency is part of a money series' selector digest, so values in different
currencies neither collide nor compare.

Ingestion and full projection repair take the same transaction-scoped advisory
lock derived from the tenant id. The lock serializes only analytics mutations
for that tenant; it prevents a repair from deleting a concurrently promoted
winner and committing a stale reconstruction.

### Aggregate-only V1

V1 accepts exact `NUMERIC(38,12)` values only. It has no free-form JSON value,
raw event payload, arbitrary SQL, source-table registry or subject identity.
Dimensions are declared and bounded; opaque references reject spaces, email
shapes and unbounded text. Money requires an ISO currency code and non-money
values refuse one.

This is not a warehouse for copied product rows. It is the durable projection
side of an explicit typed synchronization boundary.

## Boundaries

The module does not own:

- any domain fact, calculation, lifecycle or consequence;
- first-party web observations, sessions or funnels;
- provider media/ad/social observations;
- attribution, experiments or causal credit;
- application/Prometheus observability;
- persisted dashboards, saved analyses, report schedules or delivery in V1;
- report rendering, template content or stored files; or
- connector clients, credentials, endpoints, webhooks, retry or checkpoints.

The declaring product/module computes the aggregate. The application assembly
translates it into the analytics command. Cross-application delivery uses a
versioned API/webhook and typed deduplicated observation; Integrator may carry
the transport but never writes the product's domain tables. The module imports
neither sibling packages nor an assembly.

## Persistence and tenancy

`dotmac-analytics` is stateful, tenant-only, and allocated:

- owner/manifest code: `analytics`;
- schema: `mod_analytics`;
- migration prefix: `ay`;
- branch label: `analytics`.

Every table has `tenant_id UUID NOT NULL`, tenant-composite identity and
same-migration ENABLE+FORCE RLS. Receipts, observations, declaration snapshots,
and rebuild evidence are append-only by grants and trigger.
`metric_points` is mutable because it is explicitly rebuildable. The caller's
kernel-owned transaction commits or rolls back observations and their point
updates together; module services only mutate and flush.

There is no platform plane. No named control-plane adopter needs business
analytics rows today, and a speculative second plane would weaken ADR-0023.

## Product-first migration

ERP is the qualifying source and first cutover. Its existing computers remain
ERP-owned domain calculators but emit typed analytics batches during shadow.
Readers move only after point parity and repair are proven. The old
`OrgMetricSnapshot`/`MetricStore` writer and reader paths are then retired one
family at a time. Backoffice is candidate consumer two on an exact released
pin.

The source defects are not ported: no RLS, implicit strings, free JSON values,
overwrite without source evidence, float percentage output, and clock-derived
prior periods. [`analytics-sources.md`](../inventories/analytics-sources.md)
contains the parity and retirement matrix.

## Deferred decision

ERP's analysis cubes, report definitions/instances/schedules, and Insights'
data explorer do not move in this slice. A later audit must determine whether
saved analytical views belong here and whether report execution is a separate
`dotmac-reporting` owner. It must compose, rather than absorb, Files, Template
Studio, Durable Timers and Integrator delivery.

## Consequences

- Cross-domain dashboards can read one local projection without acquiring
  authority over source domains.
- Recalculation is explainable: the winning point links to immutable evidence.
- A source event cannot silently rewrite history, and drift is repairable.
- Domains retain the code that decides what their metrics mean.
- General analytics and web analytics remain separate owners joined by typed
  data, not imports or shared tables.
