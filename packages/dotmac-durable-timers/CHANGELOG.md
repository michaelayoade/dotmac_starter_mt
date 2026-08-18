# Changelog — dotmac-durable-timers

## 0.1.0a1 — UNRELEASED

- Establishes one selectable tenant/platform owner for durable timer identity,
  generation, supersession, cancellation, trigger acceptance, replay evidence
  and terminal-history retention.
- Enqueues typed outputs through the existing kernel outbox relay in the
  caller's transaction; it owns no due scanner, lease, retry, dead-letter,
  product policy or consumer effect.
- Ships the independent `dt` migration lineage for `mod_timers`, including
  tenant FORCE RLS and exact platform-role grants/revokes.
- Requires `dotmac-kernel>=0.1.0a71`, whose namespace ledger allocates the
  module and whose test harness makes local module composition explicit.
