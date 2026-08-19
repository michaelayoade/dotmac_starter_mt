# Network module source audit and disposition

- **Audit date:** 2026-08-18
- **Audit kind:** as-built source inventory plus accepted target disposition
- **Decision:** [ADR-0043](../adr/0043-network-capabilities-decompose-sub-first.md)
- **Machine-readable ledger:** `docs/inventories/network-module-dispositions.toml`
- **Typed contract ledger:** `docs/inventories/network-module-contracts.toml`

This audit answers two separate questions:

1. What production-used network behavior exists in the Dotmac applications
   today?
2. Which proposed installable module may eventually own each behavior?

The first answer is evidence. The second is a decision. A row in the target
map does not claim that a package, migration lineage, cutover, or adopter
already exists.

## Result

`dotmac_sub` is the only qualifying product-first source and must be the first
cutover for all nine modules. Its network implementation includes the mature
models, services, lifecycle documents, operational adapters, and extensive
parity tests that new shared packages must preserve.

`dotmac_crm` has an overlapping 21-table outside-plant/PON model plus direct
service writers. That copy is a consolidation and retirement input, not a
second source from which a shared module may be greenfielded. ERP, Workspace,
and Vendor Control Plane do not contain a qualifying implementation of these
network domains at the audited revisions. There is no `dotmac_cloud`
repository in the audited workspace; the checked-in Cloud ADR is a composition
profile, not adoption evidence.

Consequently, this audit approves one suite-first programme:

1. normalize the source code to one named owner for every aggregate, beginning
   with Sub IP assignments, and record CRM outside plant as an explicit
   consolidation/retirement conflict;
2. freeze all nine ownership boundaries and their fully typed commands,
   queries, events, errors and opaque handoffs together;
3. implement all nine independent packages on
   `integration/network-module-suite` in the ledger's dependency order;
4. validate each package and the complete composed suite; and
5. require live IP parity and CRM-to-Sub outside-plant consolidation, then adopt
   the entire suite in one coordinated Sub cutover and retire all displaced
   local writers.

This intentionally separates a build input from an authority switch. Audited,
single-owner code and its parity tests are sufficient to construct a candidate;
production convergence and retirement remain non-negotiable adoption gates.

The numeric implementation order places Fiber Plant seventh and the two access
modules eighth and ninth. It orders work inside one branch; it is not an
incremental adoption sequence or a runtime module dependency.

## Evidence snapshot

The inventory is pinned to committed revisions so active work in other local
sessions cannot silently change the conclusion.

| Repository | Revision | Finding |
|---|---|---|
| Starter | `8d4ddfd9e285da06ce1fdd29b59f1b483d6ea38c` | Module framework and authority rules; none of the nine network packages exists. |
| Sub | `6c9f5215c1dd71e56f4fe0e9d629f4710ad27b46` | Qualifying production source and mandatory first adopter. |
| CRM | `c64b5aa0f7902b52e7ef73cf26f3f88687ed849d` | Overlapping outside-plant/PON state to consolidate and retire. |
| ERP | `0f4b1698ddbf27a04f4562ecdaf8b93f19c3debf` | No qualifying source implementation. |
| Workspace | `c72fe304d3c8b2a2741d111379e4c4ab0af5da57` | No qualifying source implementation. |
| Vendor Control Plane | `e6b2bbee815cf9fd3ce99ceed0ff3a1f5763f057` | No qualifying source implementation. |
| Cloud | no repository | Future composition candidate only; see ADR-0030. |

The audit inspected committed model, service, test, and source-of-truth paths.
Uncommitted work in the source worktrees was outside the network paths under
review and is intentionally not evidence for this snapshot. No other product
worktree was modified.

The 2026-08-19 integration addendum brings the independently audited
`dotmac-inventory` and `dotmac-assets` candidates onto the network-suite
branch. Their own product-first inventories and ERP-first cutover gates remain
authoritative; they are reused boundaries, not additional network-source
implementations.

## As-built source findings

### Sub: qualifying source

Sub's checked-in source map describes a broad network surface spanning network
inventory and monitoring, access, provisioning/service intent, VPN/forwarding,
IP allocation, outage response, and fiber plant. The implementation is mature
enough to supply behavior and parity tests, but it is not yet separated along
module boundaries:

- `app/models/network.py` combines generic device/port/VLAN inventory, IPAM,
  OLT/ONT/PON state, and fiber-plant state.
