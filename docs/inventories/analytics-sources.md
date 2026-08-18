# `dotmac-analytics` product-first source audit

**Audit date:** 2026-08-18
**Decision:** ERP-first metric projection extraction
**Package:** tenant-only `dotmac-analytics`
**First cutover:** `dotmac_erp`; `dotmac_backoffice` is candidate consumer two

This is the mandatory ADR-0006 product-first inventory for a reusable general
analytics owner. It does not treat every route named `analytics`, every product
dashboard, or every SQL aggregate as one capability. The audited contract is
narrower: accept a typed aggregate fact from its named domain owner, preserve
its source identity, maintain a rebuildable projection, and provide bounded
latest/history/comparison reads without querying the source owner's tables.

## Pinned revisions

| Repository | Revision | Audit reading |
|---|---:|---|
| `dotmac_starter_mt` | `97cb096d9dd19ac66829a3a259bfcfd107b20cc5` | No general analytics module. Kernel provides tenant scope, transaction authority and lineage composition. |
| `dotmac_erp` | `dd6416cd981ffdf48564e2770b87d3cd7201186c` (`origin/main`) | **Qualifying source.** `OrgMetricSnapshot`, `BaseComputer`, `MetricStore`, scheduled computers, dashboard and coach readers form a production-used, tested metric projection path. |
| `dotmac_sub` | `510b80ca7fab4f54a57f261872f94b5e972c8eb6` (`origin/main`) | KPI configuration/aggregate CRUD exists, but `compute_kpis()` returns an empty list. Operational reports remain domain-owned. Not the base. |
| `dotmac_crm` | `60daaa2dd305696636632f48505ab784110a55d2` (`origin/main`) | Many direct domain report/dashboard queries and a local metrics store, but no reusable declared metric contract or rebuildable general projection. |
| `dotmac_backoffice` | `fcdd8270262dea2a78d0d4d8c4116c1e8b7b3b2d` (`HEAD`; repository has no `origin`) | No analytics implementation. Clean candidate for the second independent consumer after ERP proves cutover. |
| `dotmac_academy_app` | `a5e25e4e829350e503e66a03d73739529ba7da7f` (`origin/main`) | Domain-specific learner, admissions and success analytics query Academy-owned tables. Candidate producer/consumer, not the base. |
| `dotmac_mkt` | `1a185b47164e34601769c84976e95578996c4523` (`main`) | Google/provider aggregate sync and marketing presentation. Those observations belong to `dotmac-media-observations`; this is not the general owner. |
| `dotmac-insights` | `fa67dd5105b4349ced926e052286911d7b671908` (`main`) | A copied cross-domain BOS monolith. Routes query replicated domain ORM tables directly and its data explorer exposes a hardcoded model list. It is retirement/decomposition evidence, not source code for the module boundary. |

The Mkt and Insights repositories were cloned temporarily for this read-only
audit because no working checkout existed. No source repository was modified.

## Qualifying ERP source

The smallest coherent production-used source is ERP's scalar metric path:

- `app/models/analytics/org_metric_snapshot.py` — one numeric/JSON value per
  organization, metric, day/granularity and single dimension;
- `app/services/analytics/base_computer.py` — a common product-side writer;
- `app/services/analytics/metric_store.py` — latest, history, prior-period and
  exact-period comparison reads;
- `app/services/analytics/dashboard_metrics.py` and
  `app/services/coach/analyzers/__init__.py` — real readers;
- `app/tasks/analytics.py` plus the six computers under
  `app/services/analytics/computers/` — scheduled production writers;
- `tests/services/test_metric_store.py` and the six
  `test_*_computer.py` suites — behavior proof for isolation by organization,
  dimension filtering, ordered history, missing values, deltas and zero-prior
  handling.

This source qualifies because both writers and readers exist and the tests
exercise the contract. ERP's separate `rpt` analysis-cube/report-definition
stack is not part of the first extraction: it is finance-coupled, stores raw
JSON definitions, template file paths and email recipients, and has services
whose `db.get()` checks do not consistently put organization scope in the
query. Porting it would combine analytical evidence, presentation, file
storage, scheduling, delivery and finance policy into one owner.

## Deliberate port corrections

The package preserves ERP's metric behavior while refusing defects that are
unsafe in a reusable tenant module.

| ERP behavior | Module behavior |
|---|---|
| `organization_id` filter in services; no database RLS | `tenant_id UUID NOT NULL`, tenant-composite keys, ENABLE+FORCE RLS in the creating migration |
| Any string can become a metric or dimension | An immutable `MetricDeclarationRegistry` names one owner, version, exact value kind, unit, granularities and bounded dimension schema |
| `value_json` accepts arbitrary structures | V1 stores one exact `NUMERIC(38,12)` aggregate only; no payload, free metadata, subject identity or raw event data |
| Upsert overwrites the previous value for a coordinate | An append-only source receipt and observation retain evidence; a mutable `metric_points` row is the rebuildable winning projection |
| No source-event replay fingerprint | `(tenant, source_owner, source_event_id)` is stable identity; `dotmac_kernel.idempotency` owns fingerprint comparison, exact replay and concurrent-key resolution, while the analytics receipt remains domain evidence rather than a second ledger |
| `float` inputs and float percentage output appear in readers/tests | Public values, deltas and percentage changes use `Decimal` end to end |
| `get_prior_period()` reads `date.today()` | Comparisons require explicit current and prior periods; no module decision reads the clock |
| Domain computers query ERP tables from the shared writer base | Computers stay with their domain owners. An ERP adapter emits typed batches after the domain transaction; the module never imports or queries product/sibling models |
| Cached snapshots can fall back to live dashboard queries | Consumers decide fallback. The module exposes projection digest and deterministic rebuild; it never reaches back into domain tables |

