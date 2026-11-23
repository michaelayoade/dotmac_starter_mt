# Network operations

- Short explanations of package responsibilities, not a claim of production readiness.
- Check the [catalog](../MODULE_CATALOG.md) for installation, release and adoption evidence.
- A **tenant** is a customer workspace; the **platform** is the operator control plane.
- [All module groups](index.md).

## Network inventory (`dotmac-network-inventory`)

- **What it does:** Registers managed sites, devices, interfaces, ports and VLANs.
- **Use it when:** Maintaining a logical inventory of managed network equipment.
- **Keep in mind:** It is not the warehouse ledger, physical asset lifecycle or IP address allocator.
- **Details:** [Package guide](../../packages/dotmac-network-inventory/README.md) · [Source and contract](../../packages/dotmac-network-inventory/EXTRACTION.toml).

## IP address management (`dotmac-ipam`)

- **What it does:** Manages address spaces, pools, reservations, assignments and utilization.
- **Use it when:** Allocating IP addresses without collisions.
- **Keep in mind:** Service eligibility and network command execution remain separate.
- **Details:** [Package guide](../../packages/dotmac-ipam/README.md) · [Source and contract](../../packages/dotmac-ipam/EXTRACTION.toml).

## Fiber plant (`dotmac-fiber-plant`)

- **What it does:** Tracks outside plant, fibers, splices, terminations and continuity.
- **Use it when:** Maintaining the physical fiber network and as-built evidence.
- **Keep in mind:** It does not own projects, customer services or generic inventory.
- **Details:** [Package guide](../../packages/dotmac-fiber-plant/README.md) · [Source and contract](../../packages/dotmac-fiber-plant/EXTRACTION.toml).

## Network topology (`dotmac-network-topology`)

- **What it does:** Maintains links, paths, reachability and rebuildable topology views.
- **Use it when:** Understanding how network elements connect.
- **Keep in mind:** It does not own the physical fiber ledger or declare an incident.
- **Details:** [Package guide](../../packages/dotmac-network-topology/README.md) · [Source and contract](../../packages/dotmac-network-topology/EXTRACTION.toml).

## Network observability (`dotmac-network-observability`)

- **What it does:** Stores network observations, measurements, availability and health projections.
- **Use it when:** Turning collector-neutral observations into local network health evidence.
- **Keep in mind:** Collectors and the external telemetry platform remain separate.
- **Details:** [Package guide](../../packages/dotmac-network-observability/README.md) · [Source and contract](../../packages/dotmac-network-observability/EXTRACTION.toml).

## Network assurance (`dotmac-network-assurance`)

- **What it does:** Manages network incidents, impacts, maintenance and SLA evidence.
- **Use it when:** Assessing service impact and coordinating assurance decisions.
- **Keep in mind:** It does not collect measurements or execute repairs itself.
- **Details:** [Package guide](../../packages/dotmac-network-assurance/README.md) · [Source and contract](../../packages/dotmac-network-assurance/EXTRACTION.toml).

## Network control (`dotmac-network-control`)

- **What it does:** Tracks requested network commands, approval, execution evidence and recovery.
- **Use it when:** Controlling network changes with a traceable decision path.
- **Keep in mind:** Provider clients, credentials and retry transport stay outside the module.
- **Details:** [Package guide](../../packages/dotmac-network-control/README.md) · [Source and contract](../../packages/dotmac-network-control/EXTRACTION.toml).

## Network access (`dotmac-network-access`)

- **What it does:** Projects access state and records authentication, accounting and session facts.
- **Use it when:** Reconciling network access observations with product decisions.
- **Keep in mind:** It does not decide commercial eligibility or own provider credentials.
- **Details:** [Package guide](../../packages/dotmac-network-access/README.md) · [Source and contract](../../packages/dotmac-network-access/EXTRACTION.toml).

## Optical access (PON) (`dotmac-pon-access`)

- **What it does:** Manages optical access equipment, ONT commissioning and desired-state reconciliation.
- **Use it when:** Operating PON networks through provider-neutral contracts.
- **Keep in mind:** Subscriber commercial decisions and vendor transport are separate.
- **Details:** [Package guide](../../packages/dotmac-pon-access/README.md) · [Source and contract](../../packages/dotmac-pon-access/EXTRACTION.toml).

## Remote access (`dotmac-remote-access`)

- **What it does:** Tracks purpose-bound, time-limited device/network access grants and evidence.
- **Use it when:** Giving approved operators temporary access to a target.
- **Keep in mind:** It does not own approval policy or implement remote transport itself.
- **Details:** [Package guide](../../packages/dotmac-remote-access/README.md) · [Source and contract](../../packages/dotmac-remote-access/EXTRACTION.toml).
