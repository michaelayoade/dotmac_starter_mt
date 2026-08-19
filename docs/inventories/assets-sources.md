# Assets product-first source inventory

**Audited:** 2026-08-18  
**ERP:** `0f4b1698ddbf27a04f4562ecdaf8b93f19c3debf`  
**CRM:** `c64b5aa0f7902b52e7ef73cf26f3f88687ed849d`  
**Sub fleet baseline:** `9f6f9f36`  
**Vendor control plane fleet baseline:** `eb667fa`

The ERP worktree had unrelated local changes, but every cited asset/fleet path
was clean against the commit above. The fleet matrix's frozen measurement is
the cross-product evidence: `assets-fleet` has 23 ERP tables and zero in CRM,
Sub, or Vendor CP. This inventory narrows that measurement bucket into the
contract Michael requested before shared behavior was written.

## Verdict

`dotmac-assets` is a tenant-only module extracted **from ERP**. ERP is both the
qualifying source and first cutover; reuse remains unproven until another
independent assembly consumes a released package.

| Repository | Existing behavior | Evidence quality | Verdict |
|---|---|---|---|
| `dotmac_erp` | Fixed-asset identity, custody/movement, maintenance, lifecycle evidence and disposal; a second overlapping vehicle owner | Production routes and migrations; strong fixed-asset/disposal tests; thinner assignment/fleet-service proof | **Qualifying source and first cutover** |
| `dotmac_sub` | No durable-asset owner in the frozen family | Fleet matrix and source sweep | No implementation to port |
| `dotmac_crm` | No durable-asset owner in the frozen family | Fleet matrix and source sweep | No implementation to port |
| Vendor CP | No product durable-asset owner | Fleet matrix and source sweep | Not a product data-plane consumer |

The source is not copied whole. ERP currently splits the same physical concept
across fixed assets, HR asset assignment, and vehicles; it also mixes finance,
inventory, GPS, workforce, and vehicle-only policy into those owners. The
shared unit is the durable asset, not that monolith slice.

## ERP source surface

### Individual fixed assets

- `app/models/fixed_assets/asset.py`
- `app/models/fixed_assets/asset_disposal.py`
- `app/models/fixed_assets/maintenance_request.py`
- `app/models/fixed_assets/maintenance_work_order.py`
- `app/models/people/assets/assignment.py`
- `app/models/people/assets/audit.py`
- `app/services/fixed_assets/asset.py`
- `app/services/fixed_assets/disposal.py`
- `app/services/people/assets/assignment_service.py`
- `app/services/people/assets/maintenance_service.py`
- `app/services/people/assets/lifecycle_event_service.py`

Behavioral proof to port:

- `tests/ifrs/fa/test_asset_service.py`
- `tests/ifrs/fa/test_asset_bulk_service.py`
- `tests/ifrs/fa/test_disposal_service.py`
- `tests/people/hr/test_employee_assigned_assets.py`

These paths prove stable per-organization identity, restricted activation,
custodian visibility, preserved lifecycle history, disposal approval with
creator/approver separation, and disposal validation. Assignment and
maintenance routes are production-reachable, but lack focused service proof;
their shared transitions therefore receive explicit package canaries rather
than being credited as proven merely because routes exist.

### Overlapping vehicle owner

- `app/models/fleet/vehicle.py`
- `app/models/fleet/vehicle_assignment.py`
- `app/models/fleet/maintenance.py`
- `app/services/fleet/vehicle_service.py`
- `app/services/fleet/assignment_service.py`
- `app/services/fleet/maintenance_service.py`
- `tests/services/test_fleet_maintenance_form.py`
- `tests/test_fleet_vehicle_edit_save.py`

The vehicle stack independently owns assignment, maintenance and terminal
disposal. Its useful parity input is the guarded maintenance state machine and
the rule that in-progress maintenance takes a unit out of service and returns
it when complete. Registration, VIN, engine, odometer, fuel, incidents,
reservations and driver policy are not generic asset facts.

