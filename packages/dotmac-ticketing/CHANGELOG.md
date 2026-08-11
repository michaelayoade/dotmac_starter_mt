# Changelog — dotmac-ticketing

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
