# Inventory cutover scoping — `dotmac-inventory` as ERP's first full proof slice

- **Status:** scoping analysis. Nothing here is an approval, a release, or a
  production authorization.
- **Date:** 2026-08-25
- **Starter revision read:** `origin/main` @ `39222359`, through the clean
  `docs/inventory-cutover-scoping` worktree. The Inventory package, ADR-0036,
  and its source inventory are byte-identical to the preserved analysis base
  `83aeb7a2`; only unrelated release-ledger material changed between them.
- **ERP revision read:** `origin/main` @ `8a40f8ec` — every ERP claim below was
  read with `git show origin/main:<path>` / `git grep origin/main`. The ERP
  working tree (branch `feat/kernel-ui-contract-alignment`, dirty) was **never**
  read. Where an ERP `path:line` appears, it is a line number in the
  `origin/main` blob.
- **Constraints honoured:** no test suite, database, container or server was
  run. Static reading and `git grep` only. No source, migration, package or ERP
  file was modified; this dossier and its inventory-index row are the only
  artifacts created.

## Reading key

Three dispositions are used throughout, and they mean precisely this:

| Term | Meaning in the clean-database programme |
|---|---|
| **migrate** | The fact becomes a `dotmac-inventory` fact and is carried into the new composed database by the governed opening pack. |
| **retain** | The concept stays **outside** `dotmac-inventory` — owned by the ERP assembly or another module — and is carried in by *that* owner's own governed import. "Retain" never means "left alone in the old database". |
| **end** | Not carried into the new database at all. Readable only in the legacy read-only ERP archive. |

---

## Not ready because

In priority order. Each blocker is decisive: the slice cannot be called ready
while any one of them stands.

### 1. Three checked-in governance documents still mandate historical ledger replay, which the clean-database ruling forbids

`docs/adr/0036-inventory-owns-stock-ledger-traceability-and-valuation.md:95-98`
(the cutover gate) reads:

> ERP adoption requires an Organization-to-Tenant mapping, **a shadow replay of
> its current inventory transactions**, equality checks for
> quantity/value/reservation and lot/serial state, exact package pinning,
> composed migration evidence, and retirement of the old writers.

`packages/dotmac-inventory/EXTRACTION.toml:45` (`first_cutover`):

> `dotmac_erp` after the Organization-to-Tenant mapping gate. **Replay the
> production-used ERP ledger**, prove quantity/value/reservation/lot/serial
> parity, compose the module lineage, then retire the old inventory writers.

`packages/dotmac-inventory/EXTRACTION.toml:46` (`shadow_and_drift`):

> Rebuild stock and valuation projections from immutable module movements plus
> active reservations; **compare them per tenant/item/warehouse/lot/serial
> against ERP's current ledger during shadow.** Any unexplained quantity or
> value delta blocks cutover.

And a third, non-ADR document repeats it —
`docs/inventories/inventory-sources.md:131-132`:

> Shadow proof **must replay the existing transaction ledger** into the module
> and compare, per tenant/item/warehouse and where relevant lot/serial: …

This is directly contradicted by the later ruling, which is already accepted
and already written down for the sibling domain.
`docs/adr/0041-accounting-owns-posting-and-ledger-evidence.md:117-135`
("Amendment, 2026-08-24 — ERP adopts through governed opening state"):

> Dotmac ERP will be recomposed as a fresh installation and **will not replay
> the legacy journal, posted-ledger or posting-batch history** into
> `dotmac-accounting`. … The module becomes authoritative at **one approved
> opening instant**. … Legacy ERP remains the read-only authority for all
> earlier transactions. … **No legacy transaction row is copied, no
> full-history equality is claimed, and no database restore is an admission
> mechanism.** Behaviour parity is proved from versioned accepted inputs on
> independently migrated clean databases.

ADR-0036 is dated 2026-08-18; the ADR-0041 amendment is dated 2026-08-24. The
inventory documents predate the ruling and have **not** received the equivalent
amendment. Exactly what must change is in § 1a below.

### 2. The module manifest declares no permissions, no capabilities, no audit actions and no routers

`packages/dotmac-inventory/src/dotmac_inventory/manifest.py:11-20` is the whole
declaration:

```python
module = ModuleManifest(
    code="inventory",
    version="0.1.0a1",
    core=False,
    short_code="inventory",
    migration_prefix="iv",
    migration_branch="inventory",
    tables=TENANT_TABLES,
    requires=(TENANT_SCOPE_CATALOG_V1.name, MODULE_DATABASE_ROLES_V1.name),
)
```

Confirmed: `permissions`, `capabilities`, `audit_actions`, `api_routers`,
`web_routers`, `nav`, `setting_domains`, `outbox_event_types` are all absent and
therefore default to empty tuples
(`packages/dotmac-kernel/src/dotmac_kernel/modules.py:148-186`). This was a
deliberate ADR-0036 decision at the time — `docs/adr/0036-…:90-91`: "No router
ships in the first alpha; capability, permission, and audit-action declarations
arrive only with guarded consumers." The cutover *is* the guarded consumer, so
the declarations are now due. Full enumeration in § 3 and § 4.

### 3. There is no opening-state command and no source-level idempotency anywhere in the module

Verified by exhaustive grep over
`packages/dotmac-inventory/src/dotmac_inventory/service.py` (1361 lines): no
occurrence of `idempot`, `execute_once`, `process_once`, `fingerprint` or
`opening`. `dotmac_kernel.idempotency` is never imported. `source_ref` is a plain
`String(240)` on `stock_movements` (`…/models.py:330`) with **no** unique
constraint — the only uniqueness on that table is
`uq_inventory_movements_tenant_id_id` (`…/models.py:290`). Consequences:

- Re-running an opening import would create a **second** set of opening
  movements and double every balance. Nothing in the module prevents it.
- `StockReservation` is the *only* table with a natural business key
  (`uq_inventory_reservation_ref`, `…/models.py:385-387`); movements, lots and
  serials have none.
- The kernel already owns the correct mechanism
  (`packages/dotmac-kernel/src/dotmac_kernel/idempotency.py:172` `execute_once`,
  ADR-0014) and the module does not use it.

### 4. There is no public read/query seam, so a thin ERP adapter cannot be written at all

`packages/dotmac-inventory/src/dotmac_inventory/__init__.py:47-86` exports 13
callables. Every one is a **writer** or a projection-repair function:
`create_item`, `create_warehouse`, `receive_stock`, `issue_stock`,
`issue_stock_evidence`, `adjust_stock`, `transfer_stock`, `reserve_stock`,
`cancel_reservation`, `expire_reservations`, `rebuild_balance`,
`record_valuation_snapshot`, plus `versions_dir`. There is no `get_on_hand`, no
`list_items`, no stock summary, no reservation query, no lot/serial lookup.

ERP's read surface that must be replaced is
`app/services/inventory/balance.py`'s `InventoryBalanceService` — nine methods:
`get_on_hand:168`, `get_reserved:233`, `get_available:276`,
`get_item_balance:303`, `get_item_stock_summary:356`, `get_low_stock_items:441`,
`get_batch_stock_levels:562`, `get_warehouse_inventory:665`, plus the two
allocation writers `allocate_inventory:707` / `deallocate_inventory:846`. Under
rule 1 of `AGENTS.md` (adapters never query the DB), an ERP thin adapter cannot
legally reach into `mod_inventory` tables itself, so **today it has nothing to
call**. Proposed signatures in § 4.

### 5. FIFO lot selection is not implemented — the module refuses a lot-tracked issue unless the caller names the lot

`packages/dotmac-inventory/src/dotmac_inventory/service.py:587-588`:

