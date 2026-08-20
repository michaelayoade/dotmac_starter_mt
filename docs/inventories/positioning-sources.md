# Positioning product-first source inventory

**Audited:** 2026-08-18  
**Starter:** `92ae7a6f9c83` (`origin/main`)  
**Sub:** `6c9f5215c1dd`  
**CRM:** `c64b5aa0f790`  
**ERP:** `0f4b1698ddbf`  
**Workspace:** `c72fe304d3c8`  
**Vendor Control Plane:** `e6b2bbee815c`  
**Backoffice:** `fcdd8270262d`  
**Integrator:** `783baf23cbf5`

**Starter implementation reconciliation base:** `4b285cb` on 2026-08-18

**ERP adoption readiness rechecked:** `dd6416cd981f` on 2026-08-18

Sub, CRM and ERP had unrelated local modifications during the audit. None of
the paths cited below was modified, so the pinned commits describe the audited
positioning behavior. The remaining repositories were clean.

This inventory is the evidence ADR-0006 required before
`dotmac-positioning` behavior was implemented. The audit is preserved as the
source record; the resulting audit-complete package now ports the corrected
Sub behavior into a product-neutral tenant module. It distinguishes position
evidence from three nearby but separately owned capabilities: field-work
lifecycle, asset/fleet lifecycle and map presentation.

## Contract being compared

The candidate contract is:

> Tenant-scoped, provider-neutral ingestion of time-stamped position
> observations with stable client identity and provenance; rebuildable current
> position and trail projections; explicit collection/sharing grants and
> retention; and explicitly product-selected, product-neutral geofence
> evaluation facts. Products own the
> tracked subject, authorization, business consequences and presentation.

The contract does **not** include a technician or vehicle registry, shifts,
work orders, dispatch/route optimization, attendance decisions, customer ETA,
plant/network coordinates, provider credentials, Leaflet templates or map
popup/actions.

## Product verdict

| Repository | Existing behavior | Evidence quality | Verdict |
|---|---|---|---|
| `dotmac_sub` | Field-mobile collection, technician presence/current fix, immutable-ish ping trail, admin live map and playback, routing proximity, geofence-triggered work behavior, customer technician view | Production-shaped implementation with focused ingest/routing/map/customer tests, but material contract, transaction, privacy and retention defects | **Qualifying source and first cutover**, after the defects below are corrected canary-first |
| `dotmac_crm` | Older field tracking fork with live/recent tracks, movement playback, geofencing, public Track My Visit, 120-second freshness and configurable ping pruning; separate browser agent-location pings | Strongest retention, freshness and public-privacy tests; field stack is the predecessor being consolidated into Sub | Mandatory parity/delta source, **not** an independent consumer |
| `dotmac_erp` | Mature vehicle/driver/assignment/reservation/fuel/maintenance/incident lifecycle; attendance circle/polygon geofence; vehicle GPS placeholder columns | No writer for vehicle current location and no focused geometry proof; attendance tests mock the geofence validator | Concrete second candidate consumer and requirement input; not source of the observation engine |
| `dotmac_workspace` | No position observation, tracker or map capability found | None | No source behavior |
| `dotmac_vendor_control_plane` | Deployment “fleet” concerns software desired/applied state, not physical location | None for physical positioning | Out of scope; name collision explicitly rejected |
| `dotmac_backoffice` | No qualifying position behavior found | None | No source behavior |
| `dotmac_integrator` | Thin runtime assembly; no local provider-independent physical-position owner | Connector/runtime contracts only | Future transport assembly for external telematics, never the position-row writer |

## Sub — qualifying implementation

### Source paths

- Models: `dotmac_sub:app/models/field_location.py`
- Ingest/current projection: `dotmac_sub:app/services/field/location_tracking.py`
- Live map and playback reads: `dotmac_sub:app/services/field_maps.py`
- Geofence evaluation and work consequence:
  `dotmac_sub:app/services/field/geofence.py`
- Routing distance: `dotmac_sub:app/services/field/routing.py`
- Customer projection:
  `dotmac_sub:app/services/customer_work_order_selfcare.py`
- Mobile source and payload:
  `dotmac_sub:field_mobile/lib/core/location/device_location.dart`,
  `dotmac_sub:field_mobile/lib/features/location/location_cadence.dart`, and
  `dotmac_sub:field_mobile/lib/features/location/location_ping_service.dart`
- Product-owned static map assets:
  `dotmac_sub:app/services/field/map_assets.py` — audited to establish the
  exclusion; these coordinates remain plant/work-domain facts.

### Tests to preserve or strengthen

- `dotmac_sub:tests/test_field_location_tracking.py`
- `dotmac_sub:tests/test_field_routing.py`
- `dotmac_sub:tests/test_admin_maps_web.py`
- `dotmac_sub:tests/test_customer_work_order_selfcare.py`
- the location-focused Dart tests under
  `dotmac_sub:field_mobile/test/features/location/`

Sub qualifies because it is the target authority for field work and contains
the implementation the first cutover can actually retire. Qualification is not
an instruction to copy its defects.

