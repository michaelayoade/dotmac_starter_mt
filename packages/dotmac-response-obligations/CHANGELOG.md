# Changelog — dotmac-response-obligations

## 0.1.0a1 — 2026-08-24 (unreleased)

- Extracts the response-time clock product-first from Sub's
  `sla_policies`/`sla_targets`/`sla_clocks`/`sla_breaches`, which already
  carried `entity_type` on both policy and clock. ERP recomputes a target at
  read time with no clock and no pause; CRM keeps a third copy.
- Adds tenancy, composite parent keys and forced RLS in revision 1 — the source
  is a single-schema application and has no tenant column at all.
- Four obligation kinds (`FIRST_RESPONSE`, `NEXT_RESPONSE`, `QUEUE_WAIT`,
  `RESOLUTION`) as separate clocks, because they start and stop at different
  moments and a desk can hit one while missing another.
- Targets resolve priority-specific first, then the `NULL`-priority default. A
  partial unique index makes "one default per policy and kind" real despite
  PostgreSQL permitting many NULLs in a `UNIQUE`.
- Itemises paused time. The source keeps only `total_paused_seconds`, so "why
  was this clock stopped for fourteen hours" has no answer;
  `sla_clock_pauses` records each interval with a required reason.
- Business hours arrive as `PauseReason.OUTSIDE_BUSINESS_HOURS` rather than a
  calendar engine, so there is one time-arithmetic implementation, not two.
- A breach is an append-only observation with NO status. The source's
  open/acknowledged/resolved is the escalation decision
  `dotmac-operational-escalations` owns; `sweep_due_clocks` returns
  `EscalationRequested` values and enqueues two declared outbox events instead.
- `due_at` is stored and moved by a resume, never derived at read time, so the
  sweep can read the front of `(tenant, status, due_at)` rather than rescanning.
- A `PAUSED` clock is excluded from the sweep: nobody owes an answer while the
  promise is stopped.
- Completing after `due_at` settles the clock as `BREACHED` with a settlement
  instant — late completion does not erase the breach.
- `start_clock` is idempotent on `dedup_key`, and a partial unique index stops a
  subject holding two live clocks of the same kind.