```python
    if item.track_lots and command.lot_id is None:
        raise TraceabilityRequired("lot-tracked issue requires a lot")
```

and `:644-647` — a FIFO or specific-identification item raises
`TraceabilityRequired("costing method requires a lot")` when `lot is None`.
`create_item` **forces** `track_lots=True` for FIFO and specific-identification
items (`…/service.py:359-361`), so every FIFO item is unissuable without an
explicit lot id.

The pure `consume_fifo` function exists at
`packages/dotmac-inventory/src/dotmac_inventory/valuation.py:80` but is called by
nothing: grep for `consume_fifo` across the package returns only its own
definition and its `__all__` entry — it is **not** imported by `service.py` and
**not** re-exported from `__init__.py`. ERP by contrast picks layers
automatically in `app/services/inventory/fifo_valuation.py:213`
(`consume_inventory_fifo`). This is a behaviour parity gap, not a configuration
difference.

### 6. `dotmac-inventory` 0.1.0a1 is the only published version, and it cannot carry the cutover

`packages/dotmac-inventory/CHANGELOG.md:3-8` records 0.1.0a1 as published from
`bfc112fc` by release run `32481134625`, and states plainly: "Publication is
supply-chain evidence only; it composes no product and moves no authority."
`EXTRACTION.toml:48` (`next_action`) and `:37` (`contract_consumers = []`) agree.
Every gap in blockers 2–5 requires code, so a new release is unavoidable before
ERP can pin anything. Per `AGENTS.md` rule 30 / Governance ADR-0013, a
pinnable-version claim needs an external oracle (`release_run` / `peeled_tag`),
which does not yet exist for any version after 0.1.0a1.

### 7. There is no outbound evidence contract, so Finance/Accounting cannot apply a posting policy to a module movement

The accounting seam is: **Inventory owns costing method, movement value and
immutable valuation evidence; Finance/Accounting owns whether those facts cause
immediate GL posting or periodic aggregation, plus account selection and
fiscal-period checks.** The module currently satisfies the *negative* half of
that contract perfectly (see the "not blockers" list below) but implements
**none** of the positive half.

The only evidence type in the package is `StockIssueEvidence`
(`packages/dotmac-inventory/src/dotmac_inventory/contracts.py:106-121`), returned
only by `issue_stock_evidence` (`…/service.py:742`). It covers **issues only** —
there is no receipt, transfer-leg or adjustment evidence type — and it omits
`costing_method`, which a posting policy needs in order to explain the value it
is posting. Receipts, transfers and adjustments return a bare `StockMovement`
ORM row (`…/service.py:518, 833, 978`), which is a persistence object, not a
contract, and which a thin adapter must not be handed. Shape proposed in § 4.4.

The module also declares no `outbox_event_types`
(`manifest.py:11-20`), so there is today no declared route by which any evidence
could leave the process. Whether the evidence reaches Accounting in-process or
through the outbox is **undecided** and is called out as an open decision in
§ 4.4.

### Three things that are NOT blockers (checked, and they clear)

- **The module's accounting boundary is currently correct, and this was
  verified rather than assumed.** An exhaustive grep over every `.py` file in
  `packages/dotmac-inventory/src/dotmac_inventory/` (including the migration)
  for `journal`, `general_ledger`, `gl_account`, `posting`, `account_id`,
  `fiscal`, `resolve_value`, `valuation_mode`, `real_time`, `outbox`, `enqueue`
  returns **zero matches**. The package's complete import surface is
  `dotmac_kernel.models`, `dotmac_kernel.namespaces`, `dotmac_kernel.modules`,
  `dotmac_kernel.prerequisites`, `dotmac_kernel.db.conflict_savepoint`,
  `dotmac_kernel.migrations.verify`, plus `sqlalchemy`/stdlib. So today the
  module: reads no accounting-mode setting, branches on no posting timing,
  writes no GL row, and holds no `fiscal_period_id` column anywhere in
  `models.py`. The requirement is to **keep** it that way while adding the
  outbound evidence contract of blocker 7 — not to fix a violation.

- **The Organization-to-Tenant mapping gate named by ADR-0036:95 is already
  satisfied.** `dotmac_erp` `app/tenancy.py:26-34`
  (`OrganizationTenantContext.for_organization`) returns
  `cls(organization_id=organization_id, tenant_id=organization_id)` — an identity
  mapping. `public.tenants` is hosted by ERP's own revision
  `20260813_tenant_projection` and written by the single writer
  `app/services/tenant_projection.py`. `EXTRACTION.toml:48` still lists "prepare
  ERP's Organization-to-Tenant mapping" as a pending next action; it is not
  pending.
- **Both prerequisites the inventory lineage declares are already bound in
  ERP.** `manifest.py:19` requires `TENANT_SCOPE_CATALOG_V1` and
  `MODULE_DATABASE_ROLES_V1`; `dotmac_erp` `app/migration_bindings.py:62-71`
  already binds both (to `20260813_tenant_projection` and
  `20260814_database_roles`). Composition needs no new prerequisite binding —
  only a `COMPOSED_MODULE_LINEAGES` entry and an `alembic.ini`
  `version_locations` entry (`dotmac_erp` `alembic.ini:16`).

### 1a. What must change in the three documents

| Document | Line(s) | Current text (abridged) | Required change |
|---|---|---|---|
| `docs/adr/0036-…-valuation.md` | 93-98 (`## Cutover gate`) | "requires an Organization-to-Tenant mapping, a shadow replay of its current inventory transactions, equality checks for quantity/value/reservation and lot/serial state…" | Add an `## Amendment, <date> — ERP adopts through governed opening state` section mirroring ADR-0041:117-143. It must state: no `inv.inventory_transaction` row is replayed; the module becomes authoritative at one approved opening instant; the admission mechanism is a typed opening-pack command, not a copy or restore; legacy ERP is the read-only authority for all earlier movements; parity is proved from versioned synthetic inputs on an independently migrated clean database. It must also repeat ADR-0041:137-143 verbatim in substance — ADR-0031's single-transaction sealing cannot be honestly claimed across two databases, so the production switch stays blocked on an accepted cross-database write-fence decision. |
| `docs/adr/0036-…-valuation.md` | 82-83 (`## Consequences`) | "ERP is the qualifying source and first cutover. Its inventory behaviour and parity tests are ported…" | Unchanged in substance (behaviour and tests *do* port) — but add that data does not. |
| `docs/adr/0036-…-valuation.md` | 88-89 | "Stock reads may use the projection, but reconciliation must be able to rebuild it from movements plus active reservations and report drift." | **Keep exactly as is.** This is the post-opening drift rule and it survives; it is about rebuilding from the module's *own* movements, not ERP's. |
| `packages/dotmac-inventory/EXTRACTION.toml` | 45 (`first_cutover`) | "…Replay the production-used ERP ledger, prove quantity/value/reservation/lot/serial parity…" | Replace "Replay the production-used ERP ledger" with the opening-pack admission: "Import one approved opening pack through the module's typed opening command on a clean composed database; reconcile the pack's declared control totals against the module's rebuilt projections; …". Note the Organization-to-Tenant gate is already met (`app/tenancy.py:34`). |
| `packages/dotmac-inventory/EXTRACTION.toml` | 46 (`shadow_and_drift`) | "…compare them per tenant/item/warehouse/lot/serial **against ERP's current ledger during shadow**." | Delete the comparison against ERP's ledger. Replace with: rebuild from module movements plus active reservations and compare against the **opening pack's own declared totals** at the opening instant, and against the module's own projections thereafter. Drift detection stays; the ERP-ledger oracle goes. |
| `packages/dotmac-inventory/EXTRACTION.toml` | 48 (`next_action`) | "…then prepare ERP's Organization-to-Tenant mapping and shadow replay." | Rewrite: the mapping exists; the next action is the opening-pack contract, the manifest declarations, the read seam and the idempotency design. |
| `packages/dotmac-inventory/EXTRACTION.toml` | 47 (`local_copy_retirement`) | ERP retires `app/models/inventory` and `app/services/inventory` writers after parity. | Keep, but make it exhaustive against § 2 of this document — the current wording names two directories and misses writers in `app/services/finance/ap/`, `app/services/finance/ar/`, `app/services/sync/`, `app/services/operations/inv_web.py`, `app/tasks/inventory.py` and `scripts/`. |
| `docs/inventories/inventory-sources.md` | 126-142 (`## Adoption and retirement gates`) | "Shadow proof must replay the existing transaction ledger into the module and compare…" | Same replacement as `EXTRACTION.toml:46`. The five comparison bullets (`:134-138`) are all still wanted — they become the **opening-pack reconciliation totals** of § 5, measured against the pack, not against ERP. |