## CRM — parity and mandatory deltas

CRM and Sub carry the same field-tracking ancestry. Their location controller
and map-coordinate helpers are substantially forked; counting them as two
consumers would call duplication reuse.

The CRM implementation nevertheless supplies behavior Sub currently lacks:

- configurable retention and an hourly pruning task;
- recent breadcrumb and bounded ping-history reads;
- a public privacy gate requiring dispatched + en-route + sharing + a fix
  fresher than 120 seconds; and
- focused retention, recent-track, geofence and routing tests.

Source paths:

- `dotmac_crm:app/models/field_location.py`
- `dotmac_crm:app/services/field/location_tracking.py`
- `dotmac_crm:app/services/field/tracking.py`
- `dotmac_crm:app/services/field/geofence.py`
- `dotmac_crm:app/services/field/routing.py`
- `dotmac_crm:app/tasks/field.py`
- `dotmac_crm:app/models/crm/presence.py`
- `dotmac_crm:app/services/crm/presence.py`

Parity tests:

- `dotmac_crm:tests/test_field_location_tracking.py`
- `dotmac_crm:tests/test_field_recent_tracks.py`
- `dotmac_crm:tests/test_field_location_retention_task.py`
- `dotmac_crm:tests/test_field_tracking.py`
- `dotmac_crm:tests/test_field_geofence.py`
- `dotmac_crm:tests/test_field_routing.py`

CRM's `FieldPresenceStatus.on_break` also proves the current Sub mobile code did
not invent that vocabulary; Sub's backend changed to `break` without moving the
client contract. The extraction selects one declared vocabulary rather than
adding another silent alias.

## ERP — concrete adopter, not source

ERP vehicle GPS fields are declarations only:

- `dotmac_erp:app/models/fleet/vehicle.py` declares `has_gps_tracker`,
  `gps_device_id`, `last_known_location` and `last_location_update`;
- `dotmac_erp:app/services/fleet/import_export.py` imports/exports the tracker
  declaration; and
- no application writer updates the current-location fields.

ERP attendance supplies product requirements for circle/polygon evaluation:

- `dotmac_erp:app/models/finance/core_org/location.py`
- `dotmac_erp:app/services/people/attendance/attendance_service.py`
- `dotmac_erp:app/services/people/hr/web/location_web.py`

The attendance service and geofence editor duplicate haversine and polygon
evaluation, while attendance tests mock `_validate_geofence`. These paths are
requirements to characterize before adoption, not a qualifying shared source.
ERP retains attendance acceptance/rejection and its Location configuration; it
calls the module for geometry facts. ERP retains the complete fleet lifecycle;
its product-owned vehicle link references an opaque tracked-unit id.

ERP already has the right assembly mechanics to build on: it composes the
installed `dotmac-files` lineage through `version_locations`, installs logical
prerequisite bindings before migration discovery, binds
`tenant_scope_catalog.v1` and `module_database_roles.v1` to revisions in its own
lineage, and requires a separate `MIGRATION_DATABASE_URL` whose executor is
`app_admin`. That does not make ERP ready to consume positioning. At the
readiness recheck it pinned `dotmac-kernel` `0.1.0a56`, while positioning a1
requires a71; no immutable positioning wheel existed; and its development and
generated deployment runtime still used the PostgreSQL `postgres` superuser.
ERP's accepted RLS boundary records 72 cross-organization callers as blocked
before the application can cut over to `app_user`. Positioning must therefore
join ERP only after the released dependency pin and the existing least-privilege
programme reach their own gates. A module-specific superuser exception would
invalidate, not accelerate, the adoption proof.

## Defects the extraction must not carry forward

### D1 — client/API vocabulary and context drift

Sub mobile sends `on_break`; the backend accepts `break`. Mobile sends
`work_order_id`; location ingest accepts only `crm_work_order_id`. Required RED
proofs:

- every mobile shift state validates against the API declaration; and
- a submitted work-order context survives typed parsing and reaches the stored
  observation or explicit product link.

### D2 — a rejected batch row can commit

Sub and CRM add a ping before validating status, catch the error without a
savepoint and commit the shared session. Required RED proof: a batch containing
one accepted and one rejected row persists exactly the accepted observation and
reports exactly one acceptance.

### D3 — retry has no identity

Neither field payload carries a stable `client_observation_id`. Required RED
proofs: replay returns the original result, produces no second row, and reuse of
the same key with a different fingerprint fails as a conflict.

### D4 — observation quality is discarded

Sub's mobile source reduces the device `Position` to latitude/longitude and
restamps a cached last-known fix with the current clock. Required RED proofs:
device time and accuracy reach ingest unchanged; `received_at` remains separate;
late evidence does not roll the projection backward; implausibly future fixes
cannot freeze it.

### D5 — privacy is enforced only by client cadence and selected reads

Sub ingest accepts pings without an active server-side collection grant, and
its customer view has no freshness cutoff. Required RED proofs: expired,
disabled or wrong-purpose collection is rejected; disclosure requires its own
active audience/purpose grant; stale customer positions are unavailable.

