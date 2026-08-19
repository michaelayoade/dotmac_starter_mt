# ADR-0036: Inventory owns the stock ledger, traceability, and valuation facts

- Status: Accepted
- Date: 2026-08-18
- Deciders: Dotmac architecture programme
- Scope: `dotmac-inventory` and every product that adopts it
- Source evidence: `docs/inventories/inventory-sources.md`

## Context

The fleet decomposition groups inventory and procurement because ERP currently
implements both in one application. That source layout is not a reusable
boundary. ERP inventory code imports General Ledger, fiscal periods, settings,
notifications, people, projects, tickets, provider sync, and web concerns. CRM
keeps a smaller stock copy fed from ERP, while Sub correctly treats ERP item and
warehouse data as rebuildable observations for material-request workflows.

The reusable capability requested for the first extraction is narrower and
coherent: SKUs, warehouses, stock levels, receipts, issues, transfers,
reservations, lots/serials, and valuation.

## Decision

`dotmac-inventory` is the sole owner of local stocked-item identity and stock
state in an adopting application. It owns:

- SKU identity, base unit, tracking rules, and costing method;
- warehouse identity and receiving/shipping eligibility;
- immutable receipt, issue, transfer-leg, and adjustment movements;
- on-hand, reserved, available, and carrying-value projections;
- reservation lifecycle and its effect on available stock;
- lot and serial identity, warehouse custody, and movement traceability;
- weighted-average/FIFO/specific/standard cost calculations; and
- immutable as-of valuation snapshots, including lower-of-cost-and-NRV facts.

The module is tenant-only. Each adopting application installs its own
`mod_inventory` lineage and owns its local rows. Every table has
`tenant_id UUID NOT NULL`, composite tenant-preserving foreign keys, forced RLS,
and grants created in the same migration.

### One ledger, one projection writer

`stock_movements` is the immutable operational ledger. Quantity and value
deltas are signed. A transfer is one atomic operation represented by paired
outbound and inbound legs sharing a `movement_group_id`; it is never a direct
edit of two balance rows with no durable evidence.

`stock_balances` and `lot_balances` are rebuildable projections. Only
`dotmac_inventory.service` writes them, while holding the affected rows with
`SELECT ... FOR UPDATE`. Receipts, issues, transfers, reservations, lot/serial
updates, and their projection changes flush in the caller's transaction. The
module never commits or rolls back.

The same service owns valuation calculation. Finance consumes inventory
valuation facts and decides journal, fiscal-period, statutory, and tax
consequences; Inventory never writes a General Ledger table.

### Product seams

The module stores only opaque source and actor references. It has no foreign
key to purchase orders, suppliers, sales orders, work orders, projects,
tickets, people, Finance, CRM, Sub, or ERP tables.

The wider procurement workflow remains outside this first package:
procurement plans, requisitions, RFQs, quotations, bid evaluation, supplier
prequalification, purchase-order approval, contract administration, and
three-way match are not stock facts. A product or future procurement owner may
request a receipt using an opaque source reference; it may not write an
inventory balance or movement directly.

Similarly, a serialized stocked unit is not automatically a durable asset.
When a product capitalises or deploys it, that is an explicit handoff from an
inventory serial fact to the Assets owner. Inventory does not acquire custody,
maintenance, depreciation, impairment, or disposal policy.

Provider clients, remote credentials, webhooks, checkpoints, and retries stay
in Integrator connector plugins. An importer records observations and calls the
local inventory owner; it never assigns balances directly.

## Consequences

- ERP is the qualifying source and first cutover. Its inventory behaviour and
  parity tests are ported, while finance and product imports become typed seams.
- CRM's direct `inventory_stock` writer is retirement debt, not a second
  reference implementation.
- Sub's item/warehouse projections and material-request decisions remain
  product-owned observations until Sub deliberately adopts local inventory.
- Stock reads may use the projection, but reconciliation must be able to rebuild
  it from movements plus active reservations and report drift.
- No router ships in the first alpha; capability, permission, and audit-action
  declarations arrive only with guarded consumers.

## Cutover gate

ERP adoption requires an Organization-to-Tenant mapping, a shadow replay of its
current inventory transactions, equality checks for quantity/value/reservation
and lot/serial state, exact package pinning, composed migration evidence, and
retirement of the old writers. Publishing a package alone is not extraction.
