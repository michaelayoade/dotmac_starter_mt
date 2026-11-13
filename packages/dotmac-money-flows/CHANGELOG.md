# Changelog — dotmac-money-flows

## 0.1.0a1 — unreleased

Source only; not published, not composed into any assembly.

- Tenant-plane manifest (`money_flows` / `mflows` / `mod_mflows` / `mf`), the
  instance-table allowlist and the Settle outbox event types.
- Migration `mf_0001` creates `settle_instances` with forced row-level security,
  no DELETE grant for the runtime role and a trigger refusing identity changes
  and changes to rows in a terminal state.