One thing the amendment must **not** do: weaken the "one ledger, one projection
writer" rule at `docs/adr/0036-…:41-52`. Opening state enters as *movements*
(kind `adjustment`, in a dedicated opening `movement_group_id`), never as a
direct write to `stock_balances`. That is what keeps `rebuild_balance`
(`…/service.py:1174-1230`) able to reproduce the opening figure from the ledger
alone.

---
## 1. Legacy relation classification

Enumerated from the models, not from memory. The source of the enumeration is
`git show origin/main:app/models/inventory/__init__.py` (36 exported names over
16 model files), cross-checked against the production table catalogue
`docs/inventories/erp-production-catalog-2026-08-15.tsv` in the ERP repo, which
lists exactly **21 tables in schema `inv`**. The two lists agree with no
residue: every `inv.*` table in production has a model, and every model has a
production table.

### 1.1 Schema `inv` — all 21 tables

| # | Table | Model (`origin/main`) | Disposition | Reason |
|---|---|---|---|---|
| 1 | `inv.item` | `app/models/inventory/item.py:44` | **migrate (subset)** | SKU identity, base UOM, costing method, tracking rules are exactly ADR-0036's owned set (`docs/adr/0036-…:27`). Only a subset maps: `item_code→sku`, `item_name→name`, `base_uom`, `costing_method`, `standard_cost`, `currency_code`, `track_lots`, `track_serial_numbers→track_serials`, `is_active`, `description`. See § 1.4 for the 22 columns that do **not** map. |
| 2 | `inv.item_category` | `app/models/inventory/item_category.py:27` | **retain** (ERP/Accounting) | It is a GL account-mapping table wearing a taxonomy hat: `inventory_account_id:61`, `cogs_account_id:65`, `revenue_account_id:69`, `inventory_adjustment_account_id:73`, `purchase_variance_account_id:77`. Account selection is Finance/Accounting-owned; Inventory must never hold it. `reorder_point:83`/`minimum_stock:84` are planning policy, also not stock facts. |
| 3 | `inv.warehouse` | `app/models/inventory/warehouse.py:25` | **migrate (subset)** | Warehouse identity + receiving/shipping eligibility is owned (`docs/adr/0036-…:28`). Maps: `warehouse_code→code`, `warehouse_name→name`, `description`, `is_receiving→allows_receipts`, `is_shipping→allows_issues`, `is_active`. See § 1.4 for the rest. |
| 4 | `inv.warehouse_location` | `app/models/inventory/warehouse_location.py:23` | **end** | Bins. Static evidence says the capability is dormant — see § 1.5. No production caller creates, lists or deactivates a row. Do **not** add bins to the module on this evidence. |
| 5 | `inv.inventory_transaction` | `app/models/inventory/inventory_transaction.py:31` | **end** | The historical movement ledger. Under the clean-database ruling these rows are **not replayed** (ADR-0041:120-122 pattern). They stay in the read-only legacy archive. Movements from the opening instant onward are `mod_inventory.stock_movements`. |
| 6 | `inv.item_wac_ledger` | `app/models/inventory/item_wac_ledger.py:26` | **end (as a table); its live rows seed the opening pack** | It is ERP's per-`(item, warehouse)` balance projection: `current_wac:66`, `quantity_on_hand:72`, `total_value:78`. The *table* ends; the *values it holds at the opening instant* become the pack's declared quantity and carrying value, admitted as opening movements and reprojected into `mod_inventory.stock_balances`. `last_transaction_id:85` points into the archived ledger and does not travel. |
| 7 | `inv.inventory_lot` | `app/models/inventory/inventory_lot.py:33` | **migrate (active lots only, subset)** | Lot identity and traceability is owned (`docs/adr/0036-…:32`). Maps: `lot_number→code`, `supplier_lot_number→supplier_lot_ref`, `manufacture_date`, `expiry_date`, `received_date→received_at`, `unit_cost`. `is_active:87` + quarantine state fold into `Lot.status`. Lots with zero balance everywhere are **end**. |
| 8 | `inv.inventory_lot_balance` | `app/models/inventory/inventory_lot_balance.py:31` | **migrate (open balances only)** | Warehouse-scoped lot balance → `mod_inventory.lot_balances`. `quantity_allocated→quantity_reserved`; `quantity_available:75` is a stored column in ERP and a **derived property** in the module (`…/models.py:222-224`), so it does not travel. `is_quarantined:80`/`quarantine_reason:81`/`qc_status:82` fold into `Lot.status` + `Lot.quarantine_reason` (which the module holds on the lot, not the balance). |
| 9 | `inv.inventory_serial` | `app/models/inventory/inventory_serial.py:25` | **migrate (serials in custody)** | Serial identity + warehouse custody is owned (`docs/adr/0036-…:32`). Maps: `serial_number`, `warehouse_id`, `lot_id`, `status`. `location_id:66` does **not** travel (bins are dormant). `notes:88` has no module column — **end**. |
| 10 | `inv.inventory_serial_movement` | `app/models/inventory/inventory_serial.py:108` | **end** | The historical serial trail, and it is a *movement* table — same ruling as row 5. Forward serial evidence is `mod_inventory.movement_serials`. The *current* warehouse/status of each serial is carried by row 9; the history is archive-only. |
| 11 | `inv.stock_reservation` | `app/models/inventory/stock_reservation.py:52` | **migrate (ACTIVE reservations only)** | Reservation lifecycle is owned (`docs/adr/0036-…:31`). Only `RESERVED` and `PARTIALLY_FULFILLED` rows travel; `FULFILLED`/`CANCELLED`/`EXPIRED` are settled history — **end**. `quantity_cancelled:104 → quantity_released`. `source_type:111`+`source_id:115`+`source_line_id:116` collapse into the module's opaque `reservation_ref` (§ 4 flags the round-tripping problem). `priority:130` has **no module column** — see § 4. |
| 12 | `inv.inventory_valuation` | `app/models/inventory/inventory_valuation.py:26` | **end (history); one opening snapshot is newly recorded** | Immutable as-of valuation snapshots are owned (`docs/adr/0036-…:34`), but historical ones are not replayed. Exactly one new `mod_inventory.valuation_snapshots` row per `(item, warehouse[, lot])` is recorded **at** the opening instant from the pack's carrying values, through the module's normal `record_valuation_snapshot` writer. `fiscal_period_id:53` and `write_down_journal_entry_id:116` do not travel — both are Finance-owned. |
| 13 | `inv.inventory_count` | `app/models/inventory/inventory_count.py:43` | **end (rows); the CAPABILITY is added to the module** | Historical count documents are not replayed. Cycle-count evidence and reconciliation are an explicit ADD to Inventory (see § 1.5 for the activity proof and § 4 for the tables the module must gain). `fiscal_period_id:69`, `adjustment_journal_entry_id:103`, `approved_by_user_id:113` do not travel — Finance/Accounting and people identity. |
| 14 | `inv.inventory_count_line` | `app/models/inventory/inventory_count_line.py:25` | **end (rows); CAPABILITY added** | Same as row 13. `location_id:60` drops with bins. |
| 15 | `inv.inventory_return` | `app/models/inventory/inventory_return.py:48` | **retain** (ERP) | Returns policy is explicitly retained. The row stays an ERP-owned document; only its *stock effect* becomes a module command. `posted_transaction_id:130` and `source_transaction_id:125` currently FK into `inv.inventory_transaction` — after cutover they become opaque module movement ids, which means an ERP-side column type/semantics change, not a move. |
| 16 | `inv.material_request` | `app/models/inventory/material_request.py:61` | **retain** (ERP) | Material-request workflow is explicitly retained. Also carries CRM/ERPNext sync identity (`erpnext_id:152`, `crm_id:153`, `source_system:159`) which is transport state, not stock. |
| 17 | `inv.material_request_item` | `app/models/inventory/material_request.py:198` | **retain** (ERP) | Same. `serial_numbers:258` is an `ARRAY(Text)` of *requested* serials — a request, not custody; custody stays in `mod_inventory.serials`. |
| 18 | `inv.price_list` | `app/models/inventory/price_list.py:37` | **retain** (ERP/Sales) | Price lists explicitly retained. ADR-0036 does not list pricing among the owned facts (`:27-34`). |
| 19 | `inv.price_list_item` | `app/models/inventory/price_list.py:117` | **retain** (ERP/Sales) | Same. |
| 20 | `inv.bill_of_materials` | `app/models/inventory/bom.py:41` | **retain** (ERP/Manufacturing) | BOM/manufacturing definitions explicitly retained. |
| 21 | `inv.bom_component` | `app/models/inventory/bom.py:121` | **retain** (ERP/Manufacturing) | Same. Note `warehouse_id:169` is a *default issuing warehouse* on a definition — it becomes an opaque module warehouse id held by ERP. |

