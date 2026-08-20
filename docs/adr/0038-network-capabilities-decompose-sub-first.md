# ADR-0038: Network capabilities are built as one bounded suite before Sub adoption

- Status: Accepted
- Date: 2026-08-18
- Deciders: Michael
- Supersedes: none
- Related: ADR-0006 (product-first extraction and dossier states), ADR-0017
  (adoption is the scarce resource), ADR-0024 (applications are independent),
  ADR-0030 (Cloud composition profile), ADR-0031 (authority cutover sealing)

## Context

Sub has a mature network implementation, but its present model and service
boundaries are substantially broader than one coherent installable module.
Generic inventory, address allocation, monitoring, topology, outage response,
command execution, outside plant, RADIUS/access, and OLT/ONT/PON lifecycles are
interleaved across product models and services. Shipping that entire surface as
one `network` package would preserve the monolith at a different import path.

CRM also carries an overlapping 21-table outside-plant/PON model with direct
writers. ERP, Workspace, and Vendor Control Plane have no qualifying shared
implementation of the proposed network domains. Cloud is currently a profile,
not an audited repository or adopter.

The product-first source inventory is
[`network-module-sources.md`](../inventories/network-module-sources.md). Its
reviewable disposition data is
`docs/inventories/network-module-dispositions.toml`. The complete public
command/query/snapshot/event vocabulary is
[`network-module-contracts.toml`](../inventories/network-module-contracts.toml).
The audit establishes four facts that constrain the decision:

1. Sub is the only qualifying production source and contains the parity tests.
2. Sub's IP assignment lifecycle is still shadowing legacy writers; it is not
   ready to move.
3. CRM outside plant must consolidate into Sub before Fiber Plant can be
   adopted without preserving two authorities; the audited Sub implementation
   remains the primary code source for building the candidate.
4. Provider clients currently embedded in product network code cannot be
   copied into provider-neutral modules under ADR-0024.

Michael has directed this named network-decomposition programme and later
selected a suite-first delivery: define and build every approved boundary on
one Starter integration branch, validate the composition as a whole, and adopt
the complete suite in Sub through one coordinated cutover. That direction
lifts ADR-0017's supply-push moratorium for the nine modules in this ADR only.
It is not permission to add unrelated shared facilities or to weaken any
module's product-first entry, parity, isolation, or retirement gate.

## Decision

### 1. One integration suite contains nine independent modules

The accepted target set and its sequence are:

| Order | Module | Named owner |
|---:|---|---|
| 1 | `dotmac-ipam` | Address-space, pool/prefix/address, assignment, collision, utilization, and repair lifecycle. |
| 2 | `dotmac-network-inventory` | Generic site, device, interface, port, VLAN, capability, admission, and archive lifecycle. |
| 3 | `dotmac-network-observability` | Typed network observations, measurement facts, availability facts, local alert evidence, and health projections. |
| 4 | `dotmac-network-topology` | Declared/observed links, forwarding decisions, path resolution, reachability, and rebuildable topology projections. |
| 5 | `dotmac-network-assurance` | Incident/outage lifecycle, maintenance, impact classification, SLA evidence, and escalation recommendations. |
| 6 | `dotmac-network-control` | Provider-neutral command intent, approval/dispatch state, execution evidence, reconciliation, and recovery. |
| 7 | `dotmac-fiber-plant` | Outside-plant structures/assets, strands, splices, terminations, splitters, continuity, field evidence, and change control. |
| 8 | `dotmac-network-access` | NAS/RADIUS access projection, authentication/accounting observations, sessions, reconciliation, and drift evidence. |
| 9 | `dotmac-pon-access` | OLT/ONT/PON inventory, assignment, commissioning, desired configuration, provisioning, observation, and reconciliation. |

All nine are built and composition-tested together on
`integration/network-module-suite`, but each remains an independently
versioned, stateful, tenant-plane module with one `mod_*` namespace and one
migration lineage. No module imports another module or its assembly. The
sequence above orders commits and contract stabilization inside the integration
branch; it creates neither a sibling package dependency nor permission for an
incremental product cutover. Products exchange typed data with each installed
module through published commands, queries, events, and locally owned opaque
correlation state.

The exact current table/service-family dispositions, source paths, parity tests,
entry gates, and retirement gates live in
`docs/inventories/network-module-dispositions.toml`. Changing that contract
requires changing the ledger, this ADR by amendment, and the architecture
canary in the same review.

### 2. Product-first means Sub-first and one suite cutover

Every network module uses Sub's production implementation and tests as its
initial code source. Sub is also the concrete candidate consumer and the first
network-suite cutover. A network package does not become `adopted` merely
because its code or lineage is complete: all nine remain `audit-complete`
until the composed suite is exact-pinned, migrated, shadow-verified and enabled
in Sub and every displaced local writer is sealed and retired. A failure in
one module's adoption gate blocks the coordinated suite cutover.

