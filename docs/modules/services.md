# Service delivery and physical resources

- Short explanations of package responsibilities, not a claim of production readiness.
- Check the [catalog](../MODULE_CATALOG.md) for installation, release and adoption evidence.
- A **tenant** is a customer workspace; the **platform** is the operator control plane.
- [All module groups](index.md).

## Assets (`dotmac-assets`)

- **What it does:** Tracks durable assets, custody, assignments, maintenance and disposal.
- **Use it when:** Managing where physical assets are and who holds them.
- **Keep in mind:** Financial depreciation belongs to Finance; stock movements belong to Inventory.
- **Details:** [Package guide](../../packages/dotmac-assets/README.md) · [Source and contract](../../packages/dotmac-assets/EXTRACTION.toml).

## Inventory (`dotmac-inventory`)

- **What it does:** Owns stock items, warehouses, movements, reservations and valuation.
- **Use it when:** Tracking quantities, traceability and stock value.
- **Keep in mind:** Buying decisions and durable asset custody remain separate.
- **Details:** [Package guide](../../packages/dotmac-inventory/README.md) · [Source and contract](../../packages/dotmac-inventory/EXTRACTION.toml).

## Procurement (`dotmac-procurement`)

- **What it does:** Manages requisitions, sourcing, supplier awards and purchase commitments.
- **Use it when:** Controlling what the business decides to buy.
- **Keep in mind:** It does not own warehouse stock or supplier payment execution.
- **Details:** [Package guide](../../packages/dotmac-procurement/README.md) · [Source and contract](../../packages/dotmac-procurement/EXTRACTION.toml).

## Service catalog (`dotmac-service-catalog`)

- **What it does:** Defines technical service specifications, plan families and characteristics.
- **Use it when:** Describing the technical service that can be offered.
- **Keep in mind:** Commercial prices and a customer's live service instance are separate.
- **Details:** [Package guide](../../packages/dotmac-service-catalog/README.md) · [Source and contract](../../packages/dotmac-service-catalog/EXTRACTION.toml).

## Qualification (`dotmac-qualification`)

- **What it does:** Records expiring evidence and decides service eligibility.
- **Use it when:** Checking whether a proposed service can be supplied under declared rules.
- **Keep in mind:** It does not collect coordinates, own network topology or activate a service.
- **Details:** [Package guide](../../packages/dotmac-qualification/README.md) · [Source and contract](../../packages/dotmac-qualification/EXTRACTION.toml).

## Service orders (`dotmac-service-orders`)

- **What it does:** Tracks delivery orders and activation-readiness decisions.
- **Use it when:** Coordinating the delivery prerequisites for a service.
- **Keep in mind:** The live service's lifecycle belongs to Services.
- **Details:** [Package guide](../../packages/dotmac-service-orders/README.md) · [Source and contract](../../packages/dotmac-service-orders/EXTRACTION.toml).

## Live service instances (`dotmac-services`)

- **What it does:** Owns individual service instances and their lifecycle history.
- **Use it when:** Recording which service a customer actually has and its current state.
- **Keep in mind:** Billing, subscriptions, fulfillment and network enforcement remain separate.
- **Details:** [Package guide](../../packages/dotmac-services/README.md) · [Source and contract](../../packages/dotmac-services/EXTRACTION.toml).

## Service changes (`dotmac-service-changes`)

- **What it does:** Tracks plan-change, relocation and vacation requests with guarded checkpoints.
- **Use it when:** Applying customer-requested changes across several domain owners.
- **Keep in mind:** Each domain still owns its own state and decision.
- **Details:** [Package guide](../../packages/dotmac-service-changes/README.md) · [Source and contract](../../packages/dotmac-service-changes/EXTRACTION.toml).

## Service access policy (`dotmac-service-access-policy`)

- **What it does:** Combines per-service policy observations into desired allow, restrict or deny decisions.
- **Use it when:** Applying prepaid, usage or collections restrictions to one service.
- **Keep in mind:** It does not change the whole customer's status or perform network commands.
- **Details:** [Package guide](../../packages/dotmac-service-access-policy/README.md) · [Source and contract](../../packages/dotmac-service-access-policy/EXTRACTION.toml).

## Positioning (`dotmac-positioning`)

- **What it does:** Records tracked-unit locations, collection grants, history and geofence facts.
- **Use it when:** Using location evidence with explicit purpose and retention controls.
- **Keep in mind:** Location alone does not decide service eligibility or operational action.
- **Details:** [Package guide](../../packages/dotmac-positioning/README.md) · [Source and contract](../../packages/dotmac-positioning/EXTRACTION.toml).
