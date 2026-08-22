# Changelog — dotmac-operational-escalations

## 0.1.0a1 — 2026-08-22

- Extracts Sub's operational escalation boundary
  (`app/models/operational_escalation.py`).
- Replaces the source's mutable policy row with immutable policy versions:
  editing Sub's policy silently rewrote the terms every already-open escalation
  had been raised under, and nothing could read back what it said at the time.
- Enforces exactly one ACTIVE version per policy in the database, not only in
  the writer, so a concurrent activation cannot leave two.
- Leaves delivery out entirely — the source stored delivery rows beside the
  policy, which made the escalation owner the delivery owner.
- Keeps cancellation distinct from resolution: resolution says the condition
  ended, cancellation says the escalation was wrong, and collapsing the two
  loses the only signal that a policy is misconfigured.
- Creates three directly tenant-scoped, forced-RLS tables.
