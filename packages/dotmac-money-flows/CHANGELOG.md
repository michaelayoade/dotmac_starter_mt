# Changelog — dotmac-money-flows

## 0.1.0a1 — unreleased

Source only; not published, not composed into any assembly.

- Tenant-plane manifest (`money_flows` / `mflows` / `mod_mflows` / `mf`), the
  instance-table allowlist and the Settle outbox event types.
- Migration `mf_0001` creates `settle_instances` with forced row-level security,
  no DELETE grant for the runtime role and a trigger that holds the whole
  transition graph: insert only as settled, refused or unapplied at version 0;
  refused to refused, settled or unapplied; unapplied to resolved only; settled
  and resolved terminal; every update advances `version` by exactly one; identity
  columns and an accepted `settlement_id` never change. A resolved row keeps its
  settlement and reason code.

### Before release

- Add an `information_schema` column-vocabulary test over the migrated database
  (no amount, balance, lease, timer ... column, only common columns and
  `_id`/`_ref` refs), alongside the first flow behaviour.
- Raise the kernel floor to the kernel release that carries the `money_flows`
  migration-namespace reservation.
