# ADR-0042: Assets own durable units, not finance or product extensions

**Status:** Accepted
**Date:** 2026-08-18
**Decision owner:** Michael
**Scope:** FLEET-WIDE. Applies to every Dotmac application that manages an
individual durable asset and to the reusable `dotmac-assets` module.
**Relates to:** ADR-0006 (product-first extraction), ADR-0010 (thin adapters),
ADR-0014 (one at-most-once owner), ADR-0017 (adoption is scarce), ADR-0023
(persistence planes), ADR-0024 (applications synchronize data), ADR-0031
(authority cutover evidence)
**Evidence:** [`assets-sources.md`](../inventories/assets-sources.md)

## Context

ERP is the only measured product with an `assets-fleet` implementation: 23
tables versus zero in CRM, Sub, and Vendor CP. It nevertheless has two
overlapping internal owners. Fixed assets own the register, employee custody,
maintenance work orders, accounting lifecycle and disposal; Fleet separately
owns vehicles, assignments, maintenance and disposal.

Copying both stacks would preserve duplicate decisions. Copying the whole
fixed-asset schema would also make a shared asset package own depreciation,
revaluation, impairment, inventory parts, GL posting, employees, locations and
projects. A durable physical unit and its financial carrying value collaborate,
but they are not one authority.

Michael selected the reusable boundary as individual durable assets, custody,
assignment, maintenance, lifecycle and disposal.

## Decision

### 1. `dotmac-assets` owns the product-neutral durable-asset aggregate

`dotmac-assets` is an independently versioned, tenant-plane module. Each
adopting application installs its own `as` lineage and owns its local rows; no
application reads another application's asset schema.

The module owns:

- stable tenant-local asset identity, kind, serial/tag and physical condition;
- guarded physical lifecycle and an opaque current-location projection;
- at most one active custody assignment, plus complete return, loss and
  transfer history;
- scheduled, in-progress, completed and cancelled maintenance history;
- requested, separately approved, completed and cancelled disposal state; and
- an append-only ordered lifecycle trail for all of those changes.

Services acquire aggregate locks, validate expected state, mutate and flush
inside the caller's transaction. They never commit, roll back, deliver a
notification, call a provider, or write another application's data.

### 2. Physical, custody, maintenance and disposal state stay separate

An asset's physical lifecycle is `registered`, `in_service`,
`out_of_service`, `retired`, or terminal `disposed`. Its condition, assignment,
maintenance and disposal each have their own status. `fully_depreciated` is an
accounting fact, not a physical lifecycle state; `reserved` is a vehicle-use
decision, not a generic asset state.

Starting maintenance takes an in-service asset out of service. Completion may
return it to service through the same owner. Retirement refuses active custody
or unfinished maintenance. Disposal begins only after retirement, requires an
independent approver, and becomes terminal only when completion evidence is
written.

### 3. Products own subjects, policy and specialized extensions

Custodian, actor and location identifiers are opaque local UUIDs. The module
has no employee, department, customer, subscriber, warehouse, project, vehicle,
ticket or work-order foreign key. The adopting product resolves and authorizes
those identities and owns its relation to the local asset id.

ERP or its Backoffice successor retains vehicle registration/specification,
driver and pool policy, odometer, fuel, incidents, reservations, and document
meaning. `dotmac-files` may own referenced bytes but not the asset document's
business validity. Notification and approval eligibility remain product
decisions; the module only enforces the recorded creator/approver distinction.

### 4. Finance and Inventory remain separate owners

Finance owns capitalization threshold, acquisition cost, depreciation method
and schedules, impairment, revaluation, carrying value, disposal proceeds,
gain/loss and journal posting. The module may retain an opaque external finance
reference as correlation evidence but never derives or posts money.

Inventory owns stocked items, parts, quantities, valuation, issue and
procurement. When stock becomes an individually controlled durable asset, that
is an explicit typed handoff between owners. Maintenance may request an
Inventory consequence through the product; it does not move stock itself.

### 5. Positioning observes; Assets decides its location projection

`dotmac-positioning` owns provider-neutral coordinates, timestamps, accuracy,
grants, retention and geofence facts. The product owns the asset-to-tracked-unit
link and the policy that decides whether an observation changes authoritative
asset location. If accepted, it calls the `dotmac-assets` location command.
The asset module stores no latitude, longitude, GPS device, provider credential
or collection schedule.

### 6. ERP is cutover 1

ERP composes the tenant lineage, backfills the generic aggregate, and shadows
old and module decisions in one transaction. Cutover requires typed full-state
and lifecycle-digest parity, cross-tenant PostgreSQL proof, a one-writer seal,
and verified product adapters for the excluded owners. The displaced generic
ERP writers and tables are then retired or migration-sealed; excluded finance,
inventory and vehicle extensions are rekeyed to the module asset.

The future Backoffice application pins and composes the released package. It
does not inherit an ERP-local copy. Package supply alone is `audit-complete`;
ERP's actual one-writer switch makes it `adopted`; a second independent exact-
pin consumer is required for `reuse-proven`.

### 7. The initial module is tenant-only

All present demand is in a tenant product data plane. No named control-plane
application owns customer assets today, so revision 1 declares no platform
tables. Adding a control plane requires a real assembly, separate declared
tables, supported plane selections, and the full ADR-0023 isolation contract.
A nullable or sentinel tenant is never an alternative.

## Consequences

- The fleet matrix's ERP-sourced `assets-fleet` family now has a narrow module
  destination without moving its finance or vehicle-only decisions.
- One active custodian is enforced in the database, not only by a pre-insert
  query.
- Lifecycle evidence is append-only by grants and trigger.
- Positioning and Assets collaborate through an assembly-owned subject link;
  neither imports the other.
- Asset verification/audit plans, fleet incidents, reservations, fuel and
  document validity remain outside revision 1 until separately sourced.

## Alternatives rejected

### Copy ERP fixed assets wholesale

Rejected because it would make the module a finance, inventory, HR and project
monolith and would preserve organization-scoped rather than tenant-structural
isolation.

### Make vehicles a separate shared owner immediately

Rejected for the selected slice. A vehicle is a durable asset plus product
extensions. A later independently reusable vehicle-operations module may own
fuel, reservations or incidents, but it must link to `dotmac-assets` rather
than recreate identity, custody, maintenance and disposal.

### Let Positioning write asset location directly

Rejected because an observation is not the decision that a business asset has
moved. The product policy accepts the observation and asks the asset owner to
change source state.