### 1.2 Inventory-adjacent tables outside schema `inv`

These are not in `app/models/inventory/` but hold inventory state or drive it,
so leaving them unclassified would leave a hole.

| Table | Model | Disposition | Reason |
|---|---|---|---|
| `ap.invoice_inventory_receipt_approval` | `app/models/finance/ap/invoice_inventory_receipt_approval.py:25` | **retain** (Payables) | It is an approval document (ADR-0026: approvals decide approval, never the transition). Its `inventory_transaction_id:103` becomes an opaque module movement id. `warehouse_id:65` and `item_id:60` become opaque module ids. |
| `ap.goods_receipt`, `ap.goods_receipt_line` | `app/models/finance/ap/goods_receipt.py`, `…/goods_receipt_line.py` | **retain** (Payables/Procurement) | Explicitly outside the package by `docs/adr/0036-…:64-69` and `docs/inventories/inventory-sources.md:114-124`. A goods receipt *requests* a module receipt with an opaque source reference. |
| `core_org.location` | referenced by `inv.warehouse.location_id` FK (`app/models/inventory/warehouse.py:53-56`) | **retain** (People/Org) | **This is not a bin.** It is the organisational/geographic site table owned by HR/Org — its writers are `app/services/people/hr/organization.py` via `app/api/people/hr.py:297,274,346,328`. Do not confuse it with `inv.warehouse_location`. |

### 1.3 CRM and Sub relations named by `EXTRACTION.toml`

Out of scope for **this** cutover (ERP only) but recorded so the ledger is not
silently incomplete. `EXTRACTION.toml:50-67` lists them:

| Product | Relations | Disposition for the ERP slice |
|---|---|---|
| `dotmac_crm` | `inventory_items`, `inventory_locations`, `inventory_stock`, `inventory_reservations` (`app/models/inventory.py`) | **retain in CRM, unchanged by this slice.** `EXTRACTION.toml:51-55` marks CRM a `legacy_writer` with `retirement_required = true`, but that is a separate adoption. `docs/inventories/inventory-sources.md:82-95` characterises it as an observation cache fed from ERP. **Not verified by me** — I did not read the CRM repo in this task. |
| `dotmac_sub` | `FieldInventoryItem`, `FieldInventoryWarehouse`, `FieldMaterialRequest` (`app/models/field_material.py`) | **retain in Sub.** `EXTRACTION.toml:62-66` marks it `inventory_only`, `retirement_required = false`. **Not verified by me** — Sub repo not read in this task. |

### 1.4 Columns that do NOT survive the move (the "generalisation delta", made concrete)

`docs/inventories/inventory-sources.md:59-75` asserts a generalisation delta in
prose. Here it is column by column, because the opening pack has to say where
each one goes.

**`inv.item` — 22 columns with no module counterpart** (`app/models/inventory/item.py`):

| Column(s) | Line | Where it goes |
|---|---|---|
| `item_type` | `:72` | ERP product table. The module has no item-type concept; `ItemType` includes non-stocked kinds. |
| `category_id` | `:77` | ERP product table (FK to retained `inv.item_category`). |
| `purchase_uom`, `sales_uom` | `:85-86` | ERP product table (Procurement/Sales concerns). |
| `last_purchase_cost`, `average_cost` | `:95-98` | **End as stored fields.** `docs/inventories/inventory-sources.md:73-75` already rules this: warehouse-scoped valuation lives in one projection (`stock_balances.current_unit_cost`) and item-level read models are derived. Any ERP screen reading `item.average_cost` must be rewritten against a module read (see § 4). |
| `list_price` | `:102` | ERP/Sales (price lists). |
| `track_inventory` | `:105` | ERP product table. The module has no "not stocked" state — a non-stocked ERP item simply gets no `mod_inventory.items` row. |
| `reorder_point`, `reorder_quantity`, `minimum_stock`, `maximum_stock`, `lead_time_days` | `:112-120` | ERP planning table. Needed by `get_low_stock_items` (`app/services/inventory/balance.py:441`) — see § 4, the read seam must support a caller-supplied threshold. |
| `weight`, `weight_uom`, `volume`, `volume_uom` | `:123-126` | ERP product table. |
| `barcode`, `manufacturer_part_number` | `:129-130` | ERP product table. |
| `tax_code_id`, `is_taxable` | `:135-138` | Tax owner (ADR-0045). |
| `inventory_account_id`, `cogs_account_id`, `revenue_account_id` | `:141-148` | **Accounting.** Never Inventory — this is the GL-account-selection boundary the refined ruling names. |
| `default_supplier_id` | `:154` | Procurement (ADR-0050). |
| `is_purchaseable`, `is_saleable` | `:160-161` | ERP product table. |
| `ERPNextSyncMixin` fields | `:44` (mixin) | Integrator transport state — `docs/adr/0036-…:76-78`. |

**`inv.warehouse` — 8 columns with no module counterpart** (`app/models/inventory/warehouse.py`):
`location_id:53` (→ `core_org.location`, ERP), `address:60` JSONB (ERP),
`contact_name/phone/email:63-65` (ERP), `is_consignment:70` and `is_transit:71`
(**no module concept** — see § 4), `cost_center_id:74` (Accounting), plus the
`ERPNextSyncMixin` fields.

