# Changelog — dotmac-ticketing

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