This decision uses ADR-0006's 2026-08-12 amendment: a second consumer proves
reuse but is not permission to share. The three dossier states therefore apply
normally:

- `audit-complete`: this inventory exists, Sub is the concrete candidate, and
  no cutover has happened;
- `adopted`: Sub's first cutover is complete; and
- `reuse-proven`: a second independent assembly exercises the same contract.

ADR-0031 later says “shared code requires two CURRENT consumers” while
discussing whether to extract its cutover-sealing mechanism. Read as a general
module rule, that sentence conflicts with ADR-0006's explicit 2026-08-12
amendment and its enforced dossier states. For this programme it is scoped to
the cutover-sealing mechanism discussed by ADR-0031; it does not reinstate the
old two-consumer permission gate for installable modules. This ADR records that
resolution explicitly rather than silently choosing between two authoritative
texts.

Cloud does not count as the second consumer today. `dotmac-ipam`,
`dotmac-network-inventory`, `dotmac-network-observability`,
`dotmac-network-topology`, `dotmac-network-assurance`, and
`dotmac-network-control` are compatible target candidates for a future Cloud
assembly. They reach `reuse-proven` only after a real Cloud repository pins,
composes, migrates, and exercises their contracts. `dotmac-fiber-plant`,
`dotmac-network-access`, and `dotmac-pon-access` remain ISP-only.

### 3. Build together, validate together, adopt together

Source normalization has two distinct gates. Before code is copied, the source
product must have one named code owner and audited parity tests: Sub's prepared
IP assignment one-writer service is therefore the IPAM build input, while the
audited Sub implementation is the Fiber Plant build input and CRM's duplicate
is an explicit conflict/retirement input. Before adoption, live authority must
also be singular: Sub's production IP cohort must pass its exact-service
cutover evidence, and CRM outside-plant authority must consolidate into Sub.
Building the candidates before those production gates does not move authority;
it prevents runtime cutover work from being confused with package design.

The implementation branch then proceeds in dependency order while retaining a
single adoption gate:

- Inventory precedes Observability and Topology so identity inputs are typed;
- Observability and Topology precede Assurance so observations are separated
  from decisions and impact projections are rebuildable;
- Control follows typed inventory/observation seams so command state is not
  coupled to provider clients;
- Fiber Plant may be built from the audited Sub primary implementation, but its
  suite adoption waits for a sealed CRM-to-Sub outside-plant consolidation; and
- Network Access and PON Access wait for commercial/product decisions and
  provider transport to be separated from their domain lifecycles; and
- no completed package is composed into Sub early merely because another
  package is still under construction.

For every package, its `EXTRACTION.toml` is created with the implementation,
not speculatively by this ADR. It names Sub source paths/tests, owner, contract,
candidate/contract consumers, first cutover, shadow/drift proof, and local-copy
retirement gate. Namespace, migration, RLS, manifest, isolation, and live
catalog canaries land before the cutover. The final branch must also pass a
composed-suite migration gate, cross-tenant isolation canaries, parity replay,
drift reconciliation, rollback rehearsal, and a proof that no module imports a
sibling. Only then is the complete suite adopted in one Sub release.

### 4. Existing Inventory and Assets owners are reused, not rebuilt

The integration branch includes the existing audit-complete
`dotmac-inventory` and `dotmac-assets` candidates. They are reusable
foundations with distinct qualifying source and cutover evidence; including
them here does not rewrite their ERP-first dossiers or claim an adopter.

- `dotmac-inventory` remains the only owner of SKUs, warehouses, stocked
  serials/lots, quantities, reservations, movements and valuation. A router,
  ONT, cable or spare is Inventory state while stocked. Network modules never
  maintain warehouse quantity, stock status or cost.
- `dotmac-assets` remains the only owner of stable durable-unit identity,
  physical condition/lifecycle, custody, maintenance and disposal. Network
  Inventory owns management identity, role, interfaces, ports, VLANs and
  capabilities—not a second physical-asset lifecycle.
- A product-owned composition workflow performs the explicit
  stock-issue → asset-registration → network-admission handoff using public
  typed contracts in the caller's kernel-owned transaction. Module packages do
  not import one another and no cross-module foreign key is introduced.
- Hardware correlation uses opaque local references. PON or access modules may
  own protocol registration identifiers and desired network state, but they do
  not become the writer of the asset's physical serial, condition, custody or
  disposal.

Where the existing candidates expose ORM instances instead of immutable public
results, snapshots, or events needed by this workflow, the integration branch
adds those typed contract surfaces without adding network-specific fields to
Inventory or Assets. The concrete bridge surfaces are
`dotmac_inventory.StockIssueEvidence` returned by `issue_stock_evidence` and
`dotmac_assets.AssetSnapshot` returned by `create_asset_snapshot`; Network
Inventory then receives only the resulting opaque `asset_ref`.