**`inv.inventory_transaction` — fields with no module counterpart**
(`app/models/inventory/inventory_transaction.py`), listed because they are the
reason the archive matters:
`fiscal_period_id:63` (NOT NULL — a hard Finance coupling in the schema itself;
`mod_inventory.stock_movements` deliberately has none),
`location_id:79`/`to_location_id:95` (bins), `uom:101` (the module reads UOM from
the item, not per movement), `total_cost:103` (module stores signed
`value_delta`), `quantity_before:112` (module stores only `quantity_after`),
`source_document_type:116`/`source_document_id:117`/`source_document_line_id:120`
and `reason_code:127` (**all collapse into one opaque `source_ref` String(240)** —
§ 4 flags this as lossy), `journal_entry_id:130` (Accounting),
`created_by_user_id:135` (→ opaque `actor_ref`).

### 1.5 The two conditional ADDs — activity proof

The brief allows adding cycle-count evidence and warehouse bins **only if
current callers prove them active**. Here is what static evidence shows.

**Warehouse bins (`inv.warehouse_location`): INACTIVE. Do not add.**

`WarehouseService` defines three bin writers/readers —
`create_location` (`app/services/inventory/warehouse.py:147`),
`list_locations` (`:415`), `deactivate_location` (`:553`). A `git grep` for each
name across `origin/main -- app/ scripts/` returns **no production caller**: the
only non-test hits are `app/api/people/hr.py:274,297,346,328` and
`app/services/people/hr/web/location_web.py:70,215,333` — and those call the
*HR* `OrganizationService`, a different class operating on `core_org.location`
(confirmed by the FK at `app/models/inventory/warehouse.py:55`). The only callers
of the `WarehouseService` methods are
`tests/ifrs/inv/test_warehouse_service.py:133,151,175,245,348,360,374,390`.
There is no `/warehouses/{id}/locations` route in `app/web/inventory.py` (I read
every `@router.` decorator in the 2148-line file) and none in
`app/api/inventory/__init__.py`.

Stronger than dormant — **the sole writer cannot execute.**
`app/services/inventory/warehouse.py:184-198` constructs
`WarehouseLocation(... description=input.description, ... is_pickable=input.is_pickable, ...)`,
but the model has **neither** column: `app/models/inventory/warehouse_location.py:34-81`
spells the flag `is_picking` (`:65`) and has no `description` at all, and the DDL
agrees (`alembic/versions/create_ifrs_schemas.py:2523-2546`). A SQLAlchemy
declarative constructor with an unmapped keyword raises `TypeError` before any
`db.add`. Corroborating: `tests/ifrs/inv/test_warehouse_service.py:120-122`
carries an explicit note that `test_create_location_success` was *removed*
because the service "creates a real `WarehouseLocation` SQLAlchemy model which
cannot be easily mocked", and the three surviving tests (`:124`, `:138`, `:155`)
all assert failures raised *before* line 184. The test file even defines a
`MockWarehouseLocation` (`:23`) precisely because the real one cannot be built.

No bin logic exists either: nothing anywhere reads `is_picking`,
`is_receiving`, `is_shipping` or `is_quarantine` off `WarehouseLocation`, and
nothing walks `parent_location_id` (`app/models/inventory/warehouse_location.py:50-54`).
The `location_id` columns on inventory tables are inert pass-throughs — the
grep census in § 4.6 finds no stock decision reading bin semantics. The
enforced tenancy baseline agrees:
`tests/integration/tenant_table_inventory.tsv` classifies
`inv warehouse_location` as `inherited` with **no RLS and zero policies**, while
`inv inventory_count` is `direct` with RLS enabled+forced and 4 policies — the
count table went through the tenancy programme, the bin table did not.

**Cycle counts (`inv.inventory_count`, `inv.inventory_count_line`): ACTIVE. Add
the capability.** Both surfaces are live in a default deployment: `app/main.py:140`
puts `"inventory"` in `_ALL_MODULES` and `app/main.py:148-153` makes an empty
`ENABLED_MODULES` mean all of them, so `app/main.py:826-832` mounts both the
JSON router (`/api/v1/inventory`) and the web router (`/inventory`). Ten live
HTML count routes (`app/web/inventory.py:1426,1454,1468,1482,1498,1519,1534,1549,1564,1582`),
five live JSON routes (`app/api/inventory/__init__.py:863,893,908,937,967`), three
rendered templates (`templates/inventory/counts.html` at
`app/services/operations/inv_web.py:1006`, `count_form.html` at `:2330`,
`count_detail.html` at `:5069`), and real navigation
(`templates/inventory/index.html:201-205`, `templates/operations/dashboard.html:209`
backed by live tallies at `app/services/operations/dashboard_web.py:88-113`).
Three of the JSON routes are pinned in the architecture contract snapshot
`tests/architecture/openapi_contract_surface.json:2009,2024,2036`.

**But note what "active" does and does not mean here.** No seed, fixture, demo
script or migration creates a single `InventoryCount` row (`scripts/` and
`alembic/` contain DDL and indexes only —
`alembic/versions/create_ifrs_schemas.py:3548-3549`,
`alembic/versions/add_inventory_extensions.py:374-377`,
`alembic/versions/20260206_add_inv_count_indexes.py`). Liveness here is *code
reachability*, not proof of production usage. **Static evidence cannot settle
whether production holds count rows.** What would settle it:
`SELECT count(*) FROM inv.inventory_count;` and
`SELECT count(*), count(location_id) FROM inv.inventory_count_line;` against the
production database. Same for bins:
`SELECT count(*) FROM inv.warehouse_location;` — the broken constructor makes a
non-zero count essentially impossible *via application code*, but a hand-run SQL
insert or a pre-refactor historical row cannot be excluded statically.

---

## 2. Legacy writer and caller retirement ledger

This is a writer ledger, not a directory-deletion proposal. A caller that owns
a procurement, sales, asset or Finance decision remains; only its direct access
to legacy Inventory state retires. Conversely, a file inside
`app/services/inventory/` is not automatically deleted when it owns one of the
retained documents in § 1.1.

### 2.1 Inventory-owned writers that move

| Legacy owner at ERP `origin/main` | What it writes now | Cutover disposition |
|---|---|---|
| `app/services/inventory/item.py` | stocked-item identity plus ERP product, tax, price, supplier and GL fields | **Split.** Item identity/tracking/costing calls move to module commands. ERP retains the non-stock columns enumerated in § 1.4 in a product-side relation. Direct `inv.item` writes end. |
| `app/services/inventory/warehouse.py` | warehouse identity and the broken/dormant bin surface | Warehouse identity moves. The `WarehouseLocation` surface ends; it is not ported without production evidence. ERP retains organisation-site, contact, consignment, transit and cost-centre metadata. |
| `app/services/inventory/transaction.py` | receipt/issue/transfer/adjustment movements, lot/serial state, WAC projection, and three inline Finance decisions | Movement/cost/traceability writes move. `_is_real_time_valuation_enabled:1230` and `_post_inventory_transaction:1248` are **removed from Inventory**, not ported; see § 2.4. |
| `app/services/inventory/balance.py` | legacy lot projection, availability allocation and all stock reads | Projection writes and reads move to the module. Product callers receive immutable query DTOs; no adapter queries `mod_inventory` directly. |
| `app/services/inventory/stock_reservation.py` | reservation lifecycle and legacy allocated quantity | Moves to module reservation commands. Its callers retain their sales-order decisions and store opaque reservation ids. |
| `app/services/inventory/lot_serial.py` and `serial.py` | lot balance/custody/quarantine and serial movement history | Move to module commands and traceability queries. No product FK crosses into `mod_inventory`. |
| `app/services/inventory/fifo_valuation.py` and `wac_valuation.py` | FIFO/WAC layers, consumption and valuation rows | Move after blocker 5 is closed. `scripts/archive/rebuild_inventory_wac_ledger.py` ends; module reconciliation replaces it. |
| `app/services/inventory/count.py` | count header/lines and adjustment creation | Moves only after the cycle-count ADD in § 4.5. Historical rows end. The failure-masking behaviour in § 2.5 is explicitly excluded from parity. |
| `app/models/inventory/` relations classified **migrate/end** in § 1.1 | persistence identity | Retire after the writer seal. Relations classified **retain** remain product-owned, but any FK to an ending `inv.*` relation becomes an opaque module reference. |

