# Inventory/Procurement source inventory

Audit date: 2026-08-18. This is as-built evidence, not cutover approval.

Pinned remote revisions:

| Repository | `origin/main` |
|---|---|
| `dotmac_erp` | `b969a889e8aba7255e32aa466960c22347c02fd8` |
| `dotmac_sub` | `510b80ca7fab4f54a57f261872f94b5e972c8eb6` |
| `dotmac_crm` | `60daaa2dd305696636632f48505ab784110a55d2` |
| `dotmac_starter_mt` | current working source; package not previously present |

The revisions were refreshed from their remotes before inspection. File/line
claims below must still be reverified at adoption time.

## Ruling

`dotmac-inventory` is **product-first from ERP**. ERP is the only qualifying,
production-used implementation of the requested stock capability. CRM is a
thin, mutable local copy whose sync explicitly pulls from ERP. Sub carries
rebuildable ERP catalogue/warehouse observations and product-owned material
requests, not a competing stock ledger.

The extracted unit is the stock owner named by ADR-0041: SKUs, warehouses,
movements, balances, reservations, lots/serials, and valuation. The pre-award
and commercial procurement workflow is audited here because the fleet matrix
currently groups it with inventory, but it is not smuggled into this package.

## ERP: qualifying source

ERP has 18 inventory model files, 31 inventory service files, and 21 files in
`tests/ifrs/inv` (59 directly named tests at the pinned revision). Its core
state includes:

| Capability | Source |
|---|---|
| SKU and tracking/cost policy | `app/models/inventory/item.py`, `item_category.py`; `app/services/inventory/item.py` |
| Warehouses and bins | `warehouse.py`, `warehouse_location.py`; `services/inventory/warehouse.py` |
| Movement ledger | `inventory_transaction.py`; `services/inventory/transaction.py` |
| Receipts/issues/transfers/adjustments | `services/inventory/transaction.py`; paired transfer tests in `test_inv_transaction_service.py` |
| Stock reads | `services/inventory/balance.py` |
| Reservations | `stock_reservation.py`; `services/inventory/stock_reservation.py`; lifecycle tests in `test_stock_reservation_service.py` |
| Lots and warehouse lot balances | `inventory_lot.py`, `inventory_lot_balance.py`; `lot_serial.py` |
| Serials and movement trail | `inventory_serial.py`; `services/inventory/serial.py`; serial tests/workflow tests |
| FIFO/WAC and period valuation | `fifo_valuation.py`, `wac_valuation.py`, `inventory_valuation.py`, `item_wac_ledger.py`; valuation suites |

Behaviours preserved in the first package include positive-quantity guards,
no negative stock, warehouse eligibility, paired transfer evidence, reservation
partial/fulfilled/cancelled/expired states, exact serial counts and uniqueness,
warehouse-scoped lot balances, quarantined-lot refusal, WAC receipt/issue
math, FIFO lot ordering, and zero stock/value convergence.

### Required generalisation delta

ERP's source is not copied whole:

- `organization_id` becomes the module's real `tenant_id` with composite
  tenant foreign keys and forced RLS.
- General Ledger posting, fiscal-period lookup, account selection, tax,
  supplier, projects, tickets, people, notifications, settings cache, web
  rendering, and ERPNext sync become product/integration seams.
- Source-document identity becomes an opaque reference. The module does not
  enumerate sales order, purchase order, manufacturing order, or work-order
  types.
- Source services use FastAPI exceptions, product settings, broad fallbacks,
  and compatibility snapshots. The module uses domain exceptions, a single
  flush-only writer, explicit locking, and rebuildable projections.
- ERP maintains item-level `average_cost` beside a warehouse WAC ledger. The
  package keeps warehouse-scoped valuation state in one projection and derives
  read models from it.

## CRM: non-qualifying copy and retirement target

CRM has five directly related tables in `app/models/inventory.py`:
`inventory_items`, `inventory_locations`, `inventory_stock`,
`inventory_reservations`, and product-owned `work_order_materials`.

`app/services/inventory.py` directly edits stock quantities and commits inside
the service. Reservation release/consume changes the mutable stock row without
an immutable movement ledger, lot/serial trace, valuation, tenant scope, or
concurrency lock. `app/services/dotmac_erp/inventory_sync.py` states that it
pulls items, warehouses, on-hand, and reserved quantities from ERP and writes
the CRM copy. It is an observation cache, not an authority to extract.

The only focused item-numbering suite proves generated/manual/disabled SKU
number selection. Number allocation belongs to the adopting product's
numbering port, not to Inventory.

At CRM cutover, product-specific work-order material links remain CRM-owned.
The stock copy and direct quantity writers retire or become a projection over a
deliberately adopted local Inventory owner.

## Sub: observation and product-workflow boundary

Sub's `FieldInventoryItem` and `FieldInventoryWarehouse` explicitly describe
ERP catalogue facts and rebuildable ERP warehouse catalogue data in
`app/models/field_material.py`. `app/services/field/inventory.py` exposes only
the active item lookup; its location method intentionally returns an empty list
until a full inventory port. The focused tests prove active-item filtering,
search, and that empty location boundary.

Sub owns material need and field-work consequences:
`FieldMaterialRequest`, its items, technician/person context, work-order/ticket/
project links, approval state, and fulfillment interpretation. Those rows do
not move into `dotmac-inventory`. If Sub later adopts the module, it links its
request to an opaque reservation/issue fact from a Sub-owned table.

Sub's asset inventory is also excluded. ONTs, CPEs, routers, network devices,
vehicles, technician custody, and subscriber assignment are Assets/Network/Sub
facts, not stock levels merely because their screens say “inventory”.

## Procurement split

ERP contains 11 pre-award/contract procurement tables plus Purchase Order and
Goods Receipt tables under Finance/AP. Plans, requisitions, RFQs, invitations,
quotations, evaluations, vendor prequalification, contracts, budgets,
approvals, and three-way match have distinct owners and dependencies.

The reusable Inventory owner accepts a receipt with an opaque procurement
source reference and records accepted stock/cost. A Procurement or Payables
owner decides whether an order/receipt is authorised and consumes the resulting
inventory fact. This preserves one stock writer without making Inventory the
owner of supplier selection or accounting.

## Adoption and retirement gates

First cutover: `dotmac_erp` after its Organization-to-Tenant identity mapping is
approved and implemented.

Shadow proof must replay the existing transaction ledger into the module and
compare, per tenant/item/warehouse and where relevant lot/serial:

- on-hand, reserved, and available quantity;
- WAC/FIFO layer values and total carrying value;
- active reservation remaining quantity and lifecycle;
- serial warehouse/status and lot balance; and
- paired transfer conservation across source and destination.

Only after exact package pinning, composed migration/RLS evidence, shadow
equality, rollback rehearsal, and product CI may ERP's old inventory writers be
retired. Sub/CRM adoption is separate and does not authorize shared database
access or provider-specific branches in the package.