- `app/models/network_monitoring.py` combines sites/devices/interfaces,
  measurements, alerts, topology links, outage lifecycle, customer impact,
  maintenance, availability, and device projections.
- `app/models/network_operation.py`, `app/models/forwarding_topology.py`,
  RADIUS models, router-management models, and the newer fiber/ONT authority
  ledgers add distinct owners that cannot be copied as one package.
- The test suite contains focused lifecycle, architecture, reconciliation,
  collision, outage, topology, fiber, RADIUS, OLT, and ONT canaries. The
  representative parity set for each target is pinned in the TOML ledger.

Two source constraints block immediate extraction:

1. `docs/designs/IP_ASSIGNMENT_LIFECYCLE_SOT.md` records the IP assignment
   authority as shadowing and lists remaining generic CRUD, provisioning,
   admin/bulk, ONT WAN, terminal-release, and deletion writers. The first
   deliverable is therefore a verified one-writer cutover inside Sub, not a
   new IPAM package.
2. Sub currently includes provider-specific collectors and command clients
   around routers, UISP, OLTs, ACS/TR-069, SNMP, and RADIUS. ADR-0024 assigns
   external provider clients, credentials, schedules, checkpoints, and retry
   engines to Integrator connector plugins. Those paths are extraction inputs,
   not code that may remain in a provider-neutral module.

### CRM: consolidation and retirement input

At the pinned revision, `app/models/network.py` declares 21 network tables:

- nine OLT/ONT/PON tables (`olt_devices` through `ont_assignments`) whose
  eventual disposition is `dotmac-pon-access`; and
- twelve outside-plant/merge-ledger tables (`fdh_cabinets` through
  `fiber_asset_merge_logs`) whose eventual disposition is
  `dotmac-fiber-plant`.

CRM also exposes APIs, web adapters, `app/services/network/`,
`app/services/network_impl.py`, and `app/services/fiber_plant.py`. These are
parallel writers. The fleet decomposition matrix already requires
consolidation into Sub before module extraction, so the CRM implementation is
not allowed to become a permanent fork or a second module source.

CRM's `CLAUDE.md` still calls CRM authoritative for projects, work orders,
quotes, and customer support tickets. That conflicts with accepted ADR-0024,
which assigns operational customer/service tickets and network state to Sub.
The conflict is recorded in the ledger and resolved in favor of ADR-0024. CRM
guidance must be corrected as part of its authority-retirement work; it does
not authorize Network Assurance to decide or close tickets.

### ERP, Workspace, Vendor Control Plane, and Cloud

A committed-path scan found no network/IPAM/fiber/OLT/ONT/RADIUS/topology/
outage source family in ERP, Workspace, or Vendor Control Plane that qualifies
as the initial shared implementation. Product-specific ticket, support,
commercial, approval, licensing, or allocation behavior remains with its named
owner and is not absorbed into a network module.

Cloud is deliberately different: ADR-0030 describes a thin composition
profile, but the absence of a repository means there is no implementation,
test suite, namespace, lineage, or cutover to audit. Cloud eligibility below
is a future compatibility constraint only. It must not be reported as a
current consumer or used to justify Cloud-first extraction.

## Reused foundations

The network suite does not create another meaning of “inventory” or “asset”:

| Existing candidate | Reused ownership | Network boundary |
|---|---|---|
| `dotmac-inventory` | SKU/warehouse identity, immutable movements, quantities, reservations, lots/serials and valuation while equipment is stocked. | Network modules consume opaque typed issue evidence; they own no stock row, warehouse quantity or cost. |
| `dotmac-assets` | Durable-unit identity, condition, physical lifecycle, custody, maintenance, disposal and ordered evidence. | Network Inventory owns managed-node identity, interfaces, ports, VLANs, roles and capabilities; it correlates the physical unit opaquely and owns no second asset lifecycle. |

The explicit deployment workflow is stock issue → durable-asset registration →
network admission. The adopting product composes those public contracts in its
kernel-owned transaction. None of the three packages imports another, and no
cross-module foreign key is allowed. Landing the two candidates on this branch
does not report them adopted or change their ERP-first dossiers. Its concrete
handoffs are the immutable `StockIssueEvidence` and `AssetSnapshot` public
results; managed Network Inventory stores only the opaque `asset_ref`.

## Accepted target disposition

All nine targets are stateful, tenant-plane installable modules. Each owns one
namespace and lineage when implemented, imports neither its assembly nor a
sibling module, and exchanges data through typed local ports/events rather
than cross-module ORM imports. `delivery_after` in the ledger expresses the
safe engineering sequence, never a runtime package dependency.

