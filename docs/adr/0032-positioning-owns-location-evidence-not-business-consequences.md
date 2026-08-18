# ADR-0032: Positioning owns location evidence, not business consequences

**Status:** Accepted  
**Date:** 2026-08-18  
**Decision owner:** Michael  
**Scope:** FLEET-WIDE. Applies to every Dotmac application, the independently
deployed Integrator, installable modules and shared map presentation.  
**Relates to:** ADR-0006 (product-first extraction), ADR-0008 (declaration
registries), ADR-0010 (thin adapters), ADR-0014 (at-most-once execution),
ADR-0017 (adoption is scarce), ADR-0023 (persistence planes), ADR-0024
(applications synchronize data), ADR-0028 (assemblies select module planes),
ADR-0031 (authority cutover evidence)  
**Evidence:**
[`positioning-sources.md`](../inventories/positioning-sources.md)

## Context

Sub and its CRM predecessor already collect technician positions, maintain a
latest-position snapshot, render live and historical maps, evaluate proximity
to work orders and expose a restricted customer view. CRM additionally carries
the strongest freshness and retention behavior. ERP owns a mature fleet
lifecycle and attendance geofences, but its vehicle GPS fields have no writer.

Those implementations currently combine four different concerns:

1. evidence reported by a device or provider;
2. a current-position or trail projection derived from that evidence;
3. a business decision such as starting work, accepting attendance or sharing
   a technician's position with a customer; and
4. Leaflet/Jinja presentation of domain objects on a map.

Treating all four as a generic map package would transfer product authority into
a renderer. Treating location as part of `field-workforce` would make vehicle
tracking rebuild the same evidence mechanism. Treating it as part of
`assets-fleet` would make technicians and future tracked units into vehicles.

The checked-in fleet matrix previously classified `field_tech_location_pings`
and the mixed `field_tech_presence` snapshot under `field-workforce`. This
decision deliberately amends that target boundary. The source table remains
mixed until cutover; the target module separates position projection from
workforce status and sharing policy.

## Decision

### 1. `dotmac-positioning` is the optional position-evidence owner

`dotmac-positioning` is a stateful, independently versioned optional module.
Its first supported installation is the tenant plane. Each adopting application
pins its own copy, composes the module lineage into its own database and owns
its own rows. Applications never share a positioning database.

The module owns:

- an opaque tracked-unit identity;
- provider-neutral tracker/source identity and time-bounded assignment;
- immutable position observations;
- collection sessions and server-enforced purpose/time grants;
- one rebuildable current-position projection and ordered trail reads;
- retention expiry and idempotent pruning;
- product-neutral circle/polygon geometry evaluation; and
- deduplicated geofence entry/exit observations.

It owns no person, technician, vehicle, work order, customer, subscriber,
attendance record, network asset or provider account.

### 2. Products own links, policy and business consequences

The consuming product owns the relation between its local subject and an opaque
tracked-unit id. The module has no foreign key to a product or sibling-module
table, no product enum, no nullable polymorphic subject columns and no
`is_sub`/`is_erp` branch. If subject-kind declarations are required, they use an
ADR-0008 assembly registry rather than a fixed shared enum.

Sub remains authoritative for technicians, shifts, work orders, dispatch,
route order, job transitions, customer-sharing policy and ETA. ERP remains
authoritative for vehicles, drivers, assignments, reservations, maintenance,
fuel, incidents, documents and attendance consequences. Sub's plant owners
remain authoritative for fiber/network assets, routes, as-built geometry and
qualification. A geofence entry is an observation; it is not permission to
start a work order or accept an attendance punch.

The positioning service validates and flushes inside the caller-owned
transaction and returns typed derived facts. The local owning service decides
and records any consequence. A non-transactional notification or external
delivery leaves through the outbox after commit.

### 3. Observation identity and time are explicit

Every accepted observation carries at least:

- tenant and tracked-unit identity;
- a source/tracker identity and stable `client_observation_id`;
- `captured_at` from the observing device/provider and `received_at` from the
  accepting application;
- latitude and longitude;
- accuracy when the source supplies it; and
- source/provenance sufficient to explain the observation.

Altitude, speed, heading and provider sequence are optional typed fields. The
at-most-once key is `(tenant, source/tracker, client_observation_id)`; a reused
key with a different fingerprint is a conflict, not a replay. Validation occurs
before mutation. Partial batch acceptance uses isolated savepoints or a fully
prevalidated batch; an error reported for one row may never leave that row
pending for the final commit.

