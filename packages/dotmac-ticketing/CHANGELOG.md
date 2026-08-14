# Changelog — dotmac-ticketing

## 0.1.0a3 — 2026-08-14

Makes the module installable in a platform-only assembly (ADR-0027).

`0.1.0a2` declared what the lineage needs instead of naming a foreign revision,
which unblocked ERP. It did not help the vendor control plane: one flat
`requires` list containing `tenant_scope_catalog.v1` meant `upgrade()` demanded a
tenant catalogue before creating ANY table — and a control plane has none,
permanently and by design. So the module that ADR-0023 made dual-plane could not
be installed by the control plane it was dual FOR.

### Changed

- The manifest declares `module_database_roles.v1` as `requires` and
  `tenant_scope_catalog.v1` as `tenant_requires`.
- `tk_0001_tickets` builds the platform plane unconditionally and the tenant
  plane only where the assembly bound a tenant catalogue, and grants schema
  USAGE to `app_user` only where there is something there for it to reach.
- Kernel floor is `>=0.1.0a60`, the release that added per-plane prerequisites.

No behaviour changes, and nothing about a built plane changes: a tenant table
still carries `tenant_id NOT NULL`, composite identity and FORCEd RLS; a
platform table is still REVOKEd from `app_user`.

## 0.1.0a2 — 2026-08-13

Declares the database EFFECTS this lineage needs instead of naming a foreign
revision (ADR-0006 D1 amendment).

### Changed

- `tk_0001_tickets` previously read
  `depends_on = ("0001_initial_tenant_schema",)`. That edge is true only in an
  assembly that runs the kernel lineage: ERP hosts `public.tenants` in its own
  lineage and can never run kernel `0001`, so the module was un-installable
  there for want of a foreign-key target. The manifest now declares
  `requires=("tenant_scope_catalog.v1", "module_database_roles.v1")`, the root
  resolves its `depends_on` from the assembly's bindings, and `upgrade()` proves
  both effects against the live catalog before any DDL.
- Kernel floor raised to `>=0.1.0a56`, the release that added the prerequisite
  contract. A kernel below it cannot import this manifest.


## 0.1.0a1 — amended 2026-08-13, before release

Split into two persistence planes (ADR-0023). Amended IN PLACE rather than
released and superseded: `0.1.0a1` was never published, has no consumers, and
`contract_consumers` is empty — so this costs nothing now and would have been a
breaking change for two products in a month.

### Why

The named cutover-2 adopter, the vendor control plane, is platform-only
(`get_platform_db` at 15 sites, zero `require_tenant`). It cannot operate
tenant-scoped tables: the RLS predicate tests a tenant GUC it never sets. That
was a current adoption blocker, not future-proofing.

### Changed — BREAKING relative to the unreleased 0.1.0a1

- `Ticket` → `TenantTicket`; `TicketComment` → `TenantTicketComment`. Both names
  are now explicit; a bare `Ticket` in a product's imports is the ambiguity the
  split exists to remove.
- `link_subject()` → `link_tenant_subject()`, joined by
  `link_platform_subject()`. Two functions rather than one with a `platform=`
  flag: a flag has a default, and whichever value it takes is the plane a caller
  gets by forgetting to think.
- Kernel floor `>=0.1.0a39` → `>=0.1.0a53`, which adds
  `ModuleManifest.platform_tables` and the platform half of the live-catalog
  contract. This is the one module whose floor is set by a kernel capability
  rather than by its own namespace allocation.

### Added

- `PlatformTicket` / `PlatformTicketComment` in `mod_tkt`: no `tenant_id`, no
  RLS, numbers unique control-plane-wide, GRANTed to `platform_api`/`app_admin`
  and **REVOKEd from `app_user`**. On that plane the revoke is the isolation.
- `TENANT_TABLES` / `PLATFORM_TABLES`, and the manifest's `platform_tables`
  declaration the kernel gate reads.
- `tk_0001_tickets` now creates four tables. No foreign key crosses the planes,
  and the kernel gate refuses one that does.

### Unchanged

The lifecycle, status vocabulary, transition guards and reason registry are
shared by both planes and import no persistence — there is a test pinning that.
One behaviour, two planes.

## 0.1.0a1 — 2026-08-11

The first release: the lifecycle, the vocabulary registry, the tables, and the
subject-linking helper. No routers yet — those land with the first adopter's
surface, so the FastAPI dependency arrives with the code that needs it.

### Added
- `lifecycle` — `LifecycleClass` (5, fixed), `Status` (9 standard helpdesk
  terms, closed), `Priority`, `Channel`, a transition table, and the
  `is_open` / `sla_clock_runs` predicates every product currently hand-writes
  as a set literal.
- `vocabulary` — the product-declared status-reason registry (ADR-0008 applied
  to a lifecycle), scoped per status, with four core reasons the module owns.
- `models` — `Ticket` and `TicketComment` in `mod_tkt`, tenant-scoped with
  composite `(tenant_id, id)` references throughout.
- `linking.link_subject` — generates a product-owned link table with real
  foreign keys, indexes, FORCEd RLS, the isolation policy and grants, into the
  **product's** lineage.
- Migration `tk_0001_tickets`, the lineage root.
- Ledger allocation `mod_tkt` / prefix `tk` / branch `ticketing` in the kernel.

### Notes
- `sla_clock_runs` counts only class `OPEN`. This deliberately diverges from
  `dotmac_sub`, whose `SLA_APPLICABLE_STATUSES` includes `waiting_on_customer`
  and `on_hold` and whose transition handler treats membership as *resume* — so
  a Sub ticket blocked on a customer burns SLA its operator could not prevent.