| Order | Target | Scope boundary | Cloud disposition | Mandatory entry condition |
|---:|---|---|---|---|
| 1 | `dotmac-ipam` | Address spaces, pools, blocks/prefixes, addresses, assignment lifecycle, collision prevention, utilization, repair. No Sub subscriber/subscription/service/device FKs. | Candidate | Sub IP one-writer cutover is complete and verified. |
| 2 | `dotmac-network-inventory` | Managed sites/nodes, interfaces, ports, VLANs, roles, capabilities and network admission/archive. Stock remains in `dotmac-inventory`; physical lifecycle remains in `dotmac-assets`. | Candidate | Generic network identity is split from stock, durable assets, monitoring, access, PON, and control. |
| 3 | `dotmac-network-observability` | Typed observations, measurements, availability facts, local alert evidence, and health projections. It does not replace Dotmac Observability. | Candidate | Provider collection is expressed as versioned Integrator connector capabilities. |
| 4 | `dotmac-network-topology` | Declared/observed links, forwarding decisions, paths, reachability, and rebuildable impact projections. | Candidate | Canonical writers for declarations, observations, decisions, and projections are named. |
| 5 | `dotmac-network-assurance` | Incident/outage lifecycle, maintenance, impact classification, SLA and escalation evidence. It cannot close tickets or work orders. | Candidate | Observation/topology inputs and evidence-only product handoffs are stable. |
| 6 | `dotmac-network-control` | Provider-neutral command intent, approval/dispatch state, execution evidence, reconciliation, and recovery. | Candidate | Kernel execution and Integrator connector seams are proven before state moves. |
| 7 | `dotmac-fiber-plant` | OSP structures/assets, strands, splices, terminations, splitters, continuity, field evidence, reviews, costs, and changes. | ISP only | CRM outside-plant authority is first consolidated into Sub and sealed. |
| 8 | `dotmac-network-access` | NAS/RADIUS access-state projection, authentication/accounting observations, active sessions, and reconciliation. | ISP only | Commercial eligibility is separated from provider-neutral access state and transport. |
| 9 | `dotmac-pon-access` | OLT/ONT/PON inventory, assignment, commissioning, desired configuration, provisioning, observation, and reconciliation. | ISP only | Product eligibility, fiber links, generic inventory, and provider clients are separated. |

The full present-day table/service families, representative source paths,
parity tests, exclusions, entry gates, and retirement gates are in
`docs/inventories/network-module-dispositions.toml`. That file is checked by
`tests/architecture/test_network_module_decomposition.py`, including detector
sensitivity proofs for a missing module, Cloud scope creep, assurance taking
ticket authority, Cloud-first extraction, incremental Sub adoption, and loss
of either reused foundation.

## Cross-cutting authority boundaries

These constraints apply to every implementation slice:

- Sub remains authoritative for subscriber, subscription, service, ticket,
  work-order, customer-impact decision, entitlement, and commercial state.
  Modules accept typed facts/requests and return evidence or domain outcomes.
- Integrator owns all external provider clients, wire mappings, endpoints,
  credential materialization, connector schedules, checkpoints, delivery
  retries, and webhook verification. A module may own provider-neutral
  installation-independent lifecycle state, but not a provider branch.
- Dotmac Observability remains the production metrics/logs/traces control
  plane. Network Observability supplies network-domain facts and projections;
  it does not install a second telemetry stack.
- No module reads a product database or another module's ORM tables. Product
  links use typed ports/events and locally owned correlation/projection state.
- Every authority migration needs an explicit old owner, new owner, shadow
  phase, comparison evidence, cutover gate, privilege/write-path seal, and
  local-copy retirement proof under ADR-0031.

## What this audit does not prove

This change brings the audit-complete Inventory and Assets candidates, their
independent lineages and their dossiers onto the integration branch. It creates
none of the nine network packages and proves no product adoption or production
cutover. The network-suite implementation must still:

1. create every network extraction dossier from the pinned Sub paths/tests;
2. allocate each independent namespace and migration owner with its package;
3. write isolation and authority canaries first;
4. validate all packages as one composed suite before Sub changes authority;
5. cut over the complete suite in Sub, seal and retire the old writers, and
   keep Inventory/Assets at their truthful dossier states; and
6. update this inventory if later source evidence changes a boundary.