`app/tasks/inventory.py` and every inventory maintenance script become thin
scheduled adapters over module repair/expiry commands or end. They may not
retain a second projection writer. The cutover change needs a static guard over
**all entry-point families** (routes, tasks, scripts, CLI and workers), not a
check limited to `app/services/inventory` (ADR-0018 / hard rule 25).

### 2.2 Cross-domain callers that remain but must be rewired

The following list is derived from every production reference to
`InventoryTransactionService`, `InventoryBalanceService`,
`StockReservationService`, `InventorySerialService`, `WACValuationService` and
`InventoryCountService` under ERP `origin/main -- app/ scripts/`.

| Retained owner | Current direct Inventory dependency | Required seam |
|---|---|---|
| Payables/Procurement | `finance/ap/auto_inventory_receipt.py:235`, `goods_receipt.py:608`, `inventory_receipt_approval.py:482`; `supplier_invoice.py:1769` reads balance | Retain authorization/documents; submit `ReceiptCommand` with one idempotency key and store returned movement id/evidence. Balance reads use the query port. |
| Receivables/Sales | `finance/ar/ar_inventory_integration.py:117,343,351`; `sales_order.py:475,758,912` and its web adapter create/fulfil/cancel reservations | Retain sales decisions; issue/return stock and reservations through typed commands. No AR service writes a module balance. |
| Material requests and returns | `inventory/material_request_web.py:1308,1338,1363`; `return_web.py:424` | Retain the documents in ERP. Their accepted transition requests the module effect and stores opaque movement ids. |
| Assets/Maintenance | `people/assets/maintenance_service.py:383,394` | Assets retains the maintenance decision; the consumable-stock issue is an Inventory command. Durable-unit handoff remains explicit (ADR-0036). |
| Reorder/notifications/admin views | `inventory/reorder.py:154`, `notifications.py:32`, `admin/crm_sync_web.py:521` | Read DTOs only. Reorder thresholds remain caller-supplied planning policy, not columns copied into Inventory. |
| Finance reporting | `finance/rpt/inventory_valuation.py` uses `WACValuationService` | Consume valuation query/evidence DTOs; never import module ORM models. |
| Operations web | `operations/inv_web.py` calls count, serial and WAC services | Becomes validate/authorize/delegate/render glue. Module routers may replace the owned parts; ERP retains material requests, returns, price lists and BOM UI. |

### 2.3 Transport surfaces end, not reappear as module options

`app/services/sync/crm/inventory.py`,
`app/services/sync/crm/procurement.py`,
`app/services/sync/inventory_push_service.py` and the inventory portions of
`app/services/admin/crm_sync_web.py` are external-connection debt. Per ADR-0024
they retire behind Integrator connector/app-sync bindings. The Inventory module
exposes provider-neutral commands, queries and outbox evidence only; it never
imports a CRM/Sub/provider client, names a provider, stores provider
credentials, or owns retry/checkpoint state. These ERP direct-sync surfaces are
retirement paths, **not** an Inventory cutover dependency and not a
service-guarded exception to preserve. Only provider-neutral evidence and
correlation survive, and only where a named Integrator consumer exists.

### 2.4 The Accounting boundary, site by site

Today `InventoryTransactionService` reads
`inventory_valuation_mode` (`transaction.py:1230-1245`) and conditionally calls
the GL adapter after a receipt (`:509-515`), issue (`:889-895`) and adjustment
(`:1217-1223`). That entire branch retires from Inventory. The account lookup
in `finance/ap/posting/helpers.py:122-129` and
`finance/ar/ar_inventory_integration.py:364-428` remains Finance-owned.

The required seam is exactly:

> Inventory movement → typed immutable quantity/value evidence →
> Finance/Accounting posting policy → immediate journal or periodic
> aggregation.

Consequently Inventory must not read `inventory_valuation_mode`, select an
account, branch on posting timing, import a Finance service, or write a journal
row. Finance retains those decisions and consumes the evidence contract in
§ 4.4. A same-process coordinator may call both owners; neither module imports
the other. Cross-application delivery uses the declared outbox event carrying
the same evidence.

### 2.5 Cycle-count defects are exclusions, not parity requirements

The active count surface contains more than the four initially reported
swallows. The web adapter catches and logs every failure from start, complete,
post, single-line record and bulk record
(`operations/inv_web.py:5282-5291,5306-5314,5332-5341,5388-5410,5471-5486`)
and still returns a normal `303`. It also converts invalid quantities to zero
(`:5373-5376,5454-5459`), silently redirects on a bad line id
(`:5379-5386`), silently skips bad bulk ids (`:5446-5449`), and `post_count`
skips a missing item yet marks the header `POSTED`
(`inventory/count.py:642-681`).

Those are live containment defects in the legacy adapter, not behaviour the
module may preserve. The cycle-count port must prove: invalid input is refused;
a bulk request is all-or-nothing; a missing line/item refuses posting; an
injected adjustment failure rolls back every earlier line; and the header is
not posted until every required adjustment exists. Fixing the current adapter
may land separately under the severe-risk rule, but it does not make the module
ready.

### 2.6 The writer seal is a measured deletion

The authority switch is not complete until one architecture test enumerates
the prohibited legacy model writes/imports over routes, services, tasks,
workers, scripts and CLI; fails on a planted direct `InventoryTransaction`
write; and names every retained exception with a machine-checkable premise.
The existing ERP web-authorization ratchet is lowered in the same change as
legacy route deletion. A declining baseline with no reviewed lowering must
fail, because otherwise a route can disappear without evidence that the module
replaced it.

---

## 3. Manifest declarations due with the first consumer

These are the proposed module-owned declarations. They reuse ERP's existing
permission strings so cutover does not mint a parallel authorization language.
The implementation must declare only codes with real guarded consumers and
must keep ERP-only decisions out.

### 3.1 Permissions

| Module-owned code(s) | Consumer |
|---|---|
| `inventory:items:read`, `inventory:items:create`, `inventory:items:update`, `inventory:items:delete` | Item query/create/update/deactivate routes. `delete` retains its existing meaning: deactivate, not physical deletion. |
| `inventory:warehouses:read`, `inventory:warehouses:manage` | Warehouse queries and identity/eligibility commands. |
| `inventory:transactions:read`, `inventory:transactions:receipt`, `inventory:transactions:issue`, `inventory:transactions:transfer`, `inventory:transactions:adjust` | Movement query and exact effect commands. |
| `inventory:stock:read`, `inventory:stock:allocate` | Balance/availability reads and reservation lifecycle. |
| `inventory:lots:read`, `inventory:lots:create`, `inventory:lots:allocate`, `inventory:lots:quarantine` | Lot/serial traceability and controlled custody changes. |
| `inventory:counts:read`, `inventory:counts:create`, `inventory:counts:post` | Cycle-count lifecycle; posting is separate from recording. |
| `inventory:valuation:read`, `inventory:valuation:create`, `inventory:valuation:update`, `inventory:valuation:calculate`, `inventory:valuation:revalue` | Valuation reads, layer/snapshot creation, consumption and NRV/revaluation commands where each route really exists. |