## The measured 23-table family decomposed

| Source state | Initial destination |
|---|---|
| `asset`, generic identity/lifecycle columns of `vehicle` | `mod_assets.assets` |
| `asset_assignment`, `vehicle_assignment` | `mod_assets.asset_assignments` |
| `asset_assignment_movement`, `asset_lifecycle_event` | `mod_assets.asset_lifecycle_events` |
| generic lifecycle from `maintenance_record`, `maintenance_request`, `maintenance_work_order` | `mod_assets.asset_maintenance` |
| `asset_disposal` and vehicle disposal fields | `mod_assets.asset_disposals` |
| `asset_tracking_event` | `dotmac-positioning` observation plus product-owned asset link; accepted location consequences enter through `dotmac-assets` |
| `asset_category`, `asset_component`, `asset_impairment`, `asset_revaluation`, `depreciation_run`, `depreciation_schedule` | Finance/fixed-asset accounting owner |
| `maintenance_status_log` | superseded by the module's append-only lifecycle event |
| `maintenance_work_order_part` | Inventory transaction/procurement plus product maintenance adapter |
| `fuel_log_entry`, `vehicle_document`, `vehicle_incident`, `vehicle_reservation` | product-owned vehicle extensions (files owns bytes only) |

Physical audit-plan rows are classified under the fleet's audit family rather
than the 23-table bucket. They remain deferred: asset verification evidence may
be a later `dotmac-assets` slice, but only after its source tests and adjustment
authority are separately adjudicated.

## Mandatory corrections carried by revision 1

### D1 — one active custodian is a database invariant

Both ERP assignment owners query before insert and can race. The module adds a
tenant-scoped partial unique index for one active assignment per asset, while
the locked service returns a typed conflict.

### D2 — physical lifecycle is not an accounting classifier

ERP's fixed-asset status includes `FULLY_DEPRECIATED`, while the vehicle status
mixes reservation and maintenance. Revision 1 keeps physical state
(`registered`, `in_service`, `out_of_service`, `retired`, `disposed`) separate
from condition, assignment, maintenance, and disposal status. Depreciation
never changes physical lifecycle by itself.

### D3 — lifecycle history is append-only

ERP writes a lifecycle table but does not make immutability a database
property. The module grants the online role only `SELECT, INSERT` and installs a
rewrite-refusal trigger. The PostgreSQL canary proves even the migration role
cannot update evidence accidentally.

### D4 — tenant integrity is structural

Every table carries `tenant_id UUID NOT NULL`; module relationships use
composite tenant foreign keys; the creating migration enables and forces RLS.
The PostgreSQL canary proves isolation, rejects a cross-tenant assignment, and
includes a sensitivity case that disables RLS and sees both tenants.

### D5 — disposal cannot bypass custody, maintenance, or separation of duties

Disposal starts only after retirement, rejects an active assignment or open
maintenance, requires approval by someone other than the requester, and changes
the asset to terminal `disposed` only when completion evidence is recorded.
Finance receives an opaque reference; gain/loss and journal state remain with
the finance owner.

## Cutover and retirement

1. Validate the package's unit, architecture, migration and PostgreSQL canaries
   on a fresh Observer worktree.
2. Release the namespace allocation and package only with the ERP adoption
   slice ready.
3. Compose `as` in ERP, map organization to tenant, and backfill the five
   generic tables without changing authority.
4. Shadow old and module commands in the same transaction; compare typed state,
   refusals and ordered lifecycle digests.
5. Seal the one-writer switch under ADR-0031 after zero unexplained drift and
   effective-privilege proof.
6. Delete or migration-seal displaced generic ERP writers and tables. Rebind,
   rather than copy, finance, inventory, vehicle, files and positioning
   extensions.
7. The future Backoffice assembly exact-pins the released module; it does not
   inherit a second implementation from ERP.

Until step 6, the package is `audit-complete`, not adopted. A second independent
released consumer is required before reuse is proven.