### 5. Products retain business decisions

Extraction does not move product authority merely because a network row refers
to a customer or service:

- Sub retains subscriber, subscription, service, entitlement, commercial,
  customer-impact decision, operational customer/service ticket, and work-order
  authority.
- ERP retains its internal business/support owners.
- Vendor Control Plane retains vendor/licensing/allocation owners.
- A module receives typed facts or requests and returns domain outcomes,
  projections, or evidence. It never reads another application's database,
  imports product ORM models, or writes product tables.

`dotmac-network-assurance` may correlate an outage to a product ticket or
work order and emit resolution/impact evidence. It may not decide or close that
ticket/work order. The CRM guidance that claims broader operational authority
is stale against ADR-0024 and must be corrected during CRM retirement.

`dotmac-ipam` does not carry foreign keys to Sub subscribers, subscriptions,
services, addresses, OLTs, NAS devices, or VLAN rows. Product-specific links are
represented through typed opaque references/ports or locally owned link state;
the module contract remains usable by a non-ISP assembly.

### 6. Integrator owns external provider I/O

The modules declare provider-neutral capabilities and data contracts. The
independently deployed Integrator remains the external connector control plane:
connector plugins own provider SDKs, wire mappings, endpoints, credential
references/materialization, webhook verification, schedules, checkpoints, and
delivery retry behavior.

This applies to SNMP, UISP, RouterOS/router APIs, RADIUS delivery, OLT vendor
protocols, ACS/TR-069, GIS/field systems, and future Cloud/network providers.
Products and modules do not grow provider enums, imports, or mode flags.

`dotmac-network-control` owns command lifecycle state, while `dotmac-kernel`
provides the shared execution primitives and Integrator plugins perform the
external operation. The effect and its evidence use the existing idempotency
and outbox rules; a web route or product task is never a second command engine.

### 7. Network Observability does not replace Dotmac Observability

`dotmac-network-observability` owns network-domain observations and local
projections needed by products and the other bounded domains. Dotmac
Observability remains the production metrics/logs/traces control plane,
including Prometheus/Mimir, Loki, Tempo, Grafana, and their operational
lifecycles. Extraction must route useful telemetry to that stack through its
supported surface and must not install a parallel general telemetry platform.

## Consequences

- Sub adopts the complete network suite through one coordinated cutover. The
  branch may contain many small independently reviewable commits, but it has
  one composed release gate and no partially adopted network authority.
- The initial package can be legitimate with one concrete adopter, but its
  dossier must say `audit-complete` or `adopted`, never `reuse-proven`.
- Cloud compatibility constrains the six shared contracts without pretending
  that Cloud already exists or has adopted them.
- CRM's outside-plant implementation is explicit retirement debt. Fiber Plant
  cannot use that duplicate as a reason to preserve two implementations.
- Provider separation is part of extraction, not a later cleanup; a module
  containing provider clients fails ADR-0024 even if its domain behavior is
  otherwise correct.
- The decomposition creates more packages, migrations and validation surfaces,
  while the chosen adoption strategy creates one larger cutover. The cost buys
  stable typed seams before Sub changes authority and avoids temporary
  module-by-module adapters becoming permanent.
- The machine ledger and its sensitivity-tested architecture canary make scope
  additions, Cloud scope creep, first-cutover reversal, and ticket-authority
  drift explicit review events.

## Alternatives rejected

**Keep the network domains inside Sub.** That preserves mature behavior but
prevents a thin Sub assembly and forces a future Cloud product to rebuild
generic network capabilities.

**Extract one large `dotmac-network` package.** It keeps the same coupled
lifecycles and makes every adopter inherit ISP-specific RADIUS, PON, and fiber
state.

**Adopt modules in Sub one by one as soon as they are built.** That would
temporarily distribute related writers across Sub and Starter, force contracts
to stabilize against intermediate architectures, and create compatibility
paths that the final thin assembly does not need. Ordered implementation is
retained, but product authority switches only after the whole suite is ready.

**Build shared modules greenfield beside Sub.** ADR-0006 and hard rule 24 forbid
parallel implementations when a production-used, tested source exists.

**Extract from CRM first.** CRM is the smaller duplicate and retirement target.
Selecting it would discard Sub's broader authority work and preserve the wrong
writer.

**Make Cloud the first adopter.** No Cloud repository, migration lineage, tests,
or runtime exists to perform that cutover. A profile is not a consumer.

**Let modules or Integrator own product decisions.** That would make transport
or shared infrastructure authoritative for customer, ticket, work-order,
subscription, or entitlement state and violates ADR-0024.

**Copy provider clients into each module.** That turns provider identity into a
module vocabulary and creates multiple connector control planes.

**Fold general observability into Network Observability.** The production
telemetry control plane already exists and has a different operational owner;
duplicating it creates drift rather than a reusable network domain.