Do **not** declare `inventory:transactions:post`: ERP defines it as “Post
transactions to GL” (`scripts/seed_rbac.py:221`), which is Finance-owned after
the seam is corrected. The generic `inventory:transactions:create` route also
retires in favour of the existing effect-specific receipt/issue/transfer/adjust
permissions. Also excluded: `inventory:access`, `:dashboard`,
categories, receipt approvals, material requests, price lists and BOM codes;
their decisions remain in ERP or another module.

### 3.2 Capability, audit and outbox vocabularies

- `CapabilitySpec(code="inventory.use", ...)` is the one tenant entitlement
  for the owned stock capability. Do not make every command a licensable
  capability.
- Audit actions must be subject-oriented and consumed by the command that
  makes the decision: `inventory.item.changed`,
  `inventory.warehouse.changed`, `inventory.movement.recorded`,
  `inventory.reservation.changed`, `inventory.lot.changed`,
  `inventory.count.transitioned`, `inventory.valuation.recorded`, and
  `inventory.opening.admitted`.
- `inventory.movement.recorded.v1` is the first-class outbox type carrying the
  evidence in § 4.4. Add `inventory.reservation.changed.v1` only when a named
  external consumer exists; a declaration without a consumer is forbidden by
  hard rule 12.

### 3.3 Routers and product shell

The module needs guarded `api_routers` for its owned command/query surface.
Whether its HTML `web_router` replaces ERP's owned stock pages is an
implementation choice, but the resulting route must remain a thin adapter.
ERP retains the application shell and the non-owned material-request, return,
pricing and BOM pages. Mounting a router proves only reachability; exact pin,
lineage composition, entitlement, permission declarations and live-catalog RLS
proof remain independent gates.

---

## 4. Contracts required before a thin ERP adapter exists

All public results below are frozen dataclasses or Pydantic values. No public
function returns `Item`, `StockBalance`, `StockMovement`, `Lot`, `Serial` or any
other live ORM row.

### 4.1 Read/query port

At minimum the package needs tenant-scoped calls equivalent to:

```python
get_item(db, *, tenant_id, item_id) -> ItemView
list_items(db, *, tenant_id, active, cursor, limit) -> Page[ItemView]
get_warehouse(db, *, tenant_id, warehouse_id) -> WarehouseView
list_warehouses(db, *, tenant_id, active, cursor, limit) -> Page[WarehouseView]
get_stock_balance(db, *, tenant_id, item_id, warehouse_id, lot_id=None) -> StockView
get_item_stock_summary(db, *, tenant_id, item_id) -> ItemStockSummary
get_batch_stock_levels(db, *, tenant_id, item_ids, warehouse_id=None) -> tuple[StockView, ...]
list_warehouse_stock(db, *, tenant_id, warehouse_id, cursor, limit) -> Page[StockView]
list_low_stock(db, *, tenant_id, thresholds: Mapping[UUID, Decimal]) -> tuple[LowStockView, ...]
list_reservations(db, *, tenant_id, source_ref=None, status=None) -> tuple[ReservationView, ...]
get_lot_trace(db, *, tenant_id, lot_id) -> LotTraceView
get_serial_trace(db, *, tenant_id, serial_number) -> SerialTraceView
```

The caller supplies reorder thresholds because planning policy remains in ERP.
Queries validate tenant identity at the service boundary even under RLS, and
pagination is bounded. The API adapter may shape these DTOs into ERP's current
responses; it may not reproduce the query against module tables.

### 4.2 Command identity and idempotency

Every externally repeatable command carries an explicit `operation_key` and
canonical request fingerprint. The module calls kernel `execute_once` inside
the same transaction as its movement/reservation/opening effect. Reuse of the
key with the same fingerprint returns the recorded immutable result; reuse with
a different fingerprint is a conflict. `source_ref` remains correlation, not
the idempotency ledger and not an overloaded structured document identity.

The inventory manifest therefore needs `IDEMPOTENCY_LEDGER_V1` in `requires`.
ERP already binds that prerequisite at
`app/migration_bindings.py:72-76`; resolution is not live-catalog proof.

### 4.3 FIFO command parity

For a FIFO item, an issue with no explicit lot must lock eligible lot balances,
order them deterministically by receipt time and stable id, exclude
quarantined/expired lots, and consume enough layers or fail without a write.
Because one issue may span lots and `stock_movements` has one `lot_id`, the
result is multiple issue movements sharing one `movement_group_id`, not one row
pretending to have one lot. Explicit lot selection remains supported;
specific-identification continues to require it. Concurrency tests must prove
two issuers cannot consume the same quantity.

The current pure `consume_fifo` function is useful arithmetic, but its zero
callers, lack of row locking and lack of a multi-movement service path mean it
does not close blocker 5.

### 4.4 Outbound quantity/value evidence — first-class contract

Every receipt, issue, adjustment and transfer leg returns an immutable
`InventoryMovementEvidence`; transfer and multi-lot FIFO issue return a tuple
under one group. Required fields are:

- schema version, tenant id, movement id, movement-group id, kind and
  effective/recorded timestamps;
- item id, source/destination warehouse id as applicable, lot id and serial
  references;
- signed quantity delta, signed value delta, unit cost, currency and costing
  method;
- resulting quantity and carrying value for the affected projection; and
- opaque source, actor and idempotency references.

The digest covers the canonical evidence payload. Receipts, issues, transfers
and adjustments all produce the same contract; no command returns a bare ORM
row. The assembly can synchronously hand it to a Finance-owned posting
coordinator when policy says “immediate”, while periodic policy consumes the
declared `inventory.movement.recorded.v1` outbox stream. Both paths consume the
same evidence and Inventory is ignorant of the decision.

### 4.5 Cycle counts — proven ADD, bounded scope

Add tenant tables for count headers and frozen count lines, with one lifecycle
owner and forced RLS. A count snapshots module query values, records first
count/recount evidence, derives variances, and posts every non-zero variance as
module adjustment movements sharing the count reference. It stores opaque actor
and optional external approval references — never `fiscal_period_id`, a journal
id, a people FK or a bin id. Historical ERP count rows do not migrate.

Before implementation, the source parity suite must settle one discrepancy:
ERP has `approve_count`, but `post_count` requires only `COMPLETED` and never
checks `approved_by_user_id` (`count.py:548-627`). The module must not silently
claim approval is mandatory when the qualifying source does not enforce it;
either port the actual permission-gated transition or record an explicit
behaviour change with tests.

### 4.6 Warehouse metadata deliberately left outside

`is_consignment` and `is_transit` are create/update/form fields only; exhaustive
ERP grep finds no stock decision reading either. They remain ERP warehouse
metadata on present evidence and do not justify new module columns. The same
rule applies to contacts, address, organisation location and cost centre.

---

## 5. Governed opening state and import gates

The data admission is **not** ledger replay. It is one versioned inventory
opening pack applied to a clean composed database at one approved instant.
`dotmac-imports` owns durable validation/apply progress and `dotmac-files` owns
the immutable bytes; Inventory owns every domain validation and mutation.

### 5.1 Pack identity and layouts

The pack manifest is immutable and contains `pack_id`, schema version,
tenant/organization id, opening instant, source snapshot coordinate, currency,
file ids, per-file SHA-256 digests, row counts and declared control totals.
Layouts are dependency ordered:

