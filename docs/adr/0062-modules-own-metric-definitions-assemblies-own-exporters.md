# ADR-0062: Modules own metric definitions; assemblies own exporters

> **Number allocation, 2026-08-24.** See the note in ADR-0061 — `0059` and
> `0060` are allocated on sibling branches that have not merged.

- Status: Accepted
- Date: 2026-08-24
- Deciders: Michael
- Supersedes: none
- Extends: ADR-0024 § 2 (every module is independently releasable and locally
  installed) and § 4 (shared behavior contains no product or provider switch)
- Related: ADR-0006 (module/assembly ownership boundaries), ADR-0008
  (declaration registries — a vocabulary is declared, never inferred),
  ADR-0032 (unobserved is UNKNOWN, never ABSENT), ADR-0043 (Analytics owns
  projections, not domain facts), `docs/ARCHITECTURE.md`
  § "Application and module composition boundary"

## Context

`dotmac-integration` derives runtime numbers about its own outbound queue
(`operations.dispatch_metrics`) and about what is stuck
(`operations.health_report`). Something has to turn those into a scrape
endpoint, and the tempting place to put it is the module — it is where the
numbers are, and a module that ships its own exporter looks self-contained.

It is the wrong place, for the reason ADR-0024 § 2 already gives about
persistence and § 4 gives about provider clients. Several assemblies compose
one module. An exporter inside it would make the package depend on whichever
metrics client the first adopter preferred, and would give every later adopter
a second observability path beside the one its deployment already runs. The
module would be quietly choosing the deployment's monitoring stack.

The opposite failure is just as real. A module that produces numbers with no
stable names produces dashboards that break silently: a field rename empties a
graph rather than failing a build, and a dashboard written against one
deployment reads nothing on another.

`dotmac-integration` resolved both correctly in code
(`operations.METRIC_NAMES`, and a docstring saying "this module produces
numbers; it does not export them"). That resolution is a comment inside one
module. Nothing binds the next module to it, and nothing catches the next
exporter.

## Decision

### 1. A module owns the DEFINITION of every runtime metric it produces

A shared module declares stable, language-neutral metric names and derives
their values from its own facts. The names are a published contract:

- **declared in one place**, as a module-level tuple or registry, not implied
  by dataclass field names — a rename is then a visible diff against the
  declaration rather than a silently empty graph;
- **prefixed with the module's own namespace** (`integration_outbound_*`,
  `integration_inbound_*`, `integration_connector_*`), so a name is unambiguous
  when several modules are exported from one process;
- **unit-suffixed where the value is not a plain count** (`_seconds`), and
  `_total` only for a monotonic-by-nature lifetime count, never for a gauge;
- **derived at read time from the module's own ledgers**, never stored. A
  stored gauge is a second writer over facts a table already holds and drifts
  the moment a worker dies between the two updates; and
- **versioned like any other contract** — removing or renaming a declared name
  is a breaking change to the module's published surface.

### 2. The deployed assembly owns the EXPORTER

The exporter — the metrics client, the registry, the scrape endpoint, the push
gateway, the label set, the scrape interval, the retention — belongs to the
assembly that deploys the module (`dotmac_integrator`, a product assembly, the
vendor control plane). It reads the module's declared mapping and renders it.

A module therefore contains no metrics client dependency, no counter/gauge
registry, no `/metrics` route and no exporter configuration knob. Two
assemblies composing the same module may export it to different systems, and
neither is a fork.

### 3. A module never owns a second observability path

One derivation per number, one owner. A module does not additionally log its
metrics for scraping, write them to a table for a dashboard to read, push them
to a collector, or expose a parallel "stats" API beside the declared mapping. A
second path is a second writer with a different clock, and the two disagree
exactly when someone is trying to use them.

### 4. This is about RUNTIME metrics, not domain metrics

The distinction is load-bearing and easy to lose:

| | owner | example |
|---|---|---|
| **runtime metric** — a number about the software's own behaviour, derived, never stored | this ADR: module defines, assembly exports | `integration_outbound_queue_depth` |
| **domain metric** — a business fact or projection the product is authoritative for, persisted, repairable | its domain owner under ADR-0043 and its own ADR | `dotmac-analytics` declared metric codes; `dotmac-media-observations` period metrics |

`dotmac-analytics` and `dotmac-media-observations` own domain metrics as rows
with provenance, drift detection and repair. Nothing in this ADR applies to
them, and nothing here permits a runtime gauge to be stored as if it were a
domain fact.

### 5. Known divergence at acceptance

Recorded so this ADR is not read as a description of a finished state.

**D1 — the rule currently lives in a docstring, not in governance.**
`packages/dotmac-integration/src/dotmac_integration/operations.py` and that
package's `README.md` state § 1 and § 2 correctly and are the source this ADR
generalises. No ADR carried it before this one, no `AGENTS.md` rule names it,
and there is no guard: a module that added `prometheus_client` to its
dependencies and a `/metrics` route would pass every gate in the repository
today.

**D2 — half of `dotmac-integration`'s own derived numbers are unnamed.**
`DispatchMetrics.as_metrics()` returns exactly `METRIC_NAMES` — thirteen
prefixed, unit-suffixed names. `HealthReport.as_dict()` (same module, same
file, same read-time derivation) returns bare dataclass field names —
`dead_letter`, `checkpoints_stale`, `receipts_unprocessed`, and so on — with no
declared tuple and no `integration_` prefix. An assembly that exports the
health signals, which is the natural thing to do with an object whose purpose
is "is anything silently stuck?", exports six unprefixed, undeclared names. § 1
is satisfied for one object in the module and not the other.

**D3 — the rule has never been exercised by a second module.** No other shared
distribution declares a runtime metric name today, so § 1's conventions have
one implementation and no cross-module proof. `dotmac-platform-health` is the
nearest neighbour and is deliberately *not* an instance of this pattern: it
owns persisted, provenance-carrying health observations and projections, which
are domain facts under § 4, and its contract already excludes metric storage.

**D4 — the connector distributions declare no runtime metric names at all.**
That is currently correct rather than a gap: a connector returns typed outcomes
and the engine counts them, so the numbers belong to `dotmac-integration`. It
is recorded because the first connector to want its own provider-side number
(rate-limit headroom, token expiry) must add it to the engine's declaration or
open a declaration of its own under § 1 — not reach for an exporter.

## Consequences

- The exporter work is real work and it lands in an assembly repository. A
  module cannot be "done" observability-wise on its own, and that is the
  intended trade.
- Closing D2 is a `dotmac-integration` change: give `HealthReport` its own
  declared, prefixed name tuple beside `METRIC_NAMES`, pinned by a literal test
  the way `METRIC_NAMES` already is. It is a minor version, not a fix.
- A future guard is possible and is described rather than asserted: a static
  check that no `packages/*/src/**` module imports a metrics client or declares
  a `/metrics` route, plus a check that every public `as_metrics`-shaped
  mapping's keys come from a declared tuple. ADR-0018 applies — such a guard
  ships with its own sensitivity proof or it is not a guard.
- Metric names become part of what a module's `COMPATIBILITY.md` promises.

## Alternatives rejected

**The module ships an exporter.** It picks the deployment's monitoring stack on
the deployment's behalf, adds a runtime dependency every adopter inherits, and
creates a second observability path in any assembly that already exports.

**The assembly also names the metrics.** Then two assemblies name the same
number differently, a dashboard is not portable, and the module can rename a
field without anything noticing. Naming belongs where the number is derived.

**No stable names; let the exporter map field names.** The mapping then lives in
each assembly and drifts per deployment, which is the same failure with more
copies.
