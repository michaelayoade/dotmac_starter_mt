# dotmac-analytics

`dotmac-analytics` owns declared aggregate metric evidence and a rebuildable
tenant projection. A domain calculates a metric from its authoritative rows;
this module validates the declared seam, preserves the aggregate observation,
and serves bounded latest, history and explicit-period comparison reads.

## What it owns

- immutable metric declaration snapshots;
- kernel-deduplicated source-event receipts and append-only aggregate observations;
- one deterministic, rebuildable winning-point projection;
- exact `Decimal` latest, history and explicit-period comparison reads; and
- projection digests plus append-only rebuild evidence.

Metric identities are versioned and owner-namespaced, such as
`billing.revenue.collected`. Declarations constrain the numeric kind, unit,
allowed granularities and bounded dimensions. Money points require a currency;
non-money points refuse one. A source may publish only metrics declared by that
same owner.

## What it does not own

It does not calculate source-domain metrics, read product tables, decide a
business lifecycle, or trigger consequences. It is not a raw-event warehouse,
data explorer, observability store, web-analytics collector, media-observation
store, attribution engine, dashboard designer, report scheduler or delivery
system. Those remain separate owners and synchronize typed data.

In particular:

- first-party events, sessions and funnels belong to `dotmac-web-analytics`;
- provider ad/social observations belong to `dotmac-media-observations`;
- cross-application transport belongs to Integrator; and
- a domain retains the code that decides what its metric means.

The complete boundary and source audit are in
[`analytics-sources.md`](../../docs/inventories/analytics-sources.md) and
[ADR-0036](../../docs/adr/0036-analytics-owns-projections-not-domain-facts.md).

## Persistence and transaction contract

The module is tenant-only. Every adopter installs its own `ay` lineage and owns
its own rows in `mod_analytics`; applications never share that schema or query
another application's database. Every table has `tenant_id NOT NULL`,
tenant-composite identity and same-migration ENABLE+FORCE RLS.

Replay, fingerprint comparison and the concurrent-key race are delegated to
`dotmac_kernel.idempotency`, the fleet's one at-most-once owner. The analytics
receipt is domain evidence, never a second replay ledger. Receipts,
observations, declaration snapshots and rebuild records are append-only by
grant and trigger. `metric_points` is the only mutable projection and can be
recreated from retained observations.

Ingestion and full repair share a transaction-scoped per-tenant write lock, so
a rebuild cannot race a newly promoted observation into stale projection
state.

Services accept the caller's SQLAlchemy `Session`, mutate and flush. They never
open, commit or roll back a transaction. The application-owned
`dotmac_kernel.db` boundary commits observations and their projection updates
together.

## Adoption

ERP is the qualifying product source and first cutover. Its domain computers
stay in ERP, emit typed batches in shadow, and retire the legacy
`OrgMetricSnapshot`/`MetricStore` path only after parity and repair evidence.
Backoffice is the candidate second independent adopter.