1. stocked-item subset and warehouses;
2. active lots and serials in custody;
3. opening quantity/value rows per `(item, warehouse, lot?)`;
4. active reservation remainders; and
5. one opening valuation snapshot per stocked projection.

No `inv.inventory_transaction`, serial-movement history, historical valuation,
historical count, fulfilled/cancelled reservation or archived lot with zero
balance is an admissible row. A generic database extractor or restore is not an
admission mechanism.

### 5.2 Validation before mutation

`dotmac-imports` dry-run must finish with zero rejected rows before promotion.
Inventory validators prove referential closure, unique SKU/warehouse/lot/serial
identity, positive/non-negative rules, currency agreement, serial quantity
agreement, reservation remainder ≤ available quantity, lot totals equal their
warehouse totals, and each row belongs to the declared tenant. The pack records
the ERP snapshot coordinate but never asks the new database to query ERP.

Production-derived facts still needed before the pack can be finalized are the
three read-only counts named in § 1.5: count headers/lines (including non-null
bin ids) and warehouse-bin rows. A non-zero result does not authorize history
replay; it determines whether an open operational item or continuity mapping is
needed.

### 5.3 Apply, seal and idempotency

Partition/chunk completion alone cannot make a half-applied opening
authoritative. The Inventory opening command needs an opening header with a
pack digest and explicit `draft/applying/sealed/failed` state (or an equivalent
single-owner mechanism). Once an opening has been registered, normal stock
reads and commands refuse it until sealed; a tenant that starts legitimately
empty and registers no opening remains usable. Each partition applies through
idempotent Inventory commands; the final seal runs only after rebuilding
projections and matching every declared total.

The `inventory.opening.admitted` idempotency record and sealed opening state
commit with the final admission. Re-delivering the same pack is a no-op;
reusing its id with different bytes fails. Opening quantities enter as dedicated
opening adjustment movements, never direct `stock_balances` writes, so
`rebuild_balance` reproduces them.

### 5.4 Control totals and drift proof

At the opening instant, compare the module's rebuilt projections to **the
pack's** totals, not to a replayed ERP ledger:

- quantity and carrying value by tenant/item/warehouse;
- lot quantity/value and quarantine status;
- serial custody/status and exact count;
- active reservation remaining quantity; and
- grand quantity/value totals by currency.

After sealing, reconciliation compares module movements plus active
reservations to module projections. Behavioural parity is independently proved
by driving versioned synthetic receipt/issue/transfer/adjustment/FIFO/count
inputs through clean ERP source and module implementations. It is not a data
copy and makes no claim that archived histories are row-equal.

---

## 6. Cutover, rollback and acceptance evidence

### 6.1 Ordered gates

| Gate | Required immutable evidence |
|---|---|
| A — authority text | Accepted ADR-0036 opening-state amendment plus matching `EXTRACTION.toml` and `inventory-sources.md`; no replay wording remains. |
| B — module completeness | Read DTOs, command idempotency, FIFO service integration, all-kind movement evidence, cycle counts, opening seal, declarations and canary-first tests. |
| C — release | A new `dotmac-inventory` release run that publishes, installs back, verifies and tags the exact commit. a1 is insufficient. |
| D — composed but disabled ERP | Exact private-index pin and lock; `dotmac_inventory.migrations:versions`; `COMPOSED_MODULE_LINEAGES["inventory"]`; prerequisite resolution; fresh-Postgres migration and live RLS/catalog proof. Storage is not authority. |
| E — import rehearsal | Production-derived pack through `dotmac-files` + `dotmac-imports`; zero rejected rows; sealed digest; every § 5.4 total equal on a disposable clean database. |
| F — behavioural parity | Versioned synthetic commands cover WAC, multi-lot FIFO, standard/specific cost, reservations, lot/serial custody, transfer atomicity, count failure rollback and evidence digests on real Postgres. |
| G — Finance boundary | Inventory contains no valuation-mode read, account selection or GL import; Finance consumes typed evidence and proves immediate/periodic policy with one canonical journal writer. |
| H — writer fence | Accepted cross-database write-fence decision; named operator/owner; legacy Inventory mutations quiesced; architecture guard proves every legacy writer/caller disposition and is sensitivity-tested. |
| I — authority switch | One named deployment commit/digest enables the module routes and adapters; no dual write; legacy database becomes read-only for pre-opening history. |
| J — observation and retirement | Post-switch movement/control-total reconciliation is clean for the named window; legacy routes/models/tasks/scripts are deleted or retained only under an enforced premise; ratchet baseline lowered in the same PR. |

The clean-install composition already used for Accounting and Imports is the
template: ERP currently lists `files`, `accounting` and `imports` in
`COMPOSED_MODULE_LINEAGES` (`app/migration_bindings.py:112-116`) and resolves
their wheel-owned version locations from `alembic.ini:16`. Inventory must gain
the same static and live proofs; copying migrations into ERP is forbidden.

### 6.2 Rollback has three different meanings

- **Before opening seal:** discard the disposable clean database or failed
  opening and rerun from immutable bytes. Legacy remains authoritative.
- **After seal but before the authority switch:** rollback is allowed only while
  the module has accepted zero post-opening operational commands; release the
  legacy write fence and discard the new opening.
- **After the first module-owned movement:** never point authority back at the
  stale legacy ledger. Roll back the application to a build that still reads
  and writes `mod_inventory`, or fix forward. Returning to legacy would require
  a separately authorized, lossless export of every new movement/reservation/
  traceability fact and is not part of this slice.

Before Gate H, an accepted ADR must name how the legacy write fence and new
enablement are coordinated across two databases. ADR-0031's single-transaction
seal cannot be claimed across them. The rollback owner, triggers, maximum
window and evidence location are deployment inputs, not defaults in the module.

---

## 7. Implementation sequence and readiness verdict

This dossier authorizes no implementation or cutover. It defines the smallest
sequence that can make a later authorization truthful:

1. Amend ADR-0036, `EXTRACTION.toml` and `inventory-sources.md` together to
   replace history replay with governed opening state and record the Finance
   evidence boundary.
2. Add module contracts and behaviour in independent canary-first slices:
   read DTOs/idempotency; FIFO multi-lot issue; all-kind movement evidence;
   cycle counts; opening admission/seal; then manifest routers/declarations
   with their real consumers.
3. Run the module unit/architecture and live Postgres isolation/concurrency
   suites on Observer and Git-hosted CI; publish a new release only after they
   are green.
4. In ERP, exact-pin and compose that release **disabled**, following the
   Accounting/Imports precedent. Add the ownership map, lineage/catalog gates
   and a static no-authority proof before changing a caller.
5. Build the ERP opening-pack validator/applier on its already composed
   `dotmac-files`/`dotmac-imports` lane. Rehearse against production-derived,
   immutable bytes on clean databases until every control total and parity
   test is green.
6. Rewire the retained callers in § 2.2 and the Finance evidence coordinator;
   replace owned web/API routes with thin module adapters. Retire Integrator
   debt rather than moving provider code into the module.
7. Accept the cross-database fence/rollback decision, execute one named
   cutover, observe, seal the writer guard, delete legacy writers and lower the
   route ratchet.

**Verdict: not ready.** The current a1 package is a sound tenant-isolated stock
kernel, but it has no consumer surface and cannot admit or serve ERP. The seven
blockers at the top are all release/cutover blockers. Warehouse bins stay out;
cycle counts come in; historical Inventory rows remain in the legacy archive;
and Finance — not Inventory — owns every decision that turns movement evidence
into a journal.