`captured_at` and `received_at` are never interchangeable. A late observation
may remain valid trail evidence without rolling back the current projection. A
future or implausibly stale observation cannot freeze or advance that
projection merely because it arrived last.

### 4. Privacy is a server-side contract and retention is product policy

Client cadence is battery behavior, not authorization. Ingest requires an
active server-side collection grant naming purpose and time bounds. Reads that
share a position require a separate active audience/purpose grant and a
caller-supplied or declared freshness contract. Disabling or expiring a grant
stops new collection or disclosure even if a client continues sending.

Raw observations have explicit expiry. The module sets no universal retention
duration: each product installs policy, and the idempotent sweep removes expired
rows while preserving any separately owned legal-hold decision. Current
position is a rebuildable projection, not an unbounded substitute for history.

### 5. Provider transport belongs to Integrator connector plugins

The module exposes a provider-neutral typed ingest contract. GPS/telematics
vendors, credentials, webhook verification, polling, wire mapping, checkpoints
and delivery retries live in Integrator connector plugins and the
`dotmac-integration` runtime. Integrator sends authenticated versioned
observations through an application's API; it never writes the application's
module tables and the module contains no provider catalogue.

Device collection embedded in a Dotmac-owned mobile application may call the
same application contract directly. It receives no exception from observation
identity, privacy, freshness or retention rules.

### 6. Maps are a presentation contract, not this owner

`dotmac-positioning` returns typed position/trail/geofence DTOs and may provide
GeoJSON serialization as a pure published adapter. It ships no Leaflet
template, popup, permission check or product action.

A future `dotmac_ui.maps` supported public module may own self-hosted map assets,
map lifecycle, marker/polyline layers, bounds/focus and stale-state
presentation. Products supply endpoints, authorization, domain markers, popup
content and actions. That presentation slice starts only after Sub and ERP use
the same released positioning read contract; CRM's fork is not a second
independent consumer.

Static plant, qualification, service-address and as-built maps remain views
over their domain owners. Route optimization remains dispatch/scheduling
policy. Neither moves into positioning because coordinates are present.

### 7. Product-first source and cutover order are fixed

Sub is the qualifying source and first cutover. CRM is evidence from the same
retiring product lineage; its stronger freshness, retention and privacy tests
are mandatory parity inputs but do not count as independent adoption. ERP is
the concrete second candidate and proves reuse only after its vehicle tracking
path consumes the same released contract.

Before shared behavior is implemented:

1. correct Sub's client/API vocabulary and context mismatch;
2. prove rejected batch rows do not commit and retries deduplicate;
3. preserve source timestamp and accuracy;
4. add server-side collection/share grants, freshness and retention canaries;
5. record the complete source/test inventory and `EXTRACTION.toml`; and
6. allocate the module namespace and lineage through the kernel ledger.

Sub then shadows local and module observations/projections, compares accepted,
rejected and replay counts plus latest/trail results, seals the cutover under
ADR-0031 evidence, and retires the local writer. ERP adoption follows through a
product-owned vehicle-to-tracked-unit link. No permanent dual writer is
permitted.

## Consequences

- `field-workforce` and `assets-fleet` keep their business lifecycles while
  sharing one evidence mechanism.
- The existing `field_tech_presence` table must split: shift/work status and
  sharing decisions remain product-owned; current coordinates become the
  module's rebuildable projection.
- Existing direct service commits and best-effort post-commit geofence
  consequences are not ported.
- Location correctness, privacy, idempotency and retention become parity gates,
  not cleanup after extraction.
- The fleet decomposition classifier gains a narrow `positioning` measurement
  family. Its exact matcher is intentionally not a generic `location|map|geo`
  bucket.

## Alternatives rejected

### Keep tracking inside `field-workforce`

Rejected because ERP vehicle tracking and future asset/device applications
would either import workforce concepts or rebuild the same observation owner.

### Put tracking inside `assets-fleet`

Rejected because a technician, agent or other tracked unit is not a vehicle and
must not acquire a fleet lifecycle to report a position.

### Extract a generic map package first

Rejected because the current maps combine unrelated authoritative data and
actions. Presentation reuse follows a shared read contract; it does not create
the data owner.

### Put distance and geofencing in the kernel

Rejected because every assembly does not require persisted positioning or
geofence behavior. The optional module is the narrowest shared layer, and pure
geometry remains available through its published surface without adding a
stateful kernel dependency.