### D6 — Sub has no retention owner

CRM has a configurable retention setting/task; Sub has no equivalent. Required
RED proofs: policy sets explicit expiry, pruning is idempotent, tenant-scoped
and does not remove unexpired rows, and the current projection remains
rebuildable from retained evidence.

### D7 — geofence consequences cross the transaction boundary

Sub commits the batch, then best-effort evaluates geofences that can start work.
Required proof: positioning returns typed entry/exit facts while the product
owner applies any work/attendance consequence in its own transaction or emits a
durable outbox request. The module never imports work-order or attendance code.
Position ingest also cannot select every active tenant fence implicitly: the
product resolves and supplies only the opaque fence ids applicable to that
observation, and stale observations cannot regress per-unit/per-fence state.

## First-cutover and retirement plan

### Adoption hold — 2026-08-18

Michael instructed that positioning is **not to be adopted yet**. The package
therefore remains `audit-complete`, unallowlisted and unreleased, with Sub and
ERP recorded only as candidate consumers. No product may compose the `po`
lineage, add subject links or shadow writers, switch reads, retire a local
writer, claim a contract consumer, or begin shared map extraction while this
hold is active. Sub's canary-first local corrections remain reference
hardening, not module adoption. Resume the sequence below only after an
explicit adoption instruction; do not infer authorization from package
readiness or a green Observer run.

**First cutover: `dotmac_sub`.** The order is fixed:

1. land D1–D7 canaries and corrections in Sub without changing the authority;
2. port corrected behavior and parity tests into `dotmac-positioning`;
3. install the module tenant plane in Sub and create product-owned links from
   technicians/work contexts to tracked units;
4. shadow dual-ingest/read and compare accepted/rejected/replay counts,
   current-position digests, trail digests, retention results and geofence
   facts;
5. seal the cutover evidence under ADR-0031;
6. switch Sub reads and writes to the module and retire the local ping/current
   writer; and
7. route the CRM retirement into the module rather than creating a third owner.

The shadow is bounded and read-verified; it is not a permanent dual writer.
Rollback before the seal returns reads to the corrected Sub owner. After the
seal, fallback does not restore the retired writer.

### Consumer composition readiness is a cutover gate

The module passing its own migration and RLS suite does not make an application
a consumer. A candidate application must first prove all of the following from
its own checked-in assembly and deployed-schema rehearsal:

- an immutable released `dotmac-positioning` pin and its public
  `versions_dir()` are used; no source checkout, copied migration or editable
  path is a production dependency;
- one Alembic graph composes the product lineage and the `po` lineage, installs
  assembly-owned prerequisite bindings before revision discovery; positioning
  has one atomic tenant-only shape and therefore rejects a redundant
  `ModulePlaneSelection` rather than pretending the only plane is a choice;
- the bound product revisions really provide `tenant_scope_catalog.v1` and
  `module_database_roles.v1`; the module's live verifier, rather than a stamp or
  assertion, is the acceptance gate;
- the online application session runs as the non-superuser, non-BYPASSRLS
  `app_user` role and installs `app.current_tenant` transaction-locally; and
- migrations run through the separate `app_admin` authority. A superuser
  application DSN is not accepted as RLS evidence even in a single-operator
  topology.

Sub is not yet at that gate. Its current `alembic.ini` has no composed
`version_locations`; its adoption ledger explicitly keeps kernel/module
migrations inert; migration 508 supplies only part of the tenant catalogue and
does not supply the verified `app_current_tenant_id()` contract; and the
checked-in development DSN still uses the PostgreSQL superuser. Those are
product assembly prerequisites, not reasons to weaken the module or add a
single-tenant bypass. Sub cutover therefore requires an accepted assembly
migration decision, the released package/kernel pins, least-privilege role
provisioning, and a real predecessor-to-head rehearsal before any local writer
is retired.

**Second candidate: `dotmac_erp`.** ERP adopts the same released contract for
vehicle observations through a product-owned vehicle-to-tracked-unit link. It
must characterize its attendance geometry behavior and add real geometry
canaries before replacing either duplicated evaluator. ERP adoption moves the
positioning contract from `adopted` to `reuse-proven`; its presence in the
dossier before cutover is candidate evidence only. ERP's existing module
composition and prerequisite bindings should be extended rather than forked,
but the positioning pin and lineage stay absent until kernel a71 and
positioning a1 are immutable releases and ERP's ordinary runtime can exercise
the tenant plane under `app_user`.

## Map presentation gate

Sub's many Leaflet templates cover live technicians, movement playback,
customers, fiber/network assets, vendor routes and qualification. Those do not
share one domain contract. No template is ported into `dotmac-positioning`.

A `dotmac_ui.maps` slice may begin only after Sub and ERP exercise the same
released position read DTO. Its allowed surface is map lifecycle,
self-hosted assets, marker/polyline layers, bounds/focus and stale-state
presentation. Product endpoints, guards, marker meaning, popups and actions
remain outside. Until that gate is met, the map presentation work is deferred,
not zero-consumer scaffolding.
