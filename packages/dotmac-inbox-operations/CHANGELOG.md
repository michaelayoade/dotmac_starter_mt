# Changelog — dotmac-inbox-operations

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