## Fleet findings

### `dotmac_sub`

`app/models/analytics.py`, `app/schemas/analytics.py` and
`app/services/analytics.py` define mutable `KPIConfig` and `KPIAggregate` CRUD,
but the generic calculator is deliberately empty after CRM removal. The
service commits directly, stores free-form parameters/metadata, has no tenant
column, and its API has no evidence of a general projection consumer. Its real
inbox, subscriber, network and regulatory reports remain authoritative reads
owned by those domains. This path contributes retirement mappings, not the
base implementation.

### `dotmac_crm`

CRM has many analytics/report surfaces, but they calculate directly from CRM,
network, subscriber and workforce tables. They are domain readers and several
belong to owners already being decomposed out of CRM. No shared metric
definition/version, immutable receipt, rebuild protocol or tenant isolation is
present. Porting the routes would recreate the monolith in a package.

### `dotmac-insights`

Insights is the strongest negative evidence. `app/api/analytics.py` imports
Party, Subscription, Invoice, Payment, Conversation, SalesOrder, Ticket,
Expense, Employee and network ORM models and runs live queries in its route.
`app/api/data_explorer.py` hardcodes those model classes in a `TABLES` map and
exposes their columns. `app/api/reports.py` repeats live financial calculations.
That topology violates ADR-0024: an analytics application may ingest versioned
facts, but it may not become authoritative by copying or directly querying
every domain's database. The useful behavior is the user need—cross-domain
latest/trend/comparison reads—not this implementation boundary.

### Mkt, Academy and Backoffice

- Mkt's analytics path reads provider aggregates and credentials. Provider
  hierarchy/metrics belong to `dotmac-media-observations`, and provider I/O to
  Integrator connector plugins.
- Academy's analytics are calculations over learner and admissions facts. The
  domain retains those calculations and may publish declared aggregate facts.
- Backoffice has no implementation to retire and therefore makes a clean
  second-consumer proof; composing it first would not satisfy product-first
  extraction because ERP is the qualifying source.

## V1 ownership

`dotmac-analytics` owns:

1. immutable, typed metric declarations captured when first observed;
2. append-only source-event receipts and aggregate observations;
3. immutable accepted-batch receipts, with replay/conflict enforcement delegated
   to the kernel's one at-most-once owner;
4. the deterministic current metric-point projection;
5. bounded latest/history/explicit-period comparison reads; and
6. projection digest, deterministic rebuild and append-only repair evidence.

Every application installs its own tenant-plane lineage and owns its own rows.
The declaring domain owns metric meaning and calculation. It sends a typed
aggregate through an assembly adapter or versioned API/webhook; a remote
delivery may travel through Integrator, which remains transport rather than a
business-metric owner.

## Explicit exclusions

- source-domain facts, calculations, lifecycle decisions and consequences;
- web events/sessions/funnels (`dotmac-web-analytics`);
- provider ad/social aggregates (`dotmac-media-observations`);
- attribution, experimentation and causal/revenue-credit decisions;
- Prometheus/application observability metrics;
- raw rows, arbitrary SQL, table discovery, customer/person/subscriber ids,
  email, phone, IP address, user agent or free-form JSON;
- report rendering, stored files, email recipients, connector delivery and
  retry; and
- persisted dashboards, saved analyses and report schedules in V1.

The final exclusion is intentional. A later reporting/view-definition audit
must separate query semantics from Template Studio, Files, Durable Timers,
Integrator delivery and each domain's statutory meaning before any of ERP's
`rpt` tables move.

## Cutover and retirement

ERP is cutover one:

1. declare the existing dashboard metric vocabulary at exact versions;
2. adapt each existing computer to emit a typed batch while the legacy
   `OrgMetricSnapshot` upsert remains the live writer;
3. shadow latest/history/comparison output and projection digests for an agreed
   window, including corrections, missing metrics, dimensions and zero-prior
   comparisons;
4. move `DashboardMetricsService` and coach readers to the released package;
5. flip one computer family at a time, then retire `OrgMetricSnapshot`,
   `BaseComputer`'s store/upsert path and `MetricStore`; and
6. keep the domain computers in ERP until their owning domain modules extract.

Backoffice then proves the same released contract independently. Adoption is
not complete while either application keeps a parallel writer or reads another
application's database.

## Validation matrix

- pure declaration/command validation and source-parity read behavior;
- replay versus changed-fingerprint conflict;
- deterministic correction ranking and projection repair;
- one tenant-scoped transaction lock shared by ingestion and full repair;
- exact decimal money and comparison arithmetic;
- no clock, product, sibling module, provider or environment reads;
- manifest/namespace/lineage/prerequisite consistency;
- migration-created tenant column, composite keys, same-migration RLS and exact
  table ownership; and
- real PostgreSQL FORCE-RLS isolation, fail-closed missing tenant context,
  append-only triggers and live-catalog verification.

All executable tests run only on Observer from a fresh isolated writable
worktree pinned to the exact committed branch revision.
