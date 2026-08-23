# Changelog — dotmac-inbox-operations

## 0.1.0a3 — 2026-08-22 (unreleased)

- Makes routing executable and records an idempotent append-only routing
  decision bound to the selected rule, queue and durable queue entry.
- Requires callers to supply queue-eligible opaque agent references and a
  presence-freshness cutoff. Atomic promotion chooses the agent from current
  capacity and the durable round-robin cursor; callers no longer choose the
  queue winner.
- Serializes admission on the queue row and capacity decisions on presence
  rows; FIFO claims use `FOR UPDATE SKIP LOCKED` and expected uniqueness races
  stay inside kernel conflict savepoints.
- Replaces lifetime conversation uniqueness with active-only partial indexes,
  allowing release/reassignment and promotion/cancellation/requeue while
  retaining historical rows and workflow evidence.
- Adds a fair cohort dispatcher that attempts one item from every queue rather
  than allowing a blocked global-oldest window to starve another queue.
- Adds migration `io_0003_operational_safety` and real-Postgres concurrency
  canaries for queue positions and assignment capacity.

## 0.1.0a2 — 2026-08-22

- Adds Sub's durable FIFO admission (`inbox_queue_entries`) and round-robin
  rotation state (`inbox_round_robin_cursors`), migration `io_0002`.
- Position is a stored column with a per-queue unique constraint rather than an
  ordering derived at read time: a customer-visible place in the line must not
  change because a reader sorted differently.
- Rotation state is durable because an in-memory cursor restarts at the same
  agent after every deploy, quietly concentrating work on whoever sorts first.
- `promote_from_queue` promotes through `assign_conversation`, so an entry
  cannot become an assignment while bypassing the presence and capacity
  refusals — the queue decides order, assignment still decides admissibility.

## 0.1.0a1 — 2026-08-20

- Adjudicates Sub's staffed-inbox implementation with CRM queue/presence evidence.
- Owns queues, routing rules, agent presence, assignments, and workflow events.
- Creates five directly tenant-scoped, forced-RLS tables.
